"""MB-3306 — Customer Negotiation: World Bootstrap.

Real-API-only. Customer (Alice) and Merchant (Bob), one real product
with a real listed price. The seller's real floor (min_price) lives on
the product entity — the Merchant's own NegotiatePrice call reads real
numbers, never an invented one.
"""

from __future__ import annotations

import sys
from typing import Any

import httpx

from _common import ApiError, call, client, create_geo, verify_world

TRACKED_PRODUCT_NAME = "Wireless Gaming Mouse"
TRACKED_PRODUCT_PRICE = 59.99
TRACKED_PRODUCT_FLOOR = 48.00


def build_geography(c: httpx.Client) -> dict[str, str]:
    planet = create_geo(c, "planet", "Earth")
    country = create_geo(c, "country", "United States", planet)
    state = create_geo(c, "state", "California", country)
    county = create_geo(c, "county", "San Francisco County", state)
    city = create_geo(c, "city", "San Francisco", county)
    street = create_geo(c, "street", "Market Street", city)

    spaces = {}
    for key, label in (
        ("customer", "Customer Plaza"),
        ("merchant", "Merchant Storefront"),
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


def build_societies(c: httpx.Client, spaces: dict[str, str]) -> dict[str, str]:
    societies: dict[str, str] = {}
    for key, name, description in (
        ("customer", "Customer Society", "Customers browsing and negotiating"),
        ("merchant", "Merchant Society", "Merchant storefront operations"),
    ):
        result = call(c, "POST", "/societies", json={"name": name, "description": description})
        society_id = result["society_id"]
        societies[key] = society_id
        call(
            c,
            "POST",
            f"/planet/geo/{spaces[key]}/host",
            json={"society_id": society_id},
        )
    return societies


def build_actors(c: httpx.Client, societies: dict[str, str]) -> dict[str, str]:
    actors: dict[str, str] = {}
    actor_defs = (
        (
            "customer",
            "Customer",
            "human",
            ["browse_and_purchase"],
            {
                "preferences": {"cost": 0.9, "speed": 0.1},
                "negotiation_policy": "competitive",
            },
        ),
        (
            "merchant",
            "Merchant",
            "human",
            ["manage_store"],
            {
                "preferences": {"profit": 0.7, "customer_satisfaction": 0.3},
                "negotiation_policy": "balanced",
            },
        ),
    )
    for society_key, name, actor_type, goals, strategy in actor_defs:
        result = call(
            c,
            "POST",
            "/actors",
            json={
                "name": name,
                "actor_type": actor_type,
                "goals": goals,
                "society_id": societies[society_key],
                "capabilities": [{"name": "general"}],
                "metadata": {"strategy": {"resources": {}, "risk_tolerance": 0.5, **strategy}},
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
            "price": TRACKED_PRODUCT_PRICE,
            "quantity": 10,
            "min_price": TRACKED_PRODUCT_FLOOR,
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
        societies = build_societies(c, spaces)
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
