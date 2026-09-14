"""MB-3403 — Unauthorized Communication: World Bootstrap.

Real-API-only. TWO separate Societies (Customer, Executive). The CEO
holds a real "executive_team" Affiliation shared with no one the
Customer knows; the Customer holds a real, unrelated "customer_support"
Affiliation. Neither actor is affiliation-empty (so the router's
same-society "no affiliations" fallback is never in play even by
accident), and they are in different Societies besides.
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
EXECUTIVE_TEAM = "executive_team"


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
        ("executive_suite", "Executive Suite"),
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
            ("executive", "executive_suite", "Executive Society", "Company leadership"),
        ):
            society_id = call(c, "POST", "/societies", json={"name": name, "description": description})["society_id"]
            host_society(c, spaces[space_key], society_id)
            societies[key] = society_id

        actors = {
            "Customer": create_actor(c, "Customer", societies["customer"], ["get_order_status"]),
            "CEO": create_actor(c, "CEO", societies["executive"], ["set_company_strategy"]),
        }
        affiliate(c, actors["Customer"], CUSTOMER_SUPPORT)
        affiliate(c, actors["CEO"], EXECUTIVE_TEAM)

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
