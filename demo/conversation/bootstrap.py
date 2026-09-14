"""Natural Language Actor-to-Actor Communication: World Bootstrap.

Same real-API-only principle as demo/coordination/bootstrap_mb31XX.py —
every entity here is created via a real production REST call, never a
KG hack. This world stays STATIC (geography, societies, actors, the
product catalog): run_conversation.py itself drives the order/shipment
lifecycle mutations BETWEEN conversation turns via real API calls, so
each actor is genuinely reasoning over whatever the real world state is
at that point in the story, not a pre-scripted snapshot.

Five actors, one per role the demo's own script calls for: Customer,
Warehouse Worker, Inventory Robot, Driver, Support Agent — named
exactly as their role, matching the transcript labels the spec itself
uses.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

import httpx

BASE_URL = os.getenv("DEMO_BASE_URL", "http://localhost:8031/api/v1/agentos")
TIMEOUT = 180.0

TRACKED_PRODUCT_NAME = "Wireless Gaming Mouse"
TRACKED_PRODUCT_PRICE = 59.99
TRACKED_PRODUCT_QUANTITY = 3


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
        ("warehouse", "Distribution Center"),
        ("inventory", "Inventory Control Room"),
        ("logistics", "Logistics Hub"),
        ("support", "Support Desk"),
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
    ("customer", "Customer Society", "Customers browsing and purchasing"),
    ("warehouse", "Warehouse Society", "Picking and packing"),
    ("inventory", "Inventory Society", "Stock and reservation management"),
    ("logistics", "Logistics Society", "Delivery routing and driver coordination"),
    ("support", "Support Society", "Customer support operations"),
)


def build_societies(client: httpx.Client, spaces: dict[str, str]) -> dict[str, str]:
    societies: dict[str, str] = {}
    for key, name, description in SOCIETY_DEFS:
        result = _call(
            client,
            "POST",
            "/societies",
            json={"name": name, "description": description},
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
    ("customer", "Customer", "human", ["browse_and_purchase"]),
    ("warehouse", "Warehouse Worker", "human", ["pack_orders"]),
    ("inventory", "Inventory Robot", "robot", ["manage_inventory"]),
    ("logistics", "Driver", "human", ["deliver_package"]),
    ("support", "Support Agent", "human", ["assist_customers"]),
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
        f"/actors/{actors['Customer']}/addresses",
        json={
            "actor_id": actors["Customer"],
            "address_type": "physical",
            "value": "500 Customer Ave, San Francisco, CA 94103",
            "is_primary": True,
        },
    )

    return actors


def build_commerce(client: httpx.Client) -> dict[str, Any]:
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
            "price": TRACKED_PRODUCT_PRICE,
            "quantity": TRACKED_PRODUCT_QUANTITY,
        },
    )
    product_id = product.get("product_id", product.get("id", ""))

    return {
        "store_id": store_id,
        "merchant_id": "merchant_bob",
        "products": {TRACKED_PRODUCT_NAME: product_id},
    }


def build_rider(client: httpx.Client) -> str:
    result = _call(client, "POST", "/riders", json={"name": "Driver"})
    return result.get("rider_id", result.get("id", ""))


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
        commerce = build_commerce(client)
        rider_id = build_rider(client)
        verification = verify_world(client)
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
    print(f"  Validation: {'PASSED' if world['verification'].get('ok') else 'FAILED'}")
