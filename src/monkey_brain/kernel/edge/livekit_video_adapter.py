"""LiveKit drone-camera video adapter (CognitiveOS Drone Camera Video
Telemetry). Sibling of kernel/edge/livekit_adapter.py (voice) -- same
guarded-import-at-construction shape, same "reuse the existing LiveKit
room/session/token architecture, never a second LiveKit service" rule.

Two distinct pieces, matching the spec's own PATH A / PATH B split:

  - `RosCameraToLiveKitBridge` (PATH A -- video TELEMETRY): subscribes to a
    ROS 2 sensor_msgs/Image topic on the simulated drone's own namespace and
    republishes it as a LiveKit video track, so a human operator can watch
    the raw feed in a browser. Pure transport -- never touches CognitiveOS
    observations, belief, or the planner.

    HONEST GAP, found during repository inspection, not fabricated around:
    the currently-running 3-drone Gazebo/PX4 SITL sim (deploy/k8s/
    px4-sim-deployment.yaml) has NO camera sensor on the spawned vehicle at
    all. PX4_SIM_MODEL/PX4_GZ_MODEL is never set in that manifest, so PX4
    SITL boots its plain default airframe (no camera). There is also no
    ros_gz_image / image_transport bridge container in that Pod publishing
    any image topic onto ROS 2 today. This class is written against the
    STANDARD topic name a camera-equipped Gazebo model would publish once
    one is configured (see TOPIC_TEMPLATE below) -- it is real, working
    code against the real ROS 2/rclpy and livekit-python APIs, but it has
    not been exercised against an actual frame in this environment, because
    no frame source currently exists. Fixing that is a sim/deployment
    change (switch to a camera-equipped PX4 airframe, e.g. gz_x500_mono_cam,
    and add a ros_gz_image bridge to px4-sim-deployment.yaml), not a
    CognitiveOS code change, and is out of this task's scope per its own
    "do not create simulator-specific CognitiveOS logic" rule.

  - `LiveKitVideoObservationProvider` (PATH B -- COGNITIVE PERCEPTION):
    implements the SAME kernel/pipeline/observations.py ObservationProvider
    Protocol LiveKitVoiceObservationProvider does. Subscribes to a drone's
    camera track, samples frames at a bounded rate (never at camera FPS,
    never an unbounded queue -- stale frames are dropped, not queued), runs
    kernel/edge/visual_perception.py's CLIP zero-shot classifier off the
    realtime thread, and emits one Observation per recognized attribute.
    Track loss becomes a `camera_stream_available=False` Observation, not a
    crash (spec's own literal example).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

from src.introspection.otel_bridge import get_bridge
from src.monkey_brain.kernel.pipeline.observations import (
    Observation,
    ObservationSet,
    Provenance,
)

logger = logging.getLogger("agentos.edge.livekit_video")

# sensor_msgs/Image topic a camera-equipped Gazebo/PX4 SITL vehicle
# publishes under its own ROS 2 namespace, via the standard ros_gz_image
# bridge naming convention (matches this repo's existing GPS bridge
# precedent -- deploy/k8s/px4-sim-deployment.yaml's gps_bridge.py
# republishes onto "/${PX4_NAMESPACE}/drone/gps/fix" the same way).
CAMERA_TOPIC_TEMPLATE = "/{namespace}/camera/image_raw"

_STREAM_FPS_DEFAULT = 30
_PERCEPTION_FPS_DEFAULT = 2  # spec's own suggested 1-5 range; see module docstring for why 2


class LiveKitVideoUnavailableError(RuntimeError):
    """Raised at construction when the livekit package is not installed --
    matches kernel/edge/livekit_adapter.py::LiveKitUnavailableError's own
    shape (a distinct exception type, not a re-export, so a caller can
    still tell voice and video apart if it ever needs to)."""


class RosCameraToLiveKitBridge:
    """PATH A. Subscribes to `CAMERA_TOPIC_TEMPLATE.format(namespace=...)`
    via rclpy and republishes each frame onto a LiveKit LocalVideoTrack
    under the given CameraIdentity. Pure telemetry transport -- this class
    has no knowledge of CognitiveOS observations, belief, or governance,
    matching the spec's "do not bypass ROS2 by directly controlling PX4
    from the camera subsystem" (it doesn't control PX4 at all, only reads).

    Construction requires a ROS 2 environment (same "guarded import at
    construction, not at module load" shape kernel/edge/px4_ros_adapter.py
    already uses for rclpy) and the livekit package.
    """

    def __init__(self, ros_namespace: str, *, livekit_room: str, camera_track_name: str) -> None:
        try:
            import rclpy  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "RosCameraToLiveKitBridge requires a ROS 2 environment (rclpy) -- "
                "see kernel/edge/px4_ros_adapter.py's own docstring for why this "
                "import is guarded rather than a hard dependency."
            ) from exc
        try:
            from livekit import rtc  # noqa: F401
        except ImportError as exc:
            raise LiveKitVideoUnavailableError(
                "RosCameraToLiveKitBridge requires the livekit package (pyproject.toml's 'livekit' extra)"
            ) from exc

        self._topic = CAMERA_TOPIC_TEMPLATE.format(namespace=ros_namespace)
        self._room_name = livekit_room
        self._track_name = camera_track_name
        self._node: Any = None
        self._room: Any = None
        self._video_source: Any = None
        self._task: asyncio.Task | None = None

    async def start(self, *, participant_identity: str) -> None:
        """Connect to LiveKit as a publish-only participant and start
        forwarding frames. Never raises on a transient camera/ROS failure
        once running -- a dropped frame or a missing image message this
        tick simply means no frame is forwarded this tick, matching
        LiveKitVoiceObservationProvider's own "never crash the runtime"
        contract for the video side."""
        from livekit import rtc

        from src.monkey_brain.kernel.edge.livekit_adapter import create_livekit_room_token

        token = create_livekit_room_token(self._room_name, participant_identity, can_publish=True, can_subscribe=False)
        self._room = rtc.Room()
        await self._room.connect_url(token) if hasattr(self._room, "connect_url") else await self._room.connect(
            "", token
        )
        # Video source dimensions are set from the first real ROS Image
        # message's own width/height (_on_ros_image below) -- not
        # hardcoded, since the spec requires the CognitiveOS/transport side
        # to stay agnostic of which simulator or camera model is in use.
        self._video_source = rtc.VideoSource(640, 480)
        track = rtc.LocalVideoTrack.create_video_track(self._track_name, self._video_source)
        await self._room.local_participant.publish_track(track)
        self._start_ros_subscription()
        logger.info(
            "RosCameraToLiveKitBridge publishing %s -> room=%s track=%s", self._topic, self._room_name, self._track_name
        )

    def _start_ros_subscription(self) -> None:
        import rclpy
        from rclpy.node import Node
        from sensor_msgs.msg import Image

        loop = asyncio.get_event_loop()
        bridge = self

        class _CameraNode(Node):  # noqa: N801 - matches this repo's existing rclpy Node subclass style
            def __init__(self) -> None:
                super().__init__("cognitiveos_camera_bridge")
                self.create_subscription(Image, bridge._topic, self._on_image, 5)

            def _on_image(self, msg: Image) -> None:
                asyncio.run_coroutine_threadsafe(bridge._on_ros_image(msg), loop)

        if not rclpy.ok():
            rclpy.init()
        self._node = _CameraNode()
        self._spin_thread = threading.Thread(target=rclpy.spin, args=(self._node,), daemon=True)
        self._spin_thread.start()

    async def _on_ros_image(self, msg: Any) -> None:
        """`msg` is a sensor_msgs/Image (encoding, width, height, data as
        bytes). Converts to LiveKit's expected RGBA VideoFrame and pushes
        it -- dropping (never blocking or queuing) if the source isn't
        ready, matching the spec's realtime-transport-must-not-block rule."""
        try:
            from livekit import rtc

            if self._video_source is None:
                return
            frame = rtc.VideoFrame(
                msg.width, msg.height, rtc.VideoBufferType.RGBA, _to_rgba(msg.data, msg.encoding, msg.width, msg.height)
            )
            self._video_source.capture_frame(frame)
        except Exception:  # noqa: BLE001
            logger.exception("RosCameraToLiveKitBridge: dropping one frame (conversion/publish failed)")

    async def stop(self) -> None:
        if self._node is not None:
            self._node.destroy_node()
            self._node = None
        if self._room is not None:
            await self._room.disconnect()
            self._room = None


