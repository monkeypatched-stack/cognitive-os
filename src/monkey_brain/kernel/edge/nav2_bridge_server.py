"""nav2-bridge — the ROS 2/rclpy side of a ground-robot actor, split into
its own container (deploy/k8s/nav2-sim-deployment.yaml), for the SAME reason
kernel/edge/ros_bridge_server.py exists for the drone/PX4 case: the
CognitiveOS actor container itself never needs ROS 2 installed. Wraps
kernel/edge/nav2_ros_adapter.py::Nav2RosExecutionAdapter behind one HTTP
endpoint that kernel/edge/ros_integration.py::RemoteRosExecutionAdapter
(unchanged, already vehicle-agnostic) calls into.

Governance already ran, in the actor's own process, before a request ever
reaches here -- see RemoteRosExecutionAdapter's own docstring. This process
performs no authorization of its own.

No camera/LiveKit wiring here (v1 ground-robot scope has no camera sensor) --
a separate file from ros_bridge_server.py rather than a shared/parameterized
one, specifically so this addition cannot change that file's behavior for
the drone path at all.

Run as: python -m src.monkey_brain.kernel.edge.nav2_bridge_server
(or via uvicorn directly -- see docker/Dockerfile.ros-bridge-nav2's CMD).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .nav2_ros_adapter import Nav2RosExecutionAdapter

logger = logging.getLogger("agentos.edge.nav2_bridge_server")


class InvokeRequest(BaseModel):
    capability: str
    parameters: dict[str, Any] = {}


def _build_app() -> FastAPI:
    app = FastAPI(title="nav2-bridge")
    adapter_holder: dict[str, Nav2RosExecutionAdapter] = {}

    @app.on_event("startup")
    def _startup() -> None:
        actor_id = os.getenv("ACTOR_ID", "").strip()
        namespace = os.getenv("NAV2_NAMESPACE", "").strip()
        if not actor_id:
            raise RuntimeError("nav2-bridge requires ACTOR_ID to be set")
        adapter_holder["adapter"] = Nav2RosExecutionAdapter(actor_id=actor_id, namespace=namespace)
        logger.info("nav2-bridge ready: actor_id=%s namespace=%s", actor_id, namespace)

    @app.get("/live")
    def live() -> dict[str, Any]:
        return {"live": True, "adapter_ready": "adapter" in adapter_holder}

    @app.get("/state")
    def state() -> dict[str, Any]:
        """Always {"state": None} in this first pass -- Nav2RosExecutionAdapter.
        latest_state() has no telemetry fusion yet (see its own docstring).
        Matches kernel/edge/ros_bridge_server.py's own "{"state": null}, not
        a 404, when nothing has arrived yet" contract, so
        RemoteRosExecutionAdapter's polling loop (kernel/edge/
        ros_integration.py, unchanged, already vehicle-agnostic) degrades
        cleanly to "no telemetry" rather than erroring."""
        adapter = adapter_holder.get("adapter")
        if adapter is None:
            return {"state": None}
        robot_state = adapter.latest_state()
        return {"state": robot_state}

    @app.post("/invoke")
    async def invoke(body: InvokeRequest) -> dict[str, Any]:
        adapter = adapter_holder.get("adapter")
        if adapter is None:
            raise HTTPException(status_code=503, detail="Nav2RosExecutionAdapter not initialized")
        return await adapter.invoke(capability=body.capability, parameters=body.parameters)

    return app


app = _build_app()
