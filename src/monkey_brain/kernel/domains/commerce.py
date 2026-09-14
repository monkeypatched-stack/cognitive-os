"""Commerce-domain primitives shared by verticals.

Commerce owns the capability-bus contract used to assemble a vertical's
buying and selling workflow.  The Grocery vertical supplies the grocery
capabilities (product matching, recipes, household sourcing, and the
grocery-specific checkout policy), while this module keeps the orchestration
container reusable for retail, ecommerce, and other commerce verticals.

The bus deliberately has no knowledge of grocery names or entities.  A
capability is any object exposing a ``name`` attribute and a ``handle``
method; this is the same small contract consumed by ``ActionExecutor``.
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

CapabilityHandler = Callable[..., Any]


class DomainCapability:
    """Small first-class capability contract used by the capability bus.

    A capability exposes named operations, rather than requiring the planner
    to know which vertical module contains the implementation.  Domain
    capabilities may be composed into a vertical bundle and discovered by
    name at runtime.
    """

    name = "capability"

    def __init__(self, handlers: Mapping[str, CapabilityHandler] | None = None):
        self._handlers: dict[str, CapabilityHandler] = dict(handlers or {})

    def operations(self) -> tuple[str, ...]:
        return tuple(self._handlers)

    def can_handle(self, operation: str) -> bool:
        return operation in self._handlers

    def invoke(self, operation: str, *args: Any, **kwargs: Any) -> Any:
        try:
            handler = self._handlers[operation]
        except KeyError as exc:
            raise KeyError(
                f"{self.name} does not provide operation {operation!r}"
            ) from exc
        return handler(*args, **kwargs)


@dataclass(frozen=True)
class ProductReview:
    """One customer review of a product — a normalized view over one
    attributes["reviews"] entry (MB-3005 Product Detail)."""

    reviewer: str = ""
    rating: float = 0.0
    comment: str = ""

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ProductReview":
        return cls(
            reviewer=str(d.get("reviewer", "")),
            rating=float(d.get("rating", 0.0) or 0.0),
            comment=str(d.get("comment", "")),
        )


@dataclass(frozen=True)
class ProductVariant:
    """One alternative option for a product — a different size, type, or
    similar — referencing another catalog entity (MB-3005 Product
    Detail). Distinct from grocery.py's informal use of "variant" to mean
    a substitution alternative (2% -> whole -> oat milk): this is a
    structured reference get_product_detail() resolves against the same
    KnowledgeGraph, not prose in a docstring."""

    entity_id: str = ""
    label: str = ""


def get_effective_price(kg: Any, product_id: str, now: float | None = None) -> float:
    """MB-3034 Promotion: the single source of truth for what a product
    ACTUALLY costs right now — a product's attributes["price"] alone
    ignores an active time-limited promotion (create_promotion());
    every price-reading call site (get_product_detail(), add_to_cart())
    calls this instead, so a sale is never visible in one place and not
    the other. Falls back to the regular price when the product doesn't
    exist, has no promotion, or the promotion hasn't started yet /
    already ended (an open-ended promotion — ends_at is None — never
    expires on its own).
    """
    product = kg.get_entity(product_id)
    if product is None:
        return 0.0
    regular_price = float(product.attributes.get("price", 0.0) or 0.0)
    promo = product.attributes.get("promotion")
    if not promo:
        return regular_price

    now = now if now is not None else time.time()
    starts_at = promo.get("starts_at", 0)
    ends_at = promo.get("ends_at")
    if now < starts_at or (ends_at is not None and now >= ends_at):
        return regular_price
    return float(promo.get("sale_price", regular_price))


def create_promotion(
    kg: Any,
    product_id: str,
    merchant_id: str,
    sale_price: float,
    starts_at: float | None = None,
    ends_at: float | None = None,
    now: float | None = None,
) -> dict:
    """MB-3034 Promotion: a merchant runs a time-limited sale on their
    own product — a real discount, automatically applied while it's
    running, with no code the customer has to enter (the opposite of
    MB-3009's coupons, which are opt-in and code-gated). Refuses unless
    merchant_id owns the product's store (require_store_owner(), the
    same rule every other merchant-facing write uses), and refuses a
    sale_price that isn't genuinely a discount (must be non-negative and
    strictly less than the regular price — a "sale" that doesn't
    actually lower the price is a contradiction, not a valid promotion).

    starts_at defaults to now (the sale is live immediately); ends_at
    left unset means open-ended (runs until a merchant changes it) —
    get_effective_price() is what actually applies the time window.
    """
    now = now if now is not None else time.time()
    product = kg.get_entity(product_id)
    if product is None:
        return {"success": False, "error": f"no such product {product_id!r}"}
    store_id = product.attributes.get("store_id")
    if (denied := require_store_owner(kg, store_id, merchant_id)) is not None:
        return denied

    regular_price = float(product.attributes.get("price", 0.0) or 0.0)
    if sale_price < 0:
        return {
            "success": False,
            "error": f"sale price must be non-negative, got {sale_price!r}",
        }
    if sale_price >= regular_price:
        return {
            "success": False,
            "error": f"sale price {sale_price} must be less than the regular price {regular_price}",
        }
    starts_at = starts_at if starts_at is not None else now
    if ends_at is not None and ends_at <= starts_at:
        return {"success": False, "error": "ends_at must be after starts_at"}

    kg.update_entity(
        product_id,
        attributes={
            "promotion": {
                "sale_price": sale_price,
                "starts_at": starts_at,
                "ends_at": ends_at,
                "created_by": merchant_id,
            }
        },
    )
    return {
        "success": True,
        "product_id": product_id,
        "sale_price": sale_price,
        "regular_price": regular_price,
        "starts_at": starts_at,
        "ends_at": ends_at,
    }


@dataclass(frozen=True)
class ProductDetail:
    """ "Open product" (MB-3005): inventory, images, reviews, and variants
    assembled from one product's catalog entity."""

    product_id: str
    name: str = ""
    price: float = 0.0
    inventory: int = 0
    images: tuple[str, ...] = ()
    reviews: tuple[ProductReview, ...] = ()
    average_rating: float | None = None
    """None (not 0.0) when there are no reviews, so a caller can tell
    "no reviews yet" apart from "reviews exist and average to zero"."""
    variants: tuple[ProductVariant, ...] = ()
    on_sale: bool = False
    """MB-3034: True when price reflects an active promotion rather
    than the regular price."""
    regular_price: float | None = None
    """MB-3034: the pre-promotion price — None (not price itself) when
    there's no active sale, so a caller can tell "not on sale" apart
    from "on sale at the same price it always was"."""


