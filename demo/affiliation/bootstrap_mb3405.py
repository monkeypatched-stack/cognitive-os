"""MB-3405 — Broadcast Scoping: World Bootstrap.

Real-API-only. Warehouse A Society: a Manager and two Floor Workers,
all three sharing a real "warehouse_a_ops" Affiliation. A SEPARATE
Distribution Center Society holds a Distribution Worker who ALSO holds
that exact same "warehouse_a_ops" Affiliation string — deliberately, to
prove BroadcastToAffiliation is scoped to the sender's own Society
(SocietyRuntime.broadcast_message only ever looks at its own
active_actors()), not to every actor anywhere holding a matching
Affiliation.
"""

from __future__ import annotations

import sys
from typing import Any

import httpx

from _common import (
    ApiError,
    affiliate,
    call,
    client,
    create_actor,
    create_geo,
    host_society,
    verify_world,
)

WAREHOUSE_A_OPS = "warehouse_a_ops"


def build_geography(c: httpx.Client) -> dict[str, str]:
    planet = create_geo(c, "planet", "Earth")
    country = create_geo(c, "country", "United States", planet)
    state = create_geo(c, "state", "California", country)
    county = create_geo(c, "county", "San Francisco County", state)
    city = create_geo(c, "city", "San Francisco", county)
    street = create_geo(c, "street", "Market Street", city)

    spaces = {}
    for key, label in (
        ("warehouse_a", "Warehouse A"),
        ("distribution_center", "Distribution Center"),
    ):
        building = create_geo(c, "building", f"{label} Building", street)
        space = create_geo(c, "space", f"{label} Floor", building)
        spaces[key] = space
    return {
        "planet": planet,
        "country": country,
        "state": state,
        "county": county,
        "city": city,
        "street": street,
        **spaces,
    }


def bootstrap_world(c: httpx.Client | None = None) -> dict[str, Any]:
    owns_client = c is None
    c = c or client()
    try:
        spaces = build_geography(c)

        societies = {}
        for key, space_key, name, description in (
            (
                "warehouse",
                "warehouse_a",
                "Warehouse A Society",
                "Warehouse A floor operations",
            ),
            (
                "distribution",
                "distribution_center",
                "Distribution Center Society",
                "Regional distribution",
            ),
        ):
            society_id = call(c, "POST", "/societies", json={"name": name, "description": description})["society_id"]
            host_society(c, spaces[space_key], society_id)
            societies[key] = society_id

        actors = {
            "Warehouse Manager": create_actor(c, "Warehouse Manager", societies["warehouse"], ["manage_floor"]),
            "Floor Worker One": create_actor(c, "Floor Worker One", societies["warehouse"], ["operate_floor"]),
            "Floor Worker Two": create_actor(c, "Floor Worker Two", societies["warehouse"], ["operate_floor"]),
            "Distribution Worker": create_actor(c, "Distribution Worker", societies["distribution"], ["operate_floor"]),
        }
        for name in (
            "Warehouse Manager",
            "Floor Worker One",
            "Floor Worker Two",
            "Distribution Worker",
        ):
            affiliate(c, actors[name], WAREHOUSE_A_OPS)

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
