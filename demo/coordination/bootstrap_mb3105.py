"""MB-3105 — Shipment Delivered: World Bootstrap.

Same real-API-only principle as bootstrap_mb3100.py. Also drives an
order through its real lifecycle (POST /orders -> POST /shipments ->
.../transit -> .../deliver) all the way to "delivered" — every step a
real production API call, not a KG hack — so the demo script's own
"trigger" step is exactly the one call (POST /orders/{id}/confirm-
receipt) that both completes the order AND publishes the real
ShipmentDelivered domain event or/propagation this benchmark tests.

Customer Society subscribes to "ShipmentDelivered" (being coordinated
is the real proof of "review request created", same convention MB-3101
already used for Merchant notification). Loyalty Society subscribes to
it too, reacting via LoyaltyAwardCapability (wraps the real, existing
award_points(), MB-3049).
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

import httpx

BASE_URL = os.getenv("DEMO_BASE_URL", "http://localhost:8031/api/v1/agentos")
TIMEOUT = 180.0


class ApiError(RuntimeError):
    """A production API call returned a non-2xx response."""


def _client() -> httpx.Client:
    return httpx.Client(base_url=BASE_URL, timeout=TIMEOUT)


def _call(client: httpx.Client, method: str, path: str, **kwargs: Any) -> dict:
    r = client.request(method, path, **kwargs)
    if r.status_code >= 300:
        raise ApiError(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
    if not r.text:
        return {}
    try:
        return r.json()
    except Exception:
        return {}


def _create_geo(client: httpx.Client, entity_type: str, name: str, parent_id: str | None = None) -> str:
    body: dict[str, Any] = {"entity_type": entity_type, "name": name}
    if parent_id:
        body["parent_id"] = parent_id
    result = _call(client, "POST", "/planet/geo", json=body)
    return result["entity_id"]


def build_geography(client: httpx.Client) -> dict[str, str]:
    planet = _create_geo(client, "planet", "Earth")
    country = _create_geo(client, "country", "United States", planet)
    state = _create_geo(client, "state", "California", country)
    county = _create_geo(client, "county", "San Francisco County", state)
    city = _create_geo(client, "city", "San Francisco", county)
    street = _create_geo(client, "street", "Market Street", city)

    spaces: dict[str, str] = {}
    for key, label in (
        ("customer", "Customer Plaza"),
        ("loyalty", "Loyalty Program Office"),
    ):
        building = _create_geo(client, "building", f"{label} Building", street)
        space = _create_geo(client, "space", f"{label} Floor", building)
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


SOCIETY_DEFS = (
    (
        "customer",
        "Customer Society",
        "Customers browsing and purchasing",
        ("ShipmentDelivered",),
    ),
    (
        "loyalty",
        "Loyalty Society",
        "Loyalty program administration",
        ("ShipmentDelivered",),
    ),
)


def build_societies(client: httpx.Client, spaces: dict[str, str]) -> dict[str, str]:
    societies: dict[str, str] = {}
    for key, name, description, subscribed_events in SOCIETY_DEFS:
        result = _call(
            client,
            "POST",
            "/societies",
            json={
                "name": name,
                "description": description,
                "subscribed_events": list(subscribed_events),
            },
        )
        society_id = result["society_id"]
        societies[key] = society_id
        _call(
            client,
            "POST",
            f"/planet/geo/{spaces[key]}/host",
            json={"society_id": society_id},
        )
    return societies


ACTOR_DEFS = (
    ("customer", "Alice", "human", ["browse_and_purchase"]),
    ("loyalty", "Loyalty Bot", "ai_agent", ["manage_loyalty_program"]),
)


def build_actors(client: httpx.Client, societies: dict[str, str]) -> dict[str, str]:
    actors: dict[str, str] = {}
    for society_key, name, actor_type, goals in ACTOR_DEFS:
        result = _call(
            client,
            "POST",
            "/actors",
            json={
                "name": name,
                "actor_type": actor_type,
                "goals": goals,
                "society_id": societies[society_key],
                "capabilities": [{"name": "general"}],
            },
        )
        actors[name] = result["actor_id"]

    _call(
        client,
        "POST",
        f"/actors/{actors['Alice']}/addresses",
        json={
            "actor_id": actors["Alice"],
            "address_type": "physical",
            "value": "500 Customer Ave, San Francisco, CA 94103",
            "is_primary": True,
        },
    )

    return actors


TRACKED_PRODUCT_NAME = "Wireless Gaming Mouse"


def build_commerce_and_order(client: httpx.Client, alice_actor_id: str) -> dict[str, Any]:
    merchant = _call(
        client,
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

    product = _call(
        client,
        "POST",
        "/products",
        json={
            "store_id": store_id,
            "merchant_id": "merchant_bob",
            "name": TRACKED_PRODUCT_NAME,
            "price": 59.99,
            "quantity": 40,
        },
    )
    product_id = product.get("product_id", product.get("id", ""))

    # Drive the order through its real lifecycle — every step a real
    # production API call — up to "delivered". The demo script's own
    # trigger step (POST /orders/{id}/confirm-receipt) takes it from
    # there, since that's genuinely when the order becomes eligible for
    # a loyalty award or review (both require status=="completed").
    order = _call(
        client,
        "POST",
        "/orders",
        json={
            "actor_id": alice_actor_id,
            "items": [
                {
                    "id": product_id,
                    "name": TRACKED_PRODUCT_NAME,
                    "qty": 1,
                    "price": 59.99,
                }
            ],
            "question": "deliver my order",
        },
    )
    order_id = order.get("order_id", "")

    shipment = _call(
        client,
        "POST",
        "/shipments",
        json={
            "order_id": order_id,
            "packages": [{"box": 1, "items": [product_id]}],
        },
    )
    shipment_id = shipment.get("shipment_id", "")

    _call(client, "POST", f"/shipments/{shipment_id}/transit")
    _call(client, "POST", f"/shipments/{shipment_id}/deliver")

    return {
        "store_id": store_id,
        "merchant_id": "merchant_bob",
        "products": {TRACKED_PRODUCT_NAME: product_id},
        "order_id": order_id,
        "shipment_id": shipment_id,
    }


def verify_world(client: httpx.Client, attempts: int = 4, delay_seconds: float = 2.0) -> dict:
    last_result: dict | None = None
    for attempt in range(attempts):
        result = _call(client, "POST", "/verify/world")
        if result.get("ok", False):
            return result
        last_result = result
        violations = result.get("violations", [])
        only_presence = bool(violations) and all(v.get("category") == "presence_consistency" for v in violations)
        if not only_presence or attempt == attempts - 1:
            break
        time.sleep(delay_seconds)
    raise ApiError(f"World validation failed: {last_result}")


def bootstrap_world(client: httpx.Client | None = None) -> dict[str, Any]:
    owns_client = client is None
    client = client or _client()
    try:
        spaces = build_geography(client)
        societies = build_societies(client, spaces)
        actors = build_actors(client, societies)
        commerce = build_commerce_and_order(client, actors["Alice"])
        verification = verify_world(client)
        return {
            "spaces": spaces,
            "societies": societies,
            "actors": actors,
            "commerce": commerce,
            "verification": verification,
        }
    finally:
        if owns_client:
            client.close()


if __name__ == "__main__":
    try:
        world = bootstrap_world()
    except ApiError as exc:
        print(f"Bootstrap failed: {exc}", file=sys.stderr)
        sys.exit(1)
    print("World bootstrapped:")
    print(f"  Spaces:     {len(world['spaces'])}")
    print(f"  Societies:  {len(world['societies'])}")
    print(f"  Actors:     {len(world['actors'])}")
    print(f"  Order:      {world['commerce']['order_id']} (delivered)")
    print(f"  Validation: {'PASSED' if world['verification'].get('ok') else 'FAILED'}")