def get_product_detail(kg: Any, product_id: str) -> ProductDetail | None:
    """Assemble one product's full detail view. Returns None if
    product_id isn't a real product (ASSET entity) in kg — mirrors
    open_products()'s own "only real ASSET entities" contract.

    - inventory: attributes["quantity"] — the SAME field open_products()/
      try_reserve() already treat as on-hand stock, so a product's
      inventory here always matches what checkout actually sees; this
      function does not introduce a second, parallel stock number.
    - images: attributes["images"], a list of URLs. () if none set —
      most products have none today, since no seed data populates this
      yet.
    - reviews: attributes["reviews"] (a list of {"reviewer", "rating",
      "comment"} dicts), normalized into typed ProductReview entries.
    - variants: attributes["variants"] (a list of {"entity_id", "label"}
      dicts), each resolved against kg. An entity_id that no longer
      exists in the catalog is silently dropped rather than raising —
      a variant disappearing from the catalog isn't THIS product's own
      data error.
    - price: get_effective_price() (MB-3034) — the sale price while a
      promotion is actively running, the regular price otherwise.
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    entity = kg.get_entity(product_id)
    if entity is None or entity.entity_type != EntityType.ASSET:
        return None

    attrs = entity.attributes
    reviews = tuple(ProductReview.from_dict(r) for r in (attrs.get("reviews") or ()))
    average_rating = (
        (sum(r.rating for r in reviews) / len(reviews)) if reviews else None
    )

    variants = tuple(
        ProductVariant(entity_id=v["entity_id"], label=str(v.get("label", "")))
        for v in (attrs.get("variants") or ())
        if isinstance(v, Mapping)
        and v.get("entity_id")
        and kg.get_entity(v["entity_id"]) is not None
    )

    regular_price = float(attrs.get("price", 0.0) or 0.0)
    effective_price = get_effective_price(kg, product_id)
    on_sale = effective_price < regular_price

    return ProductDetail(
        product_id=product_id,
        name=entity.name,
        price=effective_price,
        inventory=int(attrs.get("quantity", 0) or 0),
        images=tuple(attrs.get("images") or ()),
        reviews=reviews,
        average_rating=average_rating,
        variants=variants,
        on_sale=on_sale,
        regular_price=regular_price if on_sale else None,
    )


def leave_review(
    kg: Any,
    product_id: str,
    reviewer_id: str,
    rating: float,
    comment: str = "",
    now: float | None = None,
) -> dict:
    """MB-3023 Customer Review: appends a new {"reviewer", "rating",
    "comment"} entry to a product's attributes["reviews"] — the write
    side of get_product_detail()'s existing read side (MB-3005), which
    only ever displayed reviews, never let anyone leave one.

    Per explicit design choice ("verified purchase required"): a review
    requires a real, completed order (kernel/domains/grocery.py::
    OrderCreationCapability's EntityType.EVENT entities, attributes
    ["buyer_id"]/["items"]/["status"] — the SAME order history
    customers_also_bought() already mines, and the SAME "completed"
    terminal status confirm_receipt() (MB-3022) sets) placed by
    reviewer_id that actually contains product_id. Refuses otherwise —
    a review from an actor who never bought (or never actually received)
    the product isn't a genuine one.
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    now = now if now is not None else time.time()
    entity = kg.get_entity(product_id)
    if entity is None or entity.entity_type != EntityType.ASSET:
        return {"success": False, "error": f"no such product {product_id!r}"}

    if not (1.0 <= rating <= 5.0):
        return {
            "success": False,
            "error": f"rating must be between 1 and 5, got {rating!r}",
        }

    verified = any(
        order.attributes.get("buyer_id") == reviewer_id
        and order.attributes.get("status") == "completed"
        and product_id
        in {item.get("product_id") for item in (order.attributes.get("items") or ())}
        for order in kg.entities_by_type(EntityType.EVENT)
    )
    if not verified:
        return {
            "success": False,
            "error": f"no completed order for {product_id!r} found for reviewer {reviewer_id!r} "
            f"— a review requires a verified purchase",
        }

    review = {
        "reviewer": reviewer_id,
        "rating": float(rating),
        "comment": comment,
        "verified_purchase": True,
        "created_at": now,
    }
    reviews = list(entity.attributes.get("reviews") or [])
    reviews.append(review)
    kg.update_entity(product_id, attributes={"reviews": reviews})
    return {
        "success": True,
        "product_id": product_id,
        "review": review,
        "review_count": len(reviews),
    }


