"""MB-3400 — Warehouse Team: World Bootstrap.

Real-API-only. One Society ("Warehouse A Society"), four actors sharing
it: a Warehouse Worker and two Packers hold a real, shared symbolic
"warehouse_team" Affiliation; a Cashier in the SAME society holds a
different, unrelated "checkout_team" Affiliation. Because every actor
here has at least one real Affiliation, the router's same-society "no
affiliations at all" fallback never applies — same-society membership
alone is deliberately NOT enough to reach the Cashier; only a shared
Affiliation is.
"""

from __future__ import annotations

import sys
from typing import Any

import httpx

from _common import (
    ApiError,
    affiliate,
    client,
    create_actor,
    create_geo,
    create_society,
    host_society,
    verify_world,
)

WAREHOUSE_TEAM = "warehouse_team"
CHECKOUT_TEAM = "checkout_team"


def build_geography(c: httpx.Client) -> dict[str, str]:
    planet = create_geo(c, "planet", "Earth")
    country = create_geo(c, "country", "United States", planet)
    state = create_geo(c, "state", "California", country)
    county = create_geo(c, "county", "San Francisco County", state)
    city = create_geo(c, "city", "San Francisco", county)
    street = create_geo(c, "street", "Market Street", city)
    building = create_geo(c, "building", "Warehouse A Building", street)
    space = create_geo(c, "space", "Warehouse A Floor", building)
    return {
        "planet": planet,
        "country": country,
        "state": state,
        "county": county,
        "city": city,
        "street": street,
        "warehouse_a": space,
    }


def bootstrap_world(c: httpx.Client | None = None) -> dict[str, Any]:
    owns_client = c is None
    c = c or client()
    try:
        spaces = build_geography(c)
        society_id = create_society(c, "Warehouse A Society", "Warehouse A floor operations")
        host_society(c, spaces["warehouse_a"], society_id)

        actors = {
            "Warehouse Worker": create_actor(c, "Warehouse Worker", society_id, ["coordinate_packing"]),
            "Packer One": create_actor(c, "Packer One", society_id, ["pack_orders"]),
            "Packer Two": create_actor(c, "Packer Two", society_id, ["pack_orders"]),
            "Cashier": create_actor(c, "Cashier", society_id, ["process_checkout"]),
        }
        for name in ("Warehouse Worker", "Packer One", "Packer Two"):
            affiliate(c, actors[name], WAREHOUSE_TEAM)
        affiliate(c, actors["Cashier"], CHECKOUT_TEAM)

        verification = verify_world(c)
        return {
            "spaces": spaces,
            "society_id": society_id,
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
