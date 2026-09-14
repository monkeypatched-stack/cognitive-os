"""MB-3304 — Inventory Allocation: World Bootstrap.

Real-API-only. Three Customers, one real product with quantity=1.
Two Customers have real urgent (speed-weighted) preferences; one has
real patient (cost-weighted) preferences — so "priority" can emerge
from genuine strategic self-selection (the patient customer's own real
utility favors waiting), not just fixed request ordering.
"""

from __future__ import annotations

import sys
from typing import Any

import httpx

from _common import ApiError, call, client, create_geo, verify_world

TRACKED_PRODUCT_NAME = "Wireless Gaming Mouse"


def build_geography(c: httpx.Client) -> dict[str, str]:
    planet = create_geo(c, "planet", "Earth")
    country = create_geo(c, "country", "United States", planet)
    state = create_geo(c, "state", "California", country)
    county = create_geo(c, "county", "San Francisco County", state)
    city = create_geo(c, "city", "San Francisco", county)
    street = create_geo(c, "street", "Market Street", city)
    building = create_geo(c, "building", "Marketplace Building", street)
    space = create_geo(c, "space", "Marketplace Floor", building)
    return {
        "planet": planet,
        "country": country,
        "state": state,
        "county": county,
        "city": city,
        "street": street,
        "marketplace": space,
    }


def build_society(c: httpx.Client, spaces: dict[str, str]) -> dict[str, str]:
    result = call(
        c,
        "POST",
        "/societies",
        json={
            "name": "Marketplace Society",
            "description": "Customers with pending orders competing for real stock",
        },
    )
    society_id = result["society_id"]
    call(
        c,
        "POST",
        f"/planet/geo/{spaces['marketplace']}/host",
        json={"society_id": society_id},
    )
    return {"marketplace": society_id}


ACTOR_DEFS = (
    ("Customer 1", {"speed": 0.9, "cost": 0.1}, "competitive"),
    ("Customer 2", {"speed": 0.8, "cost": 0.2}, "competitive"),
    ("Customer 3", {"cost": 0.9, "speed": 0.1}, "cooperative"),
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
                "goals": ["browse_and_purchase"],
                "society_id": societies["marketplace"],
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


def build_commerce(c: httpx.Client) -> dict[str, Any]:
    merchant = call(
        c,
        "POST",
        "/merchants",
        json={
            "merchant_id": "merchant_bob",
            "store_name": "Bob's Electronics",
            "delivery_fee": 4.99,
            "address": "742 Market Street, San Francisco, CA 94102",
        },
    )
    store_id = merchant["store_id"]
    product = call(
        c,
        "POST",
        "/products",
        json={
            "store_id": store_id,
            "merchant_id": "merchant_bob",
            "name": TRACKED_PRODUCT_NAME,
            "price": 59.99,
            "quantity": 1,
        },
    )
    product_id = product.get("product_id", product.get("id", ""))
    return {
        "store_id": store_id,
        "merchant_id": "merchant_bob",
        "products": {TRACKED_PRODUCT_NAME: product_id},
    }


def bootstrap_world(c: httpx.Client | None = None) -> dict[str, Any]:
    owns_client = c is None
    c = c or client()
    try:
        spaces = build_geography(c)
        societies = build_society(c, spaces)
        actors = build_actors(c, societies)
        commerce = build_commerce(c)
        verification = verify_world(c)
        return {
            "spaces": spaces,
            "societies": societies,
            "actors": actors,
            "commerce": commerce,
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
