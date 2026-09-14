"""Commerce API — REST endpoints that build the commerce graph.

World Graph Builder / Cognitive Reasoning separation: every route here
mutates PlanetaryRuntime.knowledge_graph (the SAME KnowledgeGraph every
kernel/domains/*.py function already reads/writes — grocery.py,
commerce.py, logistics.py, finance.py) directly and deterministically.
No planning, no reasoning, no cognition happens here — that's exclusively
/prompt's job (see api/routes/prompt.py). A benchmark builds its world
entirely through routes like these, then asks a real question via
/prompt to see how an actor reasons over what was just built.

GET    /products                    — browse/search the catalog
POST   /products                    — a merchant lists a new product
GET    /products/{id}               — product detail (inventory, reviews, price)
PATCH  /products/{id}               — a merchant edits their listing
DELETE /products/{id}               — a merchant delists their product
POST   /products/{id}/promotions    — a merchant runs a time-limited sale
POST   /products/{id}/reviews       — a customer leaves a review
POST   /merchants                   — onboard a merchant (creates their store)
PATCH  /organizations/{id}          — admin-edit any organization (store, warehouse, ...)
POST   /riders                      — register a delivery rider
POST   /wallets                     — create a real payment account
GET    /wallets/{id}                — read a wallet's real current balance
DELETE /wallets/{id}                — remove a payment account entirely
GET    /actors/{id}/wallet          — find an actor's own wallet by owner (no order needed)
DELETE /actors/{id}/orders          — remove every order this actor placed
POST   /cart/{actor_id}/items       — add a product to a customer's cart
GET    /cart/{actor_id}             — view a customer's cart
POST   /cart/{actor_id}/coupon      — apply a coupon code to a cart
POST   /inventory/reserve           — hold stock (CAS, no-oversell)
POST   /inventory/confirm           — commit a held reservation
POST   /inventory/backorder         — queue a claim for out-of-stock stock
POST   /inventory/fulfill-backorders — fulfill queued claims after a restock
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from src.monkey_brain.api.dependencies import authorize_acting_for, require_permission
from src.monkey_brain.api.gateway_models import (
    BrowseProductsResponse,
    CartItemAddRequest,
    CartResponse,
    CouponApplyRequest,
    InventoryActionResponse,
    InventoryBackorderRequest,
    InventoryConfirmRequest,
    InventoryFulfillBackordersRequest,
    InventoryReserveRequest,
    MerchantCreateRequest,
    MerchantResponse,
    OrganizationUpdateRequest,
    PantryItemCreateRequest,
    PantryItemResponse,
    ProductCreateRequest,
    ProductResponse,
    ProductUpdateRequest,
    PromotionCreateRequest,
    ReviewCreateRequest,
    RiderCreateRequest,
    RiderResponse,
    WalletCreateRequest,
    WalletResponse,
)
from src.monkey_brain.api.idempotency import idempotent

logger = logging.getLogger("agentos.gateway.commerce")
router = APIRouter()


def _get_planetary_runtime(request: Request) -> Any:
    return getattr(request.app.state, "planetary_runtime", None)


def _kg(request: Request) -> Any:
    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")
    return pr.knowledge_graph


def _result(d: dict[str, Any]) -> dict[str, Any]:
    """Every kernel/domains/*.py function already returns an honest
    {"success": bool, "error": str, ...} dict — this just maps that
    onto the matching HTTP status rather than introducing a second,
    parallel error convention."""
    if not d.get("success", True):
        error = d.get("error", "request failed")
        status = 404 if "no such" in error or "not found" in error else 400
        raise HTTPException(status_code=status, detail=error)
    return d


def _publish_catalog_event(request: Request, domain_event: str, description: str, payload: dict[str, Any]) -> None:
    """list_product/update_product/remove_product mutate the KnowledgeGraph
    directly and never went through ActionExecutor or an explicit publish
    like orders.py's ShipmentDelivered — so a catalog change was invisible
    to context_stream. The plan stage's incremental-scheduling skip-gate
    (has_new_relevant_activity) only re-plans when it sees a WORLD_UPDATE
    event with domain_event set since the actor's last plan; with no event
    ever published here, a stale plan (e.g. referencing a product added or
    removed after it was cached) kept getting reused forever."""
    pr = _get_planetary_runtime(request)
    if pr is None:
        return
    from src.monkey_brain.kernel.society.context_stream import (
        ContextEvent,
        ContextEventType,
    )

    pr.context_stream.publish(
        ContextEvent(
            event_type=ContextEventType.WORLD_UPDATE,
            description=description,
            payload={**payload, "domain_event": domain_event},
        )
    )


# ── Merchants ───────────────────────────────────────────────────────────


@router.post("/merchants", tags=["Commerce"], response_model=MerchantResponse)
@idempotent("merchants.create")
async def create_merchant(
    body: MerchantCreateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    from src.monkey_brain.kernel.domains.commerce import onboard_merchant
    from src.monkey_brain.kernel.security_boundary import privileged_infrastructure

    attrs = {k: v for k, v in body.model_dump().items() if k not in ("merchant_id", "store_name")}
    # onboard_merchant() writes the Store ORGANIZATION entity via
    # kg.add_entity(), which asserts assert_state_mutation_allowed() --
    # this route is operator/onboarding-driven (no agent, no capability,
    # no committed plan behind it), so it was never actually coverable by
    # ensure_governed the way an agent capability would be. Confirmed
    # live: fails closed with SecurityBoundaryDenied (500) outside
    # insecure-dev-mode without this.
    with privileged_infrastructure(reason="POST /merchants: operator onboarding a merchant, not an agent action"):
        return _result(onboard_merchant(_kg(request), body.merchant_id, body.store_name, **attrs))


@router.get("/merchants", tags=["Commerce"])
async def list_merchants(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-world")),
) -> list[dict[str, Any]]:
    """Every real Store (ORGANIZATION) entity — store_id, name, owner_id
    (the merchant's actor_id), and every free-form store attribute
    (is_open, hours, address, category, trust_score, reputation_rating,
    delivery_fee, ...) set at onboarding. No prior route resolved
    store_id -> owning actor or exposed a store's own runtime attributes
    in one call; product listings only ever carried store_id."""
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    kg = _kg(request)
    return [
        {"store_id": e.entity_id, "name": e.name, **e.attributes} for e in kg.entities_by_type(EntityType.ORGANIZATION)
    ]


@router.get("/merchants/{store_id}", tags=["Commerce"])
async def get_merchant(
    store_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-world")),
) -> dict[str, Any]:
    entity = _kg(request).get_entity(store_id)
    if entity is None:
        raise HTTPException(status_code=404, detail=f"no such store {store_id!r}")
    return {"store_id": entity.entity_id, "name": entity.name, **entity.attributes}


@router.patch("/organizations/{org_id}", tags=["Commerce"])
@idempotent("organizations.update")
async def update_organization_route(
    org_id: str,
    body: OrganizationUpdateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    """Admin-only edit (or creation) of any ORGANIZATION entity — a
    store, warehouse, supplier, factory, truck, bank, or payment
    processor. Unlike PATCH /products/{id}, there's no merchant-
    ownership check (a warehouse isn't merchant-owned), and a new
    org_id creates rather than 404s (no dedicated warehouse/truck/
    supplier/factory creation route exists) — see update_organization()'s
    own docstring. The intended real use: injecting a genuine external
    disruption live (a warehouse fire — PATCH the warehouse's own
    "status" to anything other than "operational") against the same
    real commerce KG open_products()/supply_chain_ok() actually read,
    the organization-level equivalent of PATCH /products/{id} being
    used to simulate a single product's stock running low.
    """
    from src.monkey_brain.kernel.domains.commerce import update_organization

    updates = body.model_dump()
    org_name = updates.get("name") or getattr(_kg(request).get_entity(org_id), "name", None) or org_id
    result = _result(update_organization(_kg(request), org_id, **updates))
    _publish_catalog_event(
        request,
        "OrganizationUpdated",
        f"{org_name} updated",
        {"org_id": org_id, "updates": updates},
    )
    return result


# ── Pantry (household inventory) ───────────────────────────────────────


@router.post("/pantry", tags=["Commerce"], response_model=PantryItemResponse)
@idempotent("pantry.create")
async def create_pantry_item(
    body: PantryItemCreateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    """Adds a real household pantry item — the write side of
    find_household_pantry_stock()/predict_household_stockout()
    (grocery.py), which previously had no way to get real data to read."""
    from src.monkey_brain.kernel.domains.grocery import add_pantry_item
    from src.monkey_brain.kernel.security_boundary import privileged_infrastructure

    attrs = {
        k: v for k, v in body.model_dump().items() if k not in ("name", "quantity", "owner_id", "household_members")
    }
    # Same class of fix as POST /merchants above -- add_pantry_item()
    # writes via kg.add_entity(), operator-driven, not an agent action.
    with privileged_infrastructure(reason="POST /pantry: operator seeding a pantry item, not an agent action"):
        return _result(
            add_pantry_item(
                _kg(request),
                body.name,
                body.quantity,
                owner_id=body.owner_id,
                household_members=body.household_members,
                **attrs,
            )
        )


# ── Riders ──────────────────────────────────────────────────────────────


@router.post("/riders", tags=["Commerce"], response_model=RiderResponse)
@idempotent("riders.create")
async def create_rider(
    body: RiderCreateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    """Registers a real delivery rider — the PERSON entity
    DeliveryCapability/select_delivery_riders() (logistics.py) actually
    assigns orders to. Before this route existed, nothing in production
    ever created one; only a test fixture did, via kg.add_entity()
    directly (found live: /prompt's Delivery step failing with
    "no riders available" against a world built entirely through real
    APIs otherwise)."""
    from src.monkey_brain.kernel.domains.logistics import onboard_rider
    from src.monkey_brain.kernel.security_boundary import privileged_infrastructure

    # capacity=None is RiderCreateRequest's real "unconstrained" default
    # (see its own docstring) — dropped here, not forwarded as a literal
    # None, so select_delivery_riders() sees a genuinely missing key
    # rather than one it would (crash trying to) compare against.
    attrs = {k: v for k, v in body.model_dump().items() if k != "name" and v is not None}
    # Same class of fix as POST /merchants above -- onboard_rider() writes
    # via kg.add_entity(), operator-driven onboarding, not an agent action.
    with privileged_infrastructure(reason="POST /riders: operator onboarding a rider, not an agent action"):
        return _result(onboard_rider(_kg(request), body.name, **attrs))


# ── Wallets ─────────────────────────────────────────────────────────────


@router.post("/wallets", tags=["Commerce"], response_model=WalletResponse)
@idempotent("wallets.create")
async def create_wallet(
    body: WalletCreateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    """A real payment ACCOUNT — the financial identity a customer needs
    before Payment/PaymentConfirmation can charge them (finance.py's
    _find_wallet()/_owned_by() convention: attributes["owner"])."""
    import uuid
    from src.monkey_brain.kernel.knowledge_graph import EntityType
    from src.monkey_brain.kernel.security_boundary import privileged_infrastructure

    name = body.name or f"{body.owner}'s Wallet"
    wallet_id = body.wallet_id or f"wallet_{uuid.uuid4().hex}"
    extra = {
        k: v for k, v in body.model_dump().items() if k not in ("owner", "name", "account_type", "balance", "wallet_id")
    }

    kg = _kg(request)
    # Same class of fix as POST /merchants above -- direct kg.add_entity(),
    # operator-driven wallet provisioning, not an agent action.
    with privileged_infrastructure(reason="POST /wallets: operator provisioning a wallet, not an agent action"):
        kg.add_entity(
            wallet_id,
            EntityType.ACCOUNT,
            name,
            {
                "account_type": body.account_type,
                "balance": body.balance,
                "owner": body.owner,
                **extra,
            },
        )
    entity = kg.get_entity(wallet_id)
    return {"success": True, "wallet_id": wallet_id, **entity.attributes}


@router.get("/wallets/{wallet_id}", tags=["Commerce"])
async def get_wallet(
    wallet_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-world")),
) -> dict[str, Any]:
    """Real, direct read of one wallet/payment account — mirrors GET
    /orders/{order_id}'s exact pattern (same _kg(request).get_entity
    call, same shape). Added because no GET /wallets endpoint existed at
    all (scripts/seed_world.py's own ensure_wallet() docstring already
    documented this as a known gap: "No GET /wallets exists (audited)")
    — the frontend previously had no way to read an actor's real current
    balance except by parsing it back out of past transaction text,
    which only worked for an actor who had actually spent something."""
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    wallet = _kg(request).get_entity(wallet_id)
    if wallet is None or wallet.entity_type != EntityType.ACCOUNT:
        raise HTTPException(status_code=404, detail=f"no such wallet {wallet_id!r}")
    return {"wallet_id": wallet_id, "name": wallet.name, **wallet.attributes}


@router.post("/wallets/{wallet_id}/topup", tags=["Commerce"])
@idempotent("commerce.topup_wallet")
async def topup_wallet(
    wallet_id: str,
    body: dict[str, Any],
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    """Dev/demo-only: adds real money to a wallet with no corresponding
    real-world funding source — an actor's UPI Reserve Pay balance has no
    top-up/deposit flow at all (real gap this closes), so exercising a
    real Razorpay capture end-to-end sometimes needs a demo actor to
    simply have more funds than their seeded starting balance, with no
    real bank/card transfer behind it. Same _cas_adjust_balance CAS
    primitive Payment/refunds already use (kernel/domains/grocery.py) —
    real, contention-safe balance arithmetic, not a raw overwrite;
    perm-manage-actors-gated, same as delete_wallet right below."""
    from src.monkey_brain.kernel.knowledge_graph import EntityType
    from src.monkey_brain.kernel.domains.grocery import _cas_adjust_balance

    try:
        amount = float(body.get("amount"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="body.amount must be a real number")
    if amount <= 0:
        raise HTTPException(
            status_code=400,
            detail="body.amount must be positive — use a real debit/refund flow to reduce a balance",
        )

    kg = _kg(request)
    wallet = kg.get_entity(wallet_id)
    if wallet is None or wallet.entity_type != EntityType.ACCOUNT:
        raise HTTPException(status_code=404, detail=f"no such wallet {wallet_id!r}")
    balance_before = wallet.attributes.get("balance", 0)
    if not _cas_adjust_balance(kg, wallet_id, amount):
        raise HTTPException(
            status_code=409,
            detail="topup failed: too much contention on wallet, please retry",
        )
    balance_after = round(balance_before + amount, 2)
    return {
        "success": True,
        "wallet_id": wallet_id,
        "balance_before": balance_before,
        "balance_after": balance_after,
    }


@router.delete("/wallets/{wallet_id}", tags=["Commerce"])
@idempotent("wallets.delete")
async def delete_wallet(
    wallet_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    """Real removal of a payment account — kg.remove_entity (same primitive
    delete_product already uses), not a soft-delete/retag. Added alongside
    the UPI-Reserve-Pay-only payment model: an actor's old debit/cash
    account is genuinely gone, not just deprioritized, so it can never be
    resolved by _find_wallet/find_payment_sources again."""
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    kg = _kg(request)
    wallet = kg.get_entity(wallet_id)
    if wallet is None or wallet.entity_type != EntityType.ACCOUNT:
        raise HTTPException(status_code=404, detail=f"no such wallet {wallet_id!r}")
    kg.remove_entity(wallet_id)
    return {"success": True, "wallet_id": wallet_id}


@router.get("/actors/{actor_id}/wallet", tags=["Commerce"])
async def get_actor_wallet(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-world")),
) -> dict[str, Any]:
    """Real wallet lookup by owner — every actor gets a real wallet at
    seed time (scripts/seed_world.py::ensure_wallet is called
    unconditionally for every actor), but GET /wallets/{id} above still
    can't find it for an actor who has never made a purchase, since the
    only place a wallet_id was ever learnable from was one of that
    actor's own orders (order.paid_wallet_id). This scans ACCOUNT
    entities for attributes.owner == actor_id instead of requiring the
    id up front — same _kg(request), no new data source."""
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    for entity in _kg(request).entities_by_type(EntityType.ACCOUNT):
        if entity.attributes.get("owner") == actor_id:
            return {
                "wallet_id": entity.entity_id,
                "name": entity.name,
                **entity.attributes,
            }
    raise HTTPException(status_code=404, detail=f"no wallet found for actor {actor_id!r}")


@router.delete("/actors/{actor_id}/orders", tags=["Commerce"])
@idempotent("commerce.delete_actor_orders")
async def delete_actor_orders(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    """Real removal of every order this actor placed (attributes["buyer_id"]
    — the same field finance.py::assess_transaction_risk's velocity check
    reads). A genuine reset of test/demo order history — kg.remove_entity
    per order, not a soft-delete or status change — so a velocity-based
    fraud check (MB-3014) sees a clean window afterward rather than being
    bypassed or special-cased for testing.

    Cascades to each order's Shipment entities (logistics.py::create_shipment)
    too — leaving one behind orphans attributes["order_id"] on a deleted
    order, which world_validator.py's referential_integrity check (correctly)
    flags as shipment_references_missing_order and refuses to execute on."""
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    kg = _kg(request)
    order_ids = [e.entity_id for e in kg.entities_by_type(EntityType.EVENT) if e.attributes.get("buyer_id") == actor_id]
    order_id_set = set(order_ids)
    shipment_ids = [
        e.entity_id
        for e in kg.entities
        if e.attributes.get("shipment") is True and e.attributes.get("order_id") in order_id_set
    ]
    for shipment_id in shipment_ids:
        kg.remove_entity(shipment_id)
    for order_id in order_ids:
        kg.remove_entity(order_id)
    return {
        "success": True,
        "actor_id": actor_id,
        "orders_removed": len(order_ids),
        "order_ids": order_ids,
        "shipments_removed": len(shipment_ids),
        "shipment_ids": shipment_ids,
    }


# ── Products ────────────────────────────────────────────────────────────


@router.post("/products", tags=["Commerce"], response_model=ProductResponse)
@idempotent("products.create")
async def create_product(
    body: ProductCreateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    from src.monkey_brain.kernel.domains.commerce import list_product
    from src.monkey_brain.kernel.security_boundary import privileged_infrastructure

    attrs = {
        k: v for k, v in body.model_dump().items() if k not in ("store_id", "merchant_id", "name", "price", "quantity")
    }
    # Same class of fix as POST /merchants above -- list_product() writes
    # via kg.add_entity(), operator-driven catalog management, not an
    # agent action.
    with privileged_infrastructure(reason="POST /products: operator listing a product, not an agent action"):
        result = _result(
            list_product(
                _kg(request),
                body.store_id,
                body.merchant_id,
                body.name,
                body.price,
                body.quantity,
                **attrs,
            )
        )
    # Product name only — the raw id is still real data in the payload for
    # anything that needs it (e.g. ProductSelection's grounding-membership
    # check), but a human/LLM reading this event's description doesn't
    # need a UUID to know what happened; the name alone is what a person
    # would actually say ("Whole Milk listed"), not "product_83ea... listed."
    _publish_catalog_event(
        request,
        "ProductListed",
        f"{body.name} listed",
        {"product_id": result.get("product_id"), "store_id": body.store_id},
    )
    return result


@router.get("/products", tags=["Commerce"], response_model=BrowseProductsResponse)
async def browse_products(
    request: Request,
    query: str | None = None,
    user_id: str = Depends(require_permission("perm-view-world")),
) -> dict[str, Any]:
    """Browse the whole catalog, or search it by keyword — the same
    open_products()/search_products the domain layer already uses
    (MB-3002), just reachable over HTTP now."""
    from src.monkey_brain.kernel.domains.commerce import (
        CommerceCapability,
        CommerceCapabilityBus,
    )

    kg = _kg(request)
    bus = CommerceCapabilityBus([CommerceCapability()])
    operation = "search_products" if query else "observe_catalog"
    kwargs = {"query": query} if query else {}
    products = bus.invoke(operation, kg, **kwargs)
    return {
        "success": True,
        "products": [
            {
                "id": p.entity_id,
                "name": p.name,
                "price": p.attributes.get("price"),
                "quantity": p.attributes.get("quantity"),
                "store_id": p.attributes.get("store_id"),
            }
            for p in products
        ],
    }


@router.get("/products/{product_id}", tags=["Commerce"])
async def get_product(
    product_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-world")),
) -> dict[str, Any]:
    import dataclasses

    from src.monkey_brain.kernel.domains.commerce import get_product_detail

    detail = get_product_detail(_kg(request), product_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"no such product {product_id!r}")
    return dataclasses.asdict(detail)


@router.patch("/products/{product_id}", tags=["Commerce"])
@idempotent("products.update")
async def update_product_route(
    product_id: str,
    body: ProductUpdateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    from src.monkey_brain.kernel.domains.commerce import update_product

    updates = {k: v for k, v in body.model_dump().items() if k != "merchant_id"}
    product_name = updates.get("name") or getattr(_kg(request).get_entity(product_id), "name", None) or product_id
    result = _result(update_product(_kg(request), product_id, body.merchant_id, **updates))
    _publish_catalog_event(
        request,
        "ProductUpdated",
        f"{product_name} updated",
        {"product_id": product_id, "updates": updates},
    )
    return result


@router.delete("/products/{product_id}", tags=["Commerce"])
@idempotent("products.delete")
async def delete_product(
    product_id: str,
    request: Request,
    merchant_id: str,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    from src.monkey_brain.kernel.domains.commerce import remove_product

    product_name = getattr(_kg(request).get_entity(product_id), "name", None) or product_id
    result = _result(remove_product(_kg(request), product_id, merchant_id))
    _publish_catalog_event(
        request,
        "ProductDelisted",
        f"{product_name} delisted",
        {"product_id": product_id},
    )
    return result


@router.post("/products/{product_id}/promotions", tags=["Commerce"])
@idempotent("products.promotion")
async def create_promotion_route(
    product_id: str,
    body: PromotionCreateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    from src.monkey_brain.kernel.domains.commerce import create_promotion

    return _result(
        create_promotion(
            _kg(request),
            product_id,
            body.merchant_id,
            body.sale_price,
            starts_at=body.starts_at,
            ends_at=body.ends_at,
        )
    )


@router.post("/products/{product_id}/reviews", tags=["Commerce"])
@idempotent("products.review")
async def create_review(
    product_id: str,
    body: ReviewCreateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    from src.monkey_brain.kernel.domains.commerce import leave_review

    return _result(leave_review(_kg(request), product_id, body.actor_id, body.rating, body.comment))


# ── Cart ────────────────────────────────────────────────────────────────


@router.post("/cart/{actor_id}/items", tags=["Commerce"], response_model=CartResponse)
@idempotent("cart.add_item")
async def add_cart_item(
    actor_id: str,
    body: CartItemAddRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    import dataclasses

    from src.monkey_brain.kernel.domains.commerce import add_to_cart

    cart = add_to_cart(_kg(request), actor_id, body.product_id, body.quantity)
    if cart is None:
        raise HTTPException(status_code=404, detail=f"no such product {body.product_id!r}")
    return dataclasses.asdict(cart)


@router.get("/cart/{actor_id}", tags=["Commerce"], response_model=CartResponse)
async def get_cart_route(
    actor_id: str,
    request: Request,
    user_id: str = Depends(require_permission("perm-view-world")),
) -> dict[str, Any]:
    import dataclasses

    from src.monkey_brain.kernel.domains.commerce import get_cart

    return dataclasses.asdict(get_cart(_kg(request), actor_id))


@router.post("/cart/{actor_id}/coupon", tags=["Commerce"], response_model=CartResponse)
@idempotent("cart.coupon")
async def apply_coupon_route(
    actor_id: str,
    body: CouponApplyRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    import dataclasses

    from src.monkey_brain.kernel.domains.commerce import apply_coupon_to_cart

    result = apply_coupon_to_cart(_kg(request), actor_id, body.code, body.store_id)
    return dataclasses.asdict(result)


# ── Inventory ───────────────────────────────────────────────────────────


@router.post("/inventory/reserve", tags=["Commerce"], response_model=InventoryActionResponse)
@idempotent("inventory.reserve")
async def reserve_inventory(
    body: InventoryReserveRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    from src.monkey_brain.kernel.domains.grocery import try_reserve

    # Doot audit secondary-bypass fix (same shape as BYPASS-02/orders.py):
    # body.actor_id used to be trusted outright behind only
    # perm-manage-actors, with no self-or-behalf check.
    await authorize_acting_for(request, user_id, body.actor_id)
    ok, message = try_reserve(
        _kg(request),
        body.product_id,
        body.actor_id,
        body.qty,
        hold_seconds=body.hold_seconds,
    )
    if not ok:
        raise HTTPException(status_code=409, detail=message)
    return {"success": True, "message": message}


@router.post("/inventory/confirm", tags=["Commerce"], response_model=InventoryActionResponse)
@idempotent("inventory.confirm")
async def confirm_inventory(
    body: InventoryConfirmRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    from src.monkey_brain.kernel.domains.grocery import confirm_reservation

    await authorize_acting_for(request, user_id, body.actor_id)
    ok, message = confirm_reservation(_kg(request), body.product_id, body.actor_id)
    if not ok:
        raise HTTPException(status_code=409, detail=message)
    return {"success": True, "message": message}


@router.post("/inventory/backorder", tags=["Commerce"], response_model=InventoryActionResponse)
@idempotent("inventory.backorder")
async def backorder_inventory(
    body: InventoryBackorderRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    from src.monkey_brain.kernel.domains.grocery import place_backorder

    return {
        "success": True,
        **place_backorder(_kg(request), body.product_id, body.actor_id, body.qty),
    }


@router.post(
    "/inventory/fulfill-backorders",
    tags=["Commerce"],
    response_model=InventoryActionResponse,
)
@idempotent("inventory.fulfill_backorders")
async def fulfill_backorders_route(
    body: InventoryFulfillBackordersRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-manage-actors")),
) -> dict[str, Any]:
    from src.monkey_brain.kernel.domains.grocery import fulfill_backorders

    fulfilled = fulfill_backorders(_kg(request), body.product_id)
    return {"success": True, "fulfilled": fulfilled}
