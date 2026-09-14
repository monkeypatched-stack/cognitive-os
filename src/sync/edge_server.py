"""Edge server — a minimal, independently-deployable ASGI process hosting
ONE actor's edge-node execution domain (src/sync/edge_actor.py::EdgeActor).

STATUS (Cloud/Edge Actor Convergence): this module and EdgeActor predate
the real Actor Registry/Lifecycle Controller/Scheduler and the governed,
LLM-driven CognitiveActor's registry-integrated cross-process
reconstruction (all built later in this codebase's history). EdgeActor is
a standalone, disconnected, tabular-RL-only prototype — its actor_id is
just a string label, tied to no real ActorIdentity/ActorProfile, no
governance, no capabilities, no NATS. It is kept exactly as-is, unchanged,
for backward compatibility with its own existing tests
(tests/unit/test_edge_cloud.py) and the "Thesis 14" edge/cloud sync
demonstration it's part of (src/sync/edge_node.py, cloud_aggregator.py,
edge_cloud_sync.py) — not because it is the recommended path for a real
edge deployment.

For a real edge/device/robot deployment of the actual governed
CognitiveActor — the SAME Actor abstraction used in the cloud, with real
belief, capabilities, and TransitionGate-enforced authority — use
src/monkey_brain/actor_runtime.py instead (docs/ACTOR_ARTIFACT.md,
docs/CLOUD_EDGE_ACTOR_ARCHITECTURE.md). That module boots a real
PlanetaryRuntime-backed Actor Runtime; this one boots the standalone
EdgeActor prototype described above.

This is the real deployment vehicle for the "Actor A / CognitiveOS A /
Edge" box in the per-actor architecture diagram: unlike the main
cloud/society deployment (src/monkey_brain/api/main.py, pinned to
replicas=1 because of the process-global world-tensor file), each edge
server process owns exactly one actor's local belief (SparseTransitionTensor)
and local policy (PolicyStore, owner_id-enforced — see kernel/policy/
store.py's ownership hardening), and is safe to run as many independent
replicas as there are actors, one process/pod per actor, each syncing to
the shared cloud layer over HTTP rather than sharing any in-process state
with it or with any other actor's edge server.

Configuration (env vars, K8s-friendly):
    EDGE_ACTOR_ID   — required, this process's actor_id
    EDGE_NODE_ID    — required, this process's node_id (e.g. pod name)
    EDGE_CLOUD_URL  — the cloud deployment's base URL, e.g.
                      http://agentos.monkeybrain.svc:8031
                      (sync/world-update calls are no-ops if unset)
    EDGE_PORT       — local listen port, default 8041
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from src.sync.edge_actor import EdgeActor

logger = logging.getLogger("agentos.edge_server")

ACTOR_ID = os.environ.get("EDGE_ACTOR_ID", "")
NODE_ID = os.environ.get("EDGE_NODE_ID", "")
CLOUD_URL = os.environ.get("EDGE_CLOUD_URL", "").rstrip("/")

app = FastAPI(title="CognitiveOS Edge Actor", version="1.0.0")

_actor: EdgeActor | None = None


@app.on_event("startup")
async def _boot() -> None:
    global _actor
    if not ACTOR_ID:
        raise RuntimeError("EDGE_ACTOR_ID is required to boot an edge server")
    _actor = EdgeActor(actor_id=ACTOR_ID, node_id=NODE_ID or ACTOR_ID)
    logger.info(
        "Edge server booted: actor_id=%s node_id=%s cloud_url=%s",
        ACTOR_ID,
        NODE_ID,
        CLOUD_URL or "(none — offline mode)",
    )


def _require_actor() -> EdgeActor:
    if _actor is None:
        raise RuntimeError("Edge actor not booted")
    return _actor


# ── Health (mirrors the main deployment's /live, /ready convention) ────────


@app.get("/live")
async def live() -> dict:
    return {"status": "alive"}


@app.get("/ready")
async def ready() -> dict:
    return {"ready": _actor is not None, "actor_id": ACTOR_ID}


# ── Local cognitive operations ──────────────────────────────────────────────


class ObserveRequest(BaseModel):
    src: str
    dst: str
    domain: str = "default"


@app.post("/observe")
async def observe(body: ObserveRequest) -> dict:
    _require_actor().observe(body.src, body.dst, domain=body.domain)
    return {"success": True}


class ActRequest(BaseModel):
    state: str
    legal_actions: list[str] | None = None


@app.post("/act")
async def act(body: ActRequest) -> dict:
    action = _require_actor().act(body.state, body.legal_actions)
    return {"action": action}


class LearnRequest(BaseModel):
    state: str
    action: str
    reward: float
    next_state: str = ""


@app.post("/learn")
async def learn(body: LearnRequest) -> dict:
    _require_actor().learn(body.state, body.action, body.reward, body.next_state)
    return {"success": True}


@app.get("/summary")
async def summary() -> dict:
    return _require_actor().summary()


# ── Cloud sync ───────────────────────────────────────────────────────────────


@app.post("/sync")
async def sync_to_cloud() -> dict[str, Any]:
    """Push this edge actor's local observations/policy to the cloud
    (POST /api/v1/agentos/edge/{actor_id}/sync). Returns the cloud's
    response, or a clear offline-mode result if EDGE_CLOUD_URL is unset —
    an edge actor must keep operating locally even with no cloud
    connectivity, never block on it."""
    actor = _require_actor()
    payload = actor.sync_to_cloud()
    if not CLOUD_URL:
        return {
            "success": False,
            "reason": "EDGE_CLOUD_URL not configured — offline mode",
            "payload": payload,
        }
    import httpx

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(f"{CLOUD_URL}/api/v1/agentos/edge/{actor.actor_id}/sync", json=payload)
        resp.raise_for_status()
        return resp.json()


@app.post("/pull-world-update")
async def pull_world_update() -> dict[str, Any]:
    """Pull the shared world's current transitions from the cloud
    (GET /api/v1/agentos/edge/{actor_id}/world-update) and fold them into
    this actor's own local belief."""
    actor = _require_actor()
    if not CLOUD_URL:
        return {
            "success": False,
            "reason": "EDGE_CLOUD_URL not configured — offline mode",
        }
    import httpx

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{CLOUD_URL}/api/v1/agentos/edge/{actor.actor_id}/world-update")
        resp.raise_for_status()
        world_snapshot = resp.json()
    actor.receive_world_update(world_snapshot)
    return {"success": True, "transitions_received": world_snapshot.get("count", 0)}
