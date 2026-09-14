"""Planet API — top-level view of the Cognitive OS.

GET /planet              — full planet state
GET /planet/status       — health/status summary
GET /planet/societies    — list all registered societies
GET /planet/actors       — list all actors across all societies
GET /planet/statistics   — aggregate statistics
"""

from __future__ import annotations

import dataclasses
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from src.monkey_brain.api.dependencies import require_permission
from src.monkey_brain.api.gateway_models import (
    PlanetResponse,
    PlanetStatusResponse,
    PlanetStatisticsResponse,
    ActorResponse,
    SocietyResponse,
    GeoLocationLinkRequest,
    GeoFromAddressRequest,
)
from src.monkey_brain.api.idempotency import idempotent

router = APIRouter()

_boot_time = time.time()


def _get_planetary_runtime(request: Request) -> Any:
    return getattr(request.app.state, "planetary_runtime", None)


@router.get("/planet", response_model=PlanetResponse, tags=["Planet"])
async def get_planet(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-planet")),
) -> PlanetResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        return PlanetResponse()
    d = pr.to_dict()
    return PlanetResponse(**d)


@router.get("/planet/status", response_model=PlanetStatusResponse, tags=["Planet"])
async def get_planet_status(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-planet")),
) -> PlanetStatusResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        return PlanetStatusResponse()
    d = pr.to_dict()
    return PlanetStatusResponse(
        status="healthy",
        uptime_s=time.time() - _boot_time,
        cycle_count=d.get("cycle_count", 0),
        societies=d.get("society_registry_count", 0),
        actors=d.get("actor_count", 0),
    )


@router.get("/planet/societies", response_model=list[SocietyResponse], tags=["Planet"])
async def get_planet_societies(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-planet")),
) -> list[SocietyResponse]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        return []
    from src.monkey_brain.api.routes.societies import _society_actor_counts

    results = []
    for sr in pr.all_societies():
        actor_count, active_actors = _society_actor_counts(pr, sr.society.society_id)
        results.append(
            SocietyResponse(
                society_id=sr.society.society_id,
                name=sr.society.name,
                description=sr.society.description,
                actor_count=actor_count,
                active_actors=active_actors,
                is_active=sr.is_active,
            )
        )
    return results


@router.get("/planet/actors", response_model=list[ActorResponse], tags=["Planet"])
async def get_planet_actors(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-planet")),
) -> list[ActorResponse]:
    """Get all active actors across all active societies."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        return []
    results = []
    for society_id, sr in pr._societies.items():
        if not sr.is_active:
            continue
        for state in sr.active_actors():
            results.append(
                ActorResponse(
                    actor_id=state.actor_id,
                    name=state.profile.identity.name,
                    actor_type=state.profile.identity.actor_type.value,
                    description=state.profile.identity.description,
                    status=state.status.value,
                    cycle_count=state.cycle_count,
                    is_active=state.is_active,
                    societies=[society_id],
                    goals=list(state.profile.goals),
                    policies=list(state.profile.policies),
                    trust_level=state.profile.trust_level,
                    ownership=state.profile.ownership,
                )
            )
    return results


@router.get("/planet/statistics", response_model=PlanetStatisticsResponse, tags=["Planet"])
async def get_planet_statistics(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-planet")),
) -> PlanetStatisticsResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        return PlanetStatisticsResponse()
    d = pr.to_dict()
    return PlanetStatisticsResponse(
        total_actors=d.get("actor_count", 0),
        active_actors=d.get("active_actors", 0),
        total_societies=d.get("society_registry_count", 1),
        total_interactions=d.get("interactions", 0),
        total_reputations=d.get("reputations", 0),
        total_policies=d.get("policies", 0),
        total_federations=d.get("federations", 0),
        world_version=d.get("world_version", 0),
    )


@router.post("/planet/federations", tags=["Planet"])
@idempotent("planet.create_federation")
async def create_federation(
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-planet")),
) -> dict[str, Any]:
    """Create a new federation."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")

    # Parse JSON body
    body = await request.json()
    name = body.get("name", "")
    description = body.get("description", "")
    member_society_ids = body.get("member_society_ids", [])

    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    federation = pr.create_federation(name, description, tuple(member_society_ids))
    return {
        "federation_id": federation.federation_id,
        "name": federation.name,
        "description": federation.description,
        "member_society_ids": list(federation.member_society_ids),
    }