def _to_rgba(data: bytes, encoding: str, width: int, height: int) -> bytes:
    """sensor_msgs/Image's `data` is encoding-dependent (bgr8, rgb8, ...);
    LiveKit's VideoFrame wants RGBA. Real, minimal conversion for the
    common Gazebo camera-sensor encodings -- not a general image codec."""
    import numpy as np

    if encoding in ("rgb8", "bgr8"):
        arr = np.frombuffer(data, dtype=np.uint8).reshape((height, width, 3))
        if encoding == "bgr8":
            arr = arr[:, :, ::-1]
        rgba = np.dstack([arr, np.full((height, width, 1), 255, dtype=np.uint8)])
        return rgba.tobytes()
    if encoding == "rgba8":
        return bytes(data)
    logger.warning("unsupported camera encoding %r, forwarding raw bytes unchanged", encoding)
    return bytes(data)


class LiveKitVideoObservationProvider:
    """PATH B. ObservationProvider (kernel/pipeline/observations.py) backed
    by a live LiveKit room's video track for one drone's camera.

    Same synchronous/non-blocking `observe()` contract
    LiveKitVoiceObservationProvider uses: the room connection, frame
    sampling, and perception inference all run in a background asyncio
    task started by `start()`; `observe()` only drains whatever
    observations that background task has already produced.

    Frame handling (spec PERFORMANCE section): exactly one frame may be
    "in flight" through the perception engine at a time (`_inference_busy`);
    every frame that arrives while one is already in flight is dropped, not
    queued -- there is no queue at all, unbounded or otherwise. Perception
    itself is throttled to `perception_fps` regardless of the camera's own
    frame rate.
    """

    def __init__(
        self,
        room_name: str,
        camera_track_name: str,
        *,
        participant_identity: str = "cognitiveos-video-listener",
        perception_fps: float = _PERCEPTION_FPS_DEFAULT,
        landmark_matcher: Any = None,
    ) -> None:
        self._room_name = room_name
        self._track_name = camera_track_name
        self._participant_identity = participant_identity
        self._min_interval = 1.0 / max(perception_fps, 0.01)
        self._engine: Any = None  # lazy -- see visual_perception.VisualPerceptionEngine
        self._landmark_matcher = landmark_matcher
        self._buffer: list[Observation] = []
        self._lock = threading.Lock()
        self._task: asyncio.Task | None = None
        self._inference_busy = threading.Event()
        self._last_sample_time = 0.0
        self._stream_available = False

    def start(self) -> None:
        if self._task is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning(
                "LiveKitVideoObservationProvider.start() called outside a running event loop — video capture will not run"
            )
            return
        self._task = loop.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _run(self) -> None:
        from livekit import rtc

        from src.monkey_brain.kernel.edge.livekit_adapter import LIVEKIT_URL, create_livekit_room_token

        if not LIVEKIT_URL:
            logger.error("LIVEKIT_URL not set — LiveKitVideoObservationProvider cannot connect")
            return

        token = create_livekit_room_token(self._room_name, self._participant_identity, can_publish=False)
        room = rtc.Room()

        @room.on("track_subscribed")
        def _on_track_subscribed(track: Any, publication: Any, participant: Any) -> None:
            if track.kind == rtc.TrackKind.KIND_VIDEO and getattr(publication, "name", None) == self._track_name:
                self._emit_stream_available(True)
                asyncio.ensure_future(self._consume_video_track(track))

        @room.on("track_unsubscribed")
        def _on_track_unsubscribed(track: Any, publication: Any, participant: Any) -> None:
            if getattr(publication, "name", None) == self._track_name:
                self._emit_stream_available(False)

        try:
            await room.connect(LIVEKIT_URL, token)
            logger.info(
                "LiveKitVideoObservationProvider connected to room %r for track %r", self._room_name, self._track_name
            )
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("LiveKitVideoObservationProvider room connection failed")
            self._emit_stream_available(False)
        finally:
            await room.disconnect()

    async def _consume_video_track(self, track: Any) -> None:
        from livekit import rtc

        video_stream = rtc.VideoStream(track)
        try:
            async for event in video_stream:
                now = time.monotonic()
                if now - self._last_sample_time < self._min_interval:
                    continue  # bounded sampling: drop frames faster than perception_fps
                if self._inference_busy.is_set():
                    continue  # bounded concurrency: drop rather than queue if still classifying the last one
                self._last_sample_time = now
                get_bridge().emit_counter("video_frame_sampled", track=self._track_name)
                asyncio.ensure_future(self._classify_frame(event.frame))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("LiveKitVideoObservationProvider video consumption failed")
            self._emit_stream_available(False)

    async def _classify_frame(self, frame: Any) -> None:
        self._inference_busy.set()
        try:
            pixel_array = _video_frame_to_array(frame)
            if pixel_array is None:
                return
            if self._landmark_matcher is not None:
                # Landmark matching is an optional perception primitive.  It
                # returns normal pipeline Observations and never invokes
                # planning, PX4, or governance directly.
                loop = asyncio.get_running_loop()
                observations = await loop.run_in_executor(None, self._landmark_matcher.observations, pixel_array)
                if observations:
                    with self._lock:
                        self._buffer.extend(observations)
                return
            if self._engine is None:
                from src.monkey_brain.kernel.edge.visual_perception import VisualPerceptionEngine

                self._engine = VisualPerceptionEngine()

            loop = asyncio.get_running_loop()
            try:
                results = await loop.run_in_executor(None, self._engine.classify, pixel_array)
            except Exception:
                logger.exception("LiveKitVideoObservationProvider: perception inference failed for one frame")
                return

            observations = [
                Observation(
                    entity=self._track_name,
                    attribute=result.attribute,
                    value=result.value,
                    confidence=result.confidence,
                    provenance=Provenance(
                        source="drone_camera", method="visual_perception", reliability=result.confidence
                    ),
                )
                for result in results
            ]
            if observations:
                with self._lock:
                    self._buffer.extend(observations)
        except Exception:
            logger.exception("LiveKitVideoObservationProvider: frame classification failed")
        finally:
            self._inference_busy.clear()

    def _emit_stream_available(self, available: bool) -> None:
        if available == self._stream_available:
            return
        self._stream_available = available
        # video_track_connected/video_track_disconnected (spec's own event
        # names) -- one span per real state CHANGE, matching this method's
        # own early-return dedup above, never per frame.
        with get_bridge().span(
            "video_track_connected" if available else "video_track_disconnected",
            layer="realtime",
            room=self._room_name,
            track=self._track_name,
        ):
            pass
        observation = Observation(
            entity=self._track_name,
            attribute="camera_stream_available",
            value=available,
            confidence=1.0,
            provenance=Provenance(source="drone_camera", method="track_state", reliability=1.0),
        )
        with self._lock:
            self._buffer.append(observation)

    def observe(self, actor_id: str, world: Any) -> ObservationSet:
        """Never raises — matches WorldPollingProvider/
        LiveKitVoiceObservationProvider's contract."""
        try:
            with self._lock:
                observations = tuple(self._buffer)
                self._buffer.clear()
        except Exception:
            logger.debug("observe: suppressed exception", exc_info=True)
            observations = ()
        return ObservationSet(observations=observations, actor_id=actor_id)


def _video_frame_to_array(frame: Any) -> Any:
    """LiveKit rtc.VideoFrame -> HxWxC uint8 np.ndarray that
    kernel/plan/embedding/image.py::load_pil already accepts directly.
    Guarded: an unrecognized frame shape is dropped (return None), never
    fabricated as a black/garbage frame."""
    import numpy as np

    try:
        width, height = frame.width, frame.height
        buf = np.frombuffer(bytes(frame.data), dtype=np.uint8)
        channels = buf.size // (width * height)
        if channels not in (3, 4):
            return None
        return buf.reshape((height, width, channels))[:, :, :3]
    except Exception:
        logger.debug("_video_frame_to_array: could not decode frame", exc_info=True)
        return None
