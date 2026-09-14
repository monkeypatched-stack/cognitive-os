"""Fleet Management routes — register, heartbeat, discover, health.

FleetManager tracks runtime instances across a deployment.
Single-node MVP: in-memory registry with TTL-based health.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.monkey_brain.api.dependencies import require_permission
from src.monkey_brain.api.idempotency import idempotent

logger = logging.getLogger("agentos.fleet")
router = APIRouter()

# ── Fleet Manager (in-memory, single-node) ────────────────────────────────


@dataclass
class FleetRuntimeInfo:
    runtime_id: str
    hostname: str = ""
    status: str = "unknown"
    last_heartbeat: float = 0.0
    capabilities: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class FleetManager:
    """In-memory fleet registry. Single-node MVP."""

    def __init__(self, heartbeat_ttl: float = 60.0) -> None:
        self._runtimes: dict[str, FleetRuntimeInfo] = {}
        self._ttl = heartbeat_ttl

    def register(
        self,
        runtime_id: str,
        hostname: str = "",
        capabilities: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> FleetRuntimeInfo:
        info = FleetRuntimeInfo(
            runtime_id=runtime_id,
            hostname=hostname,
            status="online",
            last_heartbeat=time.time(),
            capabilities=capabilities or [],
            metadata=metadata or {},
        )
        self._runtimes[runtime_id] = info
        return info

    def heartbeat(self, runtime_id: str) -> bool:
        info = self._runtimes.get(runtime_id)
        if info is None:
            return False
        info.last_heartbeat = time.time()
        info.status = "online"
        return True

    def discover(self) -> list[FleetRuntimeInfo]:
        now = time.time()
        for info in self._runtimes.values():
            if now - info.last_heartbeat > self._ttl:
                info.status = "stale"
        return list(self._runtimes.values())

    def health(self) -> dict[str, Any]:
        # Evaluate staleness fresh against the real TTL rather than trusting
        # each runtime's cached .status, which is only updated by the next
        # discover() call and can otherwise report an online runtime that
        # has actually gone stale since its last heartbeat.
        now = time.time()
        stale_now = {rid: (now - i.last_heartbeat > self._ttl) for rid, i in self._runtimes.items()}
        online = sum(1 for is_stale in stale_now.values() if not is_stale)
        stale = sum(1 for is_stale in stale_now.values() if is_stale)
        return {
            "total": len(self._runtimes),
            "online": online,
            "stale": stale,
            "runtimes": {
                rid: {
                    "status": "stale" if stale_now[rid] else "online",
                    "last_heartbeat": i.last_heartbeat,
                }
                for rid, i in self._runtimes.items()
            },
        }

    def remove(self, runtime_id: str) -> bool:
        return self._runtimes.pop(runtime_id, None) is not None


_fleet = FleetManager()

# ── API Models ────────────────────────────────────────────────────────────


class RegisterRequest(BaseModel):
    runtime_id: str
    hostname: str = ""
    capabilities: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeInfoResponse(BaseModel):
    runtime_id: str
    hostname: str = ""
    status: str = "unknown"
    last_heartbeat: float = 0.0
    capabilities: list[str] = Field(default_factory=list)


class FleetHealthResponse(BaseModel):
    total: int = 0
    online: int = 0
    stale: int = 0
    runtimes: dict[str, dict[str, Any]] = Field(default_factory=dict)


# ── Endpoints ─────────────────────────────────────────────────────────────


@router.post("/fleet/register", response_model=RuntimeInfoResponse)
@idempotent("fleet.register_runtime")
async def register_runtime(
    body: RegisterRequest,
    user_id: str = Depends(require_permission("perm-manage-agents")),
):
    """Register a runtime instance with the fleet."""
    info = _fleet.register(body.runtime_id, body.hostname, body.capabilities, body.metadata)
    return RuntimeInfoResponse(
        runtime_id=info.runtime_id,
        hostname=info.hostname,
        status=info.status,
        last_heartbeat=info.last_heartbeat,
        capabilities=info.capabilities,
    )


@router.post("/fleet/{runtime_id}/heartbeat")
@idempotent("fleet.heartbeat")
async def heartbeat(
    runtime_id: str,
    user_id: str = Depends(require_permission("perm-view-agents")),
):
    """Send a heartbeat for a runtime instance."""
    ok = _fleet.heartbeat(runtime_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Runtime {runtime_id} not registered")
    return {"status": "ok", "runtime_id": runtime_id}


@router.get("/fleet/discover", response_model=list[RuntimeInfoResponse])
async def discover_runtimes(
    user_id: str = Depends(require_permission("perm-view-agents")),
):
    """Discover all registered runtime instances and their health."""
    runtimes = _fleet.discover()
    return [
        RuntimeInfoResponse(
            runtime_id=r.runtime_id,
            hostname=r.hostname,
            status=r.status,
            last_heartbeat=r.last_heartbeat,
            capabilities=r.capabilities,
        )
        for r in runtimes
    ]


@router.get("/fleet/health", response_model=FleetHealthResponse)
async def fleet_health(
    user_id: str = Depends(require_permission("perm-view-agents")),
):
    """Get fleet-wide health summary."""
    return FleetHealthResponse(**_fleet.health())


@router.delete("/fleet/{runtime_id}")
@idempotent("fleet.remove_runtime")
async def remove_runtime(
    runtime_id: str,
    user_id: str = Depends(require_permission("perm-manage-agents")),
):
    """Remove a runtime from the fleet."""
    ok = _fleet.remove(runtime_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Runtime {runtime_id} not found")
    return {"status": "removed", "runtime_id": runtime_id}