# ── Country / City (Runtime Encapsulation Refactor follow-up) ─────────────
# Planet -> Country -> City -> Society -> Team -> Actor. Containment tiers
# only — no tick()/cycle() of their own, mirrored on the /planet/federations
# route above.


@router.post("/planet/countries", tags=["Planet"])
@idempotent("planet.create_country")
async def create_country(
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-planet")),
) -> dict[str, Any]:
    """Create a new country."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")

    body = await request.json()
    name = body.get("name", "")
    description = body.get("description", "")

    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    country = pr.create_country(name, description)
    return country.to_dict()


@router.get("/planet/countries", tags=["Planet"])
async def list_countries(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-planet")),
) -> list[dict[str, Any]]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        return []
    from src.monkey_brain.kernel.geography.entity import GeographicEntityType

    return [c.to_dict() for c in pr.geo_registry.all(GeographicEntityType.COUNTRY)]


@router.post("/planet/cities", tags=["Planet"])
@idempotent("planet.create_city")
async def create_city(
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-planet")),
) -> dict[str, Any]:
    """Create a new city under an existing country."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")

    body = await request.json()
    name = body.get("name", "")
    country_id = body.get("country_id", "")
    description = body.get("description", "")

    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    if not country_id:
        raise HTTPException(status_code=400, detail="country_id is required")

    city = pr.create_city(name, country_id, description)
    if city is None:
        raise HTTPException(status_code=404, detail=f"Country {country_id} not found")
    return city.to_dict()


@router.get("/planet/cities", tags=["Planet"])
async def list_cities(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-planet")),
) -> list[dict[str, Any]]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        return []
    from src.monkey_brain.kernel.geography.entity import GeographicEntityType

    return [c.to_dict() for c in pr.geo_registry.all(GeographicEntityType.CITY)]


@router.post("/planet/cities/{city_id}/societies", tags=["Planet"])
@idempotent("planet.assign_society_to_city")
async def assign_society_to_city(
    city_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-planet")),
) -> dict[str, Any]:
    """Assign an existing society to a city (strict: a society belongs to
    at most one city — reassigning removes it from any prior city)."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")

    body = await request.json()
    society_id = body.get("society_id", "")
    if not society_id:
        raise HTTPException(status_code=400, detail="society_id is required")

    city = pr.assign_society_to_city(society_id, city_id)
    if city is None:
        raise HTTPException(status_code=404, detail=f"City {city_id} or society {society_id} not found")
    return city.to_dict()


@router.post("/planet/cities/{city_id}/tick", tags=["Planet"])
@idempotent("planet.tick_city")
async def tick_city(
    city_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-planet")),
) -> dict[str, Any]:
    """Tick every society registered under a city, cascading to every
    actor in each of those societies."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    if pr.get_geographic_entity(city_id) is None:
        raise HTTPException(status_code=404, detail=f"City {city_id} not found")
    result = await pr.tick_city(city_id)
    return {
        "city_id": result.city_id,
        "societies_ticked": list(result.societies_ticked),
        "actors_ticked_total": result.actors_ticked_total,
        "interactions_routed_total": result.interactions_routed_total,
        "duration_ms": result.duration_ms,
    }