@dataclass(frozen=True)
class ProductRecommendation:
    """One "customers also bought" suggestion — a product that
    co-occurred with the target product across other customers' real
    Orders (MB-3006 Recommendation Engine), ranked by how often that
    happened."""

    entity_id: str
    name: str = ""
    co_purchase_count: int = 0


def customers_also_bought(
    kg: Any, product_id: str, limit: int = 5
) -> tuple[ProductRecommendation, ...]:
    """Recommendation workflow: "customers who bought product_id also
    bought..." — mined directly from real Order history (kernel/domains/
    grocery.py::OrderCreationCapability persists every confirmed order as
    an EntityType.EVENT entity with attributes["items"], a list of
    {"product_id": ...} line items), not a separate, parallel
    recommendation dataset that could drift from what people actually
    bought.

    For every Order that includes product_id, every OTHER product_id in
    that same order counts as one co-purchase; results are ranked by
    co-purchase count descending, then by name for a stable tie-break,
    and capped at `limit`. Returns () if product_id was never actually
    ordered alongside anything else — including if it (or any order at
    all) has never existed — which is a valid, honest answer, not an
    error: an empty recommendation list means "no signal yet", exactly
    like get_product_detail() returning empty images/reviews/variants
    for a product that has none.
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    co_purchase_counts: dict[str, int] = {}
    for order in kg.entities_by_type(EntityType.EVENT):
        items = order.attributes.get("items") or ()
        item_ids = {item.get("product_id") for item in items if item.get("product_id")}
        if product_id not in item_ids:
            continue
        for other_id in item_ids:
            if other_id == product_id:
                continue
            co_purchase_counts[other_id] = co_purchase_counts.get(other_id, 0) + 1

    recommendations = [
        ProductRecommendation(
            entity_id=other_id,
            name=(
                kg.get_entity(other_id).name
                if kg.get_entity(other_id) is not None
                else ""
            ),
            co_purchase_count=count,
        )
        for other_id, count in co_purchase_counts.items()
    ]
    recommendations.sort(key=lambda r: (-r.co_purchase_count, r.name))
    return tuple(recommendations[:limit])


@dataclass(frozen=True)
class CartLine:
    """One line item in a customer's cart (MB-3007 Add To Cart)."""

    product_id: str
    name: str = ""
    price: float = 0.0
    quantity: int = 1


@dataclass(frozen=True)
class Cart:
    """A customer's shopping cart. Persisted as one KG entity per actor
    (_cart_entity_id), the same way an Order is persisted (kernel/
    domains/grocery.py::OrderCreationCapability) — "cart updated" means a
    real, durable state change a later request can read back, not an
    in-memory list that disappears between calls."""

    actor_id: str
    lines: tuple[CartLine, ...] = ()
    coupon_code: str = ""
    """The currently-applied coupon (MB-3009 Apply Coupon), if any — set
    by apply_coupon_to_cart() on acceptance, "" if none applied."""
    discount: float = 0.0
    """Amount deducted from subtotal by coupon_code. add_to_cart()
    deliberately does not recompute or clear this when the cart changes
    after a coupon was applied — a flat discount_amount coupon stays the
    same amount regardless of what's added next; re-validating a
    percent-based coupon against a changing subtotal is a real design
    question this scenario doesn't ask for, so it's left as a known
    simplification rather than guessed at."""

    @property
    def item_count(self) -> int:
        return sum(line.quantity for line in self.lines)

    @property
    def subtotal(self) -> float:
        return round(sum(line.price * line.quantity for line in self.lines), 2)

    @property
    def total(self) -> float:
        return round(self.subtotal - self.discount, 2)


def _cart_entity_id(actor_id: str) -> str:
    return f"cart_{actor_id}"


