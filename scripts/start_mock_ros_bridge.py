#!/usr/bin/env python3
"""Standalone HTTP ROS Bridge Server for RemoteRosExecutionAdapter.

Serves the exact HTTP contract RemoteRosExecutionAdapter (kernel/edge/ros_integration.py)
and ros_bridge_server.py expect over HTTP on localhost:9010:
  - POST /invoke   -> executes Arm/Takeoff/Waypoint/Land capability
  - GET /state     -> returns latest DroneState telemetry
  - GET /health    -> returns {"status": "ok"}
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any
from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn

app = FastAPI(title="mock-ros-bridge")

class InvokeRequest(BaseModel):
    capability: str
    parameters: dict[str, Any] = {}

class State:
    def __init__(self, actor_id: str = "drone-demo-1", namespace: str = "px4_1") -> None:
        self.actor_id = actor_id
        self.namespace = namespace
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0
        self.armed = False

state = State()

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

@app.post("/invoke")
def invoke(req: InvokeRequest) -> dict[str, Any]:
    cap = req.capability
    params = req.parameters
    if cap == "Arm":
        state.armed = True
        return {"success": True, "actor_id": state.actor_id, "namespace": state.namespace}
    elif cap == "Takeoff":
        state.z = -float(params.get("height_m", 5.0))
        return {"success": True, "actor_id": state.actor_id, "namespace": state.namespace, "altitude_m": params.get("height_m", 5.0)}
    elif cap == "Waypoint":
        state.x = float(params.get("x", 0.0))
        state.y = float(params.get("y", 0.0))
        return {"success": True, "actor_id": state.actor_id, "namespace": state.namespace, "x": state.x, "y": state.y}
    elif cap == "Land":
        state.armed = False
        state.z = 0.0
        return {"success": True, "actor_id": state.actor_id, "namespace": state.namespace}
    return {"success": False, "error": f"unsupported capability: {cap}"}

@app.get("/state")
def get_state() -> dict[str, Any]:
    return {
        "actor_id": state.actor_id,
        "namespace": state.namespace,
        "armed": state.armed,
        "position_x": state.x,
        "position_y": state.y,
        "position_z": state.z,
        "timestamp": time.time(),
        "heading": 0.0,
        "battery": 0.92,
        "flight_mode": "OFFBOARD" if state.armed else "DISARMED",
        "gps_state": "3",
    }

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9010
    print(f"Starting standalone ROS Bridge Server on http://127.0.0.1:{port}...")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