@router.post("/planet/countries/{country_id}/tick", tags=["Planet"])
@idempotent("planet.tick_country")
async def tick_country(
    country_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-planet")),
) -> dict[str, Any]:
    """Tick every city under a country, cascading to every society and
    actor beneath each city."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    if pr.get_geographic_entity(country_id) is None:
        raise HTTPException(status_code=404, detail=f"Country {country_id} not found")
    result = await pr.tick_country(country_id)
    return {
        "country_id": result.country_id,
        "cities_ticked": list(result.cities_ticked),
        "societies_ticked_total": result.societies_ticked_total,
        "actors_ticked_total": result.actors_ticked_total,
        "interactions_routed_total": result.interactions_routed_total,
        "duration_ms": result.duration_ms,
    }


## ── Generic Geography (Planet -> Country -> State -> County -> City ->
#    Street -> Building -> Space) ──────────────────────────────────────────
# Works at any of the 8 tiers uniformly, unlike the Country/City-specific
# routes above (kept for backward compatibility). Societies HOST at any
# tier — see /planet/geo/{entity_id}/host — they are never contained.


@router.post("/planet/geo", tags=["Planet"])
@idempotent("planet.create_geographic_entity")
async def create_geographic_entity(
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-planet")),
) -> dict[str, Any]:
    """Create a geographic entity at any tier. Body: entity_type (planet,
    country, state, county, city, street, building, space), name, parent_id
    (required for every tier except planet), description, and — for
    building/space — building_type/space_type."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")

    from src.monkey_brain.kernel.geography.entity import (
        GeographicEntityType,
        BuildingType,
        SpaceType,
        ROOT_ELIGIBLE,
    )

    body = await request.json()
    name = body.get("name", "")
    description = body.get("description", "")
    entity_type_str = body.get("entity_type", "")
    parent_id = body.get("parent_id", "")

    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    try:
        entity_type = GeographicEntityType(entity_type_str)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"invalid entity_type: {entity_type_str!r}")

    type_kwargs: dict[str, Any] = {}
    if entity_type == GeographicEntityType.BUILDING and body.get("building_type"):
        try:
            type_kwargs["building_type"] = BuildingType(body["building_type"])
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"invalid building_type: {body['building_type']!r}",
            )
    if entity_type == GeographicEntityType.SPACE and body.get("space_type"):
        try:
            type_kwargs["space_type"] = SpaceType(body["space_type"])
        except ValueError:
            raise HTTPException(status_code=400, detail=f"invalid space_type: {body['space_type']!r}")

    if entity_type not in ROOT_ELIGIBLE and not parent_id:
        raise HTTPException(status_code=400, detail="parent_id is required for this entity_type")
    # World persistence gap: this called pr.geo_registry.create(...)
    # directly, bypassing PlanetaryRuntime's own create_geographic_entity()
    # (same name, same underlying registry call) which already calls
    # self._save_geography() afterward — every OTHER geography-creating
    # route already goes through a PlanetaryRuntime method that saves.
    # Confirmed live: a Space created via this route was gone after a
    # restart, with nothing to persist it unless some unrelated LATER
    # geography mutation happened to trigger a save first.
    entity = pr.create_geographic_entity(
        entity_type,
        name,
        parent_id or None,
        description,
        **type_kwargs,
    )
    if entity is None:
        raise HTTPException(
            status_code=404,
            detail=f"parent_id {parent_id} not found or invalid tier pairing",
        )
    return entity.to_dict()


@router.get("/planet/geo", tags=["Planet"])
async def list_geographic_entities(
    request: Request,
    entity_type: str | None = None,
    user_id: str = Depends(require_permission("perm-view-planet")),
) -> list[dict[str, Any]]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        return []
    from src.monkey_brain.kernel.geography.entity import GeographicEntityType

    filter_type = None
    if entity_type:
        try:
            filter_type = GeographicEntityType(entity_type)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"invalid entity_type: {entity_type!r}")
    return [e.to_dict() for e in pr.geo_registry.all(filter_type)]


@router.get("/planet/geo/{entity_id}", tags=["Planet"])
async def get_geographic_entity(
    entity_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-planet")),
) -> dict[str, Any]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    entity = pr.get_geographic_entity(entity_id)
    if entity is None:
        raise HTTPException(status_code=404, detail=f"Geographic entity {entity_id} not found")
    return entity.to_dict()


@router.get("/planet/geo/{entity_id}/contents", tags=["Planet"])
async def get_space_contents(
    entity_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-planet")),
) -> dict[str, Any]:
    """What's attached to a Space: its current actor occupants and its
    hosted societies, as siblings (see kernel/geography/entity.py module
    docstring — Actors are not children of Societies)."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    contents = pr.space_contents(entity_id)
    if contents is None:
        raise HTTPException(status_code=404, detail=f"{entity_id} is not a Space")
    return contents


@router.patch("/planet/geo/{entity_id}/location", tags=["Planet"])
@idempotent("planet.link_geo_entity_location")
async def link_geo_entity_location(
    entity_id: str,
    body: GeoLocationLinkRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-planet")),
) -> dict[str, Any]:
    """Link (or, with world_location_id: null, clear) a geographic entity
    at any tier to a real WorldLocation (POST /world/locations — real
    latitude/longitude), the previously-unwired half of
    GeographicEntity.world_location_id's own docstring. Any tier, not just
    Building/Space: a Street can carry a location too (e.g. a road's
    start point), same "no tier restriction" precedent hosted_society_ids
    already set."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    if pr.get_geographic_entity(entity_id) is None:
        raise HTTPException(status_code=404, detail=f"Geographic entity {entity_id} not found")
    if body.world_location_id is not None and pr.world.get_location(body.world_location_id) is None:
        raise HTTPException(status_code=404, detail=f"World location {body.world_location_id} not found")
    entity = pr.set_geo_world_location(entity_id, body.world_location_id)
    if entity is None:
        raise HTTPException(status_code=404, detail=f"Geographic entity {entity_id} not found")
    return entity.to_dict()


