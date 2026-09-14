"""MB-3308 — Emergency Replanning: World Bootstrap.

Real-API-only, two warehouses, same fire/evacuation mechanism proven
in demo/coordination (POST /events, type=fire) — real evacuation, not
simulated.
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

    spaces = {}
    for key, label in (("warehouse_a", "Warehouse A"), ("warehouse_b", "Warehouse B")):
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
        ("warehouse_a", "Warehouse A Society", "Warehouse A picking and packing"),
        ("warehouse_b", "Warehouse B Society", "Warehouse B picking and packing"),
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
    for society_key, name in (
        ("warehouse_a", "Warehouse A Worker"),
        ("warehouse_b", "Warehouse B Worker"),
    ):
        result = call(
            c,
            "POST",
            "/actors",
            json={
                "name": name,
                "actor_type": "human",
                "goals": ["pack_orders"],
                "society_id": societies[society_key],
                "capabilities": [{"name": "general"}],
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
            "quantity": 5,
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