def get_cart(kg: Any, actor_id: str) -> Cart:
    """The actor's current cart. Always returns a Cart — empty (lines=())
    if nothing has been added yet, never None: an empty cart is a real,
    valid state, the same "empty is a valid answer, not an error"
    convention as customers_also_bought()."""
    entity = kg.get_entity(_cart_entity_id(actor_id))
    if entity is None:
        return Cart(actor_id=actor_id)
    lines = tuple(CartLine(**line) for line in entity.attributes.get("lines", ()))
    return Cart(
        actor_id=actor_id,
        lines=lines,
        coupon_code=entity.attributes.get("coupon_code", ""),
        discount=float(entity.attributes.get("discount", 0.0) or 0.0),
    )


def _persist_cart(kg: Any, cart: Cart) -> None:
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    kg.add_entity(
        _cart_entity_id(cart.actor_id),
        EntityType.OTHER,
        f"Cart: {cart.actor_id}",
        {
            "actor_id": cart.actor_id,
            "lines": [dataclasses.asdict(line) for line in cart.lines],
            "coupon_code": cart.coupon_code,
            "discount": cart.discount,
        },
    )


def add_to_cart(
    kg: Any, actor_id: str, product_id: str, quantity: int = 1
) -> Cart | None:
    """Add product_id to actor_id's cart, persist it, and return the
    UPDATED Cart. Returns None (no write performed) if product_id doesn't
    resolve to a real catalog product — mirrors get_product_detail()'s
    own "only real ASSET entities" contract; a product that doesn't exist
    in the catalog can't be added to a cart.

    Adding the same product again increases its quantity rather than
    duplicating the line — a repeat add is "I want more of this", not "a
    second, separate line for the same item". Any coupon already applied
    (Cart.coupon_code/discount) is carried over unchanged — see Cart's
    own docstring for why.

    price: get_effective_price() (MB-3034) — a new line charges the
    active sale price when a promotion is currently running, the same
    price get_product_detail() shows for this product.
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    product = kg.get_entity(product_id)
    if product is None or product.entity_type != EntityType.ASSET:
        return None

    cart = get_cart(kg, actor_id)
    lines = list(cart.lines)
    for i, line in enumerate(lines):
        if line.product_id == product_id:
            lines[i] = dataclasses.replace(line, quantity=line.quantity + quantity)
            break
    else:
        lines.append(
            CartLine(
                product_id=product_id,
                name=product.name,
                price=get_effective_price(kg, product_id),
                quantity=quantity,
            )
        )

    updated = dataclasses.replace(cart, lines=tuple(lines))
    _persist_cart(kg, updated)
    return updated


@dataclass(frozen=True)
class CouponResult:
    """MB-3009 Apply Coupon — accepted or rejected. Wraps kernel/domains/
    grocery.py's existing validate_coupon() (the one place a coupon's
    real/expired/store-scoped status is decided) into a typed shape
    consistent with the rest of this module, and computes what
    acceptance actually means for one cart's subtotal."""

    code: str
    accepted: bool
    reason: str = ""
    """Populated only when accepted is False — why the coupon was
    rejected (expired, forged/nonexistent, wrong store)."""
    fraud_suspected: bool = False
    """True for a code that never matched a real coupon, or one issued
    for a different store — validate_coupon()'s own fraud signal, not
    true for an honestly-expired coupon."""
    discount_amount: float = 0.0
    new_total: float = 0.0


def apply_coupon_to_cart(
    kg: Any, actor_id: str, code: str, store_id: str | None = None
) -> CouponResult:
    """Apply a coupon code to actor_id's current cart. Delegates
    validation entirely to grocery.py's validate_coupon() — this
    function only translates that decision into what it means for THIS
    cart (the actual discount amount, the new total) and, on acceptance,
    persists the applied coupon onto the cart so get_cart() reflects it
    afterward. Rejected: the cart is left completely unchanged."""
    from src.monkey_brain.kernel.domains.grocery import validate_coupon

    cart = get_cart(kg, actor_id)
    validation = validate_coupon(kg, code, store_id or "")

    if not validation["valid"]:
        return CouponResult(
            code=code,
            accepted=False,
            reason=validation.get("reason", ""),
            fraud_suspected=bool(validation.get("fraud_suspected", False)),
            new_total=cart.total,
        )

    discount_amount = validation.get("discount_amount") or 0.0
    discount_percent = validation.get("discount_percent") or 0.0
    if not discount_amount and discount_percent:
        discount_amount = round(cart.subtotal * discount_percent / 100, 2)
    discount_amount = min(discount_amount, cart.subtotal)  # never a negative total

    updated = dataclasses.replace(
        cart, coupon_code=code.upper(), discount=discount_amount
    )
    _persist_cart(kg, updated)

    return CouponResult(
        code=code,
        accepted=True,
        discount_amount=discount_amount,
        new_total=updated.total,
    )