@router.post("/planet/geo/from-address", tags=["Planet"])
@idempotent("planet.create_geo_from_address")
async def create_geo_from_address(
    body: GeoFromAddressRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-planet")),
) -> dict[str, Any]:
    """Real-world address ingestion: the frontend geocodes a search query
    (e.g. via Nominatim) and posts the address breakdown + real lat/lon
    here. Finds-or-creates the real Country/State/County/City/Street
    chain (reusing existing tiers by name — searching the same city twice
    must not duplicate it), creates a new Building for this address, and
    links it to a real WorldLocation. See PlanetaryRuntime.
    create_geo_from_address for the missing-tier fallback policy."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    building = pr.create_geo_from_address(
        country=body.country,
        state=body.state,
        county=body.county,
        city=body.city,
        street=body.street,
        building_name=body.building_name,
        latitude=body.latitude,
        longitude=body.longitude,
        display_address=body.display_address,
        attributes=body.attributes,
    )
    if building is None:
        raise HTTPException(status_code=400, detail="could not create geography from this address")
    return building.to_dict()


@router.delete("/planet/geo/{entity_id}", tags=["Planet"])
@idempotent("planet.delete_geographic_entity")
async def delete_geographic_entity(
    entity_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-planet")),
) -> dict[str, Any]:
    """Real deletion, any tier, cascades to descendants (a Building's
    Spaces are removed with it, not orphaned). Refuses — 409, not a
    silent no-op — to delete anything currently hosting a real Society
    (including the bootstrap Default chain, while it still hosts one);
    re-host affected Societies at a real location first. See
    PlanetaryRuntime.delete_geo_entity."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    if pr.get_geographic_entity(entity_id) is None:
        raise HTTPException(status_code=404, detail=f"Geographic entity {entity_id} not found")
    removed = pr.delete_geo_entity(entity_id)
    if removed is None:
        raise HTTPException(
            status_code=409,
            detail=f"{entity_id} cannot be deleted — it or a descendant currently hosts a Society; "
            "re-host it at a real location first",
        )
    return {"status": "deleted", "entity_id": entity_id, "removed_ids": list(removed)}


@router.post("/planet/geo/{entity_id}/host", tags=["Planet"])
@idempotent("planet.host_society_at_entity")
async def host_society_at_entity(
    entity_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-planet")),
) -> dict[str, Any]:
    """Host an existing society at a geographic entity, any tier. Strict
    single-host: a society hosted elsewhere is moved, not duplicated."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")

    body = await request.json()
    society_id = body.get("society_id", "")
    if not society_id:
        raise HTTPException(status_code=400, detail="society_id is required")

    entity = pr.host_society(entity_id, society_id)
    if entity is None:
        raise HTTPException(
            status_code=404,
            detail=f"Entity {entity_id} or society {society_id} not found",
        )
    return entity.to_dict()


@router.delete("/planet/geo/{entity_id}/host/{society_id}", tags=["Planet"])
@idempotent("planet.unhost_society_at_entity")
async def unhost_society_at_entity(
    entity_id: str,
    society_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-planet")),
) -> dict[str, Any]:
    """Inverse of POST .../host — remove a society's hosting at a
    geographic entity without deleting the society itself."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")

    entity = pr.unhost_society(entity_id, society_id)
    if entity is None:
        raise HTTPException(
            status_code=404,
            detail=f"Entity {entity_id} does not host society {society_id}",
        )
    return entity.to_dict()


