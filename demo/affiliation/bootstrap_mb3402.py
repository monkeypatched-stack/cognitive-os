"""MB-3402 — Cross-Affiliation Chain: World Bootstrap.

Real-API-only. THREE separate Societies (Merchant, Logistics,
Warehouse). Merchant and Logistics Provider share a real
"merchant_logistics" Affiliation; Logistics Provider and Warehouse
Worker share a real, DIFFERENT "logistics_warehouse" Affiliation. The
Merchant holds neither of Warehouse Worker's affiliations and vice
versa — there is deliberately no affiliation chain-of-trust logic
anywhere in the router, so two real, direct hops must NOT compose into
a third, transitive one.
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

MERCHANT_LOGISTICS = "merchant_logistics"
LOGISTICS_WAREHOUSE = "logistics_warehouse"


def build_geography(c: httpx.Client) -> dict[str, str]:
    planet = create_geo(c, "planet", "Earth")
    country = create_geo(c, "country", "United States", planet)
    state = create_geo(c, "state", "California", country)
    county = create_geo(c, "county", "San Francisco County", state)
    city = create_geo(c, "city", "San Francisco", county)
    street = create_geo(c, "street", "Market Street", city)

    spaces = {}
    for key, label in (
        ("merchant_store", "Merchant Store"),
        ("logistics_hub", "Logistics Hub"),
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
            ("merchant", "merchant_store", "Merchant Society", "Merchant storefront"),
            (
                "logistics",
                "logistics_hub",
                "Logistics Society",
                "Logistics coordination",
            ),
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
            "Merchant": create_actor(c, "Merchant", societies["merchant"], ["sell_products"]),
            "Logistics Provider": create_actor(
                c, "Logistics Provider", societies["logistics"], ["coordinate_shipping"]
            ),
            "Warehouse Worker": create_actor(c, "Warehouse Worker", societies["warehouse"], ["coordinate_packing"]),
        }
        affiliate(c, actors["Merchant"], MERCHANT_LOGISTICS)
        affiliate(c, actors["Logistics Provider"], MERCHANT_LOGISTICS)
        affiliate(c, actors["Logistics Provider"], LOGISTICS_WAREHOUSE)
        affiliate(c, actors["Warehouse Worker"], LOGISTICS_WAREHOUSE)

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
