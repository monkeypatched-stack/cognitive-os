"""Knowledge Graph API Routes — REST endpoints for KnowledgeGraph operations.

All endpoints read/write to the actor's KnowledgeGraph in the society runtime.

Production Hardening audit finding: every route in this file had NO auth
dependency at all — any unauthenticated caller could read OR WRITE
(add_entity/add_relationship) another actor's real episodic knowledge
graph, just by knowing/guessing a person_id. Fixed: all six now require
require_self_or_permission("perm-manage-actors", id_param="person_id")
(api/dependencies.py) — the actor themselves is always allowed for their
own graph; anyone else needs perm-manage-actors.
"""

from __future__ import annotations

import logging
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from src.monkey_brain.api.dependencies import require_self_or_permission
from src.monkey_brain.api.idempotency import idempotent

logger = logging.getLogger("agentos.api.knowledge_graph")

router = APIRouter(prefix="/api/v1/knowledge-graph", tags=["knowledge-graph"])


class EntityRequest(BaseModel):
    entity_id: str = ""
    entity_type: str = "other"
    name: str = ""
    attributes: dict[str, Any] = {}


class RelationshipRequest(BaseModel):
    source_id: str
    target_id: str
    relationship_type: str = "RELATED_TO"
    attributes: dict[str, Any] = {}
    confidence: float = 1.0


class QueryRequest(BaseModel):
    entity_type: str | None = None
    relationship_type: str | None = None
    min_strength: float = 0.0


def _get_actor_kg(request: Request, person_id: str):
    """Load the actor's KnowledgeGraph.

    Prefers the Neo4j-backed graph (same source /prompt reads from — see
    routes/prompt.py::_get_actor_kg) so entities created here are visible
    there. Falls back to the society runtime's in-memory graph when Neo4j
    is unreachable.
    """
    try:
        from src.monkey_brain.kernel.knowledge_graph_neo4j import (
            Neo4jBackedKnowledgeGraph,
            resolve_household_id,
        )

        household_id = resolve_household_id(person_id)
        neo4j_kg = Neo4jBackedKnowledgeGraph(person_id=person_id, household_id=household_id)
        if neo4j_kg._available:
            return neo4j_kg
    except Exception:
        logger.debug(
            "Neo4j-backed KG unavailable for %s, falling back to in-memory graph",
            person_id,
            exc_info=True,
        )

    try:
        pr = getattr(request.app.state, "planetary_runtime", None)
        if pr is None:
            return None
        for sr in pr.all_societies():
            state = sr.get_actor(person_id)
            if state is not None:
                actor = state.actor
                if actor and hasattr(actor, "knowledge_graph"):
                    return actor.knowledge_graph
        return None
    except Exception:
        return None


@router.get("/{person_id}")
async def get_knowledge_graph(
    person_id: str,
    request: Request,
    user_id: str = Depends(require_self_or_permission("perm-manage-actors", id_param="person_id")),
):
    kg = _get_actor_kg(request, person_id)
    if kg is None:
        raise HTTPException(status_code=404, detail=f"Knowledge graph not found for {person_id}")
    return {
        "person_id": person_id,
        "entity_count": kg.entity_count,
        "relationship_count": kg.relationship_count,
    }


@router.post("/{person_id}/entities")
@idempotent("knowledge_graph.add_entity")
async def add_entity(
    person_id: str,
    request: Request,
    body: EntityRequest,
    user_id: str = Depends(require_self_or_permission("perm-manage-actors", id_param="person_id")),
):
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    kg = _get_actor_kg(request, person_id)
    if kg is None:
        raise HTTPException(status_code=404, detail=f"Knowledge graph not found for {person_id}")

    type_map = {t.value: t for t in EntityType}
    entity_type = type_map.get(body.entity_type, EntityType.OTHER)

    entity = kg.add_entity(body.entity_id, entity_type, body.name, body.attributes)
    return {
        "status": "created",
        "entity_id": entity.entity_id,
        "entity_type": entity.entity_type.value,
        "name": entity.name,
    }


