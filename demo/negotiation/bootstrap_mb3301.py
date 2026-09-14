"""MB-3301 — Driver Negotiation: World Bootstrap.

Real-API-only. Customer + Warehouse Worker + Driver, a real order and
shipment (so there's a real entity for the negotiated schedule to
persist onto). The Driver's own strategy metadata carries a real
constraint (existing delivery commitments) that its replies and
NegotiateTerms bargain should genuinely reflect.
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
    for key, label in (
        ("customer", "Customer Plaza"),
        ("warehouse", "Distribution Center"),
        ("logistics", "Logistics Hub"),
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
        ("customer", "Customer Society", "Customers browsing and purchasing"),
        ("warehouse", "Warehouse Society", "Picking and packing"),
        ("logistics", "Logistics Society", "Delivery routing and driver coordination"),
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
        ("customer", "Customer", "human", ["browse_and_purchase"], None),
        ("warehouse", "Warehouse Worker", "human", ["pack_orders"], None),
        (
            "logistics",
            "Driver",
            "human",
            ["deliver_package"],
            {
                "preferences": {"reliability": 0.8, "speed": 0.2},
                "resources": {
                    "existing_deliveries_tomorrow_morning": 3,
                    "capacity_per_slot": 3,
                },
                "risk_tolerance": 0.3,
                "negotiation_policy": "balanced",
            },
        ),
    )
    for society_key, name, actor_type, goals, strategy in actor_defs:
        body: dict[str, Any] = {
            "name": name,
            "actor_type": actor_type,
            "goals": goals,
            "society_id": societies[society_key],
            "capabilities": [{"name": "general"}],
        }
        if strategy:
            body["metadata"] = {"strategy": strategy}
        result = call(c, "POST", "/actors", json=body)
        actors[name] = result["actor_id"]

    call(
        c,
        "POST",
        f"/actors/{actors['Customer']}/addresses",
        json={
            "actor_id": actors["Customer"],
            "address_type": "physical",
            "value": "500 Customer Ave, San Francisco, CA 94103",
            "is_primary": True,
        },
    )
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
            "quantity": 10,
        },
    )
    product_id = product.get("product_id", product.get("id", ""))
    return {
        "store_id": store_id,
        "merchant_id": "merchant_bob",
        "products": {TRACKED_PRODUCT_NAME: product_id},
    }


def build_rider(c: httpx.Client) -> str:
    result = call(c, "POST", "/riders", json={"name": "Driver"})
    return result.get("rider_id", result.get("id", ""))


def bootstrap_world(c: httpx.Client | None = None) -> dict[str, Any]:
    owns_client = c is None
    c = c or client()
    try:
        spaces = build_geography(c)
        societies = build_societies(c, spaces)
        actors = build_actors(c, societies)
        commerce = build_commerce(c)
        rider_id = build_rider(c)
        verification = verify_world(c)
        return {
            "spaces": spaces,
            "societies": societies,
            "actors": actors,
            "commerce": commerce,
            "rider_id": rider_id,
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
