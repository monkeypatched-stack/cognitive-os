"""MB-3305 — Delivery Optimization: World Bootstrap.

Real-API-only. Two Drivers, each with a real delivery in the OTHER
driver's usual zone — swapping genuinely reduces both drivers' real
route distance, not just a script's claim that it does.
"""

from __future__ import annotations

import sys
from typing import Any

import httpx

from _common import ApiError, call, client, create_geo, verify_world


def build_geography(c: httpx.Client) -> dict[str, str]:
    planet = create_geo(c, "planet", "Earth")
    country = create_geo(c, "country", "United States", planet)
    state = create_geo(c, "state", "California", country)
    county = create_geo(c, "county", "San Francisco County", state)
    city = create_geo(c, "city", "San Francisco", county)
    street = create_geo(c, "street", "Market Street", city)
    building = create_geo(c, "building", "Logistics Hub Building", street)
    space = create_geo(c, "space", "Logistics Hub Floor", building)
    return {
        "planet": planet,
        "country": country,
        "state": state,
        "county": county,
        "city": city,
        "street": street,
        "logistics": space,
    }


def build_society(c: httpx.Client, spaces: dict[str, str]) -> dict[str, str]:
    result = call(
        c,
        "POST",
        "/societies",
        json={
            "name": "Logistics Society",
            "description": "Drivers negotiating real route exchanges",
        },
    )
    society_id = result["society_id"]
    call(
        c,
        "POST",
        f"/planet/geo/{spaces['logistics']}/host",
        json={"society_id": society_id},
    )
    return {"logistics": society_id}


ACTOR_DEFS = (
    (
        "Driver X",
        {
            "preferences": {"distance_saved": 0.8, "effort": 0.2},
            "resources": {
                "usual_zone": "A",
                "current_route_distance_miles": 20,
                "delivery_in_zone": "B",
            },
            "risk_tolerance": 0.5,
            "negotiation_policy": "cooperative",
        },
    ),
    (
        "Driver Y",
        {
            "preferences": {"distance_saved": 0.7, "effort": 0.3},
            "resources": {
                "usual_zone": "B",
                "current_route_distance_miles": 18,
                "delivery_in_zone": "A",
            },
            "risk_tolerance": 0.5,
            "negotiation_policy": "cooperative",
        },
    ),
)


def build_actors(c: httpx.Client, societies: dict[str, str]) -> dict[str, str]:
    actors: dict[str, str] = {}
    for name, strategy in ACTOR_DEFS:
        result = call(
            c,
            "POST",
            "/actors",
            json={
                "name": name,
                "actor_type": "human",
                "goals": ["deliver_package"],
                "society_id": societies["logistics"],
                "capabilities": [{"name": "general"}],
                "metadata": {"strategy": strategy},
            },
        )
        actors[name] = result["actor_id"]
    return actors


def bootstrap_world(c: httpx.Client | None = None) -> dict[str, Any]:
    owns_client = c is None
    c = c or client()
    try:
        spaces = build_geography(c)
        societies = build_society(c, spaces)
        actors = build_actors(c, societies)
        verification = verify_world(c)
        return {
            "spaces": spaces,
            "societies": societies,
            "actors": actors,
            "verification": verification,
        }
    finally:
        if owns_client:
            c.close()


if __name__ == "__main__":
    try:
        world = bootstrap_world()
    except ApiError as exc:
        print(f"Bootstrap failed: {exc}", file=sys.stderr)
        sys.exit(1)
    print("World bootstrapped:")
    print(f"  Actors:     {len(world['actors'])}")
    print(f"  Validation: {'PASSED' if world['verification'].get('ok') else 'FAILED'}")