@router.post("/planet/geo/reconcile-default", tags=["Planet"])
@idempotent("planet.reconcile_default_geography")
async def reconcile_default_geography(
    request: Request,
    canonical_root_name: str = "Earth",
    user_id: str = Depends(require_permission("perm-manage-planet")),
) -> dict[str, Any]:
    """Explicit, idempotent cleanup of PlanetaryRuntime's eager bootstrap
    "Default Planet" geography chain, once a real canonical root (e.g.
    "Earth") exists and every real Society is already hosted under it —
    never triggered automatically. See PlanetaryRuntime.
    reconcile_default_geography for the full explanation."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    result = pr.reconcile_default_geography(canonical_root_name)
    return dataclasses.asdict(result)


@router.post("/planet/geo/ensure-default-space", tags=["Planet"])
@idempotent("planet.ensure_default_bootstrap_space")
async def ensure_default_bootstrap_space(
    request: Request,
    canonical_root_name: str = "Earth",
    user_id: str = Depends(require_permission("perm-manage-planet")),
) -> dict[str, Any]:
    """Real seeding-readiness primitive (Qualification Gap Closure,
    BUG-001): ensures a usable default bootstrap Space exists under
    canonical_root_name, independent of whether reconcile-default's
    synthetic-chain cleanup has ever run or applies on this boot. See
    PlanetaryRuntime.ensure_default_bootstrap_space for the full
    explanation. Callers (e.g. scripts/seed_world.py) should treat a
    null space_id as "the canonical root doesn't exist yet" and fail
    loudly rather than proceeding to register an Actor with no real
    fallback Space."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    space_id = pr.ensure_default_bootstrap_space(canonical_root_name)
    return {"space_id": space_id}


@router.post("/planet/geo/{entity_id}/tick", tags=["Planet"])
@idempotent("planet.tick_geographic_entity")
async def tick_geographic_entity(
    entity_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-planet")),
) -> dict[str, Any]:
    """Tick a geographic entity: every society hosted there, then every
    child entity recursively, all the way down to their actors."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    if pr.get_geographic_entity(entity_id) is None:
        raise HTTPException(status_code=404, detail=f"Geographic entity {entity_id} not found")
    result = await pr.tick_geographic_entity(entity_id)
    return {
        "entity_id": result.entity_id,
        "entity_type": result.entity_type.value if result.entity_type else None,
        "societies_ticked": list(result.societies_ticked),
        "children_ticked": list(result.children_ticked),
        "actors_ticked_total": result.actors_ticked_total,
        "interactions_routed_total": result.interactions_routed_total,
        "temporary_memberships_reconciled": result.temporary_memberships_reconciled,
        # Prompt 5 — GeoResult Refactor: membership info exposed directly,
        # so callers of this route don't need a second request to look up
        # who belongs where.
        "observed_spaces": list(result.observed_spaces),
        "observed_actors": list(result.observed_actors),
        "observed_societies": list(result.observed_societies),
        "active_actors": list(result.active_actors),
        "temporary_memberships": {k: list(v) for k, v in result.temporary_memberships.items()},
        "effective_memberships": {k: list(v) for k, v in result.effective_memberships.items()},
        "duration_ms": result.duration_ms,
    }


@router.post("/planet/interactions", tags=["Planet"])
@idempotent("planet.create_interaction")
async def create_interaction(
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-planet")),
) -> dict[str, Any]:
    """Create an interaction between actors."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")

    # Parse JSON body
    body = await request.json()
    initiator_id = body.get("initiator_id", "")
    participant_ids = body.get("participant_ids", [])
    interaction_type = body.get("interaction_type", "request")
    topic = body.get("topic", "")
    proposal = body.get("proposal", None)

    if not initiator_id:
        raise HTTPException(status_code=400, detail="initiator_id is required")

    from src.monkey_brain.kernel.society.interaction import InteractionType

    type_map = {
        "request": InteractionType.REQUEST,
        "delegate": InteractionType.DELEGATE,
        "coordinate": InteractionType.COORDINATE,
        "negotiate": InteractionType.NEGOTIATE,
    }
    itype = type_map.get(interaction_type, InteractionType.REQUEST)

    interaction = pr.send_interaction(itype, initiator_id, tuple(participant_ids), topic, proposal)
    return {
        "interaction_id": interaction.interaction_id,
        "interaction_type": interaction.interaction_type.value,
        "initiator_id": interaction.initiator_id,
        "participant_ids": list(interaction.participant_ids),
        "topic": interaction.topic,
        "status": interaction.status.value,
    }


