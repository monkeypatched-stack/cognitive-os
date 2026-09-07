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

    @app.on_event("startup")
    def _startup() -> None:
        actor_id = os.getenv("ACTOR_ID", "").strip()
        namespace = os.getenv("PX4_NAMESPACE", "").strip()
        if not actor_id or not namespace:
            raise RuntimeError("ros-bridge requires ACTOR_ID and PX4_NAMESPACE to be set")
        adapter_holder["adapter"] = Px4RosExecutionAdapter(actor_id=actor_id, namespace=namespace)
        logger.info("ros-bridge ready: actor_id=%s namespace=%s", actor_id, namespace)

    @app.get("/live")
    def live() -> dict[str, Any]:
        return {"live": True, "adapter_ready": "adapter" in adapter_holder}

    @app.post("/invoke")
    async def invoke(body: InvokeRequest) -> dict[str, Any]:
        adapter = adapter_holder.get("adapter")
        if adapter is None:
            raise HTTPException(status_code=503, detail="Px4RosExecutionAdapter not initialized")
        return await adapter.invoke(capability=body.capability, parameters=body.parameters)

    return app


app = _build_app()
