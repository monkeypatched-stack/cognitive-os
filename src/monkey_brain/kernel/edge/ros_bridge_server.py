"""ros-bridge — the ROS 2/rclpy side of a robot actor, split out into its
own container (deploy/k8s/px4-sim-deployment.yaml) so the CognitiveOS actor
container itself never needs ROS 2 installed. Wraps
kernel/edge/px4_ros_adapter.py::Px4RosExecutionAdapter (unchanged) behind
one HTTP endpoint that kernel/edge/ros_integration.py::
RemoteRosExecutionAdapter calls into.

Governance already ran, in the actor's own process, before a request ever
reaches here — see RemoteRosExecutionAdapter's own docstring. This process
performs no authorization of its own; it only executes an already-committed
action against PX4/xrce-agent over localhost, same as
Px4RosExecutionAdapter always has.

Also hosts kernel/edge/livekit_video_adapter.py::RosCameraToLiveKitBridge
(PATH A video TELEMETRY publisher), for the SAME reason Px4RosExecutionAdapter
lives here rather than in the CognitiveOS actor Pod: that bridge does
`import rclpy` at construction, and in this deployment topology (deploy/k8s/
px4-sim-deployment.yaml, ROS_ADAPTER_KIND=remote_http) the actor Pod's own
image (monkeybrain/agentos:latest) has no ROS 2 installed at all --
confirmed live (ModuleNotFoundError: No module named 'rclpy') when this
bridge was still constructed from actor_runtime.py::ActorRuntime.start()
instead. actor_runtime.py's own camera-wiring block is unchanged (still
correct for docker/Dockerfile.robot's OTHER deployment mode, where the
actor process itself embeds rclpy) -- this is an additive second place the
SAME bridge class can be started from, selected simply by which container
DRONE_CAMERA_ENABLED is actually set on.

Run as: python -m src.monkey_brain.kernel.edge.ros_bridge_server
(or via uvicorn directly — see docker/Dockerfile.ros-bridge's CMD).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .px4_ros_adapter import Px4RosExecutionAdapter

logger = logging.getLogger("agentos.edge.ros_bridge_server")


class InvokeRequest(BaseModel):
    capability: str
    parameters: dict[str, Any] = {}


def _build_app() -> FastAPI:
    app = FastAPI(title="ros-bridge")
    adapter_holder: dict[str, Px4RosExecutionAdapter] = {}
    camera_holder: dict[str, Any] = {}

    @app.on_event("startup")
    def _startup() -> None:
        actor_id = os.getenv("ACTOR_ID", "").strip()
        namespace = os.getenv("PX4_NAMESPACE", "").strip()
        if not actor_id or not namespace:
            raise RuntimeError("ros-bridge requires ACTOR_ID and PX4_NAMESPACE to be set")
        adapter_holder["adapter"] = Px4RosExecutionAdapter(actor_id=actor_id, namespace=namespace)
        logger.info("ros-bridge ready: actor_id=%s namespace=%s", actor_id, namespace)

        # Opt-in (default false -- most drones in the sim have no camera
        # sensor), same env var actor_runtime.py's own camera block reads.
        # Best-effort/non-fatal: a camera wiring failure degrades only this
        # drone's video, never this process's actual job (flight command
        # execution via the adapter above).
        if os.getenv("DRONE_CAMERA_ENABLED", "false").strip().lower() in ("true", "1", "yes"):
            import asyncio

            try:
                from src.monkey_brain.kernel.edge.camera_state import (
                    CameraIdentity,
                    register_camera_identity,
                )
                from src.monkey_brain.kernel.edge.livekit_video_adapter import (
                    RosCameraToLiveKitBridge,
                )

                identity = CameraIdentity(
                    actor_id=actor_id,
                    vehicle_id=f"px4/{actor_id}",
                    ros_namespace=namespace,
                    livekit_room=os.getenv("LIVEKIT_MISSION_ROOM", "mission-room"),
                    livekit_participant_identity=f"{actor_id}-camera",
                    camera_track_name=f"{actor_id}-camera-track",
                )
                # Construct (validates rclpy/livekit are importable) BEFORE
                # registering the identity -- api/routes/video.py trusts
                # get_camera_identity() to mean a real feed exists, same
                # ordering rule actor_runtime.py's own block documents.
                bridge = RosCameraToLiveKitBridge(
                    namespace, livekit_room=identity.livekit_room, camera_track_name=identity.camera_track_name
                )
                register_camera_identity(identity)
                camera_holder["bridge"] = bridge
                camera_holder["identity"] = identity

                async def _start_camera_bridge() -> None:
                    try:
                        await bridge.start(participant_identity=identity.livekit_participant_identity)
                    except Exception:
                        logger.warning(
                            "ros-bridge: camera publish bridge failed to start for %s (video degraded)",
                            actor_id,
                            exc_info=True,
                        )

                asyncio.get_event_loop().create_task(_start_camera_bridge())
            except Exception:
                logger.warning("ros-bridge: camera wiring skipped for %s (non-fatal)", actor_id, exc_info=True)

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        bridge = camera_holder.get("bridge")
        identity = camera_holder.get("identity")
        if bridge is not None:
            try:
                await bridge.stop()
            except Exception:
                logger.warning("ros-bridge: camera bridge stop failed (non-fatal)", exc_info=True)
        if identity is not None:
            try:
                from src.monkey_brain.kernel.edge.camera_state import unregister_camera_identity

                unregister_camera_identity(identity.actor_id)
            except Exception:
                logger.warning("ros-bridge: unregister_camera_identity failed (non-fatal)", exc_info=True)

    @app.get("/live")
    def live() -> dict[str, Any]:
        return {
            "live": True,
            "adapter_ready": "adapter" in adapter_holder,
            "camera_bridge_active": "bridge" in camera_holder,
        }

    @app.get("/state")
    def state() -> dict[str, Any]:
        """The real, in-process Px4RosExecutionAdapter's own latest_state()
        (armed/position/heading/battery/flight_mode/gps_state/etc.), as
        plain JSON -- confirmed live this was the missing half of the
        remote_http split: kernel/edge/ros_integration.py::
        RemoteRosExecutionAdapter already lets the actor Pod SEND commands
        here over HTTP (POST /invoke), but had no equivalent way to READ
        telemetry back, so kernel/pipeline/observations.py::
        WorldPollingProvider.observe() always called .latest_state() on an
        object that didn't have one and silently produced zero telemetry
        facts, no matter how healthy this process's own PX4 subscriptions
        actually were. {"state": null} (not a 404) when nothing has
        arrived yet -- matches Px4RosExecutionAdapter.latest_state()'s own
        "None means no telemetry yet, not an error" contract.
        """
        adapter = adapter_holder.get("adapter")
        if adapter is None:
            return {"state": None}
        drone_state = adapter.latest_state()
        if drone_state is None:
            return {"state": None}
        from dataclasses import asdict

        return {"state": asdict(drone_state)}

    @app.post("/invoke")
    async def invoke(body: InvokeRequest) -> dict[str, Any]:
        adapter = adapter_holder.get("adapter")
        if adapter is None:
            raise HTTPException(status_code=503, detail="Px4RosExecutionAdapter not initialized")
        return await adapter.invoke(capability=body.capability, parameters=body.parameters)

    return app


app = _build_app()