@router.post("/planet/tick", tags=["Planet"])
@idempotent("planet.planet_tick")
async def planet_tick(
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-planet")),
) -> dict[str, Any]:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    result = await pr.cycle()
    if result is None:
        raise HTTPException(
            status_code=503,
            detail="Planetary cycle already running — try again shortly",
        )
    return {
        "cycle_number": result.cycle_number,
        "actors_observed": result.actors_observed,
        "beliefs_updated": result.beliefs_updated,
        "interactions_routed": result.interactions_routed,
        "context_events": result.context_events,
        "duration_ms": result.duration_ms,
    }


@router.post("/planet/refresh", tags=["Planet"])
@idempotent("planet.planet_refresh")
async def planet_refresh(
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-planet")),
) -> dict[str, Any]:
    """Reload world state from Redis into in-memory SharedWorld.
    Required after Redis world mutations to sync in-memory state."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    try:
        pr._load_world()
        return {
            "status": "refreshed",
            "world_version": pr._world.version,
            "entities": len(list(pr._world.entities())),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"World refresh failed: {e}")


@router.delete("/planet/events", tags=["Planet"])
@idempotent("planet.clear_context_events")
async def clear_context_events(
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-planet")),
) -> dict[str, Any]:
    """Clear every ContextEvent — the data the Living World Explorer's
    Event Stream panel reads. Correction from an earlier version of this
    route: every SocietyRuntime a PlanetaryRuntime manages shares ONE
    real SocietyContextStream instance (see PlanetaryRuntime._attach_society:
    society_runtime._context_stream = self._society_runtime.context_stream;
    pr.context_stream is the same object) — so there's one in-memory
    stream to clear, not one per society; looping per-society and
    summing "cleared" counts was double-counting an already-empty list.
    ContextEvents ARE Redis-persisted too (_save_context/_load_context,
    kernel/society/integration.py) — a previous version of this route
    only cleared the in-memory copy, so a restart silently brought
    everything back. This clears both."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    cleared_in_memory = pr.context_stream.clear()
    redis_keys_deleted = 0
    if pr._redis is not None:
        keys = [pr._CONTEXT_LIST_KEY] + [f"{pr._CONTEXT_LIST_KEY}:{sr.society.society_id}" for sr in pr.all_societies()]
        redis_keys_deleted = pr._redis.delete(*keys)
    return {
        "status": "cleared",
        "events_cleared_in_memory": cleared_in_memory,
        "redis_keys_deleted": redis_keys_deleted,
    }


@router.delete("/planet/memory", tags=["Planet"])
@idempotent("planet.clear_memory")
async def clear_memory(
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-planet")),
) -> dict[str, Any]:
    """Clear the episodic memory store (experiences/conversations/
    executions MemoryManager.record_experience/persist_to_episodic_stream
    write — the retrieval-augmented grounding ContextConstructionEngine
    reads via _search_memory) WITHOUT touching real world-fact entities.

    Two backends, both real, neither cleared by DELETE /planet/events
    above (that route only clears the Context Stream, a separate system):
    - InMemoryVectorBackend: pure in-memory cosine-similarity index
      (kernel/learn/memory/vector_backend.py) -- no persistence of its
      own, so a restart already clears it; this clears it live too.
    - KnowledgeGraph: KnowledgeGraphMemoryAdapter.insert_node() (kernel/
      learn/memory/graph_adapter.py) writes episodic memories into the
      SAME persisted KnowledgeGraph real world-fact entities live in,
      distinguished only by entity_type=OTHER + attributes["label"] ==
      "EpisodicTrace" -- removed via the real, persisting
      KnowledgeGraph.remove_entity() (triggers PlanetaryRuntime.
      _on_knowledge_graph_change, same as every other KG mutation),
      never a bulk/table-level wipe that could also catch real entities.

    CognitiveGC (kernel/learn/memory/cognitive_gc.py) exists for
    staleness-based tombstoning but calls graph_db.query()/.execute()
    and vector_db.delete() -- methods KnowledgeGraphMemoryAdapter/
    InMemoryVectorBackend don't actually implement (confirmed by
    reading both) -- so it isn't usable here; this route works directly
    against the real, confirmed-live methods those classes do have.
    """
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")

    vector_backend = pr._memory_manager.vector_db
    vectors_cleared = len(getattr(vector_backend, "_vectors", {}))
    if hasattr(vector_backend, "_vectors"):
        vector_backend._vectors.clear()
    if hasattr(vector_backend, "_metadata"):
        vector_backend._metadata.clear()
    pr._memory_manager.working_memory.clear()
    pr._memory_manager._by_actor.clear()

    from src.monkey_brain.kernel.knowledge_graph import EntityType

    episodic_entities = [
        e
        for e in pr._knowledge_graph.entities_by_type(EntityType.OTHER)
        if e.attributes.get("label") == "EpisodicTrace"
    ]
    graph_nodes_removed = 0
    for entity in episodic_entities:
        if pr._knowledge_graph.remove_entity(entity.entity_id):
            graph_nodes_removed += 1

    return {
        "status": "cleared",
        "vector_entries_cleared": vectors_cleared,
        "graph_episodic_nodes_removed": graph_nodes_removed,
    }