def onboard_merchant(
    kg: Any, merchant_id: str, store_name: str, **store_attrs: Any
) -> dict:
    """MB-3037 Merchant Onboarding: creates a real Store (ORGANIZATION)
    entity owned by merchant_id — closing a real gap: "Merchant" (MB-3001)
    is an ActorType.ENTERPRISE actor in the society/geography system,
    while "Store" (used throughout checkout — DeliveryCapability,
    OrderCreationCapability, open_products) is a completely separate KG
    ORGANIZATION entity; nothing previously linked the two.

    Per explicit design choice ("store gets owner_id"): the store's
    attributes["owner_id"] is the merchant's actor_id — the SAME
    ownership-attribute convention already used elsewhere (finance.py's
    wallet _owned_by, grocery.py's pantry _pantry_owned_by), not a new
    one invented for this. A product is owned transitively through its
    store_id -> store.owner_id (see require_store_owner()), so a single
    ownership check covers every product a merchant lists.

    merchant_id is a plain actor_id string, the same convention every
    other domain-layer function already uses for buyer_id/reviewer_id/
    approved_by — it does not require (or create) a live
    PlanetaryRuntime-registered actor; that's a separate layer this
    function deliberately doesn't reach into, same as every other
    grocery.py/commerce.py capability.
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType
    import uuid

    store_id = f"store_{uuid.uuid4().hex}"
    attributes = {"owner_id": merchant_id, **store_attrs}
    kg.add_entity(store_id, EntityType.ORGANIZATION, store_name, attributes)

    # A real receivable ACCOUNT the store is credited into on every
    # completed sale — see kernel/domains/grocery.py::PaymentCapability's
    # _finalize_successful_payment/_credit_store_accounts. Before this,
    # nothing anywhere in this codebase ever credited the SELLING side of
    # a transaction at all -- Payment only ever debited the buyer; a
    # store had no account to receive into, so a real amount left one
    # side of the ledger and simply vanished. owner=store_id (not
    # merchant_id) since this is the STORE's own receivable, not the
    # merchant-actor's personal wallet — a merchant who owns multiple
    # stores keeps each store's revenue separately trackable.
    store_account_id = f"account_{store_id}"
    kg.add_entity(
        store_account_id,
        EntityType.ACCOUNT,
        f"{store_name} Receivable",
        {
            "owner": store_id,
            "store_id": store_id,
            "account_type": "merchant_receivable",
            "balance": 0.0,
        },
    )
    return {
        "success": True,
        "store_id": store_id,
        "owner_id": merchant_id,
        "name": store_name,
        "store_account_id": store_account_id,
    }


def require_store_owner(kg: Any, store_id: str, merchant_id: str) -> dict | None:
    """Shared authorization check for every merchant-facing store/product
    write (MB-3038/3039/3033/3040): returns None when merchant_id
    genuinely owns store_id, or an honest failure dict otherwise —
    callers do `if (denied := require_store_owner(...)): return denied`.
    Centralized so "who's allowed to touch this store's catalog" is
    answered exactly once, not re-derived slightly differently by each
    listing/update/removal function.
    """
    store = kg.get_entity(store_id)
    if store is None:
        return {"success": False, "error": f"no such store {store_id!r}"}
    if store.attributes.get("owner_id") != merchant_id:
        return {
            "success": False,
            "error": f"actor {merchant_id!r} does not own store {store_id!r}",
        }
    return None


def list_product(
    kg: Any,
    store_id: str,
    merchant_id: str,
    name: str,
    price: float,
    quantity: int = 0,
    **product_attrs: Any,
) -> dict:
    """MB-3038 Product Listing: a merchant lists a new product in their
    own store. Refuses outright unless merchant_id actually owns
    store_id (require_store_owner()) — a merchant can never list a
    product into a store they don't own.

    attributes["product"] = True is set unconditionally — the exact
    marker open_products() requires to treat an ASSET as a real,
    purchasable catalog item (a plain ASSET without it is invisible to
    the whole browse/search/checkout pipeline); a caller can't
    accidentally list an unlisted product by forgetting this flag.
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType
    import uuid

    if (denied := require_store_owner(kg, store_id, merchant_id)) is not None:
        return denied
    if price < 0:
        return {"success": False, "error": f"price must be non-negative, got {price!r}"}

    product_id = f"product_{uuid.uuid4().hex}"
    attributes = {
        "product": True,
        "store_id": store_id,
        "price": price,
        "quantity": quantity,
        **product_attrs,
    }
    kg.add_entity(product_id, EntityType.ASSET, name, attributes)
    return {
        "success": True,
        "product_id": product_id,
        "store_id": store_id,
        "name": name,
        "price": price,
    }


