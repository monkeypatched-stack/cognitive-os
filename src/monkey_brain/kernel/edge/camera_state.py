"""Camera / drone / LiveKit identity mapping (CognitiveOS Drone Camera
Video Telemetry, spec section "MULTI-DRONE IDENTITY").

Shared (Redis-backed, in-memory-fallback) registry -- same backend-
selection SHAPE api/idempotency.py::IdempotencyStore already uses, for the
same reason: the writer and the reader of a CameraIdentity are two
DIFFERENT OS processes, not two workers in one process. register_camera_
identity() runs inside actor_runtime.py::ActorRuntime.start(), the
standalone per-actor Pod (e.g. "cognitiveos-actor-drone-a", entrypoint
src.monkey_brain.actor_runtime:app) -- get_camera_identity() runs inside
api/routes/video.py's POST /video/sessions, served by the separate
"agentos" Pod (entrypoint services.agentos.main:app). Confirmed live: a
bare in-process dict here left every /video/sessions call 404ing with "No
camera registered" even after DRONE_CAMERA_ENABLED=true successfully
registered an identity -- the registration and the lookup were always two
different Python processes' memory, which could never see each other.
kernel/edge/drone_state.py's own get_drone_adapter()/register_drone_
adapter() correctly stays process-local instead (a live ROS execution
adapter object holding real rclpy handles cannot be serialized into Redis
the way this module's plain-dataclass CameraIdentity can) -- not the same
fix, a deliberately different one for a genuinely different kind of value.

The four identities a camera observation must NEVER blur together (this
codebase's real, already-deployed convention -- not the spec's own
"drone_01" example naming, which this module's docstrings translate to what
actually exists):

    CognitiveOS actor_id        e.g. "drone-a"   (ActorCell.actor_id)
    PX4/ROS 2 namespace         e.g. "px4_1"      (PX4_NAMESPACE, run_px4_bridge.sh)
    LiveKit participant identity e.g. "drone-a-camera"  (the ROS-bridge-side publisher)
    LiveKit video track name    e.g. "drone-a-camera-track"

Looking one of these up by display name or by "whichever camera connected
last" is exactly what this module exists to prevent -- every lookup is by
the authoritative actor_id.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass

logger = logging.getLogger("agentos.camera_state")

# How long a registered identity survives with no matching unregister call
# (e.g. the actor Pod crashed instead of shutting down cleanly) before a
# stale entry stops claiming a camera feed that no longer exists. Generous
# by design -- ActorRuntime.stop() calls unregister_camera_identity()
# directly in the normal case (see actor_runtime.py); this is only the
# crash-safety net, same role IDEMPOTENCY_TTL_SECONDS plays for
# api/idempotency.py.
_TTL_SECONDS = int(os.getenv("CAMERA_STATE_TTL_SECONDS", "86400"))
_KEY_PREFIX = "camera_identity:"


@dataclass(frozen=True)
class CameraIdentity:
    """The explicit actor_id <-> vehicle <-> ROS2 namespace <-> LiveKit
    mapping for one drone's camera. Construct one per drone at the same
    place PX4_NAMESPACE/ACTOR_ID are already assigned (deploy/k8s/
    px4-sim-deployment.yaml's per-drone envsubst rendering), never inferred
    from an incoming LiveKit event."""

    actor_id: str
    """CognitiveOS ActorCell.actor_id, e.g. "drone-a" -- the SAME id
    run_ros_action_if_governed/ActorCell already use. This is the only key
    this module or any caller may look an identity up by."""
    vehicle_id: str
    """PX4 vehicle identity, e.g. "px4/drone-a" -- human/log-facing, not
    used for routing."""
    ros_namespace: str
    """PX4_NAMESPACE, e.g. "px4_1" -- must match the SAME value the drone's
    Px4RosExecutionAdapter/ros_bridge_server Pod was started with
    (deploy/k8s/px4-sim-deployment.yaml, run_px4_bridge.sh)."""
    livekit_room: str
    """The LiveKit room this drone's camera publishes into."""
    livekit_participant_identity: str
    """The LiveKit participant identity the ROS-camera-bridge publishes
    under, e.g. "drone-a-camera" -- distinct from the human operator's own
    participant identity and from VoiceCommandRuntime's
    "cognitiveos-listener-{session_id}" (kernel/edge/voice_command_runtime.py)."""
    camera_track_name: str
    """The LiveKit video track name, e.g. "drone-a-camera-track" -- how a
    subscriber (browser or LiveKitVideoObservationProvider) identifies
    WHICH published track is this drone's camera when a room has more than
    one drone in it."""


# ── In-memory backend (single-process / test fallback) ──────────────────


class _InMemoryCameraBackend:
    def __init__(self) -> None:
        self._registry: dict[str, CameraIdentity] = {}
        self._lock = threading.Lock()

    def set(self, actor_id: str, identity: CameraIdentity) -> None:
        with self._lock:
            self._registry[actor_id] = identity

    def get(self, actor_id: str) -> CameraIdentity | None:
        with self._lock:
            return self._registry.get(actor_id)

    def delete(self, actor_id: str) -> None:
        with self._lock:
            self._registry.pop(actor_id, None)


# ── Redis backend (shared across the agentos + per-actor Pods) ──────────


class _RedisCameraBackend:
    def __init__(self, url: str) -> None:
        self._url = url
        self._client = None

    def _connect(self):
        import redis  # redis-py; a declared dependency (pyproject.toml)

        return redis.from_url(
            self._url,
            decode_responses=True,
            socket_connect_timeout=float(os.getenv("REDIS_CONNECT_TIMEOUT_SEC", "5")),
            socket_timeout=float(os.getenv("REDIS_SOCKET_TIMEOUT_SEC", "5")),
        )

    def available(self) -> bool:
        try:
            self._client = self._connect()
            self._client.ping()
            return True
        except Exception as exc:
            logger.warning("Camera state Redis backend unreachable: %s", exc)
            self._client = None
            return False

    @property
    def _r(self):
        if self._client is None:
            self._client = self._connect()
        return self._client

    def set(self, actor_id: str, identity: CameraIdentity) -> None:
        try:
            self._r.set(_KEY_PREFIX + actor_id, json.dumps(asdict(identity)), ex=_TTL_SECONDS)
        except Exception as exc:
            logger.warning("actor_id=%r camera_state(redis).set failed: %s", actor_id, exc)

    def get(self, actor_id: str) -> CameraIdentity | None:
        try:
            raw = self._r.get(_KEY_PREFIX + actor_id)
        except Exception as exc:
            logger.warning("actor_id=%r camera_state(redis).get failed: %s", actor_id, exc)
            return None
        if not raw:
            return None
        return CameraIdentity(**json.loads(raw))

    def delete(self, actor_id: str) -> None:
        try:
            self._r.delete(_KEY_PREFIX + actor_id)
        except Exception as exc:
            logger.warning("actor_id=%r camera_state(redis).delete failed: %s", actor_id, exc)


def _make_backend():
    choice = os.getenv("CAMERA_STATE_BACKEND", "auto").strip().lower()
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        url = f"redis://{os.getenv('REDIS_HOST', 'localhost')}:{os.getenv('REDIS_PORT', '6379')}/0"

    if choice == "memory":
        logger.info("CameraState: process-local memory backend (CAMERA_STATE_BACKEND=memory)")
        return _InMemoryCameraBackend()

    if choice in ("auto", "redis"):
        backend = _RedisCameraBackend(url)
        if backend.available():
            logger.info("CameraState: SHARED Redis backend -- visible across agentos + per-actor Pods")
            return backend
        if choice == "redis":
            logger.error("CameraState: Redis required (CAMERA_STATE_BACKEND=redis) but unreachable")
        logger.info("CameraState: Redis unreachable -- using process-local memory (single-process only)")
        return _InMemoryCameraBackend()

    return _InMemoryCameraBackend()


class _CameraStateStore:
    """Lazily-initialized singleton, same construction-order reasoning as
    api/idempotency.py::IdempotencyStore (backend selection needs env vars
    that may not be set yet at import time)."""

    _instance: "_CameraStateStore | None" = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "_CameraStateStore":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._backend = _make_backend()
                    cls._instance = instance
        return cls._instance


def register_camera_identity(identity: CameraIdentity) -> None:
    _CameraStateStore()._backend.set(identity.actor_id, identity)


def get_camera_identity(actor_id: str) -> CameraIdentity | None:
    return _CameraStateStore()._backend.get(actor_id)


def unregister_camera_identity(actor_id: str) -> None:
    _CameraStateStore()._backend.delete(actor_id)
