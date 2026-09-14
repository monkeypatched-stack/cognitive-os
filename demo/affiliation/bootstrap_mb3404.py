"""MB-3404 — Temporary Affiliation via Presence: World Bootstrap.

Real-API-only. TWO Societies: "Contractor Pool Society" (the
Contractor's real home/permanent membership) and "Warehouse Society"
(hosting a real Warehouse Space). The Contractor starts with ZERO
Affiliations and ZERO presence in the Warehouse — reachability, when it
appears, must come purely from real, physical Presence-driven temporary
membership (MembershipGovernor), not from any configured Affiliation.
"""

from __future__ import annotations

import sys
from typing import Any

import httpx

from _common import (
    ApiError,
    call,
    client,
    create_actor,
    create_geo,
    host_society,
    verify_world,
)


def build_geography(c: httpx.Client) -> dict[str, str]:
    planet = create_geo(c, "planet", "Earth")
    country = create_geo(c, "country", "United States", planet)
    state = create_geo(c, "state", "California", country)
    county = create_geo(c, "county", "San Francisco County", state)
    city = create_geo(c, "city", "San Francisco", county)
    street = create_geo(c, "street", "Market Street", city)

    spaces = {}
    for key, label in (
        ("contractor_office", "Contractor Pool Office"),
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
            (
                "contractor",
                "contractor_office",
                "Contractor Pool Society",
                "Contract staffing pool",
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
            "Contractor": create_actor(c, "Contractor", societies["contractor"], ["fill_in_as_needed"]),
            "Warehouse Worker": create_actor(c, "Warehouse Worker", societies["warehouse"], ["coordinate_packing"]),
        }

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