def update_product(kg: Any, product_id: str, merchant_id: str, **updates: Any) -> dict:
    """MB-3039 Product Update: a merchant edits their own listing —
    any attribute (name, price, description, images, category, ...).
    Refuses unless merchant_id owns the store the product actually
    belongs to (require_store_owner() against the product's own
    store_id, the same transitive-ownership rule list_product() writes
    at creation time) — a merchant can never edit another merchant's
    listing.

    "name" updates the entity's own name field (Entity.update handles
    that distinctly from attributes); everything else in **updates
    merges into attributes, so a partial update (e.g. price=9.99 alone)
    never touches fields the caller didn't mention.
    """
    product = kg.get_entity(product_id)
    if product is None:
        return {"success": False, "error": f"no such product {product_id!r}"}
    store_id = product.attributes.get("store_id")
    if (denied := require_store_owner(kg, store_id, merchant_id)) is not None:
        return denied

    if "price" in updates and updates["price"] < 0:
        return {
            "success": False,
            "error": f"price must be non-negative, got {updates['price']!r}",
        }

    name = updates.pop("name", None)
    kwargs: dict[str, Any] = {"attributes": updates} if updates else {}
    if name is not None:
        kwargs["name"] = name
    if kwargs:
        kg.update_entity(product_id, **kwargs)
    return {
        "success": True,
        "product_id": product_id,
        "updated": {**({"name": name} if name is not None else {}), **updates},
    }


def update_organization(kg: Any, org_id: str, **updates: Any) -> dict:
    """Admin-only edit (or creation) of any ORGANIZATION entity — a
    store, warehouse, supplier, factory, truck, bank, or payment
    processor. Unlike update_product(), there is no merchant-ownership
    check: warehouses/suppliers/factories/trucks aren't merchant-owned
    at all (only stores are, via onboard_merchant's owner_id), so this
    is gated purely by the route's own perm-manage-actors permission,
    the same privileged, demo/admin-tooling level as onboard_merchant
    itself.

    Exists specifically to let a real disruption (a warehouse fire: set
    a warehouse's own "status" to anything other than "operational") be
    injected live, over HTTP, against the same real commerce KG
    open_products()/supply_chain_ok() actually read — there was
    previously no route that could mutate an organization entity at
    all. Auto-creates org_id when it doesn't exist yet (same overwrite/
    create-on-reuse convention KnowledgeGraph.add_entity already has) —
    onboard_merchant only ever creates a Store; a warehouse/truck/
    supplier/factory a demo wants to set up first has no dedicated
    creation route of its own, and doesn't need one just for this.
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    org = kg.get_entity(org_id)
    name = updates.pop("name", None)
    if org is None:
        kg.add_entity(org_id, EntityType.ORGANIZATION, name or org_id, updates)
    else:
        kwargs: dict[str, Any] = {"attributes": updates} if updates else {}
        if name is not None:
            kwargs["name"] = name
        if kwargs:
            kg.update_entity(org_id, **kwargs)
    return {
        "success": True,
        "org_id": org_id,
        "updated": {**({"name": name} if name is not None else {}), **updates},
    }


def remove_product(kg: Any, product_id: str, merchant_id: str) -> dict:
    """MB-3040 Product Removal: delists a merchant's own product — a
    SOFT delete (attributes["product"] set to False), same pattern
    open_products() already uses for a closed store (attributes
    ["is_open"] = False): excluded from the catalog going forward
    (open_products() requires attributes["product"] to be truthy),
    without erasing the entity itself — existing orders/reviews that
    reference this product_id (customers_also_bought(), leave_review(),
    order line items) keep resolving against real data instead of a
    dangling reference. Refuses unless merchant_id owns the product's
    store, same rule as update_product().
    """
    product = kg.get_entity(product_id)
    if product is None:
        return {"success": False, "error": f"no such product {product_id!r}"}
    if product.attributes.get("product") is False:
        return {"success": False, "error": f"product {product_id!r} is already removed"}
    store_id = product.attributes.get("store_id")
    if (denied := require_store_owner(kg, store_id, merchant_id)) is not None:
        return denied

    kg.update_entity(
        product_id,
        attributes={
            "product": False,
            "removed_at": time.time(),
            "removed_by": merchant_id,
        },
    )
    return {"success": True, "product_id": product_id}


_LOW_STOCK_THRESHOLD = 5
"""MB-3050: a listed product at or below this quantity (but not zero)
counts as low stock."""


def get_store_analytics(kg: Any, store_id: str, merchant_id: str) -> dict:
    """MB-3050 Analytics: a real-time sales/inventory/customers snapshot
    for a merchant's own store — computed fresh from actual KG state on
    every call, the same "always current, never a stale cached number"
    principle trace_supply_chain()/get_product_detail() already follow,
    rather than a separately maintained dashboard total that could
    drift from what's really in the graph. Refuses unless merchant_id
    owns store_id (require_store_owner()), same rule as every other
    merchant-facing operation.

    - sales: revenue and order count from every real, PAID order
      (payment_status == "paid" — the same signal cancel_order()/
      approve_return() already trust) containing at least one of this
      store's products.
    - inventory: total stock, out-of-stock count, and low-stock count
      (0 < quantity <= _LOW_STOCK_THRESHOLD), scoped to this store's
      currently LISTED products (attributes["product"] is True — a
      removed listing, MB-3040, doesn't count toward current
      inventory).
    - customers: distinct buyer_id count and repeat-customer count
      (more than one order) among this store's real orders — mined the
      same way customers_also_bought() mines co-purchases, not a
      separate, parallel customer dataset.
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    if (denied := require_store_owner(kg, store_id, merchant_id)) is not None:
        return denied

    store_product_ids = {
        e.entity_id
        for e in kg.entities_by_type(EntityType.ASSET)
        if e.attributes.get("store_id") == store_id
    }
    listed_products = [
        e
        for e in kg.entities_by_type(EntityType.ASSET)
        if e.attributes.get("store_id") == store_id
        and e.attributes.get("product") is True
    ]

    total_stock = sum(p.attributes.get("quantity", 0) for p in listed_products)
    out_of_stock_count = sum(
        1 for p in listed_products if p.attributes.get("quantity", 0) == 0
    )
    low_stock_count = sum(
        1
        for p in listed_products
        if 0 < p.attributes.get("quantity", 0) <= _LOW_STOCK_THRESHOLD
    )

    store_orders = []
    for order in kg.entities_by_type(EntityType.EVENT):
        if order.attributes.get("payment_status") != "paid":
            continue
        item_ids = {
            item.get("product_id")
            for item in (order.attributes.get("items") or ())
            if item.get("product_id")
        }
        if item_ids & store_product_ids:
            store_orders.append(order)

    total_revenue = round(
        sum(order.attributes.get("total", 0) for order in store_orders), 2
    )

    buyer_order_counts: dict[str, int] = {}
    for order in store_orders:
        buyer_id = order.attributes.get("buyer_id")
        if buyer_id:
            buyer_order_counts[buyer_id] = buyer_order_counts.get(buyer_id, 0) + 1

    return {
        "success": True,
        "store_id": store_id,
        "sales": {
            "total_revenue": total_revenue,
            "order_count": len(store_orders),
        },
        "inventory": {
            "listed_product_count": len(listed_products),
            "total_stock": total_stock,
            "out_of_stock_count": out_of_stock_count,
            "low_stock_count": low_stock_count,
        },
        "customers": {
            "customer_count": len(buyer_order_counts),
            "repeat_customer_count": sum(
                1 for count in buyer_order_counts.values() if count > 1
            ),
        },
    }


