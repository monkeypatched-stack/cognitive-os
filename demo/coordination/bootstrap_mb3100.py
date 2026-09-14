"""MB-3100 — Customer Order Coordination: World Bootstrap.

Builds the world exclusively through production REST APIs (same
principle as demo/ecommerce/bootstrap.py). Five societies matching the
benchmark's own diagram: Marketplace, Warehouse, Inventory, Logistics,
Customer. Warehouse and Inventory both subscribe to "OrderCreated";
Logistics subscribes to "InventoryReserved"; Customer subscribes to
"ShipmentCreated" — real, inspectable per-society properties
(POST /societies' subscribed_events), not a hardcoded map anywhere in
this script or the runtime.
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


# ── Geography ────────────────────────────────────────────────────────────


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
        ("marketplace", "Marketplace"),
        ("warehouse", "Distribution Center"),
        ("inventory", "Inventory Depot"),
        ("logistics", "Logistics Hub"),
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


# ── Societies ────────────────────────────────────────────────────────────

# (key, name, description, subscribed_events)
SOCIETY_DEFS = (
    (
        "marketplace",
        "Marketplace Society",
        "Coordinates order orchestration",
        ("OrderCreated",),
    ),
    ("warehouse", "Warehouse Society", "Picking and packing", ("OrderCreated",)),
    ("inventory", "Inventory Society", "Stock reservation", ("OrderCreated",)),
    (
        "logistics",
        "Logistics Society",
        "Delivery routing and driver coordination",
        ("InventoryReserved",),
    ),
    (
        "customer",
        "Customer Society",
        "Customers browsing and purchasing",
        ("ShipmentCreated",),
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


# ── Actors ───────────────────────────────────────────────────────────────

# (society key, name, actor_type, goals)
ACTOR_DEFS = (
    ("customer", "Alice", "human", ["browse_and_purchase"]),
    ("warehouse", "Warehouse Worker", "human", ["fulfill_picking_tasks"]),
    ("warehouse", "Picker", "human", ["pick_items"]),
    ("inventory", "Inventory Robot", "robot", ["reserve_inventory"]),
    ("logistics", "Driver", "human", ["deliver_package"]),
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

    # DeliveryCapability needs a real customer delivery-address KG entity
    # (found live in demo/ecommerce this session) — POST /actors/{id}/addresses
    # writes both the society-scoped contact record AND the KG entity.
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


# ── Commerce ─────────────────────────────────────────────────────────────

TRACKED_PRODUCT_NAME = "Wireless Gaming Mouse"
PRODUCT_DEFS = ((TRACKED_PRODUCT_NAME, 59.99, 40),)


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

    products = {}
    for name, price, quantity in PRODUCT_DEFS:
        result = _call(
            client,
            "POST",
            "/products",
            json={
                "store_id": store_id,
                "merchant_id": "merchant_bob",
                "name": name,
                "price": price,
                "quantity": quantity,
            },
        )
        products[name] = result.get("product_id", result.get("id", ""))

    # A real delivery rider (found live: DeliveryCapability needs a real
    # PERSON entity with status="available" — a separate concept from the
    # Driver ACTOR above, which participates in coordination/cognition;
    # this is the KG record select_delivery_riders() actually reads).
    _call(client, "POST", "/riders", json={"name": "Driver"})

    return {"store_id": store_id, "merchant_id": "merchant_bob", "products": products}


# ── Validation ───────────────────────────────────────────────────────────


def verify_world(client: httpx.Client, attempts: int = 4, delay_seconds: float = 2.0) -> dict:
    """See demo/ecommerce/bootstrap.py's verify_world for why this retries
    specifically on all-presence_consistency violations: cheap insurance
    against one confirmed cause (an improperly-reset environment), not a
    real runtime race — the actual fix is procedural (always fully
    restart the server when resetting, never just flush)."""
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


# ── Orchestration ────────────────────────────────────────────────────────


def bootstrap_world(client: httpx.Client | None = None) -> dict[str, Any]:
    owns_client = client is None
    client = client or _client()
    try:
        spaces = build_geography(client)
        societies = build_societies(client, spaces)
        actors = build_actors(client, societies)
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
    print(f"  Products:   {len(world['commerce']['products'])}")
    print(f"  Validation: {'PASSED' if world['verification'].get('ok') else 'FAILED'}")
