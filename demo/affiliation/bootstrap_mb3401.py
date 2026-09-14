"""MB-3401 — Customer Support Routing: World Bootstrap.

Real-API-only. THREE separate Societies (Customer, Support, Warehouse) —
deliberately not one shared society, so any successful reach has to
come from a real, shared Affiliation bridging two different societies,
never from co-location. Support Agent holds two real Affiliations
("customer_support" and "warehouse_team"); the Customer holds only
"customer_support"; the Warehouse Worker holds only "warehouse_team".
The Customer has NO affiliation reaching the Warehouse Worker directly —
that gap is the point of this scenario.
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

CUSTOMER_SUPPORT = "customer_support"
WAREHOUSE_TEAM = "warehouse_team"


def build_geography(c: httpx.Client) -> dict[str, str]:
    planet = create_geo(c, "planet", "Earth")
    country = create_geo(c, "country", "United States", planet)
    state = create_geo(c, "state", "California", country)
    county = create_geo(c, "county", "San Francisco County", state)
    city = create_geo(c, "city", "San Francisco", county)
    street = create_geo(c, "street", "Market Street", city)

    spaces = {}
    for key, label in (
        ("customer_home", "Customer Home"),
        ("support_center", "Support Center"),
        ("warehouse_a", "Warehouse A"),
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
            ("customer", "customer_home", "Customer Society", "Customers"),
            ("support", "support_center", "Support Society", "Support agents"),
            (
                "warehouse",
                "warehouse_a",
                "Warehouse Society",
                "Warehouse A floor operations",
            ),
        ):
            society_id = call(c, "POST", "/societies", json={"name": name, "description": description})["society_id"]
            host_society(c, spaces[space_key], society_id)
            societies[key] = society_id

        actors = {
            "Customer": create_actor(c, "Customer", societies["customer"], ["get_order_status"]),
            "Support Agent": create_actor(c, "Support Agent", societies["support"], ["resolve_customer_inquiries"]),
            "Warehouse Worker": create_actor(c, "Warehouse Worker", societies["warehouse"], ["coordinate_packing"]),
        }
        affiliate(c, actors["Customer"], CUSTOMER_SUPPORT)
        affiliate(c, actors["Support Agent"], CUSTOMER_SUPPORT)
        affiliate(c, actors["Support Agent"], WAREHOUSE_TEAM)
        affiliate(c, actors["Warehouse Worker"], WAREHOUSE_TEAM)

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