class CommerceCapability(DomainCapability):
    """Reusable commerce competency for catalog and inventory workflows.

    The default handlers are resolved lazily from the existing commerce
    mechanics.  This keeps the migration compatible with Grocery while
    allowing Retail, Marketplace, and future verticals to discover the same
    operations without importing ``grocery.py`` directly.
    """

    name = "commerce"

    _DEFAULT_OPERATIONS = (
        "observe_catalog",
        "search_products",
        "reserve_inventory",
        "confirm_reservation",
        "cancel_order",
        "return_order",
        "refund_order",
        "place_backorder",
        "fulfill_backorders",
    )
    _NATIVE_OPERATIONS = (
        "open_product",
        "customers_also_bought",
        "add_to_cart",
        "get_cart",
        "apply_coupon_to_cart",
        "leave_review",
        "onboard_merchant",
        "list_product",
        "update_product",
        "remove_product",
        "create_promotion",
        "get_effective_price",
        "get_store_analytics",
    )
    """Operations implemented directly in this module, not delegated to
    grocery.py — see _legacy_handler vs get_product_detail()/
    customers_also_bought()/add_to_cart()/get_cart()/
    apply_coupon_to_cart() (which itself delegates coupon VALIDATION,
    but not cart application, to grocery.py::validate_coupon)."""

    def __init__(self, handlers: Mapping[str, CapabilityHandler] | None = None):
        if handlers is None:
            handlers = {
                operation: self._legacy_handler(operation)
                for operation in self._DEFAULT_OPERATIONS
            }
            handlers["open_product"] = get_product_detail
            handlers["add_to_cart"] = add_to_cart
            handlers["get_cart"] = get_cart
            handlers["customers_also_bought"] = customers_also_bought
            handlers["apply_coupon_to_cart"] = apply_coupon_to_cart
            handlers["leave_review"] = leave_review
            handlers["onboard_merchant"] = onboard_merchant
            handlers["list_product"] = list_product
            handlers["update_product"] = update_product
            handlers["remove_product"] = remove_product
            handlers["create_promotion"] = create_promotion
            handlers["get_effective_price"] = get_effective_price
            handlers["get_store_analytics"] = get_store_analytics
        super().__init__(handlers)

    @staticmethod
    def _legacy_handler(operation: str) -> CapabilityHandler:
        def handler(*args: Any, **kwargs: Any) -> Any:
            from src.monkey_brain.kernel.domains import grocery

            if operation in {"observe_catalog", "search_products"}:
                query = kwargs.pop("query", None) if kwargs else None
                products = grocery.open_products(*args, **kwargs)
                if operation == "search_products" and query:
                    query_words = set(str(query).lower().split())
                    products = [
                        p for p in products if query_words & set(p.name.lower().split())
                    ]
                return products
            if operation == "reserve_inventory":
                return grocery.try_reserve(*args, **kwargs)
            if operation == "confirm_reservation":
                return grocery.confirm_reservation(*args, **kwargs)
            if operation == "place_backorder":
                return grocery.place_backorder(*args, **kwargs)
            if operation == "fulfill_backorders":
                return grocery.fulfill_backorders(*args, **kwargs)
            if operation == "return_order":
                return grocery.return_order(*args, **kwargs)
            if operation == "refund_order":
                return grocery.refund_order(*args, **kwargs)
            raise NotImplementedError(
                f"{operation} has no vertical-neutral legacy adapter yet"
            )

        return handler


