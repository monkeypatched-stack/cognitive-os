"""World API — full CRUD for the shared world state.

GET    /world                    — full world state
GET    /world/context            — context stream events
GET    /world/beliefs            — actor beliefs
GET    /world/entities           — list entities
POST   /world/entities           — create entity
GET    /world/entities/{id}      — get entity
PUT    /world/entities/{id}      — update entity
DELETE /world/entities/{id}      — delete entity
GET    /world/relationships      — list relationships
POST   /world/relationships      — create relationship
DELETE /world/relationships/{id} — delete relationship
GET    /world/events             — list events
POST   /world/events             — create event
DELETE /world/events/{id}        — delete event
GET    /world/resources          — list resources
POST   /world/resources          — create resource
DELETE /world/resources/{id}     — delete resource
GET    /world/locations          — list locations
POST   /world/locations          — create location
GET    /world/locations/{id}     — get location by ID
PUT    /world/locations/{id}     — update location
DELETE /world/locations/{id}     — delete location
POST   /world/query              — query the world
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from src.monkey_brain.api.dependencies import require_permission
from src.monkey_brain.api.gateway_models import (
    WorldResponse,
    WorldContextResponse,
    WorldBeliefsResponse,
    WorldEntitiesResponse,
    WorldRelationshipsResponse,
    WorldQueryRequest,
    WorldQueryResponse,
    WorldEntityCreateRequest,
    WorldEntityUpdateRequest,
    WorldRelationshipCreateRequest,
    WorldEventCreateRequest,
    WorldResourceCreateRequest,
    WorldResourceUpdateRequest,
    WorldLocationCreateRequest,
    WorldLocationUpdateRequest,
)
from src.monkey_brain.api.idempotency import idempotent
from src.shared.api_protocols import (
    WorldProtocol,
    BeliefStateProtocol,
    SerializableProtocol,
    PlanetaryRuntimeProtocol,
)

logger = logging.getLogger("agentos.gateway.world")
router = APIRouter()


def _require_direct_world_mutation_allowed() -> None:
    """Block SharedWorld CRUD in production; those paths bypass TransitionGate."""
    from src.monkey_brain.kernel.production_gates import (
        block_direct_world_api_mutations,
    )

    if block_direct_world_api_mutations():
        raise HTTPException(
            status_code=403,
            detail=(
                "Direct SharedWorld API mutations are disabled. "
                "Use commerce/orders/fulfillment routes or actor capabilities instead."
            ),
        )


async def _commit_world(action: str, resource: str, effect):
    from src.monkey_brain.kernel.security_boundary import ensure_governed

    return await ensure_governed(action, resource, effect, skip_authz=True)


def _get_planetary_runtime(request: Request) -> Any:
    selector = getattr(getattr(request.app.state, "kernel", None), "runtime_selector", None)
    if selector is not None:
        try:
            return selector.select("planetary")
        except LookupError:
            logger.debug("_get_planetary_runtime: suppressed exception", exc_info=True)
    return getattr(request.app.state, "planetary_runtime", None)


# ── Full World ──────────────────────────────────────────────────────────


@router.get("/world", response_model=WorldResponse, tags=["World"])
async def get_world(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-world")),
) -> WorldResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        return WorldResponse()
    w = pr.world
    entities = [e.to_dict() for e in w.entities()] if isinstance(w, WorldProtocol) else []
    relationships = [r.to_dict() for r in w.relationships()] if isinstance(w, WorldProtocol) else []
    events = [ev.to_dict() for ev in w.events(limit=100)] if isinstance(w, WorldProtocol) else []
    resources = [r.to_dict() for r in w.resources()] if isinstance(w, WorldProtocol) else []
    return WorldResponse(
        version=w.version,
        entities=entities,
        relationships=relationships,
        events=events,
        resources=resources,
    )


# ── Context / Beliefs ──────────────────────────────────────────────────


@router.get("/world/context", response_model=WorldContextResponse, tags=["World"])
async def get_world_context(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-world")),
) -> WorldContextResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        return WorldContextResponse()
    cs = pr.context_stream
    events = cs.events(limit=100)
    return WorldContextResponse(
        events=[e.to_dict() if isinstance(e, SerializableProtocol) else {"type": str(e)} for e in events],
        event_count=cs.event_count,
    )


@router.get("/world/beliefs", response_model=WorldBeliefsResponse, tags=["World"])
async def get_world_beliefs(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-world")),
) -> WorldBeliefsResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        return WorldBeliefsResponse()
    actors = []
    for sr in pr.all_societies():
        for state in sr.all_actors():
            beliefs = {}
            if state.belief_state is not None and isinstance(state.belief_state, BeliefStateProtocol):
                for entry in state.belief_state.beliefs:
                    best = entry.best_hypothesis
                    beliefs[entry.subject] = {
                        "confidence": round(best.confidence, 3) if best else 0,
                        "predicate": best.predicate if best else "",
                    }
            actors.append({"actor_id": state.actor_id, "beliefs": beliefs})
    return WorldBeliefsResponse(actors=actors)


# ── Entities CRUD ───────────────────────────────────────────────────────


@router.get("/world/entities", response_model=WorldEntitiesResponse, tags=["World"])
async def get_world_entities(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-world")),
) -> WorldEntitiesResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        return WorldEntitiesResponse()
    w = pr.world
    entities = [e.to_dict() for e in w.entities()] if isinstance(w, WorldProtocol) else []
    return WorldEntitiesResponse(entities=entities, count=len(entities))


@router.post("/world/entities", tags=["World"])
@idempotent("world.create_world_entity")
async def create_world_entity(
    body: WorldEntityCreateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-world")),
    _: None = Depends(_require_direct_world_mutation_allowed),
) -> dict[str, Any]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    from src.monkey_brain.kernel.compile import _obs
    from src.monkey_brain.kernel.society.world import WorldEntity, WorldEntityType

    # Gate 5 (Observability) "graph update" span — deliberately wired at
    # the REST boundary (bounded by real HTTP traffic), NOT inside
    # KnowledgeGraph.add_entity() itself: that method is called thousands
    # of times per bulk Commerce operation, and Lemon's Tracer accumulates
    # every trace in an unbounded dict (introspection/tracing.py::Tracer.
    # _traces, no eviction) — wrapping the hot domain-layer path would
    # reintroduce the same class of unbounded-growth risk ADR-011 just
    # fixed for actor/context persistence, just in the tracer this time.
    _obs.start_span("graph_update", component="world", entity_type=body.entity_type)
    type_map = {t.value: t for t in WorldEntityType}
    entity = WorldEntity(
        name=body.name,
        entity_type=type_map.get(body.entity_type, WorldEntityType.ENTITY),
        attributes=body.attributes,
        state=body.state,
        provenance=body.provenance or "api:world/entities",
        owner_society_id=body.owner_society_id,
    )

    async def _mutate():
        pr.add_world_entity(entity)
        return entity.to_dict()

    out = await _commit_world("world.entity.create", body.name, _mutate)
    _obs.finish_span("ok")
    return out


@router.get("/world/entities/{entity_id}", tags=["World"])
async def get_world_entity(
    entity_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-world")),
) -> dict[str, Any]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    entity = pr.world.get_entity(entity_id) if isinstance(pr.world, WorldProtocol) else None
    if entity is None:
        raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")
    return entity.to_dict()


@router.put("/world/entities/{entity_id}", tags=["World"])
@idempotent("world.update_world_entity")
async def update_world_entity(
    entity_id: str,
    body: WorldEntityUpdateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-world")),
    _: None = Depends(_require_direct_world_mutation_allowed),
) -> dict[str, Any]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    entity = await _commit_world(
        "world.entity.update",
        entity_id,
        lambda: pr.update_world_entity(
            entity_id,
            state=body.state or None,
            owner_society_id=body.owner_society_id,
            **body.attributes,
        ),
    )
    if entity is None:
        raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")
    return entity.to_dict()


@router.delete("/world/entities/{entity_id}", tags=["World"])
@idempotent("world.delete_world_entity")
async def delete_world_entity(
    entity_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-world")),
    _: None = Depends(_require_direct_world_mutation_allowed),
) -> dict[str, str]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    removed = await _commit_world("world.entity.delete", entity_id, lambda: pr.remove_world_entity(entity_id))
    if not removed:
        raise HTTPException(status_code=404, detail=f"Entity {entity_id} not found")
    return {"status": "deleted", "entity_id": entity_id}


# ── Relationships CRUD ──────────────────────────────────────────────────


@router.get("/world/relationships", response_model=WorldRelationshipsResponse, tags=["World"])
async def get_world_relationships(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-world")),
) -> WorldRelationshipsResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        return WorldRelationshipsResponse()
    w = pr.world
    rels = [r.to_dict() for r in w.relationships()] if isinstance(w, WorldProtocol) else []
    return WorldRelationshipsResponse(relationships=rels, count=len(rels))


@router.post("/world/relationships", tags=["World"])
@idempotent("world.create_world_relationship")
async def create_world_relationship(
    body: WorldRelationshipCreateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-world")),
    _: None = Depends(_require_direct_world_mutation_allowed),
) -> dict[str, Any]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    from src.monkey_brain.kernel.society.world import (
        WorldRelationship,
        RelationshipKind,
    )

    kind_map = {k.value: k for k in RelationshipKind}
    rel = WorldRelationship(
        source_id=body.source_id,
        target_id=body.target_id,
        kind=kind_map.get(body.kind, RelationshipKind.RELATED_TO),
        attributes=body.attributes,
        confidence=body.confidence,
    )
    await _commit_world(
        "world.relationship.create",
        rel.relationship_id if hasattr(rel, "relationship_id") else "rel",
        lambda: pr.add_world_relationship(rel) or True,
    )
    return rel.to_dict()


@router.delete("/world/relationships/{relationship_id}", tags=["World"])
@idempotent("world.delete_world_relationship")
async def delete_world_relationship(
    relationship_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-world")),
    _: None = Depends(_require_direct_world_mutation_allowed),
) -> dict[str, str]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    removed = await _commit_world(
        "world.relationship.delete",
        relationship_id,
        lambda: pr.remove_world_relationship(relationship_id),
    )
    if not removed:
        raise HTTPException(status_code=404, detail=f"Relationship {relationship_id} not found")
    return {"status": "deleted", "relationship_id": relationship_id}


# ── Events CRUD ─────────────────────────────────────────────────────────


@router.get("/world/events", tags=["World"])
async def get_world_events(
    request: Request,
    limit: int = 50,
    user_id: str = Depends(require_permission("perm-view-world")),
) -> dict[str, Any]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        return {"events": [], "count": 0}
    w = pr.world
    events = [e.to_dict() for e in w.events(limit=limit)] if isinstance(w, WorldProtocol) else []
    return {"events": events, "count": len(events)}


@router.post("/world/events", tags=["World"])
@idempotent("world.create_world_event")
async def create_world_event(
    body: WorldEventCreateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-world")),
    _: None = Depends(_require_direct_world_mutation_allowed),
) -> dict[str, Any]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    from src.monkey_brain.kernel.society.world import WorldEvent, EventType

    type_map = {t.value: t for t in EventType}
    event = WorldEvent(
        event_type=type_map.get(body.event_type, EventType.STATE_CHANGE),
        entity_id=body.entity_id,
        description=body.description,
        attributes=body.attributes,
        confidence=body.confidence,
        source_actor_id=body.source_actor_id,
    )
    await _commit_world(
        "world.event.create",
        getattr(event, "event_id", "event"),
        lambda: pr.record_world_event(event) or True,
    )
    return event.to_dict()


@router.delete("/world/events/{event_id}", tags=["World"])
@idempotent("world.delete_world_event")
async def delete_world_event(
    event_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-world")),
    _: None = Depends(_require_direct_world_mutation_allowed),
) -> dict[str, str]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    removed = await _commit_world("world.event.delete", event_id, lambda: pr.remove_world_event(event_id))
    if not removed:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")
    return {"status": "deleted", "event_id": event_id}


# ── Resources CRUD ──────────────────────────────────────────────────────


@router.get("/world/resources", tags=["World"])
async def get_world_resources(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-world")),
) -> dict[str, Any]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        return {"resources": [], "count": 0}
    w = pr.world
    resources = [r.to_dict() for r in w.resources()] if isinstance(w, WorldProtocol) else []
    return {"resources": resources, "count": len(resources)}


@router.post("/world/resources", tags=["World"])
@idempotent("world.create_world_resource")
async def create_world_resource(
    body: WorldResourceCreateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-world")),
    _: None = Depends(_require_direct_world_mutation_allowed),
) -> dict[str, Any]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    from src.monkey_brain.kernel.society.world import WorldResource

    resource = WorldResource(
        name=body.name,
        resource_type=body.resource_type,
        quantity=body.quantity,
        unit=body.unit,
        location_id=body.location_id,
        owner_id=body.owner_id,
    )
    await _commit_world(
        "world.resource.create",
        getattr(resource, "resource_id", "resource"),
        lambda: pr.add_world_resource(resource) or True,
    )
    return resource.to_dict()


@router.get("/world/resources/{resource_id}", tags=["World"])
async def get_world_resource(
    resource_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-world")),
) -> dict[str, Any]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    resource = pr.world.get_resource(resource_id) if isinstance(pr.world, WorldProtocol) else None
    if resource is None:
        raise HTTPException(status_code=404, detail=f"Resource {resource_id} not found")
    return resource.to_dict()


@router.put("/world/resources/{resource_id}", tags=["World"])
@idempotent("world.update_world_resource")
async def update_world_resource(
    resource_id: str,
    body: WorldResourceUpdateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-world")),
    _: None = Depends(_require_direct_world_mutation_allowed),
) -> dict[str, Any]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    updates = {k: v for k, v in body.dict().items() if v is not None}
    resource = pr.world.update_resource(resource_id, **updates) if isinstance(pr.world, WorldProtocol) else None
    if resource is None:
        raise HTTPException(status_code=404, detail=f"Resource {resource_id} not found")
    pr._save_world()
    return resource.to_dict()


@router.delete("/world/resources/{resource_id}", tags=["World"])
@idempotent("world.delete_world_resource")
async def delete_world_resource(
    resource_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-world")),
    _: None = Depends(_require_direct_world_mutation_allowed),
) -> dict[str, str]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    removed = await _commit_world(
        "world.resource.delete",
        resource_id,
        lambda: pr.remove_world_resource(resource_id),
    )
    if not removed:
        raise HTTPException(status_code=404, detail=f"Resource {resource_id} not found")
    return {"status": "deleted", "resource_id": resource_id}


# ── Locations CRUD ───────────────────────────────────────────────────────


@router.get("/world/locations", tags=["World"])
async def get_world_locations(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-world")),
) -> dict[str, Any]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    locations = [l.to_dict() for l in pr.world.locations()] if isinstance(pr.world, WorldProtocol) else []
    return {"locations": locations, "count": len(locations)}


@router.post("/world/locations", tags=["World"])
@idempotent("world.create_world_location")
async def create_world_location(
    body: WorldLocationCreateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-world")),
    _: None = Depends(_require_direct_world_mutation_allowed),
) -> dict[str, Any]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    from src.monkey_brain.kernel.society.world import WorldLocation

    location = WorldLocation(
        name=body.name,
        address=body.address,
        latitude=body.latitude,
        longitude=body.longitude,
        attributes=body.attributes,
    )
    await _commit_world(
        "world.location.create",
        getattr(location, "location_id", "location"),
        lambda: pr.add_world_location(location) or True,
    )
    return location.to_dict()


@router.get("/world/locations/{location_id}", tags=["World"])
async def get_world_location(
    location_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-world")),
) -> dict[str, Any]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    location = pr.world.get_location(location_id) if isinstance(pr.world, WorldProtocol) else None
    if location is None:
        raise HTTPException(status_code=404, detail=f"Location {location_id} not found")
    return location.to_dict()


@router.put("/world/locations/{location_id}", tags=["World"])
@idempotent("world.update_world_location")
async def update_world_location(
    location_id: str,
    body: WorldLocationUpdateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-world")),
    _: None = Depends(_require_direct_world_mutation_allowed),
) -> dict[str, Any]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    updates = {k: v for k, v in body.dict().items() if v is not None}
    location = await _commit_world(
        "world.location.update",
        location_id,
        lambda: pr.update_world_location(location_id, **updates) if isinstance(pr, PlanetaryRuntimeProtocol) else None,
    )
    if location is None:
        raise HTTPException(status_code=404, detail=f"Location {location_id} not found")
    return location.to_dict()


@router.delete("/world/locations/{location_id}", tags=["World"])
@idempotent("world.delete_world_location")
async def delete_world_location(
    location_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-world")),
    _: None = Depends(_require_direct_world_mutation_allowed),
) -> dict[str, str]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    removed = await _commit_world(
        "world.location.delete",
        location_id,
        lambda: pr.remove_world_location(location_id) if isinstance(pr, PlanetaryRuntimeProtocol) else False,
    )
    if not removed:
        raise HTTPException(status_code=404, detail=f"Location {location_id} not found")
    return {"status": "deleted", "location_id": location_id}


# ── Query ───────────────────────────────────────────────────────────────


@router.post("/world/query", response_model=WorldQueryResponse, tags=["World"])
@idempotent("world.query_world")
async def query_world(
    body: WorldQueryRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-world")),
) -> WorldQueryResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        return WorldQueryResponse()
    from src.monkey_brain.kernel.society.world import WorldEntityType

    type_map = {t.value: t for t in WorldEntityType}
    entity_type_filter = body.filters.get("entity_type")
    if entity_type_filter:
        entity_type = type_map.get(entity_type_filter)
        if entity_type is None:
            return WorldQueryResponse(results=[], count=0)
    else:
        entity_type = None
    results = pr.world.query(entity_type=entity_type, name_contains=body.query)
    return WorldQueryResponse(results=[e.to_dict() for e in results], count=len(results))
