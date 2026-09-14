"""MB-3104 — Customer Cancels Order: World Bootstrap.

Same real-API-only principle as bootstrap_mb3100.py. Warehouse subscribes
to both "OrderCreated" (reserve, same InventoryReserveCapability path
MB-3100 already proved) and "OrderCancelled" (release, same
InventoryReleaseCapability MB-3102/3103 already proved works in
isolation). Logistics and Merchant both subscribe to "OrderCancelled"
too — being coordinated at all is the proof of "notified", the same
convention MB-3101 already used for Merchant.
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
        ("warehouse", "Distribution Center"),
        ("logistics", "Logistics Hub"),
        ("merchant", "Merchant Plaza"),
        ("customer", "Customer Plaza"),
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
        "warehouse",
        "Warehouse Society",
        "Picking and inventory",
        ("OrderCreated", "OrderCancelled"),
    ),
    (
        "logistics",
        "Logistics Society",
        "Delivery routing and driver coordination",
        ("InventoryReserved", "OrderCancelled"),
    ),
    (
        "merchant",
        "Merchant Society",
        "Merchant storefront operations",
        ("OrderCancelled",),
    ),
    ("customer", "Customer Society", "Customers browsing and purchasing", ()),
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
    ("warehouse", "Inventory Robot", "robot", ["manage_inventory"]),
    ("logistics", "Driver", "human", ["deliver_package"]),
    ("merchant", "Bob", "human", ["manage_store"]),
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


def build_wallet(client: httpx.Client, owner_actor_id: str) -> str:
    """A real payment account (POST /wallets) — without this, Payment/
    PaymentConfirmation always fails with "no wallet account found",
    which blocks CancelOrder's own real business rule ("nothing to
    reverse" for an order that was never paid). Confirmed live: this
    fixture previously had no wallet at all, so the cancel-cascade this
    benchmark exists to prove could never be reached."""
    result = _call(client, "POST", "/wallets", json={"owner": owner_actor_id, "balance": 500.0})
    return result["wallet_id"]


TRACKED_PRODUCT_NAME = "Wireless Gaming Mouse"


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

    result = _call(
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
    product_id = result.get("product_id", result.get("id", ""))

    return {
        "store_id": store_id,
        "merchant_id": "merchant_bob",
        "products": {TRACKED_PRODUCT_NAME: product_id},
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
        build_wallet(client, actors["Alice"])
        commerce = build_commerce(client)
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
    print(f"  Validation: {'PASSED' if world['verification'].get('ok') else 'FAILED'}")