class CommerceCapabilityBus:
    """Registry for commerce capabilities.

    Capability names are explicit and deterministic.  Registering a second
    capability with the same name replaces the previous implementation,
    which lets a vertical override one step without changing the executor.
    """

    def __init__(self, capabilities: Iterable[Any] | None = None):
        self._capabilities: dict[str, Any] = {}
        if capabilities:
            for capability in capabilities:
                self.register(capability)

    def register(self, capability: Any) -> None:
        name = getattr(capability, "name", capability.__class__.__name__)
        self._capabilities[name] = capability

    def discover(self, name: str) -> Any | None:
        """Return the named capability, as required by ``ActionExecutor``."""
        return self._capabilities.get(name)

    def names(self) -> tuple[str, ...]:
        """Return registered names in registration order."""
        return tuple(self._capabilities)

    def discover_operation(self, operation: str) -> tuple[Any, str] | None:
        """Find the first registered capability that provides ``operation``."""
        for capability in self._capabilities.values():
            if getattr(capability, "can_handle", lambda _name: False)(operation):
                return capability, operation
        return None

    def invoke(self, operation: str, *args: Any, **kwargs: Any) -> Any:
        """Dispatch an operation without coupling callers to a domain name."""
        found = self.discover_operation(operation)
        if found is None:
            raise KeyError(f"no registered capability handles {operation!r}")
        capability, operation_name = found
        return capability.invoke(operation_name, *args, **kwargs)


@dataclass(frozen=True)
class VerticalDefinition:
    """Declarative capability requirements for a vertical agent."""

    name: str
    requires: tuple[str, ...]


class VerticalCapabilityBundle:
    """Resolved capability composition for a declarative vertical."""

    def __init__(self, definition: VerticalDefinition, bus: CommerceCapabilityBus):
        self.definition = definition
        self.bus = bus

    @property
    def capabilities(self) -> dict[str, Any]:
        return {
            name: self.bus.discover(name)
            for name in self.definition.requires
            if self.bus.discover(name) is not None
        }

    def missing(self) -> tuple[str, ...]:
        return tuple(
            name for name in self.definition.requires if self.bus.discover(name) is None
        )

    def validate(self) -> None:
        missing = self.missing()
        if missing:
            raise RuntimeError(
                f"{self.definition.name} is missing capabilities: {', '.join(missing)}"
            )


# These are the commerce mechanics that historically lived in grocery.py.
# They are exposed lazily to keep the existing vertical implementation
# import-safe while callers migrate to the shared domain module.  Lazy
# resolution is important here: grocery.py imports CommerceCapabilityBus,
# so an eager import would create a circular dependency during startup.
_LEGACY_COMMERCE_EXPORTS = {
    "open_products",
    "try_reserve",
    "confirm_reservation",
    "allocate_fair_share",
    "allocate_by_priority",
    "allocate_ethically",
    "pool_bulk_order",
    "resolve_store_with_fallback",
    "resume_partial_checkout",
    "record_order_outcome",
    "record_order_outcome_sharded",
    "aggregate_trust",
    "has_learned_to_avoid",
    "detect_inventory_inconsistency",
    "is_suspicious_new_seller",
}


def __getattr__(name: str) -> Any:
    """Resolve legacy commerce mechanics without an import cycle.

    The functions remain behaviorally identical during this extraction; the
    adapter gives new verticals a stable shared-domain import while Grocery
    can be migrated incrementally.
    """
    if name in _LEGACY_COMMERCE_EXPORTS:
        from src.monkey_brain.kernel.domains import grocery

        return getattr(grocery, name)
    raise AttributeError(name)


__all__ = [
    "CapabilityHandler",
    "DomainCapability",
    "CommerceCapability",
    "CommerceCapabilityBus",
    "VerticalDefinition",
    "VerticalCapabilityBundle",
    "ProductReview",
    "ProductVariant",
    "ProductDetail",
    "get_product_detail",
    "leave_review",
    "ProductRecommendation",
    "customers_also_bought",
    "CartLine",
    "Cart",
    "get_cart",
    "add_to_cart",
    "CouponResult",
    "apply_coupon_to_cart",
    "onboard_merchant",
    "require_store_owner",
    "list_product",
    "update_product",
    "remove_product",
    "get_effective_price",
    "create_promotion",
    "get_store_analytics",
    *sorted(_LEGACY_COMMERCE_EXPORTS),
]