@router.get("/{person_id}/entities")
async def list_entities(
    person_id: str,
    request: Request,
    entity_type: str | None = None,
    user_id: str = Depends(require_self_or_permission("perm-manage-actors", id_param="person_id")),
):
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    kg = _get_actor_kg(request, person_id)
    if kg is None:
        raise HTTPException(status_code=404, detail=f"Knowledge graph not found for {person_id}")

    if entity_type:
        type_map = {t.value: t for t in EntityType}
        et = type_map.get(entity_type, EntityType.OTHER)
        entities = kg.entities_by_type(et)
    else:
        entities = list(kg._entities.values()) if hasattr(kg, "_entities") else []

    return {
        "person_id": person_id,
        "entities": [
            {
                "entity_id": e.entity_id,
                "entity_type": e.entity_type.value,
                "name": e.name,
                "attributes": e.attributes,
            }
            for e in entities
        ],
    }


@router.post("/{person_id}/relationships")
@idempotent("knowledge_graph.add_relationship")
async def add_relationship(
    person_id: str,
    request: Request,
    body: RelationshipRequest,
    user_id: str = Depends(require_self_or_permission("perm-manage-actors", id_param="person_id")),
):
    from src.monkey_brain.kernel.knowledge_graph import RelationshipType

    kg = _get_actor_kg(request, person_id)
    if kg is None:
        raise HTTPException(status_code=404, detail=f"Knowledge graph not found for {person_id}")

    type_map = {t.value: t for t in RelationshipType}
    rt = type_map.get(body.relationship_type, RelationshipType.RELATED_TO)

    rel = kg.add_relationship(body.source_id, body.target_id, rt, body.attributes, confidence=body.confidence)
    return {"status": "created", "relationship_id": rel.relationship_id}


@router.get("/{person_id}/relationships")
async def list_relationships(
    person_id: str,
    request: Request,
    relationship_type: str | None = None,
    user_id: str = Depends(require_self_or_permission("perm-manage-actors", id_param="person_id")),
):
    kg = _get_actor_kg(request, person_id)
    if kg is None:
        raise HTTPException(status_code=404, detail=f"Knowledge graph not found for {person_id}")

    rels = list(kg._relationships.values()) if hasattr(kg, "_relationships") else []
    if relationship_type:
        rels = [r for r in rels if r.relationship_type.value == relationship_type]

    return {
        "person_id": person_id,
        "relationships": [
            {
                "relationship_id": r.relationship_id,
                "source_id": r.source_id,
                "target_id": r.target_id,
                "relationship_type": r.relationship_type.value,
                "attributes": r.attributes,
            }
            for r in rels
        ],
    }


@router.post("/{person_id}/snapshot")
@idempotent("knowledge_graph.create_snapshot")
async def create_snapshot(
    person_id: str,
    request: Request,
    user_id: str = Depends(require_self_or_permission("perm-manage-actors", id_param="person_id")),
):
    # Gate 3 (ADR-010) — before save: this is the one persistence-adjacent
    # operation that exists today (a per-person KG snapshot, not a
    # whole-world save/export — no such operation exists yet in this
    # codebase; see ADR-010's "before save" section for why this hook is
    # best-effort rather than a clean fit). Validates the WHOLE world, not
    # just this person's graph, since that's the only validator that
    # exists — a future whole-world persistence operation should call the
    # same validate_world() this does.
    import os

    if os.getenv("WORLD_VALIDATION_GATE_SAVE", "true").strip().lower() != "false":
        pr = getattr(request.app.state, "planetary_runtime", None)
        if pr is not None:
            from src.monkey_brain.kernel.validation.world_validator import (
                validate_world,
            )

            report = validate_world(pr)
            if not report["ok"]:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "world validation failed — refusing to snapshot a structurally inconsistent world",
                        "violation_count": report["violation_count"],
                        "categories": report["categories"],
                    },
                )
    kg = _get_actor_kg(request, person_id)
    if kg is None:
        raise HTTPException(status_code=404, detail=f"Knowledge graph not found for {person_id}")
    snapshot = kg.create_snapshot()
    return {
        "status": "created",
        "snapshot_id": (snapshot.snapshot_id if hasattr(snapshot, "snapshot_id") else "ok"),
    }
