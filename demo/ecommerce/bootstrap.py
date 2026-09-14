"""CognitiveOS E-Commerce Demo — World Bootstrap.

Builds the entire demo world exclusively through production REST APIs
(the same ones any external client uses — no runtime internals, no
direct graph mutation, no bypassed validation). This module owns *world
construction only*: geography, societies, actors, merchants, and
product/inventory. It never reasons — reasoning belongs to /prompt,
called from run_demo.py, not from here.

Every call below hits the live server at BASE_URL. Run the server
first (scripts/start_server.sh) before running this module or
run_demo.py.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

import httpx

BASE_URL = os.getenv("DEMO_BASE_URL", "http://localhost:8031/api/v1/agentos")
# World-construction calls are fast. /prompt is not: it runs a real LLM
# planning call (observed 5-15s for planning alone) plus, at default
# run_type="full", query + simulate + execute — comfortably over 30s in
# practice. One generous client-wide timeout is simpler and safer than
# tracking which specific calls need more room.
TIMEOUT = 180.0


class ApiError(RuntimeError):
    """A production API call returned a non-2xx response. Bootstrap
    fails loudly rather than continuing on a half-built world."""


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
    """Planet -> Country -> State -> County -> City -> Street, then one
    Building+Space per society this demo hosts. Real entities, created
    through POST /planet/geo — the same endpoint any client would use;
    nothing here is a PlanetaryRuntime auto-bootstrap default."""
    planet = _create_geo(client, "planet", "Earth")
    country = _create_geo(client, "country", "United States", planet)
    state = _create_geo(client, "state", "California", country)
    county = _create_geo(client, "county", "San Francisco County", state)
    city = _create_geo(client, "city", "San Francisco", county)
    street = _create_geo(client, "street", "Market Street", city)

    spaces: dict[str, str] = {}
    for key, label in (
        ("marketplace", "Marketplace"),
        ("merchant", "Merchant Plaza"),
        ("warehouse", "Distribution Center"),
        ("logistics", "Logistics Hub"),
        ("payment", "Payment Processing Center"),
        ("customer", "Customer Plaza"),
        ("support", "Support Center"),
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

SOCIETY_DEFS = (
    (
        "marketplace",
        "Marketplace Society",
        "Coordinates catalog browsing and order orchestration",
    ),
    ("merchant", "Merchant Society", "Merchant storefront operations"),
    ("warehouse", "Warehouse Society", "Inventory, picking, and packing"),
    ("logistics", "Logistics Society", "Delivery routing and driver coordination"),
    ("payment", "Payment Society", "Payment authorization and settlement"),
    ("customer", "Customer Society", "Customers browsing and purchasing"),
    (
        "support",
        "Customer Support Society",
        "Post-purchase support and issue resolution",
    ),
)


def build_societies(client: httpx.Client, spaces: dict[str, str]) -> dict[str, str]:
    """One Society per SOCIETY_DEFS entry, each hosted at its matching
    Space from build_geography() via POST /planet/geo/{id}/host — "every
    Society is associated with at least one Space" is enforced by the
    real registration workflow this same host call feeds, not asserted
    here; verify/world (called at the end of bootstrap_world) is the
    actual check."""
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


# ── Actors ───────────────────────────────────────────────────────────────

ACTOR_DEFS = (
    # (society key, name, actor_type, goals)
    ("customer", "Alice", "human", ["browse_and_purchase"]),
    ("merchant", "Bob", "human", ["manage_store"]),
    ("warehouse", "Warehouse Worker", "human", ["manage_inventory"]),
    ("warehouse", "Picker", "human", ["pick_items"]),
    ("warehouse", "Packer", "human", ["pack_orders"]),
    ("logistics", "Driver", "human", ["deliver_package"]),
    ("warehouse", "Inventory Robot", "robot", ["restock_shelves"]),
    ("support", "Customer Support Agent", "ai_agent", ["assist_customers"]),
)


def build_actors(client: httpx.Client, societies: dict[str, str]) -> dict[str, str]:
    """Every actor is registered with an explicit society_id — the real
    registration workflow (PlanetaryRuntime.register_actor(), reached
    through this same POST /actors route) places each actor at its
    society's hosted Space as part of registering it; no separate move
    call is needed for initial placement. actor_type must be one of the
    real ActorType values (human/robot/ai_agent/enterprise/government)."""
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

    # DeliveryCapability (grocery.py) resolves a delivery address from a
    # real EntityType.ADDRESS entity in the commerce KG — found live, via
    # a genuine execution failure ("no delivery address on file for
    # actor"), not a guess. POST /actors/{id}/addresses is the real,
    # production way to set one (it now also writes the matching KG
    # entity, not just the society-scoped contact record).
    _call(
        client,
        "POST",
        f"/actors/{actors['Alice']}/addresses",
        json={
            "actor_id": actors["Alice"],
            "address_type": "physical",
            "value": "1600 Customer Ave, San Francisco, CA 94103",
            "is_primary": True,
        },
    )

    return actors


# ── Commerce ─────────────────────────────────────────────────────────────

PRODUCT_DEFS = (
    ("Wireless Gaming Mouse", 59.99, 40),
    ("Mechanical Keyboard", 89.99, 25),
    ("USB-C Hub", 34.99, 60),
    ("Webcam 1080p", 45.00, 15),
)


def build_commerce(client: httpx.Client) -> dict[str, Any]:
    """One merchant storefront and a small real product catalog with
    real starting inventory (quantity is set at product creation — the
    same field a real merchant onboarding flow would set)."""
    merchant = _call(
        client,
        "POST",
        "/merchants",
        json={
            "merchant_id": "merchant_bob",
            "store_name": "Bob's Electronics",
            "delivery_fee": 4.99,
            # DeliveryCapability (grocery.py) requires a store address before
            # it will schedule a pickup/delivery — found live, via a genuine
            # execution failure ("no address on file for store ..."), not a
            # guess. address is a real, already-supported MerchantCreateRequest
            # field (api/gateway_models.py) this just wasn't populating.
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

    # DeliveryCapability's rider assignment (select_delivery_riders,
    # logistics.py) requires at least one real PERSON entity with
    # status="available" — found live ("no riders available"), not a
    # guess. POST /riders is the real, production way to register one
    # (the Driver actor created in build_actors() is a society/geography
    # actor, a completely separate system from the commerce KG's rider
    # pool DeliveryCapability actually reads from).
    _call(client, "POST", "/riders", json={"name": "Driver"})

    return {"store_id": store_id, "merchant_id": "merchant_bob", "products": products}


# ── Validation ───────────────────────────────────────────────────────────


def verify_world(client: httpx.Client, attempts: int = 4, delay_seconds: float = 2.0) -> dict:
    """POST /verify/world — the real world-invariant validator (Gate 3,
    docs/adr/010-world-validation-engine.md), not a demo-local check.
    Raises if the world is invalid so the demo never proceeds on a
    broken graph.

    Retries specifically when EVERY violation is presence_consistency's
    actor_without_presence, as cheap insurance against exactly one
    confirmed cause: resetting the environment by flushing Redis/Mongo
    WITHOUT restarting the server leaves the live PlanetaryRuntime's
    in-memory actor/geography state stale relative to the now-empty
    backing store, which manifests as every actor missing presence
    (verified directly: a properly reset run — stop server, flush,
    restart — hit zero violations on the first attempt with this same
    code; a flush-only reset reproduced the failure twice in a row). A
    genuine async-write race was suspected first and could still exist,
    but wasn't actually confirmed — this retry is deliberately kept
    anyway since it's harmless when unneeded and cheap insurance if a
    real one turns up later. Any OTHER violation category fails
    immediately, no retry — those are real world-construction bugs a
    retry wouldn't fix, and this must not paper over them. The actual
    fix for the confirmed cause is procedural, not code: always fully
    restart the server when resetting (see README.md).
    """
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
    """Build the complete demo world. Returns every id run_demo.py
    needs (actors, societies, spaces, store/product ids)."""
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
