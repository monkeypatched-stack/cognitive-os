"""MB-3303 — Merchant Competition: World Bootstrap.

Real-API-only. Two Merchant actors competing for one real scarce
logistics resource — an expedited delivery slot, modeled the same way
MB-3300's last-unit product was (a real entity with quantity=1, real
CAS reservation via try_reserve). Different real preferences so the
competition has two genuinely different reasons behind it, not two
identical clones.
"""

from __future__ import annotations

import sys
from typing import Any

import httpx

from _common import ApiError, call, client, create_geo, verify_world

DELIVERY_SLOT_NAME = "Expedited Delivery Slot"


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
            "description": "Merchants competing for real delivery capacity",
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
    ("Merchant A", {"speed": 0.8, "cost": 0.2}, "competitive"),
    ("Merchant B", {"cost": 0.7, "speed": 0.3}, "competitive"),
)


def build_actors(c: httpx.Client, societies: dict[str, str]) -> dict[str, str]:
    actors: dict[str, str] = {}
    for name, preferences, policy in ACTOR_DEFS:
        result = call(
            c,
            "POST",
            "/actors",
            json={
                "name": name,
                "actor_type": "human",
                "goals": ["manage_store"],
                "society_id": societies["logistics"],
                "capabilities": [{"name": "general"}],
                "metadata": {
                    "strategy": {
                        "preferences": preferences,
                        "resources": {},
                        "risk_tolerance": 0.5,
                        "negotiation_policy": policy,
                    }
                },
            },
        )
        actors[name] = result["actor_id"]
    return actors


def build_resource(c: httpx.Client) -> dict[str, Any]:
    merchant = call(
        c,
        "POST",
        "/merchants",
        json={
            "merchant_id": "merchant_dispatch",
            "store_name": "Dispatch Coordination",
            "delivery_fee": 0.0,
            "address": "1 Logistics Hub, San Francisco, CA 94102",
        },
    )
    store_id = merchant["store_id"]
    slot = call(
        c,
        "POST",
        "/products",
        json={
            "store_id": store_id,
            "merchant_id": "merchant_dispatch",
            "name": DELIVERY_SLOT_NAME,
            "price": 0.0,
            "quantity": 1,
        },
    )
    slot_id = slot.get("product_id", slot.get("id", ""))
    return {"store_id": store_id, "slot_id": slot_id}


def bootstrap_world(c: httpx.Client | None = None) -> dict[str, Any]:
    owns_client = c is None
    c = c or client()
    try:
        spaces = build_geography(c)
        societies = build_society(c, spaces)
        actors = build_actors(c, societies)
        resource = build_resource(c)
        verification = verify_world(c)
        return {
            "spaces": spaces,
            "societies": societies,
            "actors": actors,
            "resource": resource,
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
