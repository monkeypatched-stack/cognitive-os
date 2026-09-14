"""Edge CognitiveOS sync — real API surface for an independently-deployed
actor edge node (src/sync/edge_actor.py::EdgeActor) to exchange state with
the cloud/society layer, closing the previously-real gap this session's
architecture audit flagged: sync_to_cloud()/receive_world_update() existed
on EdgeActor with nothing on the cloud side to talk to.

POST /edge/{actor_id}/sync           — fold an edge node's local
                                        observations into the SHARED tenant
                                        world tensor (the same one
                                        GraphManager/CognitiveOS.graph_manager
                                        read — "Shared World Tensor" in the
                                        per-actor architecture diagram).
GET  /edge/{actor_id}/world-update    — the shared tensor's current
                                        transitions, shaped for
                                        EdgeActor.receive_world_update().

Deliberately does NOT accept or store a raw policy_snapshot into any
shared structure — Q-values are actor-owned (PolicyStore ownership
enforcement, this session) and folding them into shared state here would
reopen exactly the isolation gap that was just closed. The snapshot is
accepted and acknowledged (for observability/audit) but not merged
anywhere.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.monkey_brain.api.dependencies import require_self_or_permission
from src.monkey_brain.api.idempotency import idempotent

logger = logging.getLogger("agentos.gateway.edge")
router = APIRouter()


class EdgeObservation(BaseModel):
    src: str
    dst: str
    domain: str = "edge_sync"
    probability: float = 0.5


class EdgeSyncRequest(BaseModel):
    node_id: str = ""
    observations: list[EdgeObservation] = []
    policy_snapshot: dict[str, float] = {}


@router.post("/edge/{actor_id}/sync", tags=["Edge"])
@idempotent("edge.edge_sync")
async def edge_sync(
    actor_id: str,
    body: EdgeSyncRequest,
    user_id: str = Depends(require_self_or_permission("perm-manage-actors", id_param="actor_id")),
) -> dict[str, Any]:
    from src.monkey_brain.kernel.compile.world_tensor import observe_execution

    folded = observe_execution(
        [o.src for o in body.observations] + [o.dst for o in body.observations],
        [(o.src, o.dst) for o in body.observations],
        domain=body.observations[0].domain if body.observations else "edge_sync",
        reward=((sum(o.probability for o in body.observations) / len(body.observations)) if body.observations else 1.0),
    )
    logger.info(
        "[edge] sync from actor=%s node=%s: %d observation(s) folded, %d policy Q-value(s) acknowledged (not merged — actor-owned)",
        actor_id,
        body.node_id,
        folded,
        len(body.policy_snapshot),
    )
    return {
        "success": True,
        "actor_id": actor_id,
        "observations_folded": folded,
        "policy_values_acknowledged": len(body.policy_snapshot),
    }


@router.get("/edge/{actor_id}/world-update", tags=["Edge"])
async def edge_world_update(
    actor_id: str,
    limit: int = 500,
    user_id: str = Depends(require_self_or_permission("perm-view-world", id_param="actor_id")),
) -> dict[str, Any]:
    from src.monkey_brain.kernel.compile.world_tensor import get_world_tensor

    tensor = get_world_tensor("default")
    from src.monkey_brain.kernel.compile.tensor import Feature

    transitions = []
    for src, dst in tensor:
        if len(transitions) >= limit:
            break
        transitions.append(
            {
                "src": src,
                "dst": dst,
                "domain": tensor.domain_of(src),
                "probability": tensor.feature(src, dst, Feature.PROBABILITY),
            }
        )
    return {"actor_id": actor_id, "transitions": transitions, "count": len(transitions)}