@router.delete("/planet/executions", tags=["Planet"])
@idempotent("planet.clear_executions")
async def clear_executions(
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-planet")),
) -> dict[str, Any]:
    """Clear every EXECUTION timeline entry (kernel/timeline/store.py::
    TimelineStore, written by belief_runtime.py's _record_execution every
    tick) -- the per-actor history backing /actors/{id}/executions and
    /actors/{id}/execution-history. A third, distinct system from
    DELETE /planet/events (Context Stream) and DELETE /planet/memory
    (episodic memory) above -- TimelineStore is otherwise append-only
    by design, so this is the one deletion path it exposes (clear_kind),
    scoped to EXECUTION only so Presence/Membership/Goal/Belief/
    Relationship/Activity timelines are untouched.
    """
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")

    from src.monkey_brain.kernel.timeline.entry import TimelineKind
    from src.monkey_brain.kernel.timeline.store import TimelineStore

    executions_cleared = TimelineStore().clear_kind(TimelineKind.EXECUTION)

    return {
        "status": "cleared",
        "executions_cleared": executions_cleared,
    }


@router.post("/planet/perturbations", tags=["Planet"])
@idempotent("planet.report_world_perturbation")
async def report_world_perturbation(
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-planet")),
) -> dict[str, Any]:
    """Context Grounding: the real, operator-triggered replacement for
    "external world events" — no weather/traffic/NOAA feed exists in this
    codebase, so this is the honest alternative: a real capability
    invocation (kernel/domains/grocery.py::ReportWorldPerturbationCapability),
    called directly here the same way AskActorCapability already directly
    invokes AnswerQuestionCapability in-process — not a second
    implementation of what the capability does.

    Body: {"entity_id": "<real KG entity id>", "description": "Warehouse A fire",
    "impact_attributes": {"quantity": 0}}. impact_attributes is merged into
    the real entity's attributes (kg.update_entity), so this genuinely
    changes what the next ProductSelection/etc. sees — not just an event.

    Real-Time World Changes refactor: the capability also queues this as
    a real World Perturbation (kernel/society/perturbation_queue.py),
    additive to the immediate kg.update_entity above — the next Planetary
    Tick reconciles it into SharedWorld and considers affected actors for
    Deja Vu replay (kernel/society/integration.py::_run_cycle), on top
    of (not instead of) the commerce KG already having been updated
    synchronously by the time this call returns."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")

    body = await request.json()
    entity_id = body.get("entity_id", "")
    description = body.get("description", "")
    impact_attributes = body.get("impact_attributes")
    if not entity_id or not description or not isinstance(impact_attributes, dict) or not impact_attributes:
        raise HTTPException(
            status_code=400,
            detail="entity_id, description, and a non-empty impact_attributes object are required",
        )

    from src.monkey_brain.kernel.domains.grocery import (
        ReportWorldPerturbationCapability,
    )

    result = ReportWorldPerturbationCapability().handle(
        {
            "context": {
                "knowledge_graph": pr.knowledge_graph,
                "planetary_runtime": pr,
                "actor_id": user_id,
            },
            "parameters": {
                "entity_id": entity_id,
                "description": description,
                "impact_attributes": impact_attributes,
            },
        }
    )
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error", "perturbation failed"))

    from src.monkey_brain.kernel.compile import _obs

    _obs.counter("context.external_events_processed")
    return result
