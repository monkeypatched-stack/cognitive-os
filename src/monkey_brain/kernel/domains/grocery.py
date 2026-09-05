"""Grocery vertical — grocery-specific commerce capabilities.

The vertical composes the shared commerce, finance, logistics, supply-chain,
and domain-security primitives with grocery-specific product matching and
household behavior.  Each capability queries the actor's KnowledgeGraph;
there is no hardcoded catalog or fulfillment state here.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from src.monkey_brain.kernel.domains.commerce import (
    CommerceCapability,
    CommerceCapabilityBus,
    DomainCapability,
    VerticalDefinition,
    VerticalCapabilityBundle,
)
from src.monkey_brain.kernel.compile import _obs
from src.monkey_brain.common.correlation import new_correlation_id
from src.monkey_brain.kernel.society.transition_gate import ProposedTransition


GROCERY_VERTICAL = VerticalDefinition(
    name="Grocery",
    requires=("commerce", "grocery", "finance", "logistics", "supply_chain"),
)


class GroceryCapability(DomainCapability):
    """Small grocery-specific competency layered on shared commerce."""

    name = "grocery"

    def __init__(self):
        super().__init__({
            "substitution_rules": _matches_request,
            "nutrition": self._nutrition,
            "perishable_handling": self._perishable_handling,
        })

    @staticmethod
    def _nutrition(*args, **kwargs):
        return NutritionCapability().handle({"context": kwargs.get("context", {})})

    @staticmethod
    def _perishable_handling(product):
        return {"cold_chain": bool(getattr(product, "attributes", {}).get("cold_chain"))}


def grocery_capability_bundle(bus: CommerceCapabilityBus | None = None) -> VerticalCapabilityBundle:
    """Build the Grocery vertical from discoverable domain capabilities."""
    capability_bus = bus or CommerceCapabilityBus()
    if capability_bus.discover("commerce") is None:
        capability_bus.register(CommerceCapability())
    if capability_bus.discover("grocery") is None:
        capability_bus.register(GroceryCapability())
    if capability_bus.discover("finance") is None:
        from src.monkey_brain.kernel.domains.finance import FinanceCapability
        capability_bus.register(FinanceCapability())
    if capability_bus.discover("logistics") is None:
        from src.monkey_brain.kernel.domains.logistics import LogisticsCapability
        capability_bus.register(LogisticsCapability())
    if capability_bus.discover("supply_chain") is None:
        from src.monkey_brain.kernel.domains.supply_chain import SupplyChainCapability
        capability_bus.register(SupplyChainCapability())
    if capability_bus.discover("support") is None:
        from src.monkey_brain.kernel.domains.support import SupportCapability
        capability_bus.register(SupportCapability())
    return VerticalCapabilityBundle(GROCERY_VERTICAL, capability_bus)


def build_default_capability_bus() -> "GroceryCapabilityBus":
    """Assemble the Grocery vertical's full capability bus.

    Registers every step-level capability the ActionExecutor invokes
    directly, then layers the domain-competency bundle (commerce/grocery/
    finance/logistics/supply_chain) on top via `grocery_capability_bundle`
    and validates it. The platform's API route should only ever need this
    one call — it has no business importing each capability class by name
    to assemble a vertical-specific bus itself.
    """
    bus = GroceryCapabilityBus()
    bus.register(DelegationCheckCapability())
    bus.register(RecipeExpansionCapability())
    bus.register(SocietyQueryCapability())
    bus.register(CounterfactualCapability())
    bus.register(ExplainCapability())
    bus.register(NutritionCapability())
    bus.register(BeliefCheckCapability())
    bus.register(HouseholdCognitionCapability())
    bus.register(SocialSourcingCapability())
    bus.register(ProductSelectionCapability())
    bus.register(OrderConfirmationCapability())
    bus.register(OrderCreationCapability())
    bus.register(InventoryReserveCapability())
    bus.register(InventoryReleaseCapability())
    bus.register(LoyaltyAwardCapability())
    bus.register(AnswerQuestionCapability())
    bus.register(AskActorCapability())
    bus.register(DelegateTaskCapability())
    bus.register(BroadcastToAffiliationCapability())
    bus.register(RespondToInquiryCapability())
    bus.register(EvaluateStrategyCapability())
    bus.register(CompeteForResourceCapability())
    bus.register(RecordAgreementCapability())
    bus.register(GetAgreementsCapability())
    bus.register(ReportWorldPerturbationCapability())
    bus.register(PrepareOrderCapability())
    bus.register(AcceptDeliveryCapability())
    bus.register(NegotiatePriceCapability())
    bus.register(NegotiateTermsCapability())
    bus.register(PaymentConfirmationCapability())
    bus.register(PaymentCapability())
    bus.register(CancelOrderCapability())
    bus.register(ReturnOrderCapability())
    bus.register(ApproveReturnCapability())
    bus.register(RefundOrderCapability())
    bus.register(DeliveryCapability())
    from src.monkey_brain.kernel.domains.recall import RecallCapability
    bus.register(RecallCapability())
    grocery_capability_bundle(bus).validate()
    return bus


def project_action_result_to_context(result: dict, context: dict) -> None:
    """Project a grocery capability's result keys onto the execution context.

    Moved out of ActionExecutor (kernel/pipeline/action_executor.py) — a
    domain-agnostic executor has no business knowing that "selected"
    should become "selected_product" or that an "order_id" means the whole
    result becomes context["order"]. That mapping is grocery-specific
    knowledge, so it lives here and is injected into ActionExecutor via its
    optional context_projector parameter.
    """
    if "stores" in result or "products" in result:
        context["stores"] = result.get("stores", context.get("stores", []))
        context["products"] = result.get("products", context.get("products", []))
    if "selected" in result:
        # A multi-item request ("buy milk and pizza") plans one
        # ProductSelection step per item — a plain overwrite here left only
        # the LAST item's selection in context by the time OrderCreation
        # ran, silently dropping every earlier item from the order. Each
        # step's selection is appended onto the same cart instead.
        new_items = result["selected"]
        if not isinstance(new_items, list):
            new_items = [new_items]
        existing = context.get("selected_product") or []
        if isinstance(existing, dict):
            existing = [existing]
        context["selected_product"] = existing + new_items
    if "order_id" in result:
        context["order"] = result
        context["total"] = result.get("total", 0)
    if result.get("agreed") and result.get("product_id"):
        # NegotiatePriceCapability's real, bounded deal becomes an
        # override OrderCreationCapability applies to that specific
        # item's price — see OrderCreationCapability's own comment for
        # where this is read. Gated on product_id (planner-supplied,
        # optional) so an unlinked negotiation still just returns its
        # deal without silently affecting an unrelated cart item.
        negotiated = context.setdefault("negotiated_prices", {})
        negotiated[result["product_id"]] = result["price"]
    if "payment_id" in result:
        context["payment"] = result
    if "delivery_id" in result:
        context["delivery"] = result


def _propose_transition(action, context: dict):
    """Pre-commit negotiation gate (kernel/society/transition_gate.py):
    builds the ProposedTransition for the two grocery capabilities that
    actually touch shared inventory/budget state -- OrderCreation (places
    the reversible hold) and Payment (the real, irreversible commit via
    confirm_reservation). Every other grocery capability returns None,
    meaning ActionExecutor's gate check is a no-op for it, same as before
    this existed. Moved out of ActionExecutor for the same reason
    project_action_result_to_context above is -- a domain-agnostic
    executor has no business knowing "OrderCreation" touches a "selected
    product", let alone what a shared budget id is.

    resource_ids comes from context, not from re-deriving what the
    capability itself will touch -- context["selected_product"] is the
    SAME cart project_action_result_to_context already builds from
    ProductSelection's real outcome, and context["shared_budget_id"] is
    the same id OrderCreation/Payment themselves read. No parallel
    selection logic is invented here.

    SECURITY (Doot audit BYPASS-01 fix): SocialSourcingCapability also
    mutates cross-actor-owned shared state -- borrow_item appends to
    another actor's `loans` list, buy_from_neighbor debits/credits two
    actors' wallets -- but unlike OrderCreation/Payment has no earlier
    ProductSelection-style step that records which resource it will
    touch in context; the borrow/sale candidate is resolved inside
    handle() itself. The branch below does the SAME read-only candidate
    lookup handle() does (find_borrowable / the for-sale entity query),
    reusing those exact functions rather than re-implementing matching
    logic, purely to learn each candidate's real id and owner_id ahead
    of the commit. Unlike a store product, a social-sourcing resource's
    owner IS the live negotiation counterparty by definition of what
    the capability does -- so this sets ProposedTransition.
    required_consent_from to that owner rather than depending on an
    opt-in requires_consent_from flag no seed path ever sets, closing
    the actual gap (the flag mechanism existed and worked for Order/
    Payment but was silently unreachable for this capability). Same
    gate, same negotiation_store/negotiate route, same commit-after-
    accept flow -- nothing new invented.
    """
    if action.capability == "SocialSourcing":
        kg = context.get("knowledge_graph")
        actor_id = context.get("actor_id", "")
        question = context.get("question", "")
        if kg is None or not question:
            return None
        resource_ids: list[str] = []
        owners: list[str] = []
        for item_phrase in _split_requested_items(question):
            if _parse_requested_volume_ml(item_phrase) or _parse_requested_mass_g(item_phrase):
                continue
            borrowable = find_borrowable(kg, item_phrase, exclude_owner_id=actor_id)
            if borrowable:
                lender = max(
                    borrowable,
                    key=lambda e: e.attributes.get("quantity", 0) - sum(l.get("qty", 0) for l in e.attributes.get("loans", [])),
                )
                resource_ids.append(lender.entity_id)
                owner = lender.attributes.get("owner_id")
                if owner:
                    owners.append(owner)
                continue
            from src.monkey_brain.kernel.knowledge_graph import EntityType
            for_sale_kw = _keyword_surface_forms(_keywords(item_phrase))
            for_sale_pool = (
                [e for e in kg.entities_by_keywords(for_sale_kw) if e.entity_type == EntityType.ASSET]
                if for_sale_kw else kg.entities_by_type(EntityType.ASSET)
            )
            for_sale = [
                e for e in for_sale_pool
                if e.attributes.get("pantry")
                and e.attributes.get("for_sale") and e.attributes.get("owner_id") != actor_id
                and _match_score(e.name, item_phrase) > 0 and e.attributes.get("quantity", 0) > 0
            ]
            if for_sale:
                seller = min(for_sale, key=lambda e: e.attributes.get("price", float("inf")))
                resource_ids.append(seller.entity_id)
                owner = seller.attributes.get("owner_id")
                if owner:
                    owners.append(owner)
        if not resource_ids:
            return None
        required_consent_from = tuple(dict.fromkeys(o for o in owners if o and o != actor_id))
        return ProposedTransition(
            actor_id=actor_id,
            resource_ids=tuple(resource_ids),
            mutation_kind="social_source",
            capability=action.capability,
            action_id=action.action_id,
            required_consent_from=required_consent_from,
        )

    if action.capability not in ("OrderCreation", "Payment"):
        return None

    products = context.get("selected_product") or []
    if isinstance(products, dict):
        products = [products]
    resource_ids = tuple(p["id"] for p in products if p.get("id"))
    shared_budget_id = context.get("shared_budget_id")
    if shared_budget_id:
        resource_ids = resource_ids + (shared_budget_id,)
    if not resource_ids:
        return None

    magnitude = round(sum(p.get("price", 0) * p.get("qty", 1) for p in products), 2)
    constraints: dict = {}
    max_price = context.get("max_price")
    if max_price is not None:
        constraints["max_price"] = max_price

    # try_reserve/confirm_reservation (grocery.py) key a hold by order_id,
    # not by actor_id -- by Payment time, this actor's own prior
    # OrderCreation hold shows up under context["order"]["order_id"], and
    # must not be mistaken for a live claim by someone else (see
    # ProposedTransition.self_holder_ids' own docstring).
    order_id = (context.get("order") or {}).get("order_id", "")
    self_holder_ids = (order_id,) if order_id else ()

    return ProposedTransition(
        actor_id=context.get("actor_id", ""),
        resource_ids=resource_ids,
        mutation_kind="reserve" if action.capability == "OrderCreation" else "confirm",
        capability=action.capability,
        action_id=action.action_id,
        magnitude=magnitude,
        constraints=constraints,
        self_holder_ids=self_holder_ids,
    )


# True Multi-Actor Coordination: which real-world business event a
# capability's outcome represents, keyed (capability name, success) — the
# vocabulary a Society's subscribed_events matches against. Same injection
# principle as project_action_result_to_context above: a domain-agnostic
# ActionExecutor has no business knowing "OrderCreation succeeding means
# the world just got an OrderCreated event", so this lives here and is
# injected via ActionExecutor's optional domain_event_resolver parameter,
# not imported/hardcoded into action_executor.py.
CAPABILITY_DOMAIN_EVENTS: dict[tuple[str, bool], str] = {
    ("OrderCreation", True): "OrderCreated",
    ("OrderCreation", False): "InventoryUnavailable",
    ("InventoryReserve", True): "InventoryReserved",
    ("InventoryRelease", True): "InventoryReleased",
    ("Payment", True): "PaymentAuthorized",
    ("Payment", False): "PaymentDeclined",
    ("PaymentConfirmation", False): "PaymentDeclined",
    ("Delivery", True): "ShipmentCreated",
    ("CancelOrder", True): "OrderCancelled",
    ("RefundOrder", True): "OrderRefunded",
    ("LoyaltyAward", True): "LoyaltyPointsAwarded",
    # Multi-Actor Execution Handoff: the Warehouse/Logistics side of the
    # cascade PrepareOrderCapability/AcceptDeliveryCapability (below)
    # continue — see _propagate_coordination's own docstring for how a
    # domain event here becomes the next round's real cognitive tick.
    ("PrepareOrder", True): "OrderPrepared",
    ("AcceptDelivery", True): "OutForDelivery",
    # Real gap this closed: CompeteForResourceCapability wraps the exact
    # same try_reserve CAS primitive InventoryReserveCapability does (a
    # real reservation genuinely placed on a real entity), but had no
    # domain_event tag -- context_engine.py's "meaningful success" ACTION
    # event filter requires both success AND a real tag, so a real,
    # successful competitive reservation never surfaced in Context
    # Events at all, only the actor's own OLDER, unrelated purchase
    # history stayed visible. Same real-world meaning as
    # InventoryReserved (a hold now exists); kept as its own name since
    # WHO/WHY placed it differs (an actor's explicit competitive bid vs.
    # the automatic checkout-flow reservation).
    ("CompeteForResource", True): "ResourceReserved",
    # RecordAgreement genuinely persists a negotiated outcome onto a
    # real order/shipment entity (kg.update_entity) -- same real-mutation
    # class as the others above, same prior gap.
    ("RecordAgreement", True): "AgreementRecorded",
}


def resolve_domain_event(capability: str, success: bool, result: Any = None) -> str | None:
    """ActionExecutor's domain_event_resolver for the grocery vertical —
    returns None (no event) for capabilities with no real-world business
    meaning to broadcast (ProductSelection, SocietyQuery, ...): those are
    reasoning/lookup steps, not world mutations a Society would ever need
    to react to.

    OrderCreationCapability is special-cased: it already has a real,
    existing partial-fulfillment design (MB-3031) where success=True
    even when every item was backordered (try_reserve failed, place_
    backorder queued it) — that is a genuine InventoryUnavailable
    reaction for a Merchant/Inventory Society to coordinate on, not an
    OrderCreated one, even though the capability itself "succeeded".
    """
    if capability == "OrderCreation" and success and isinstance(result, dict):
        items = result.get("items") or []
        backordered = result.get("backordered") or []
        if backordered and len(backordered) >= len(items):
            return "InventoryUnavailable"
        return "OrderCreated"
    return CAPABILITY_DOMAIN_EVENTS.get((capability, success))


logger = logging.getLogger("agentos.domain.grocery")

# Words that carry no product-identifying signal — quantities, units, and
# request verbs. Stripped from both sides before matching so "buy 1L of
# milk" reduces to {"milk"}, which is compared against each candidate
# product's own name keywords.
_STOPWORDS = {
    "buy", "get", "order", "purchase", "some", "a", "an", "the", "of", "for",
    "me", "please", "and", "l", "kg", "g", "ml", "oz", "lb", "lbs", "one",
    "two", "three", "four", "five", "liter", "liters", "litre", "litres",
    "half", "bottle", "bottles", "x", "lactose", "dairy", "free", "budget",
    "with", "within", "under", "no", "more", "than", "stay",
    "gram", "grams", "kilogram", "kilograms", "before", "by", "need",
    "dinner", "lunch", "breakfast", "they", "expire", "expires", "tomorrow",
}

# Regional/marketing terms for the same product, canonicalized to whatever
# word this KG's product names actually use — "whole", "skim" — so a
# request phrased differently still resolves correctly instead of relying
# on luck. GS-0002: with only ONE milk product in the KG, "full cream milk"
# already matched "Whole Milk" by sharing the word "milk" alone — that's not
# real synonym resolution, just an accident of there being nothing else to
# confuse it with. It broke the moment a cheaper "Skim Milk" was added:
# "full cream milk" still shared "milk" with skim milk too, and without
# canonicalizing "cream" -> "whole" there was nothing to prefer the whole
# milk match specifically (see _match_score, which does that preferring).
_SYNONYMS = {
    "cream": "whole",       # "full cream milk" (AU/UK/IN)
    "fullcream": "whole",
    "skimmed": "skim",      # "skimmed milk" (UK) -> skim milk (US)
    "fatfree": "skim",
    "nonfat": "skim",
}

# Reverse of _SYNONYMS: canonical term -> every raw surface form (including
# itself) that _keywords() would fold into it. Phase 6 (GS-6000): the
# knowledge graph's own keyword index (KnowledgeGraph._keyword_index) stores
# RAW, un-canonicalized name tokens — it has no idea "nonfat" means "skim".
# A pre-filter query built from _keywords(item_phrase) alone would therefore
# silently miss a literally-named "Nonfat Milk" product when the request
# says "skim milk", since the KG index only ever sees the raw token
# "nonfat" on that product, never "skim". Expanding the query back out to
# every surface form before hitting the index keeps the pre-filter a true
# superset of what _matches_request would ultimately accept.
_REVERSE_SYNONYMS: dict[str, set[str]] = {}
for _alias, _canonical in _SYNONYMS.items():
    _REVERSE_SYNONYMS.setdefault(_canonical, {_canonical}).add(_alias)


def _keyword_surface_forms(keywords: set[str]) -> set[str]:
    """Expand canonical request keywords out to every raw surface form
    that could tokenize down to them, for querying the KG's raw keyword
    index (see _REVERSE_SYNONYMS)."""
    forms = set(keywords)
    for kw in keywords:
        forms |= _REVERSE_SYNONYMS.get(kw, set())
    return forms


_TIME_KEYWORDS = {"fastest", "quickest", "quick", "fast", "nearest", "closest", "asap", "soonest"}
_QUALITY_KEYWORDS = {
    "quality", "premium", "best", "organic", "top", "finest", "highest",
    "healthy", "healthiest",  # GS-0400: no nutrition data modeled — quality
    # score is the only "how good is this" dimension we have, so it doubles
    # as the proxy for "healthy" (organic/higher-tier dairy skews both ways
    # in the seeded data, which is at least directionally defensible).
}
_COST_KEYWORDS = {"cheap", "cheapest", "affordable", "inexpensive", "frugal"}


def detect_optimization(question: str) -> str | None:
    """
    What's being optimized for: cost, time, or quality — or None if the
    text states no preference at all, letting the caller pick its own
    default rather than always defaulting to "cost" here.
    """
    words = set(re.findall(r"[a-zA-Z]+", (question or "").lower()))
    if words & _TIME_KEYWORDS:
        return "time"
    if words & _QUALITY_KEYWORDS:
        return "quality"
    if words & _COST_KEYWORDS:
        return "cost"
    return None


def _keywords(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Z]+", (text or "").lower())
    canonical = (_SYNONYMS.get(w, w) for w in words)
    return {w for w in canonical if w not in _STOPWORDS and len(w) > 1}


def _match_score(product_name: str, request_text: str) -> int:
    """How specifically a product matches the request: size of the keyword
    overlap. Two candidates can both share a generic word like "milk" while
    only one also shares the descriptor ("whole") that actually
    distinguishes what was asked for — a boolean any-overlap check treats
    both as equally valid and lets price alone break the tie, which silently
    picks the wrong variant. Selection prefers the highest-scoring
    candidates (see ProductSelectionCapability), not just any match.
    """
    request_kw = _keywords(request_text)
    if not request_kw:
        return 1
    return len(request_kw & _keywords(product_name))


def _matches_request(product_name: str, request_text: str) -> bool:
    """Whether a product is relevant to what was actually asked for.

    Without this, ProductSelectionCapability picked the cheapest asset-type
    entity across the ENTIRE knowledge graph regardless of the request —
    verified by seeding a $0.99 "White Bread" entity and watching "buy 1L of
    milk" select it purely because it was cheaper. Requests with no
    extractable keywords (empty/unparseable) don't filter, since there is
    nothing to match against.
    """
    return _match_score(product_name, request_text) > 0


_NUMBER_WORDS = {
    "a": 1, "an": 1,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_UNIT_ML = {
    "ml": 1, "milliliter": 1, "milliliters": 1, "millilitre": 1, "millilitres": 1,
    "l": 1000, "liter": 1000, "liters": 1000, "litre": 1000, "litres": 1000,
}
_QTY_RE = re.compile(
    # "a"/"an" before a unit means count=1 ("a litre of milk") — without it,
    # this phrase parsed no volume at all and fell back to plain per-unit
    # price ranking, which bought a 500ml bottle for a request that asked
    # for a full litre: cheapest available unit, silently half the quantity
    # asked for, no error.
    #
    # \d+(?:\.\d+)? (not bare \d+): a decimal amount like "2.5 liters" was
    # silently misparsed — \d+ can't match across the decimal point, but
    # re.search still finds SOME digit run to match later in the string,
    # so "2.5 liters" matched num="5" (the digit immediately before
    # "liters") and silently returned 5000ml for a 2.5-liter request. This
    # is a decimal digit run, not multiple separate quantities, so it must
    # be captured as one number, never left for the scanner to find a
    # smaller, wrong match downstream.
    r"(?P<num>\d+(?:\.\d+)?|a|an|one|two|three|four|five|six|seven|eight|nine|ten)\s*"
    r"(?P<half>half[- ])?"
    r"(?:x\s*)?"
    r"(?P<unit>ml|milliliters?|millilitres?|l|liters?|litres?)\b",
    re.IGNORECASE,
)


def _parse_requested_volume_ml(text: str) -> int | None:
    """Total volume requested in ml, e.g. 'two half-liter bottles' -> 1000,
    '1L' -> 1000, '2.5L' -> 2500. None when no quantity+unit phrase is
    found — the caller then falls back to a plain per-unit pick (GS-0001
    behavior, unchanged).

    GS-0003: without this, "buy two half-liter bottles" carried no notion of
    "500ml x 2 = 1 liter" at all — the pipeline can only reason about
    quantity if something actually converts the phrase to a volume first.
    """
    m = _QTY_RE.search(text or "")
    if not m:
        return None
    num_raw = m.group("num").lower()
    count = _NUMBER_WORDS.get(num_raw)
    if count is None:
        try:
            count = float(num_raw)
        except ValueError:
            return None
    unit_ml = _UNIT_ML.get(m.group("unit").lower())
    if unit_ml is None:
        return None
    if m.group("half"):
        unit_ml //= 2
    return round(count * unit_ml)


_UNIT_G = {
    "g": 1, "gram": 1, "grams": 1,
    "kg": 1000, "kilogram": 1000, "kilograms": 1000,
}
_MASS_RE = re.compile(
    # \d+(?:\.\d+)? — see _QTY_RE's comment: a bare \d+ silently mis-parses
    # a decimal like "1.5kg" by matching only the digits after the point.
    r"(?P<num>\d+(?:\.\d+)?|a|an|one|two|three|four|five|six|seven|eight|nine|ten)\s*"
    r"(?:x\s*)?"
    r"(?P<unit>kg|kilograms?|g|grams?)\b",
    re.IGNORECASE,
)


def _parse_requested_mass_g(text: str) -> int | None:
    """Total mass requested in grams, e.g. '2kg of strawberries' -> 2000,
    '1.5kg' -> 1500. Mirrors _parse_requested_volume_ml for solid goods
    (produce, meat) — GS-0701 needs an explicit requested quantity to
    reason about "is this more than can be used before it spoils", the
    same way GS-0003 needed one to reason about "does 2x500ml satisfy a 1L
    request"."""
    m = _MASS_RE.search(text or "")
    if not m:
        return None
    num_raw = m.group("num").lower()
    count = _NUMBER_WORDS.get(num_raw)
    if count is None:
        try:
            count = float(num_raw)
        except ValueError:
            return None
    unit_g = _UNIT_G.get(m.group("unit").lower())
    if unit_g is None:
        return None
    return round(count * unit_g)


def _split_requested_items(question: str) -> list[str]:
    """Split a request like 'buy milk and bread' into per-item phrases.

    Without this, "buy milk and bread" was treated as ONE product search:
    SocietyQuery matched both milk and bread candidates (union of keywords),
    then ProductSelection picked a single global cheapest across that pool —
    verified by seeding bread and eggs and watching a "milk and bread"
    request silently buy only bread (it was $2.49, cheaper than any milk),
    with the response still reporting full success. Splitting on commas and
    "and" keeps each item scoped to its own candidate search. A question
    with no separator returns as a single-item list, preserving existing
    single-item behavior exactly.
    """
    parts = re.split(r",|\band\b", question, flags=re.IGNORECASE)
    items = [p.strip() for p in parts if p.strip()]
    return items or [question]


# Substitution preference order per base item, when the specifically-asked
# (or default-preferred) variant is out of stock. GS-0201: "milk unavailable
# -> search alternatives -> 2% -> whole -> oat -> almond, according to
# policy" — a policy is a defined FALLBACK ORDER, not "whatever else happens
# to match the word milk". Keys are the base noun as it appears in _keywords
# output; values are the type-descriptor keyword used in this KG's product
# names, walked in preference order. "2%" is named "reduced" here (the
# literal characters "2%" have no letters, so _keywords — which only
# extracts [a-zA-Z]+ — would strip it to nothing and the type would be
# unmatchable; "Reduced Fat Milk" gives it a real descriptor word).
_SUBSTITUTION_POLICIES = {
    "milk": ["reduced", "whole", "oat", "almond"],
}


def _base_item(item_phrase: str) -> str | None:
    """Which known substitution category (if any) this item phrase names."""
    kws = _keywords(item_phrase)
    for base in _SUBSTITUTION_POLICIES:
        if base in kws:
            return base
    return None


def _type_keyword_of(product_name: str) -> str | None:
    """Level 19: which substitution-policy type keyword (reduced/whole/oat/
    almond, ...) a product's own NAME actually names, if any — captured at
    purchase time so learned_preference can later ask "what type has this
    actor actually been buying" from real line items, not by re-deriving
    it from a product entity that might not exist anymore."""
    kws = _keywords(product_name)
    for policy in _SUBSTITUTION_POLICIES.values():
        for type_kw in policy:
            if type_kw in kws:
                return type_kw
    return None


def wants_lactose_free(question: str) -> bool:
    """GS-0202: 'lactose free' / 'dairy free' as a hard filter, not a
    preference — a request for lactose-free milk must never be satisfied by
    dairy milk regardless of price/quality/stock. Checked on raw text (not
    _keywords) since "lactose" and "dairy"/"free" are stopwords for product
    matching — they'd never survive to be checked as keywords."""
    q = (question or "").lower()
    return "lactose" in q or "dairy free" in q or "dairy-free" in q


def wants_approval_before_substitution(question: str) -> bool:
    """Qualification Gap Closure, Phase 9 (a real wiring gap found while
    re-verifying Phase 3's approval mechanism end-to-end): the generic
    execution state machine (action_executor.py, OrderConfirmationCapability
    below) reacts correctly to context["approval_required_for_substitution"],
    but nothing upstream of it ever SET that flag from a real prompt —
    Phase 3's own tests set it directly, bypassing the planner entirely, so
    the gap was invisible at the unit level. Checked on raw text the same
    way wants_pickup/wants_lactose_free already are (their own established
    precedent for "a real spoken constraint the planner's structured
    parameters don't carry"), not a new mechanism."""
    q = (question or "").lower()
    if "approv" not in q and "ask" not in q:
        return False
    markers = (
        "ask me", "ask before", "ask for approval", "must approve",
        "without my approval", "without approval", "before buying",
        "before purchasing", "before substituting", "stop and ask",
    )
    return any(m in q for m in markers)


def wants_pickup(question: str) -> bool:
    """Delivery vs Pickup: an explicit self-pickup request is a hard
    fulfillment-method choice, not a preference DeliveryCapability can
    silently override — before this, every order was scheduled for
    delivery regardless of what was asked, with no way to ever choose
    picking it up yourself (no delivery fee, no rider wait, but the actor
    travels to the store). Checked on raw text the same way
    wants_lactose_free is, since "pick"/"pickup" would need to survive as
    a product-matching keyword otherwise, which it was never meant to be."""
    q = (question or "").lower()
    return "pick up" in q or "pick-up" in q or "pickup" in q or "picking up" in q or "self-pickup" in q


def wants_low_sodium(question: str) -> bool:
    """Level 24 (GS-2401): 'low sodium' as a hard filter, same principle as
    wants_lactose_free — a doctor's dietary restriction must never be
    satisfied by a normal-sodium product regardless of price/quality."""
    q = (question or "").lower()
    return "low sodium" in q or "low-sodium" in q or "reduced sodium" in q


_ALLERGENS = ("peanut", "tree nut", "shellfish", "gluten", "soy", "egg", "dairy")


def wants_allergen_free(question: str) -> set[str]:
    """Level 24 (GS-2402): a stated allergy is a HARD safety exclusion, not
    a soft preference — unlike a plain stockout, there is no substitution-
    policy fallback for "closest available" when the constraint is safety.
    ANY product tagged with a mentioned allergen (attributes["allergens"])
    is removed from consideration entirely. Requires the word "allerg-"
    somewhere in the question so an unrelated mention of "soy" (e.g. "soy
    milk please") isn't treated as an exclusion of soy itself.
    """
    lowered = (question or "").lower()
    if "allerg" not in lowered:
        return set()
    return {a.replace(" ", "_") for a in _ALLERGENS if a in lowered}


_BUDGET_RE = re.compile(r"\$\s?(\d+(?:\.\d{1,2})?)")
_BUDGET_KEYWORDS = ("budget", "spend", "afford", "under", "less than", "no more than",
                    "at most", "cap", "limit", "have exactly")


def parse_budget(question: str) -> float | None:
    """A stated spending cap like '$10 budget' or 'stay under $7'. None if
    no dollar amount is present in the request, or the dollar amount isn't
    actually budget language at all.

    GS-0200: without this, a budget constraint in the request text was
    simply never read — the pipeline would happily complete an order over
    whatever the actor said they wanted to spend, with no check at all.

    Real gap this closes: the original version matched ANY dollar amount
    ANYWHERE in the request, unconditionally — confirmed live once
    NegotiatePrice's own real deal-parameters started reaching this same
    text (e.g. "negotiate milk down from its listed price, floor $3.00,
    then buy it"): the negotiation floor was misread as a stated
    total-order spending cap, rejecting a perfectly affordable order.
    Gated on real budget language (same keyword-gating convention
    parse_shared_budget below already uses for its own "shared"/"joint"
    distinction), not proximity to the dollar sign — the two real
    existing phrasings ("under a $20 budget", "Do not spend more than
    $5") both still match.
    """
    q = question or ""
    if not any(kw in q.lower() for kw in _BUDGET_KEYWORDS):
        return None
    m = _BUDGET_RE.search(q)
    return float(m.group(1)) if m else None


def parse_shared_budget(question: str) -> float | None:
    """Qualification Gap Closure, Phase 9: a real, joint spending ceiling
    two or more agents draw against TOGETHER ("two agents, shared $20
    budget"), distinct from an ordinary personal budget statement
    (parse_budget above) an actor states about their OWN single order —
    the same dollar-amount grammar, gated on real "shared" language so a
    plain "buy milk and pizza for less than $20" never mistakenly spins
    up a throwaway shared-budget entity for what is, and stays, a normal
    single-actor purchase."""
    q = (question or "").lower()
    if not any(m in q for m in ("shared budget", "joint budget", "shared $", "joint $", "budget between", "combined budget")):
        return None
    m = _BUDGET_RE.search(question or "")
    return float(m.group(1)) if m else None


from src.monkey_brain.kernel.domains.logistics import (
    parse_deadline_minutes, predict_delivery_delay, _format_address,
    estimate_pickup_minutes, select_delivery_riders,
    find_schedule_conflict, create_shipment, mark_rider_assigned,
)


def _unit_cost(entity, stores_by_id=None) -> float:
    """Price per ml when size_ml is known, else raw price, divided by the
    selling store's trust score when one is known. Shared by
    ProductSelectionCapability's normal pick and its store-consolidation
    comparison, so both agree on what "cheapest" means — a $1.75 500ml
    bottle is NOT cheaper than a $2.79 1L bottle ($3.50/L vs $2.79/L); only
    raw price makes it look that way.

    GS-0801/0802: dividing by trust means a store that repeatedly cancels
    orders gets systematically more expensive in every cost comparison it's
    part of, without needing separate "is this store trustworthy enough"
    logic bolted on elsewhere — trust=0.4 makes a $2.79 item behave like
    $6.98 for ranking purposes; trust recovering back toward 1.0 lets it
    naturally reclaim a competitive price again. A store with ZERO
    successful fulfillments (trust=0, all cancellations) is excluded
    outright (infinite effective cost) rather than divided into a merely
    large number, since "reliability score of zero" shouldn't be
    reachable by just being cheap enough.
    """
    price = entity.attributes.get("price", float("inf"))
    size = entity.attributes.get("size_ml")
    base = price / size if size else price
    if stores_by_id is not None:
        store = stores_by_id.get(entity.attributes.get("store_id"))
        trust = store.attributes.get("trust", 1.0) if store else 1.0
        if trust <= 0:
            return float("inf")
        base = base / trust
    return base


_REJECTION_THRESHOLD = 2  # GS-0800: this many rejections of the same type reads as "always rejects"


def record_rejection(kg, type_keyword: str, max_attempts: int = 5) -> int:
    """Record that the actor rejected a product of this type (e.g.
    'almond'). Stored as a small Preference entity in the actor's OWN
    graph — rejection is personal to the actor, not a property of the
    product or store, unlike trust (see record_order_outcome). Returns the
    new cumulative count.

    Uses compare_and_swap, not a plain read-then-write — Level 12's
    concurrency stress test caught the same read-modify-write race here
    that record_order_outcome had: unconditionally overwriting count based
    on a snapshot read loses updates the instant two writers overlap. Less
    likely to matter in practice for a per-actor preference than for a
    shared store's trust score, but it's the same class of bug and the fix
    is identical, so there's no reason to leave it unfixed now that it's
    been found.
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType
    entity_id = f"pref_reject_{type_keyword}"
    for _ in range(max_attempts):
        existing = kg.get_entity(entity_id)
        if existing is None:
            # First rejection of this type — no prior version to race
            # against; add_entity's own write is the initial creation.
            entity = kg.add_entity(entity_id, EntityType.OTHER, f"Rejection: {type_keyword}",
                                    {"type_keyword": type_keyword, "count": 1, "last_rejected": time.time()})
            return entity.attributes["count"]

        count = existing.attributes.get("count", 0) + 1
        version = kg.version_of(entity_id)
        ok, _current = kg.compare_and_swap(entity_id, version, {
            "type_keyword": type_keyword, "count": count, "last_rejected": time.time(),
        })
        if ok:
            return count
    current = kg.get_entity(entity_id)
    return current.attributes.get("count", 0) if current else 0


def get_rejected_keywords(kg) -> frozenset:
    """Type keywords the actor has rejected often enough to treat as a
    standing preference against them, not just a one-off "not today"."""
    from src.monkey_brain.kernel.knowledge_graph import EntityType
    rejected = set()
    # record_rejection (above) always creates these as EntityType.OTHER --
    # narrowing to that type keeps this off the catalog-scale ASSET pool
    # entirely (GS-6000), rather than scanning every product to find a
    # handful of preference-rejection markers.
    for e in kg.entities_by_type(EntityType.OTHER):
        if e.entity_id.startswith("pref_reject_") and e.attributes.get("count", 0) >= _REJECTION_THRESHOLD:
            kw = e.attributes.get("type_keyword", "")
            if kw:
                rejected.add(kw)
    return frozenset(rejected)


def record_order_outcome(kg, store_id: str, fulfilled: bool, max_attempts: int = 30) -> float:
    """Record a store's order outcome and update its trust score (fraction
    of orders actually fulfilled, not cancelled). Returns the new trust
    score. GS-0801/0802: trust lives on the STORE entity (shared/global,
    unlike rejection which is per-actor) since reliability is a property of
    the store itself, not any one shopper's opinion of it.

    Uses compare_and_swap, not the plain kg.update_entity read-then-write
    this originally did. That version passed Level 9's test because it was
    only ever called sequentially there — Level 12's 300-concurrent-writer
    stress test caught the actual bug: EVERY writer read roughly the same
    starting fulfilled_count, computed "+1" from it, and wrote unconditionally,
    so whichever write landed last won and every other writer's increment
    was silently discarded. Of 300 concurrent outcome reports, only 4 total
    survived. A shared trust score updated by many customers' concurrent
    orders is exactly the case a per-actor preference (record_rejection)
    mostly isn't — this is the one where the bug was always going to bite.
    """
    import random
    for attempt in range(max_attempts):
        store = kg.get_entity(store_id)
        if store is None:
            return 1.0
        fulfilled_count = store.attributes.get("fulfilled_count", 0) + (1 if fulfilled else 0)
        cancelled_count = store.attributes.get("cancelled_count", 0) + (0 if fulfilled else 1)
        total = fulfilled_count + cancelled_count
        trust = round(fulfilled_count / total, 4) if total else 1.0

        version = kg.version_of(store_id)
        ok, _current = kg.compare_and_swap(store_id, version, {
            "fulfilled_count": fulfilled_count, "cancelled_count": cancelled_count, "trust": trust,
        })
        if ok:
            return trust
        # Lost the race — compare_and_swap refreshed the local cache with
        # the current state. A short, jittered backoff before retrying
        # matters here specifically because MANY writers are hammering the
        # SAME single node: retrying instantly just re-collides with
        # whichever other writer is also mid-retry, so under heavy
        # contention a fixed small attempt budget with no backoff means
        # some callers exhaust every attempt and give up with their
        # increment never applied — not corruption (compare_and_swap never
        # applies a write against a stale version), just never getting a
        # turn. Level 12's 300-concurrent-writer test caught this too: even
        # with correct CAS, 5 attempts and no backoff left ~57% of writers
        # empty-handed.
        time.sleep(random.uniform(0.001, 0.005) * (attempt + 1))
    current = kg.get_entity(store_id)
    return current.attributes.get("trust", 1.0) if current else 1.0


_TRUST_SHARD_COUNT = 10


def record_order_outcome_sharded(kg, store_id: str, fulfilled: bool, shard_count: int = _TRUST_SHARD_COUNT, max_attempts: int = 40) -> None:
    """Level 12: record_order_outcome's single-counter CAS retry loop —
    correct, but under 300 concurrent writers to ONE node, retries and
    backoff alone still left ~43% of writers empty-handed even with 30
    attempts and jitter (and took 6x longer doing it). More retries can't
    fix it: the ceiling is how many transactions ONE node's lock can
    actually serialize per second, not how patient the callers are.

    Sharded counters are the standard fix for a high-write-concurrency
    aggregate (the same pattern Firestore/Datastore's own docs recommend
    for exactly this "many writers, one counter" problem): split the
    counter into shard_count independent entities, have each writer land on
    a RANDOM shard instead of the one shared node. Two writers only
    actually contend when they happen to pick the SAME shard — with 10
    shards, that's roughly 1/10th the collision rate of everyone hammering
    a single node, so far more writers succeed in far less time. The true
    aggregate is computed at READ time by summing every shard
    (aggregate_trust) rather than kept as one continuously-updated total —
    eventually consistent, not instantaneously so, which is the real
    tradeoff this pattern makes.

    Shards should be PRE-PROVISIONED (add_entity'd with zeroed counts)
    before concurrent writers start, not created lazily on first write —
    the "shard doesn't exist yet" branch below falls back to a plain
    add_entity, which is unconditional (no CAS possible against a node not
    yet in the local cache at all). If many writers all hydrate before any
    shard exists — the normal case at real concurrency, not an edge case —
    they can all race that same unconditional-create path and lose updates
    exactly like the un-sharded version did. Caught by testing this
    function the same way the bug it exists to fix was caught: with real
    concurrent load, not by reasoning about it.
    """
    import random
    shard_id = f"{store_id}_shard_{random.randrange(shard_count)}"
    from src.monkey_brain.kernel.knowledge_graph import EntityType
    for attempt in range(max_attempts):
        shard = kg.get_entity(shard_id)
        if shard is None:
            kg.add_entity(shard_id, EntityType.OTHER, f"trust shard for {store_id}", {
                "parent_store_id": store_id,
                "fulfilled_count": 1 if fulfilled else 0,
                "cancelled_count": 0 if fulfilled else 1,
            })
            return
        fulfilled_count = shard.attributes.get("fulfilled_count", 0) + (1 if fulfilled else 0)
        cancelled_count = shard.attributes.get("cancelled_count", 0) + (0 if fulfilled else 1)
        version = kg.version_of(shard_id)
        ok, _current = kg.compare_and_swap(shard_id, version, {
            "fulfilled_count": fulfilled_count, "cancelled_count": cancelled_count,
        })
        if ok:
            return
        # Same reasoning as record_order_outcome's backoff: many writers can
        # still land on the same shard (300 writers / 10 shards is ~30-way
        # contention on an unlucky shard, not negligible) so a jittered
        # backoff is still needed, just scaled to the lower per-shard load.
        time.sleep(random.uniform(0.001, 0.005) * (attempt + 1))


def aggregate_trust(kg, store_id: str, shard_count: int = _TRUST_SHARD_COUNT) -> float:
    """Sum every shard to get the store's real current trust score. Read
    cost scales with shard_count (shard_count entity reads instead of one),
    which is exactly the tradeoff sharding makes — cheap, low-contention
    writes in exchange for a slightly more expensive read."""
    fulfilled = cancelled = 0
    for i in range(shard_count):
        shard = kg.get_entity(f"{store_id}_shard_{i}")
        if shard is not None:
            fulfilled += shard.attributes.get("fulfilled_count", 0)
            cancelled += shard.attributes.get("cancelled_count", 0)
    total = fulfilled + cancelled
    return round(fulfilled / total, 4) if total else 1.0


def pick_best_unit(candidates, item_optimization, stores_by_id):
    """Single-unit pick (qty=1) by the actor's optimization preference.

    Module-level (not a ProductSelectionCapability closure) so
    OrderConfirmationCapability can call it too, when replanning around an
    item that went stale between selection and confirmation (GS-0500/0501)
    — the replacement should honor the same preference the original pick
    was made under, not silently default to cost.

    Three real branches, not "cost, and an else that happened to also cover
    time": optimization_objective used to be hardcoded to "cost" everywhere
    (routes/prompt.py), so "buy the fastest milk" and "buy the highest
    quality milk" were both silently cost-optimized regardless of what was
    asked — see detect_optimization, which now actually reads the request.

    Cost compares price PER ml when size_ml is known, not raw sticker price
    — verified with a real case: for a bare "milk" request (no
    quantity/volume specified), a $1.75 500ml bottle beat every 1L option on
    raw price alone despite costing more per liter ($3.50/L) than a $2.09
    1L bottle ($2.09/L). Candidates without size_ml (bread, eggs) fall back
    to raw price.

    Quality picks the highest quality score in the candidate pool, ignoring
    price entirely — GS-0102 explicitly requires this "should ignore
    cheapest option", not just weight quality in.
    """
    if item_optimization == "cost":
        best = min(candidates, key=lambda e: _unit_cost(e, stores_by_id))
        price = best.attributes.get("price", 0)
        size = best.attributes.get("size_ml")
        store = stores_by_id.get(best.attributes.get("store_id")) if stores_by_id else None
        trust = store.attributes.get("trust") if store else None
        trust_note = f", trust {trust:.2f}" if trust is not None and trust < 1.0 else ""
        if size:
            reason = f"Best value at ${price:.2f} (${price / size * 1000:.2f}/L{trust_note})"
        else:
            reason = f"Lowest price at ${price:.2f}{trust_note}"
    elif item_optimization == "quality":
        best = max(candidates, key=lambda e: e.attributes.get("quality", 0))
        reason = f"Highest quality ({best.attributes.get('quality', 0):.1f}/5) at ${best.attributes.get('price', 0):.2f}"
    else:  # "time" — GS-1302: fastest PREDICTED delivery, not just nearest
        # store — a closer store on a congested route (high traffic_factor)
        # can predict slower than a farther store with clear roads.
        def _predicted_minutes(p):
            store = stores_by_id.get(p.attributes.get("store_id")) if stores_by_id else None
            return predict_delivery_delay(store)
        best = min(candidates, key=_predicted_minutes)
        store = stores_by_id.get(best.attributes.get("store_id")) if stores_by_id else None
        predicted = _predicted_minutes(best)
        any_traffic = stores_by_id and any(
            s.attributes.get("traffic_factor", 1.0) != 1.0 for s in stores_by_id.values()
        )
        if store and any_traffic:
            reason = (f"Fastest predicted delivery (~{predicted:.0f} min from "
                      f"{store.attributes.get('distance_miles', '?')} mi, traffic factor "
                      f"{store.attributes.get('traffic_factor', 1.0):.1f}x)")
        else:
            reason = f"Closest store at {store.attributes.get('distance_miles', '?')} miles" if store else "selected"
    return best, 1, reason


_OPTIMIZATION_LABELS = {
    "cost": "Minimize expected total cost, considering reliability",
    "quality": "Maximize product quality",
    "time": "Minimize predicted delivery time, considering traffic",
}


def _decision_confidence(item_optimization: str, candidates, stores_by_id) -> float:
    """GS-1600: a real, derived confidence — how decisively the winning
    candidate beat the runner-up on the SAME metric that actually decided
    it (trust-adjusted cost, quality, or predicted delivery time), not an
    arbitrary number. A single available candidate is reported as fully
    confident (there was no real alternative to weigh against, so nothing
    was uncertain about the choice); confidence otherwise scales with the
    relative gap and is bounded to [0.5, 0.99] — even a landslide win
    stops short of false certainty, and a coin-flip-close win never drops
    below "still the better option, barely."
    """
    if len(candidates) <= 1:
        return 1.0
    if item_optimization == "quality":
        metrics = sorted((c.attributes.get("quality", 0) for c in candidates), reverse=True)
        winner, runner_up = metrics[0], metrics[1]
        gap = (winner - runner_up) / winner if winner else 0.0
    elif item_optimization == "time":
        metrics = sorted(predict_delivery_delay(stores_by_id.get(c.attributes.get("store_id")) if stores_by_id else None)
                          for c in candidates)
        winner, runner_up = metrics[0], metrics[1]
        gap = (runner_up - winner) / runner_up if runner_up not in (0, float("inf")) else 0.0
    else:  # cost — the same trust-adjusted _unit_cost that actually ranked candidates
        metrics = sorted(_unit_cost(c, stores_by_id) for c in candidates)
        winner, runner_up = metrics[0], metrics[1]
        gap = (runner_up - winner) / runner_up if runner_up not in (0, float("inf")) else 0.0
    return round(min(0.99, max(0.5, 0.5 + gap)), 2)


def build_decision_trace(item_label: str, candidates, item_optimization: str, stores_by_id, chosen_selection: dict) -> dict:
    """GS-1600: a structured, honest record of what was actually weighed —
    every real candidate considered (not just the winner), the SAME
    trust-adjusted metric that actually drove the ranking (not just raw
    price, which alone can make the real decision look arbitrary — a
    $3.45 store beating a $3.20 one only makes sense once trust is
    shown), the optimization objective in plain language, and a derived
    confidence. Built from data every selection already computes — this
    doesn't infer or guess anything after the fact.
    """
    candidate_rows = []
    for c in candidates:
        store = stores_by_id.get(c.attributes.get("store_id")) if stores_by_id else None
        trust = store.attributes.get("trust", 1.0) if store else 1.0
        row = {
            "store": c.attributes.get("store_name") or (store.name if store else "?"),
            "product": c.name,
            "price": c.attributes.get("price"),
            "trust": round(trust, 2),
        }
        if item_optimization == "cost":
            # _unit_cost returns $/ml when size_ml is known (needed so a
            # 500ml and a 1L bottle compare correctly) -- displaying THAT
            # raw number rounds to $0.00 for any real volume price. Scale
            # back to $/liter (matching how pick_best_unit's own "$X/L"
            # reason string already presents this) for anything sized;
            # unsized items (bread, eggs) were never in per-ml units to
            # begin with, so their raw effective cost is already correct.
            size = c.attributes.get("size_ml")
            unit_cost = _unit_cost(c, stores_by_id)
            row["effective_cost"] = round(unit_cost * 1000, 4) if size else round(unit_cost, 4)
            row["effective_cost_unit"] = "per liter" if size else "total"
        if item_optimization == "quality":
            row["quality"] = c.attributes.get("quality")
        if item_optimization == "time" and store:
            row["predicted_minutes"] = round(predict_delivery_delay(store), 1)
        candidate_rows.append(row)

    return {
        "item": item_label,
        "optimization": item_optimization,
        "optimization_label": _OPTIMIZATION_LABELS.get(item_optimization, item_optimization),
        "candidates": candidate_rows,
        "chosen": {
            "store": chosen_selection.get("store_name"),
            "product": chosen_selection.get("name"),
            "price": chosen_selection.get("price"),
        },
        "confidence": _decision_confidence(item_optimization, candidates, stores_by_id),
    }


_NEAR_EXPIRY_DAYS = 2  # GS-0701: "expires tomorrow" and tighter triggers "buy less"
_MAX_PACKAGE_COMBO_DP_SIZE = 20_000  # bound the combo search's DP array (ml/g); beyond this, skip and use the safe single-size result


def _cheapest_package_combo(sized, requested_qty: int, size_key: str):
    """Quantity Optimization: the cheapest combination of MIXED package
    sizes that covers requested_qty, or None if no beneficial mix exists
    (fewer than 2 usable sizes, the search would be unbounded, or the
    reconstructed combo turns out to need more of a size than is in
    stock). pick_best_for_volume's existing single-size result is always
    a safe, correct fallback when this returns None — this never being
    called, or never finding anything, changes no existing behavior.

    A candidate priced or sized at 0/None is excluded (nothing to search
    over). Bounded to _MAX_PACKAGE_COMBO_DP_SIZE — real grocery requests
    and package sizes stay well under this; an adversarial or malformed
    input just skips combinatorial search rather than doing unbounded
    work, since the caller always has a safe fallback.

    Bounded 0/1 knapsack (each candidate usable at most its own on-hand
    stock many times — Partial/Split Inventory: a store with only 2 units
    left must never be treated as an unlimited source just because it's
    the cheapest one). Real on-hand limits are enforced DURING the search
    via binary decomposition (each candidate's up-to-max_count copies
    become O(log max_count) grouped 0/1 items — standard bounded-knapsack
    technique), not checked after the fact — an earlier version searched
    unbounded-supply first and discarded any answer that turned out to
    need more than was in stock, which silently gave up on exactly the
    case that matters most: two stores each holding part of what's
    needed, combined to fill the request in full.
    """
    sizes = []
    for c in sized:
        size = c.attributes.get(size_key)
        price = c.attributes.get("price")
        if not size or price is None or price < 0:
            continue
        sizes.append((c, int(size), price))
    if len(sizes) < 2:
        return None  # nothing to mix

    cap = requested_qty + max(size for _, size, _ in sizes) - 1
    if cap <= 0 or cap > _MAX_PACKAGE_COMBO_DP_SIZE:
        return None

    # Binary-decompose each candidate's available copies (on-hand stock,
    # or enough to reach `cap` alone if unmetered) into O(log max_count)
    # grouped 0/1 items, bounding total search work regardless of how
    # large on-hand stock is.
    items = []  # (candidate_idx, weight, cost)
    for idx, (c, size, price) in enumerate(sizes):
        on_hand = c.attributes.get("quantity")
        max_count = (cap // size) + 1 if on_hand is None else max(0, min(on_hand, cap // size + 1))
        remaining = max_count
        k = 1
        while remaining > 0:
            take = min(k, remaining)
            items.append((idx, take * size, take * price))
            remaining -= take
            k *= 2
    if len(items) > 4000:
        return None  # keep the DP bounded regardless of input shape

    INF = float("inf")
    best_cost = [INF] * (cap + 1)
    choice: list[tuple[int, int] | None] = [None] * (cap + 1)
    best_cost[0] = 0.0
    for item_i, (_idx, weight, cost) in enumerate(items):
        for v in range(cap, weight - 1, -1):
            prev_cost = best_cost[v - weight]
            if prev_cost == INF:
                continue
            total = prev_cost + cost
            if total < best_cost[v]:
                best_cost[v] = total
                choice[v] = (item_i, v - weight)

    target_v = min(range(requested_qty, cap + 1), key=lambda v: best_cost[v])
    if best_cost[target_v] == INF:
        return None

    counts: dict[int, int] = {}
    v = target_v
    while v > 0 and choice[v] is not None:
        item_i, prev = choice[v]
        cand_idx, weight, _cost = items[item_i]
        size = sizes[cand_idx][1]
        counts[cand_idx] = counts.get(cand_idx, 0) + weight // size
        v = prev
    if len(counts) < 2:
        return None  # cheapest reconstruction turned out to be a single size after all

    combo = [(sizes[idx][0], count) for idx, count in counts.items()]
    return best_cost[target_v], combo


def pick_best_for_volume(candidates, requested_qty, item_optimization, stores_by_id, size_key="size_ml"):
    """Multi-unit pick meeting the requested quantity (ml for liquids via
    size_key="size_ml", grams for solids via size_key="size_g"). Module-level
    for the same reason as pick_best_unit — OrderConfirmationCapability
    reuses it.

    Perishables near expiry override everything else: minimize EXCESS over
    the requested amount first, cost only as a tiebreaker. GS-0701 —
    "expires tomorrow, buy less" — cost optimization alone would happily buy
    a much bigger pack because the per-gram price is better (verified: a
    3lb strawberry pack at $8.99 undercuts 5x1lb packs at $3.99 each for a
    2kg request, but leaves 724g of excess against a 2-day shelf life vs
    270g from the smaller packs) — cheaper isn't "buy less" when it's the
    excess that's the actual problem, not the price.

    Otherwise: cost optimization compares (product, qty) combos by TOTAL
    price — 1x1L can beat 2x500ml even when 500ml has a lower per-bottle
    price, if the per-liter unit price works out cheaper. Time and quality
    optimization pick the best product FIRST (ignoring price/qty tradeoffs —
    you don't trade quality for a cheaper multi-pack) and only then compute
    how many units of THAT product are needed; comparing total cost across
    candidates would be wrong for these, since a worse-quality product could
    look "better" purely by being cheaper in bulk.
    """
    sized = [c for c in candidates if c.attributes.get(size_key)]
    if not sized:
        best, qty, reason = pick_best_unit(candidates, item_optimization, stores_by_id)
        return best, qty, reason, []

    near_expiry = all(
        c.attributes.get("expires_in_days") is not None
        and c.attributes["expires_in_days"] <= _NEAR_EXPIRY_DAYS
        for c in sized
    )
    unit_label = "ml" if size_key == "size_ml" else "g"

    def _capped_qty(c, needed_qty: int) -> int:
        """Units of c actually purchasable — never more than the shelf
        holds (attributes["quantity"]). Inventory Constraints: the ceil-
        division qty needed to satisfy a volume/mass request must never be
        assumed available just because a candidate PASSED the earlier
        quantity-!=-0 filter (eligible_candidates) — that filter only
        excludes total stockouts, not "this store has 1 bottle but the
        request needs 5". Missing attributes["quantity"] means unmetered
        stock (existing seed data that never set it), so the full needed
        amount is assumed available, unchanged from before this check."""
        on_hand = c.attributes.get("quantity")
        return needed_qty if on_hand is None else min(needed_qty, max(0, on_hand))

    def _shortfall_suffix(qty: int, needed_qty: int) -> str:
        # qty/needed_qty are PACKAGE counts (e.g. bottles), not volume/mass
        # — unit_label ("ml"/"g") describes the requested_qty amount, so it
        # must not be attached to a package count.
        shortfall = needed_qty - qty
        if shortfall <= 0:
            return ""
        return (f" — only {qty} package(s) in stock, {shortfall} short of the "
                f"{needed_qty} needed to cover the requested {requested_qty}{unit_label}")

    if near_expiry:
        scored = []
        for c in sized:
            size = c.attributes[size_key]
            needed_qty = -(-requested_qty // size)  # ceil division
            qty = _capped_qty(c, needed_qty)
            excess = max(0, qty * size - requested_qty)
            total = c.attributes.get("price", float("inf")) * qty
            scored.append((qty < needed_qty, excess, total, c, qty, needed_qty))
        has_shortfall, excess, total, best, qty, needed_qty = min(scored, key=lambda t: (t[0], t[1], t[2]))
        reason = (
            f"{qty} x {best.attributes.get(size_key)}{unit_label} = ${total:.2f} total "
            f"(needs {requested_qty}{unit_label}; minimizing excess to {excess}{unit_label} — "
            f"expires in {best.attributes.get('expires_in_days')}d)"
            f"{_shortfall_suffix(qty, needed_qty)}"
        )
        return best, qty, reason, []

    if item_optimization == "cost":
        # Ranked by trust-adjusted effective cost (a cancellation-prone
        # store's total gets systematically worse), but the REASON and the
        # qty/product handed back to OrderCreation reflect the real dollar
        # total the actor actually pays — the trust penalty only ever
        # affects which candidate wins, never what gets charged. A
        # candidate that can't fully satisfy the request (qty < needed_qty)
        # ranks behind every candidate that can, regardless of price —
        # cheaper-but-insufficient is never preferred over sufficient.
        scored = []
        for c in sized:
            size = c.attributes[size_key]
            needed_qty = -(-requested_qty // size)  # ceil division
            qty = _capped_qty(c, needed_qty)
            real_total = c.attributes.get("price", float("inf")) * qty
            effective_total = _unit_cost(c, stores_by_id) * qty * size
            scored.append((qty < needed_qty, effective_total, real_total, c, qty, needed_qty))
        has_shortfall, effective_total, real_total, best, qty, needed_qty = min(scored, key=lambda t: (t[0], t[1]))
        reason = (
            f"{qty} x {best.attributes.get(size_key)}{unit_label} = ${real_total:.2f} total "
            f"(needs {requested_qty}{unit_label}){_shortfall_suffix(qty, needed_qty)}"
        )

        # Quantity Optimization / Partial Inventory: a single package size
        # (or a single store) isn't always enough — mixing sizes can beat
        # it on cost (one 2L jug + one 500ml bottle for a 2.5L request,
        # cheaper than either buying 2 jugs or 5 bottles alone), and
        # mixing STORES can beat it on completeness (Store A has 2 of the
        # 5 needed, Store B has the rest — a real split order, not a
        # single store capped short). Only tried for cost optimization,
        # matching this function's own documented split (quality/time
        # pick the best PRODUCT first and never trade it for a cheaper
        # multi-pack). Tried even when the single best pick already has a
        # shortfall — that's exactly when combining sources matters most
        # — and always preferred when it resolves a shortfall the single
        # pick has, or is strictly cheaper when it doesn't.
        combo = _cheapest_package_combo(sized, requested_qty, size_key)
        if combo is not None and (has_shortfall or combo[0] < real_total):
            combo_cost, combo_items = combo
            (best, qty), *extra = combo_items
            names = ", ".join(f"{n} x {c.attributes.get(size_key)}{unit_label}" for c, n in combo_items)
            reason = (
                f"Mixed packages ({names}) = ${combo_cost:.2f} total "
                f"(needs {requested_qty}{unit_label}) — cheaper than any single package size"
                if not has_shortfall else
                f"Split order ({names}) = ${combo_cost:.2f} total "
                f"(needs {requested_qty}{unit_label}) — no single store had enough in stock"
            )
            return best, qty, reason, extra

        return best, qty, reason, []

    best, _, unit_reason = pick_best_unit(sized, item_optimization, stores_by_id)
    size = best.attributes[size_key]
    needed_qty = -(-requested_qty // size)  # ceil division
    qty = _capped_qty(best, needed_qty)
    total = best.attributes.get("price", 0) * qty
    reason = (
        f"{unit_reason}; {qty} x {size}{unit_label} = ${total:.2f} total "
        f"(needs {requested_qty}{unit_label}){_shortfall_suffix(qty, needed_qty)}"
    )
    return best, qty, reason, []


_LEARNED_AVOIDANCE_TRUST_THRESHOLD = 0.5
_LEARNED_AVOIDANCE_MIN_ATTEMPTS = 10


def has_learned_to_avoid(store, trust_threshold: float = _LEARNED_AVOIDANCE_TRUST_THRESHOLD,
                          min_attempts: int = _LEARNED_AVOIDANCE_MIN_ATTEMPTS) -> bool:
    """Level 17: a QUALITATIVE cutoff on top of Level 9's continuous
    trust-adjusted ranking (_unit_cost divides by trust, making a mediocre
    store systematically more expensive but never literally unavailable
    unless trust hits exactly 0). Real learning-from-experience means more
    than "penalize slightly" — after enough real evidence, the planner
    should stop considering the store at all, the same way "Iteration 20:
    planner automatically avoids Store A" reads in the original scenario.

    min_attempts is what makes this LEARNED rather than reactive: one or
    two early failures (attempts < min_attempts) never trigger avoidance
    on their own, no matter how bad they look — trust=0/1 after a single
    cancellation is statistically meaningless, and a store shouldn't be
    blacklisted off one bad first impression. Once enough real outcomes
    exist, though, this is REVERSIBLE — trust is recomputed continuously
    by record_order_outcome, so a store that starts fulfilling reliably
    again naturally climbs back above the threshold and reappears as a
    candidate, exactly like Level 9's trust score already does; nothing
    here is a permanent blacklist.
    """
    attrs = store.attributes
    attempts = attrs.get("fulfilled_count", 0) + attrs.get("cancelled_count", 0)
    if attempts < min_attempts:
        return False
    return attrs.get("trust", 1.0) < trust_threshold


def open_products(kg, actor_permissions=..., actor_attributes=..., item_phrase: str | None = None):
    """Every purchasable product in kg, excluding anything sold by a closed
    store, a store the planner has LEARNED to avoid (Level 17), OR a store
    whose upstream supply chain can't actually deliver stock right now. A
    store with no is_open/warehouse_id attribute at all is treated as fine
    (existing seed data never set these keys — this is an opt-in
    dependency, not retroactively imposed on every store).

    Performance certification (GS-6000, "1M catalog items: indexed lookups
    only"): item_phrase is an OPTIONAL pre-filter. Without it, every ASSET
    in the graph runs through the full business-rule chain below — correct,
    but O(catalog size) per call, which is the caller's only option when it
    genuinely needs the whole open catalog (compare_plans, macro shopping
    list). A caller resolving ONE requested item (ProductSelectionCapability
    per-item loop, SocialSourcing's store-price check) can instead pass the
    phrase: the ASSET pool is narrowed to the KG's real keyword index before
    any of the expensive per-product checks run, then _matches_request is
    still applied exactly as before — the index is a pre-filter, not a
    replacement for the real match, so this can only ever narrow the
    candidate pool to a safe superset, never change WHICH products are
    ultimately considered eligible.

    Level 35 (GS-3503): actor_permissions defaults to the sentinel Ellipsis
    (not None) so "the caller didn't pass anything" is distinguishable
    from "the caller genuinely checked and there are no real permissions"
    — a specially-flagged resource (attributes["restricted_permission"])
    is excluded UNLESS the actor's real permission set grants it; a caller
    that doesn't pass actor_permissions at all gets the ORIGINAL,
    unrestricted behavior every existing call site already relies on
    (Level 15's simulate_plan, OrderConfirmation's replan, etc.), rather
    than every one of them needing to be updated at once just to keep
    working.

    GS-0502: a closed store must never be offered as a candidate — not
    during normal selection, and not as a replan target for some OTHER item
    that went stale.

    GS-1000/1001/1002: a store being OPEN doesn't mean it can actually
    FULFILL an order — the warehouse feeding it might be down, or every
    truck assigned to that warehouse might be broken down. Modeling that
    dependency here (rather than as separate logic elsewhere) means a
    warehouse outage or truck breakdown gets BOTH the initial-selection
    routing AND OrderConfirmationCapability's staleness-driven replan for
    free — both already call this function, unchanged, to build their
    candidate pool (see kernel/domains/grocery.py's Level 6 work).

    Level 17: a learned-avoided store is excluded the same way a closed
    store is — from every capability's candidate pool at once, since they
    all build their candidates from this one function — rather than each
    capability needing its own separate "is this store still viable"
    check.

    Level 21/22: a product with a self-reported quantity that contradicts
    an independent verification (GS-2101, compromised/fraudulent feed) is
    excluded outright, the same way a closed store is — trusting it would
    mean building a plan around stock that may not physically exist. A
    store marked "timeout" (GS-2201, a transient outage, not a real
    closure) still offers products the actor has a CACHED belief about
    (Level 13) — degraded but usable — and excludes only the ones nobody
    has any real data for.
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType
    all_orgs = {e.entity_id: e for e in kg.entities_by_type(EntityType.ORGANIZATION)}

    open_store_ids = {
        eid for eid, e in all_orgs.items()
        if e.attributes.get("is_open", True) is not False and not has_learned_to_avoid(e)
    }
    timed_out_store_ids = {eid for eid, e in all_orgs.items() if e.attributes.get("status") == "timeout"}

    def reachable(product):
        store_id = product.attributes.get("store_id")
        if store_id not in timed_out_store_ids:
            return True
        return get_belief(kg, product.entity_id) is not None

    def resource_authorized(product):
        if actor_permissions is ...:
            return True  # caller didn't opt into this check at all
        return authorize_resource_access(actor_permissions, product)["authorized"]

    def abac_authorized(product):
        # Level 41 (GS-4100/4101/4102): same opt-in-via-Ellipsis idiom as
        # resource_authorized above — a caller that never passes
        # actor_attributes at all gets the unrestricted behavior every
        # existing call site already relies on.
        if actor_attributes is ...:
            return True
        return authorize_abac_access(actor_attributes, product)["authorized"]

    # Level 36 (GS-3600): a household "category_ban" policy excludes every
    # product tagged with a banned category from EVERY candidate pool at
    # once (store society, product selection, replan) — opt-in via the
    # policy entity's real presence, not a function parameter, the same
    # idiom as is_open/robot_status/warehouse status above. A household
    # that has never set this policy (find_household_policy returns None)
    # sees exactly the unrestricted behavior every existing call site
    # already relies on.
    category_ban_policy = find_household_policy(kg, "category_ban")
    household_banned_categories = set(category_ban_policy.attributes.get("params", {}).get("categories", [])) \
        if category_ban_policy else set()

    def policy_allowed(product):
        category = product.attributes.get("category")
        # Level 48 (GS-4800/4802): constitutionally-protected categories
        # are banned UNCONDITIONALLY — no household policy needs to exist
        # for this to apply, and no household exception can ever carve
        # one out (grant_category_ban_exception refuses these too, but
        # this is the real enforcement floor, checked before and
        # independent of anything the household's own governance set).
        if category in _CONSTITUTIONAL_PROTECTED_CATEGORIES:
            return False
        if not household_banned_categories or category not in household_banned_categories:
            return True
        # Level 42 (GS-4201): a specific, owner-approved allow-exception
        # carves ONE named product out of an otherwise-blanket HOUSEHOLD
        # category ban — specific override beats general policy, without
        # weakening the ban for anything else still in that category.
        return kg.get_entity(f"policy_allow_exception_{product.entity_id}") is not None

    if item_phrase:
        request_kw = _keyword_surface_forms(_keywords(item_phrase))
    else:
        request_kw = None
    if request_kw:
        # A safe superset: every ASSET whose name shares at least one raw
        # token with the (surface-form-expanded) request. Falls back to the
        # full type scan below if the phrase had no extractable keywords at
        # all (_matches_request's own "nothing to match against" case).
        asset_pool = [e for e in kg.entities_by_keywords(request_kw) if e.entity_type == EntityType.ASSET]
    else:
        asset_pool = kg.entities_by_type(EntityType.ASSET)

    return [
        e for e in asset_pool
        if e.attributes.get("product") and e.attributes.get("store_id") in open_store_ids
        and supply_chain_ok(all_orgs, e.attributes.get("store_id"))
        and detect_inventory_inconsistency(kg, e.entity_id).get("consistent", True)
        and reachable(e)
        and resource_authorized(e)
        # Level 42 (GS-4200): deny-overrides / most-restrictive-wins is
        # this AND chain itself — an ABAC-authorized, RBAC-eligible
        # product is STILL excluded if the household's own category-ban
        # policy independently denies it; every real enforcement layer
        # must agree, not just one of them.
        and abac_authorized(e)
        and policy_allowed(e)
    ]


from src.monkey_brain.kernel.domains.supply_chain import (
    supply_chain_ok,
)


def try_reserve(kg, entity_id: str, actor_id: str, qty: int, hold_seconds: float = 5.0, max_attempts: int = 5) -> tuple[bool, str]:
    """Attempt to hold `qty` units of entity_id for actor_id for
    hold_seconds, without overselling under concurrent reservation attempts.

    GS-0600: two actors racing for the last unit both read quantity=1, both
    decide they can have it, both write quantity=0 — plain read-then-write
    has no way to notice the other write happened in between. Each attempt
    here reads current state, then commits via
    Neo4jBackedKnowledgeGraph.compare_and_swap keyed to the version it just
    read — if another actor's reservation landed first, the version has
    already moved, the swap is atomically rejected by Neo4j itself (not
    raced in this process), and the loop re-reads the now-current state
    (compare_and_swap refreshes the local cache on conflict) and retries.

    GS-0601: an existing reservation only counts against availability while
    unexpired — a reservation whose "until" has passed is free again,
    including for a second attempt by the SAME actor whose first
    reservation lapsed (a fresh reservation, not an extension).

    Reservations are stored as a LIST (attrs["reservations"]), one entry
    per actor currently holding a hold — not a single reserved_by/
    reserved_qty pair. A single-slot design passed Level 7's original test
    (2 actors racing for exactly 1 unit) because with only 1 unit there's
    never legitimately more than one concurrent holder anyway. Level 12's
    scale test (300 actors racing for 100 units) caught the real bug a
    single slot hides: each successful reservation OVERWROTE the previous
    one instead of adding to it, so "how much is currently held" only ever
    reflected the single most recent reservation — 151 of 300 concurrent
    claims "succeeded" against 100 real units. A list, summed over
    non-expired entries, is what actually tracks concurrent holders.
    """
    for _ in range(max_attempts):
        entity = kg.get_entity(entity_id)
        if entity is None:
            return False, "not found"
        attrs = entity.attributes
        now = time.time()
        active_reservations = [r for r in attrs.get("reservations", []) if r.get("until", 0) > now]
        held_qty = sum(r.get("qty", 0) for r in active_reservations)
        available = attrs.get("quantity", 0) - held_qty
        if available < qty:
            return False, f"insufficient stock: {available} available, {qty} requested"

        # A fresh reservation from this actor supersedes any prior one of
        # theirs (expired or not) rather than stacking a second entry.
        new_reservations = [r for r in active_reservations if r.get("actor_id") != actor_id]
        new_reservations.append({"actor_id": actor_id, "qty": qty, "until": now + hold_seconds})

        version = kg.version_of(entity_id)
        ok, _current = kg.compare_and_swap(entity_id, version, {"reservations": new_reservations})
        if ok:
            return True, f"reserved {qty} until {now + hold_seconds:.1f}"
        # Lost the race — compare_and_swap already refreshed the local
        # cache with what actually won, so the next loop iteration
        # re-evaluates against reality instead of a stale guess.
    return False, "too much contention, gave up"


def confirm_reservation(kg, entity_id: str, actor_id: str, max_attempts: int = 5) -> tuple[bool, str]:
    """Convert an active reservation held by actor_id into a real stock
    decrement. Fails if the reservation expired (GS-0601: the actor must
    call try_reserve again, not assume their old hold still counts) or was
    never held by this actor."""
    for _ in range(max_attempts):
        entity = kg.get_entity(entity_id)
        if entity is None:
            return False, "not found"
        attrs = entity.attributes
        reservations = attrs.get("reservations", [])
        mine = next((r for r in reservations if r.get("actor_id") == actor_id), None)
        if mine is None:
            return False, "no active reservation held by this actor"
        if mine.get("until", 0) < time.time():
            return False, "reservation expired — retry with try_reserve"

        qty = mine.get("qty", 0)
        remaining_reservations = [r for r in reservations if r.get("actor_id") != actor_id]
        version = kg.version_of(entity_id)
        # round(..., 2): a no-op for every existing integer-quantity caller
        # (inventory) but a real fix for Phase 5's shared-budget reuse of
        # this exact function -- float subtraction on dollar amounts (e.g.
        # 20.00 - 14.04) otherwise leaves genuine binary-float noise
        # (5.960000000000001) permanently persisted in a real money
        # ceiling, compounding further with every subsequent confirm.
        ok, _current = kg.compare_and_swap(entity_id, version, {
            "quantity": round(attrs.get("quantity", 0) - qty, 2),
            "reservations": remaining_reservations,
        })
        if ok:
            return True, f"confirmed, {qty} unit(s) committed"
    return False, "too much contention, gave up"


def release_reservation(kg, entity_id: str, actor_id: str, max_attempts: int = 5) -> tuple[bool, str]:
    """MB-3102 True Multi-Actor Coordination: explicitly release
    actor_id's held reservation on entity_id — the real reaction an
    Inventory Society's own actor takes when a PaymentDeclined event
    means the hold this reservation was for will never be confirmed.
    Same real CAS loop as try_reserve/confirm_reservation above (this
    module's existing reservation primitives), just removing the hold
    instead of granting or converting one — no new reservation model
    invented for this."""
    for _ in range(max_attempts):
        entity = kg.get_entity(entity_id)
        if entity is None:
            return False, "not found"
        attrs = entity.attributes
        reservations = attrs.get("reservations", [])
        if not any(r.get("actor_id") == actor_id for r in reservations):
            return False, "no active reservation held by this actor"
        remaining_reservations = [r for r in reservations if r.get("actor_id") != actor_id]
        version = kg.version_of(entity_id)
        ok, _current = kg.compare_and_swap(entity_id, version, {"reservations": remaining_reservations})
        if ok:
            return True, "released"
    return False, "too much contention, gave up"


def ensure_shared_budget_from_question(context: dict) -> None:
    """Qualification Gap Closure, Phase 9: the real prompt-to-mechanism
    wiring Phase 5's shared budget was missing — a real live prompt
    (e.g. "Agent A buys milk while Agent B buys pizza, shared $20
    budget") has no pre-existing budget entity id to reference, unlike
    a per-order stated budget (parse_budget, read directly off
    context["question"] by OrderCreationCapability itself). Called once
    per tick, before any step runs (ActionExecutor.execute's new
    pre_execute_hook, the same injection point context_projector/
    domain_event_resolver already use to keep the executor itself
    domain-agnostic) — whichever step needs it first (the asker's own
    purchase, or a DelegateTask to another actor) finds
    context["shared_budget_id"] already set, regardless of plan step
    order. Idempotent: a context that already carries a real
    shared_budget_id (e.g. a resumed/retried tick) is never touched
    again, so exactly one real entity is created per tick."""
    if context.get("shared_budget_id"):
        return
    kg = context.get("knowledge_graph")
    if kg is None:
        return
    ceiling = parse_shared_budget(context.get("question", ""))
    if ceiling is None:
        return
    context["shared_budget_id"] = create_shared_budget(kg, ceiling, owner_ids=(context.get("actor_id", ""),))


def create_shared_budget(kg, ceiling: float, owner_ids: tuple[str, ...] = ()) -> str:
    """A shared, concurrently-claimed spending ceiling multiple actors draw
    against together (Qualification Gap Closure, Phase 5) — deliberately
    NOT a new concurrency primitive. try_reserve/confirm_reservation/
    release_reservation (this module, directly above) already give
    inventory a real, proven no-oversell guarantee under concurrent
    contention, keyed off nothing more specific than an entity's real
    "quantity" attribute and a "reservations" list — neither assumes
    "quantity" means physical units, so a real KG entity whose "quantity"
    is a dollar ceiling reserves/confirms/releases through the EXACT same
    three functions, unmodified. owner_ids is descriptive only (which
    actors this budget was created for) — try_reserve itself enforces
    nothing about who may call it, the same way it doesn't check who may
    reserve inventory."""
    import uuid
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    budget_id = f"budget_{uuid.uuid4().hex}"
    kg.add_entity(budget_id, EntityType.ACCOUNT, "Shared Budget", {
        "quantity": ceiling, "reservations": [], "owner_ids": list(owner_ids),
    })
    return budget_id


def place_backorder(kg, product_id: str, actor_id: str, qty: int, now: float | None = None) -> dict:
    """MB-3016 Inventory Conflict: record a queued claim on product_id
    for actor_id — the losing side of a try_reserve race isn't simply
    declined, it's "backordered": a real, persisted spot in line, filled
    later (FIFO) by fulfill_backorders() once stock increases. Does not
    itself reserve or charge anything, and never touches try_reserve's
    own reservations list — a backorder is a queued FUTURE claim, not a
    current hold on stock that doesn't exist yet."""
    from src.monkey_brain.kernel.knowledge_graph import EntityType
    import uuid

    now = now if now is not None else time.time()
    backorder_id = f"backorder_{uuid.uuid4().hex}"
    kg.add_entity(backorder_id, EntityType.OTHER, f"Backorder: {product_id}", {
        "backorder": True, "product_id": product_id, "actor_id": actor_id,
        "qty": qty, "created_at": now, "status": "pending",
    })
    return {"backorder_id": backorder_id, "product_id": product_id,
            "actor_id": actor_id, "qty": qty, "status": "pending"}


def fulfill_backorders(kg, product_id: str, max_attempts: int = 5) -> list[dict]:
    """Grant reservations to the OLDEST pending backorders for
    product_id first (FIFO) — call this whenever product_id's quantity
    increases (a restock). Uses the SAME try_reserve() CAS mechanism
    every other reservation goes through, not a second, parallel
    allocation system: a fulfilled backorder becomes a REAL, normal
    reservation the customer still needs to confirm/pay for through the
    ordinary checkout flow, exactly like a reservation made directly.

    Strictly FIFO: stops at the first pending backorder that still
    doesn't fit in whatever stock remains, rather than skipping ahead to
    fill a smaller LATER request first — fairness by arrival order, not
    by whichever request happens to fit. A backorder that can't be
    filled yet stays "pending" and is retried the next time this runs.

    Returns the list of backorders actually fulfilled this call, oldest
    first — empty if nothing could be filled (no pending backorders, or
    even the oldest one doesn't fit)."""
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    kg.refresh()
    pending = sorted(
        (e for e in kg.entities
         if e.entity_type == EntityType.OTHER and e.attributes.get("backorder")
         and e.attributes.get("product_id") == product_id and e.attributes.get("status") == "pending"),
        key=lambda e: e.attributes.get("created_at", 0),
    )

    fulfilled = []
    for backorder in pending:
        ok, _msg = try_reserve(
            kg, product_id, backorder.attributes["actor_id"],
            backorder.attributes["qty"], max_attempts=max_attempts,
        )
        if not ok:
            break
        kg.update_entity(backorder.entity_id, attributes={
            "status": "fulfilled", "fulfilled_at": time.time(),
        })
        fulfilled.append({
            "backorder_id": backorder.entity_id, "product_id": product_id,
            "actor_id": backorder.attributes["actor_id"], "qty": backorder.attributes["qty"],
        })
    return fulfilled


def allocate_fair_share(kg, entity_id: str, claims: list[tuple[str, int]], max_attempts: int = 5) -> dict:
    """GS-0900: neighborhood shortage — divide available stock fairly among
    everyone who wants it instead of first-come-first-served draining it
    for whoever asks first (which Level 7's try_reserve alone would do —
    the first caller to win the CAS just gets everything up to their
    request, with nothing held back for the next claimant).

    If there's enough for everyone, everyone gets exactly what they asked
    for. Otherwise this uses water-filling: each round, everyone still
    wanting more gets an equal share of what's left (bounded by their own
    remaining need), so a claimant who asked for a huge amount doesn't
    starve everyone else, and a claimant who needed less than an equal
    share doesn't get more than they asked for — the leftover from that
    gets redistributed to those still wanting more.

    Commits the FULL allocation in one compare_and_swap — either every
    actor's share is applied to the shared stock counter atomically, or
    (on conflict) the whole computation is redone against fresh state and
    retried, so nobody's share is computed against stock that's already
    stale by the time it's applied.
    """
    for _ in range(max_attempts):
        entity = kg.get_entity(entity_id)
        if entity is None:
            return {}
        available = entity.attributes.get("quantity", 0)
        allocation = _water_fill_allocation(claims, available)

        total_allocated = sum(allocation.values())
        version = kg.version_of(entity_id)
        ok, _current = kg.compare_and_swap(entity_id, version, {"quantity": available - total_allocated})
        if ok:
            return allocation
    return {}


def _water_fill_allocation(claims: list[tuple[str, int]], available: int) -> dict:
    """The water-filling MATH behind allocate_fair_share, extracted so
    Level 29's ethical allocator can reuse it for a single TIER of
    equal-priority claimants rather than the whole pool — same algorithm,
    same guarantees (enough for everyone means everyone gets their exact
    ask; otherwise nobody's leftover need is dropped and nobody hoards),
    just without the CAS write itself so it composes into a caller that
    also needs priority-tiering and rationing caps in the SAME commit.
    """
    total_requested = sum(qty for _, qty in claims)
    if total_requested <= available:
        return {actor: qty for actor, qty in claims}
    remaining = {actor: qty for actor, qty in claims}
    allocation = {actor: 0 for actor, _ in claims}
    pool = available
    active = [actor for actor, qty in claims if qty > 0]
    while pool > 0 and active:
        share = max(1, pool // len(active))
        for actor in list(active):
            give = min(share, remaining[actor], pool)
            allocation[actor] += give
            remaining[actor] -= give
            pool -= give
            if remaining[actor] <= 0:
                active.remove(actor)
            if pool <= 0:
                break
    return allocation


def allocate_by_priority(kg, entity_id: str, claims: list[tuple[str, int, int]], max_attempts: int = 5) -> dict:
    """GS-0902: food bank distribution — fulfill higher-priority claimants
    FIRST (lower priority number served first: 0=infants, 1=elderly,
    2=families, matching the exact ordering the scenario specifies), not
    fairly and not by request order. Lower-priority claimants get whatever
    remains, which can be a partial amount or zero — the ethical ordering
    is an explicit input (the priority in each claim), not something this
    function infers.
    """
    for _ in range(max_attempts):
        entity = kg.get_entity(entity_id)
        if entity is None:
            return {}
        available = entity.attributes.get("quantity", 0)
        ordered = sorted(claims, key=lambda c: c[2])
        allocation = {}
        pool = available
        for actor, qty, _priority in ordered:
            give = min(qty, pool)
            allocation[actor] = give
            pool -= give

        total_allocated = sum(allocation.values())
        version = kg.version_of(entity_id)
        ok, _current = kg.compare_and_swap(entity_id, version, {"quantity": available - total_allocated})
        if ok:
            return allocation
    return {}


_INFANT_CRITICAL_KEYWORDS = {"formula", "diaper", "diapers"}
_INFANT_AGE_MONTHS_THRESHOLD = 24


def is_infant_critical(product) -> bool:
    """Level 29 (GS-2900): whether a product is infant-critical — either
    explicitly tagged (attributes["infant_critical"]) or named as one of
    the well-known infant-critical categories. Checked on the product's
    real name/attributes, not on how the request happened to be phrased —
    ethical priority follows from WHAT is being allocated, and must not
    be gameable by wording a request a certain way.
    """
    if product.attributes.get("infant_critical") is True:
        return True
    return bool(_keywords(product.name) & _INFANT_CRITICAL_KEYWORDS)


def household_has_infant(kg) -> bool:
    """Level 29 (GS-2900): whether this household has a REAL infant member
    (attributes["has_infant"] on any entity, or a PERSON entity with
    age_months at or below the infant threshold) — a genuine household
    fact read from the KG, not self-reported in the request text, which
    would let anyone claim priority just by asking for it.
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType
    for e in kg.entities:
        if e.attributes.get("has_infant") is True:
            return True
        if e.entity_type == EntityType.PERSON and e.attributes.get("age_months") is not None \
                and e.attributes.get("age_months") <= _INFANT_AGE_MONTHS_THRESHOLD:
            return True
    return False


def derive_ethical_priority(kg, product) -> tuple[int, str]:
    """Level 29 (GS-2900): derives a real allocation priority from actual
    household composition and the product's own category — 0 (most
    urgent) only when BOTH the household genuinely has an infant AND the
    product is infant-critical; a neutral default (2, matching Level 10's
    GS-0902 "families" tier) otherwise. Deliberately narrow: a household
    with an infant does not get priority on everything, only on what an
    infant actually needs — allocate_by_priority (Level 10) always took
    this priority number as a given input; this is what derives it from
    real facts instead of requiring the caller to already know the
    ethical answer.
    """
    if is_infant_critical(product) and household_has_infant(kg):
        return 0, "infant-critical need"
    return 2, "standard priority"


_DEFAULT_RATIONING_WINDOW_HOURS = 24.0


def household_recent_purchases(kg, product_id: str, window_hours: float = _DEFAULT_RATIONING_WINDOW_HOURS) -> int:
    """Level 29 (GS-2901): real cumulative quantity this household has
    ALREADY bought of product_id within the rationing window, summed from
    actual order history — a household can't evade a per-household cap by
    splitting into several smaller orders, since each one adds to the
    same real running total.
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType
    kg.refresh()
    cutoff = time.time() - window_hours * 3600
    total = 0
    for e in kg.entities_by_type(EntityType.EVENT):
        if not e.attributes.get("order_id"):
            continue
        order_ts = e.attributes.get("created_at", e.created_at)
        if order_ts < cutoff:
            continue
        for item in e.attributes.get("items", []):
            if item.get("product_id") == product_id:
                total += item.get("qty", 0)
    return total


def apply_rationing_cap(kg, product_id: str, requested_qty: int, ration_limit: int,
                         window_hours: float = _DEFAULT_RATIONING_WINDOW_HOURS) -> dict:
    """Level 29 (GS-2901): during an active emergency ration, caps how
    much MORE this household may buy right now given what it's already
    bought recently — a hard ethical constraint (no single household may
    buy out a declared shortage), not a soft preference, and grounded in
    the household's REAL recent purchases rather than trusting this one
    request's honesty about what it already has.
    """
    already = household_recent_purchases(kg, product_id, window_hours)
    remaining_allowance = max(0, ration_limit - already)
    allowed_qty = min(requested_qty, remaining_allowance)
    return {
        "allowed_qty": allowed_qty, "requested_qty": requested_qty,
        "already_purchased": already, "ration_limit": ration_limit,
        "capped": allowed_qty < requested_qty,
    }


def allocate_ethically(shared_kg, entity_id: str, claims: list, max_attempts: int = 5) -> dict:
    """Level 29 (GS-2900/2901/2902): the full ethical allocation for one
    scarce resource — combines automatically-DERIVED priority
    (GS-2900: infant-critical need, not a manually-assigned number a
    caller has to already know), a real per-household rationing cap
    (GS-2901), and fair-share water-filling for whoever's left at equal
    priority (GS-2902: FAIR distribution within a tier, not first-come —
    plain allocate_by_priority fulfills same-priority claims in whatever
    order they were listed, which is not the same guarantee). One real,
    composed decision, not three independent mechanisms that happen to
    share a domain.

    claims: (actor_id, requested_qty, actor_kg) — actor_kg is that
    claimant's OWN KG instance, needed to check real per-household facts
    (does THIS household actually have an infant, has THIS household
    already bought some during the active ration window) that only their
    own KG can answer. shared_kg is the resource's own scope, used for
    the actual stock read/write.
    """
    for _ in range(max_attempts):
        product = shared_kg.get_entity(entity_id)
        if product is None:
            return {"allocation": {}, "details": {}}
        available = product.attributes.get("quantity", 0)
        rationed = product.attributes.get("rationed") is True
        ration_limit = product.attributes.get("ration_limit_per_household")

        prioritized = []
        details = {}
        for actor_id, qty, actor_kg in claims:
            allowed_qty = qty
            details[actor_id] = {}
            if rationed and ration_limit is not None:
                cap_info = apply_rationing_cap(actor_kg, entity_id, qty, ration_limit)
                allowed_qty = cap_info["allowed_qty"]
                details[actor_id]["rationing"] = cap_info
            priority, reason = derive_ethical_priority(actor_kg, product)
            details[actor_id]["priority"] = priority
            details[actor_id]["priority_reason"] = reason
            prioritized.append((actor_id, allowed_qty, priority))

        allocation = {}
        pool = available
        for priority_level in sorted({p for _, _, p in prioritized}):
            tier_claims = [(a, q) for a, q, p in prioritized if p == priority_level]
            tier_allocation = _water_fill_allocation(tier_claims, pool)
            allocation.update(tier_allocation)
            pool -= sum(tier_allocation.values())

        total_allocated = sum(allocation.values())
        version = shared_kg.version_of(entity_id)
        ok, _current = shared_kg.compare_and_swap(entity_id, version, {"quantity": available - total_allocated})
        if ok:
            return {"allocation": allocation, "details": details}
    return {"allocation": {}, "details": {}}


def pool_bulk_order(kg, entity_id: str, claims: list[tuple[str, int]], bulk_threshold: int,
                     bulk_discount: float, max_attempts: int = 5) -> dict | None:
    """GS-0901: community bulk purchase — pool several actors' individual
    orders into ONE purchase. Reaching bulk_threshold total units unlocks
    bulk_discount off the per-unit price for EVERYONE in the pool — buying
    power none of them could reach placing separate orders — and the whole
    pool commits as a single stock decrement instead of N independent ones.
    Same underlying idea as GS-0301's per-cart store consolidation (fewer,
    bigger transactions beat many small ones), applied across actors
    instead of across items in one cart.
    """
    for _ in range(max_attempts):
        entity = kg.get_entity(entity_id)
        if entity is None:
            return None
        available = entity.attributes.get("quantity", 0)
        total_units = sum(qty for _, qty in claims)
        if total_units > available:
            return {"success": False, "error": f"insufficient stock: {available} available, {total_units} requested by the pool"}

        unit_price = entity.attributes.get("price", 0)
        discounted = total_units >= bulk_threshold
        effective_price = round(unit_price * (1 - bulk_discount), 2) if discounted else unit_price
        shares = {actor: {"units": qty, "cost": round(qty * effective_price, 2)} for actor, qty in claims}

        version = kg.version_of(entity_id)
        ok, _current = kg.compare_and_swap(entity_id, version, {"quantity": available - total_units})
        if ok:
            return {
                "success": True,
                "total_units": total_units,
                "unit_price": unit_price,
                "effective_price": effective_price,
                "bulk_discount_applied": discounted,
                "total_cost": round(total_units * effective_price, 2),
                "shares": shares,
            }
    return None


def eligible_candidates(item_phrase: str, all_products, lactose_free: bool, rejected_keywords: frozenset = frozenset(),
                         low_sodium: bool = False, excluded_allergens: frozenset = frozenset()):
    """The candidate pool a single requested item resolves to: dietary
    filter, then learned-rejection filter, then either substitution-policy
    type resolution (generic request for a policy-covered item, e.g.
    "milk") or specificity narrowing + stock-driven substitution (explicit
    type, or no policy for this category). Returns (candidates, note) on
    success — note is a substitution explanation, "" if nothing was
    substituted — or (None, error_message) when nothing satisfies the
    request.

    ProductSelectionCapability's per-item loop AND its store-consolidation
    comparison both call this — they used to resolve the same phrase
    independently and could disagree: consolidation had no notion of the
    substitution policy at all, so a generic "milk" request (reduced-fat
    out of stock everywhere, policy says fall back to whole) could get
    silently consolidated to Skim Milk instead — cheaper, but never a
    candidate under the policy the non-consolidated plan was honoring.

    Level 24: low_sodium and excluded_allergens are hard filters, same
    tier as lactose_free — a stated dietary restriction or allergy must
    never be satisfied by a product that violates it, regardless of
    price/quality/stock. excluded_allergens fails CLOSED: a product with
    no attributes["allergens"] data at all is treated as UNSAFE, not
    assumed clear — a missing allergen label is not the same as a
    verified allergen-free product, and a real allergy is not something
    to guess about.
    """
    candidates = [p for p in all_products if _matches_request(p.name, item_phrase)]
    if not candidates:
        return None, f"no products matching request: {item_phrase!r}"

    if lactose_free:
        candidates = [c for c in candidates if c.attributes.get("lactose_free") is True]
        if not candidates:
            return None, f"no lactose-free products matching {item_phrase!r}"

    if low_sodium:
        candidates = [c for c in candidates if c.attributes.get("low_sodium") is True]
        if not candidates:
            return None, f"no low-sodium products matching {item_phrase!r}"

    if excluded_allergens:
        safe = [c for c in candidates if c.attributes.get("allergens") is not None
                and not (set(c.attributes.get("allergens", [])) & excluded_allergens)]
        if not safe:
            return None, (f"no products matching {item_phrase!r} verified free of "
                           f"{', '.join(sorted(excluded_allergens))} — refusing to guess on an allergy")
        candidates = safe

    base = _base_item(item_phrase)
    policy = _SUBSTITUTION_POLICIES.get(base) if base else None
    phrase_kws = _keywords(item_phrase)
    explicit_type = bool(policy) and any(t in phrase_kws for t in policy)

    # GS-0800: a learned rejection only suppresses IMPLICIT/default choices
    # ("buy milk" quietly skipping almond after alice has rejected it
    # repeatedly) — it never overrides an EXPLICIT direct request ("buy
    # almond milk" still gets almond milk; that's what was actually asked
    # for this time, not a default the system is choosing on her behalf).
    # And it never wins over a hard constraint: if rejection-filtering would
    # leave nothing at all (e.g. lactose-free + rejects almond and oat is
    # out of stock), fall back to the unfiltered set rather than turning a
    # soft learned preference into a request the system can't fulfill.
    if not explicit_type and rejected_keywords:
        non_rejected = [c for c in candidates if not (_keywords(c.name) & rejected_keywords)]
        if non_rejected:
            candidates = non_rejected

    if policy and not explicit_type and not lactose_free:
        for i, type_kw in enumerate(policy):
            type_candidates = [c for c in candidates
                                if type_kw in _keywords(c.name) and c.attributes.get("quantity", 1) != 0]
            if type_candidates:
                note = f"{policy[0]} milk out of stock, substituted {type_kw} per policy — " if i > 0 else ""
                return type_candidates, note
        return None, f"no in-stock {base} available (tried: {', '.join(policy)})"

    # Product Substitution: an EXPLICIT type request ("reduced fat milk")
    # whose specific type isn't available must still honor the documented
    # substitution PREFERENCE ORDER (reduced -> whole -> oat -> almond),
    # not fall through to generic match-score/cost ranking among whatever
    # else happens to share the "milk" keyword. Without this, "reduced
    # fat milk" with no reduced-fat in stock but both whole and oat
    # available tied on match score (both only share "milk" with the
    # request) and cost ranking silently picked cheaper oat over the
    # policy's preferred whole — semantically wrong regardless of price.
    if policy and explicit_type:
        asked_type = next((t for t in policy if t in phrase_kws), None)
        asked_candidates = [c for c in candidates
                             if asked_type in _keywords(c.name) and c.attributes.get("quantity", 1) != 0]
        if asked_candidates:
            return asked_candidates, ""
        for type_kw in policy:
            if type_kw == asked_type:
                continue
            type_candidates = [c for c in candidates
                                if type_kw in _keywords(c.name) and c.attributes.get("quantity", 1) != 0]
            if type_candidates:
                return type_candidates, f"{item_phrase!r} out of stock, substituted {type_kw} per policy — "
        return None, f"{item_phrase!r} out of stock, no substitute available"

    best_score = max(_match_score(c.name, item_phrase) for c in candidates)
    specific = [c for c in candidates if _match_score(c.name, item_phrase) == best_score]
    in_stock = [c for c in specific if c.attributes.get("quantity", 1) != 0]
    if not in_stock and policy:
        for type_kw in policy:
            type_candidates = [c for c in candidates
                                if type_kw in _keywords(c.name) and c.attributes.get("quantity", 1) != 0]
            if type_candidates:
                return type_candidates, f"{item_phrase!r} out of stock, substituted {type_kw} per policy — "
    if not in_stock:
        return None, f"{item_phrase!r} out of stock, no substitute available"
    return in_stock, ""


_BELIEF_TRACKED_ATTRS = ("quantity", "price", "is_open", "status")


def _belief_id(entity_id: str) -> str:
    return f"belief:{entity_id}"


def form_belief(kg, entity_id: str, tracked_attrs=_BELIEF_TRACKED_ATTRS,
                 source: str = "direct_observation", trust: float = 1.0) -> dict | None:
    """GS-1200: the actor observes ground truth right now and records what it
    BELIEVES as a distinct entity in its own private scope (belief:{entity_id}),
    separate from the shared ground-truth entity. This is what makes belief
    divergence detectable later: the belief is a snapshot that can go stale,
    not a live read of the world.

    kg.refresh() first so "observe" actually means observe — without it this
    would just re-describe whatever this KG instance happened to hydrate with
    at construction time, the same staleness bug Level 6 fixed for replanning.
    """
    kg.refresh()
    truth = kg.get_entity(entity_id)
    if truth is None:
        return None
    believed = {k: truth.attributes.get(k) for k in tracked_attrs if k in truth.attributes}
    belief_id = _belief_id(entity_id)
    attrs = {
        "target_entity_id": entity_id,
        "believed_attrs": believed,
        "source": source,
        "trust": trust,
        "observed_at": time.time(),
        "observed_version": kg.version_of(entity_id),
    }
    if kg.get_entity(belief_id) is None:
        from src.monkey_brain.kernel.knowledge_graph import EntityType
        kg.add_entity(belief_id, EntityType.OTHER, f"belief about {entity_id}", attrs)
    else:
        kg.update_entity(belief_id, attributes=attrs)
    return believed


def get_belief(kg, entity_id: str) -> dict | None:
    """What the actor currently believes about entity_id, or None if it has
    never observed it. Reads the actor's own belief record, NOT ground
    truth — this is the function an "explain your belief" query would call."""
    b = kg.get_entity(_belief_id(entity_id))
    return b.attributes.get("believed_attrs") if b else None


def detect_belief_divergence(kg, entity_id: str, tracked_attrs=_BELIEF_TRACKED_ATTRS) -> dict:
    """GS-1200: compare the actor's STANDING belief (formed at some earlier
    point, possibly long before this call) against CURRENT ground truth.
    Returns {attr: (believed, actual)} for every attribute that no longer
    matches — empty dict means no belief formed yet, or belief still holds.

    This is the "Observe -> compare -> [caller decides to Update Belief and
    Replan]" step; it only detects divergence, it doesn't resolve it — that's
    left to the caller (e.g. re-run form_belief, then replan the order) so
    detection and action stay separately testable.
    """
    belief_entity = kg.get_entity(_belief_id(entity_id))
    if belief_entity is None:
        return {}
    believed_attrs = belief_entity.attributes.get("believed_attrs", {})
    kg.refresh()
    truth = kg.get_entity(entity_id)
    if truth is None:
        return {}
    diffs = {}
    for k in tracked_attrs:
        if k in believed_attrs and believed_attrs[k] != truth.attributes.get(k):
            diffs[k] = (believed_attrs[k], truth.attributes.get(k))
    return diffs


def reconcile_beliefs(kg, entity_id: str, observations: list[dict],
                       tracked_attrs=_BELIEF_TRACKED_ATTRS) -> tuple[dict, dict]:
    """GS-1201: multiple sources disagree about the same entity (e.g. a store
    API says available, an inventory scanner says sold out). Each observation
    is {"source": str, "trust": float, "attrs": dict, "observed_at": float}.

    Per attribute, the highest-trust source wins; a tie is broken by the most
    recent observed_at. This is a per-attribute resolution, not "pick one
    source's whole record" — two sources can each win on different fields.

    Returns (resolved_attrs, attribution) where attribution maps each
    resolved attribute to which source's value was kept, so the reconciled
    belief remains explainable (GS-1600-style "why do you believe this").
    """
    winners: dict[str, dict] = {}
    resolved: dict[str, Any] = {}
    for obs in observations:
        for k in tracked_attrs:
            if k not in obs["attrs"]:
                continue
            cur = winners.get(k)
            if cur is None or obs["trust"] > cur["trust"] or (
                obs["trust"] == cur["trust"] and obs["observed_at"] > cur["observed_at"]
            ):
                winners[k] = obs
                resolved[k] = obs["attrs"][k]
    attribution = {k: winners[k]["source"] for k in resolved}
    belief_id = _belief_id(entity_id)
    attrs = {
        "target_entity_id": entity_id,
        "believed_attrs": resolved,
        "source": "reconciled",
        "trust": max((o["trust"] for o in observations), default=0.0),
        "observed_at": time.time(),
        "attribution": attribution,
    }
    if kg.get_entity(belief_id) is None:
        from src.monkey_brain.kernel.knowledge_graph import EntityType
        kg.add_entity(belief_id, EntityType.OTHER, f"belief about {entity_id}", attrs)
    else:
        kg.update_entity(belief_id, attributes=attrs)
    return resolved, attribution


def refresh_belief_before_action(kg, entity_id: str, tracked_attrs=_BELIEF_TRACKED_ATTRS) -> tuple[bool, dict]:
    """GS-1202: called immediately before executing an action that depends on
    entity_id. A planner may have formed its belief seconds or minutes ago;
    if the ground-truth entity's version has advanced since then (a new
    observation landed — another actor's write, a stock update, a price
    change), the belief is stale relative to what's about to be acted on and
    must be refreshed BEFORE acting, not after.

    Returns (updated, diffs): updated is True if the belief was stale and has
    now been refreshed; diffs is whatever detect_belief_divergence found
    right before the refresh (empty if there was no prior belief to diverge
    from). Compares Neo4j's real version counter, not wall-clock staleness —
    a version bump is the actual signal a write happened; time alone can't
    tell "nothing changed" from "changed a microsecond after I last looked."
    """
    belief_entity = kg.get_entity(_belief_id(entity_id))
    kg.refresh()
    truth = kg.get_entity(entity_id)
    if truth is None:
        return False, {}
    current_version = kg.version_of(entity_id)
    if belief_entity is None or belief_entity.attributes.get("observed_version") != current_version:
        diffs = detect_belief_divergence(kg, entity_id, tracked_attrs) if belief_entity else {}
        form_belief(kg, entity_id, tracked_attrs, source="pre_action_refresh", trust=1.0)
        return True, diffs
    return False, {}


_ORDER_STATS_RETENTION_DAYS = 120.0  # generous upper bound covering both
# predict_demand's (30d) and learned_preference's (90d) lookback windows --
# buckets older than this are pruned at write time so the aggregate never
# grows with total lifetime order count.


def _day_bucket(ts: float) -> str:
    return str(int(ts // 86400))


def _bump_daily_bucket(buckets: dict, ts: float, amount: float,
                        retention_days: float = _ORDER_STATS_RETENTION_DAYS) -> None:
    """Add amount to ts's day bucket, in place, then prune anything older
    than retention_days -- keeps the dict's size bounded by the retention
    window (a small, fixed number of days) regardless of how many orders
    have ever been written to it."""
    bucket = _day_bucket(ts)
    buckets[bucket] = buckets.get(bucket, 0) + amount
    cutoff = int(ts // 86400) - int(retention_days)
    for stale in [b for b in buckets if int(b) < cutoff]:
        del buckets[stale]


def _sum_recent_buckets(buckets: dict, now: float, lookback_days: float) -> dict:
    """{day_bucket: value} filtered to the lookback window -- callers that
    need per-day values (predict_demand's moving average) get the filtered
    dict; sum(....values()) gives a plain total."""
    cutoff = int(now // 86400) - int(lookback_days)
    return {b: v for b, v in buckets.items() if int(b) >= cutoff}


def _order_stats_id(actor_id: str) -> str:
    return f"order_stats_{actor_id}"


def update_order_stats(kg, actor_id: str, items: list[dict], ts: float, max_attempts: int = 20) -> bool:
    """Phase 6 performance certification (GS-6000, "100K execution ticks:
    no unbounded latency increase"): predict_demand and learned_preference
    used to re-scan an actor's ENTIRE order history on every call — real,
    measured linear growth with lifetime order count (0.1ms at 100 orders
    -> 8.6ms at 50,000), unbounded over a "personal lifelong runtime"
    actor's lifetime, even after both gained a lookback_days window (the
    EXPENSIVE per-order work became bounded, but the cheap "check this
    order's timestamp and skip it" still touched every order ever made).

    Real fix: OrderCreationCapability calls this once per order (here,
    not in the hot read path) to incrementally update a single persisted
    per-actor aggregate entity — day-bucketed quantities per product (for
    predict_demand) and day-bucketed counts per (base_item, attribute,
    value) (for learned_preference) — so both reads become O(days in the
    retention window), never O(orders ever placed). items is the same
    per-line-item list OrderCreationCapability already builds for the
    order entity itself (each with product_id/qty/brand/type_keyword).
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType
    entity_id = _order_stats_id(actor_id)
    for _ in range(max_attempts):
        existing = kg.get_entity(entity_id)
        if existing is None:
            stats = {"daily_qty": {}, "pref_counts": {}}
        else:
            # Deep-copy so a losing CAS attempt's mutations never leak
            # into the next retry's "current" read.
            import copy
            stats = copy.deepcopy(existing.attributes)
            stats.setdefault("daily_qty", {})
            stats.setdefault("pref_counts", {})

        for item in items:
            product_id = item.get("product_id")
            qty = item.get("qty", 0)
            if product_id and qty:
                product_buckets = stats["daily_qty"].setdefault(product_id, {})
                _bump_daily_bucket(product_buckets, ts, qty)

            base = _base_item(item.get("product_name", ""))
            if base:
                base_stats = stats["pref_counts"].setdefault(base, {})
                for attribute, value in (("brand", item.get("brand")), ("type_keyword", item.get("type_keyword"))):
                    if not value:
                        continue
                    value_buckets = base_stats.setdefault(attribute, {}).setdefault(value, {})
                    _bump_daily_bucket(value_buckets, ts, 1)

        if existing is None:
            kg.add_entity(entity_id, EntityType.OTHER, f"Order stats: {actor_id}", stats)
            return True
        version = kg.version_of(entity_id)
        ok, _ = kg.compare_and_swap(entity_id, version, stats)
        if ok:
            return True
    return False


def predict_demand(kg, product_id: str, lookback_days: float = 30.0, actor_id: str | None = None) -> dict:
    """GS-1300/Phase 5 stress (GS-5100): forecast near-term demand for
    product_id from the actor's OWN purchase history — real persisted
    Order EVENT entities (OrderCreationCapability already writes one per
    checkout, each carrying created_at and per-item {product_id, qty}),
    not a separate logging system invented just for this. Bucketed by
    calendar day, then averaged across however many days in the lookback
    window actually had a purchase — a simple moving average, not a
    trend model, but real data beats a fabricated one for a first
    prediction primitive.

    Caught live under real concurrency at scale (Phase 5 stress test,
    dozens of unrelated actors sharing one KG): this scanned EVERY
    order in the whole graph with no actor scoping at all, despite its
    own docstring's claim — one actor's demand prediction silently
    absorbed every OTHER actor's concurrent purchases as if they were
    its own history, inflating the daily rate (and therefore
    recommend_reorder_quantity's buffer) far past what that actor
    itself ever bought, causing real orders to request far more stock
    than was actually wanted or available. actor_id scopes to orders
    THIS actor placed (attributes["buyer_id"], the same field
    OrderCreationCapability's own purchase_log marker already uses) —
    actor_id=None preserves the original unscoped behavior for any
    caller that hasn't threaded an actor_id through yet.

    Performance certification (GS-6000): with actor_id given, reads the
    incremental per-actor aggregate update_order_stats() maintains
    (O(days in the retention window), never O(orders ever placed) — see
    its own docstring for the measured unbounded-growth this replaces).
    actor_id=None has no aggregate to key on (it means "every actor
    sharing this KG", which is what the ORIGINAL unscoped bug scanned by
    accident) — kept as the old full scan since it's a rare, non-hot-path
    caller (predict_household_stockout), not the per-tick path that
    actually needs to be fast at scale.

    Returns {"predicted_daily_demand", "confidence", "history_days",
    "basis"}. confidence grows with more observed days but is capped —
    this predictor has no way to earn certainty past "some purchase
    history exists", it isn't modeling seasonality or trend.
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType
    kg.refresh()
    now = time.time()
    daily: dict[int, int] = {}

    if actor_id is not None:
        stats = kg.get_entity(_order_stats_id(actor_id))
        product_buckets = stats.attributes.get("daily_qty", {}).get(product_id, {}) if stats else {}
        daily = {int(b): v for b, v in _sum_recent_buckets(product_buckets, now, lookback_days).items()}
    else:
        for e in kg.entities_by_type(EntityType.EVENT):
            if not e.attributes.get("order_id"):
                continue
            # attributes["created_at"], not e.created_at: the Entity dataclass
            # field is never persisted to Neo4j (see OrderCreationCapability),
            # so it silently resets to hydration time on every fresh KG
            # instance. Falling back to e.created_at only covers an
            # in-memory-only KG (tests), where the field is reliable.
            order_ts = e.attributes.get("created_at", e.created_at)
            age_days = (now - order_ts) / 86400.0
            if age_days > lookback_days:
                continue
            for item in e.attributes.get("items", []):
                if item.get("product_id") != product_id:
                    continue
                bucket = int(order_ts // 86400)
                daily[bucket] = daily.get(bucket, 0) + item.get("qty", 0)

    if not daily:
        return {"predicted_daily_demand": 0.0, "confidence": 0.0, "history_days": 0,
                "basis": "no purchase history for this product"}

    values = list(daily.values())
    avg = sum(values) / len(values)
    confidence = min(0.9, 0.3 + 0.1 * len(values))
    return {
        "predicted_daily_demand": round(avg, 3),
        "confidence": round(confidence, 2),
        "history_days": len(values),
        "basis": f"moving average over {len(values)} day(s) with a recorded purchase",
    }


def predict_stockout(kg, product_id: str, lookback_days: float = 30.0, urgency_days: float = 2.0,
                      actor_id: str | None = None) -> dict:
    """GS-1301: given the current quantity and predict_demand's daily rate,
    estimate days-until-stockout and flag urgency. days_remaining is None
    (not infinite/0) when there's no usable demand signal yet — "no
    prediction possible" is a different fact than "definitely won't run
    out", and callers should be able to tell them apart.
    """
    kg.refresh()
    product = kg.get_entity(product_id)
    if product is None or product.attributes.get("quantity") is None:
        return {"stockout_predicted": False, "days_remaining": None, "daily_rate": 0.0,
                "reason": "product or quantity unknown"}

    current_qty = product.attributes["quantity"]
    demand = predict_demand(kg, product_id, lookback_days=lookback_days, actor_id=actor_id)
    daily_rate = demand["predicted_daily_demand"]
    if daily_rate <= 0:
        return {"stockout_predicted": False, "days_remaining": None, "daily_rate": 0.0,
                "current_quantity": current_qty, "reason": "no demand signal"}

    days_remaining = current_qty / daily_rate
    return {
        "stockout_predicted": days_remaining <= urgency_days,
        "days_remaining": round(days_remaining, 2),
        "daily_rate": daily_rate,
        "current_quantity": current_qty,
        "confidence": demand["confidence"],
    }


_MAX_REORDER_MULTIPLE = 20  # never recommend buying more than this many times what was actually asked for


def recommend_reorder_quantity(kg, product_id: str, requested_qty: int,
                                lookback_days: float = 30.0, urgency_days: float = 2.0,
                                buffer_days: float = 5.0, actor_id: str | None = None) -> tuple[int, dict]:
    """GS-1301: "planner buys earlier" — when predict_stockout says this
    item is about to run out, buy enough now to cover buffer_days of
    predicted demand on top of what was actually requested, rather than
    waiting for a future request that might come too late. Returns
    (recommended_qty, prediction) so the caller can report WHY the qty
    changed, not just that it did — an unexplained quantity bump is
    exactly the kind of silent behavior GS-1600 (explainability) exists to
    catch.

    Capped to _MAX_REORDER_MULTIPLE x requested_qty. Without this, a real
    runaway feedback loop is possible and was actually observed: this
    function's own recommended quantity gets persisted as a real order
    line item, predict_demand's daily_rate is a plain mean over order
    history with no outlier rejection, so that inflated order raises the
    NEXT call's daily_rate, which recommends an even larger quantity —
    compounding roughly 6x per call (121 -> 726 -> 4356 -> ... -> 5.6
    million units, observed in real testing). The cap is a circuit
    breaker on the SYMPTOM (bounding what one call can ever recommend
    relative to what was actually asked for) — predict_demand's mean-based
    estimator remaining sensitive to outliers is the underlying cause and
    a real, acknowledged simplification, not fixed here.
    """
    prediction = predict_stockout(kg, product_id, lookback_days=lookback_days, urgency_days=urgency_days, actor_id=actor_id)
    if not prediction["stockout_predicted"]:
        return requested_qty, prediction
    buffer_qty = max(0, round(prediction["daily_rate"] * buffer_days))
    capped_qty = min(requested_qty + buffer_qty, requested_qty * _MAX_REORDER_MULTIPLE)
    return capped_qty, prediction


class _CounterfactualView:
    """A read-only overlay over a real KG: .entities returns copies with
    hypothetical attribute overrides applied — the real KG (and Neo4j) is
    never written to. This is what makes a "what if" a genuine
    counterfactual rather than a real speculative purchase: the intervened
    entity changes, everything else is exactly what was actually observed,
    and nothing here can leak into a real order, reservation, or belief.

    Implements what open_products/eligible_candidates/pick_best_*/
    get_belief actually call on a kg — .entities and the read-only
    get_entity/version_of/person_id — not the full KnowledgeGraph
    interface; anything that tried to WRITE through this view would fail
    loudly (no add_entity/update_entity/compare_and_swap here) rather than
    silently corrupting real data. get_entity was added when Level 21's
    open_products started calling it directly (detect_inventory_
    inconsistency, get_belief) — a read is exactly the kind of operation
    this view exists to serve safely; only mutation is off-limits.
    """
    def __init__(self, kg, overrides: dict[str, dict]):
        self._kg = kg
        self._overrides = overrides or {}
        self.person_id = kg.person_id

    @property
    def entities(self):
        import copy
        result = []
        for e in self._kg.entities:
            if e.entity_id in self._overrides:
                shadow = copy.deepcopy(e)
                shadow.attributes.update(self._overrides[e.entity_id])
                result.append(shadow)
            else:
                result.append(e)
        return result

    def get_entity(self, entity_id):
        for e in self.entities:
            if e.entity_id == entity_id:
                return e
        return None

    def entities_by_type(self, entity_type):
        # No separate shadow index for this view — it only ever wraps a
        # handful of overrides, so a plain filter over self.entities (which
        # already applies them) is fine; open_products calling this instead
        # of scanning kg.entities directly still needs SOME implementation
        # here, since it no longer does its own entity_type filtering.
        return [e for e in self.entities if e.entity_type == entity_type]

    def entities_by_keywords(self, keywords):
        from src.monkey_brain.kernel.knowledge_graph import _index_keywords
        return [e for e in self.entities if _index_keywords(e.name) & set(keywords)]

    def version_of(self, entity_id):
        return self._kg.version_of(entity_id)


def simulate_plan(kg, item_phrases: list[str], optimization: str, overrides: dict[str, dict] | None = None,
                   lactose_free: bool = False, rejected_keywords: frozenset = frozenset(),
                   force_optimization: bool = False) -> dict:
    """GS-1400/1401/1402: run the SAME selection logic ProductSelection uses
    for a real purchase — eligible_candidates, pick_best_unit/
    pick_best_for_volume — against a (possibly counterfactual) view of the
    world, WITHOUT creating an order, reserving stock, or writing anything.
    overrides={} (the default) simulates the real current world exactly;
    passing overrides is what makes this a "what if" instead of a dry run.

    Reusing the real selection functions rather than a separate simulation
    algorithm is deliberate: a simulated plan and a real one can only ever
    differ in WHAT WORLD they were evaluated against, never in HOW the
    decision logic works — otherwise "what if Costco closes" could predict
    an outcome the real planner would never actually produce.

    force_optimization=True skips the per-item detect_optimization(phrase)
    override (GS-0400's "buy healthy milk" behavior) and always uses the
    passed optimization exactly. compare_plans needs this: an item phrase
    like "quality for milk" would otherwise make detect_optimization pick
    "quality" out of the TEXT for every call, silently making the "cost"
    and "quality" comparison runs identical instead of actually comparing
    two different objectives.
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType
    kg.refresh()
    view = _CounterfactualView(kg, overrides)
    all_products = open_products(view)
    stores_by_id = {e.entity_id: e for e in view.entities if e.entity_type == EntityType.ORGANIZATION}

    selections = []
    total = 0.0
    for item_phrase in item_phrases:
        candidates, note = eligible_candidates(item_phrase, all_products, lactose_free, rejected_keywords)
        if candidates is None:
            return {"feasible": False, "reason": note, "item": item_phrase}
        item_optimization = optimization if force_optimization else (detect_optimization(item_phrase) or optimization)
        volume_ml = _parse_requested_volume_ml(item_phrase)
        mass_g = _parse_requested_mass_g(item_phrase) if volume_ml is None else None
        extra = []
        if volume_ml:
            best, qty, reason, extra = pick_best_for_volume(candidates, volume_ml, item_optimization, stores_by_id)
        elif mass_g:
            best, qty, reason, extra = pick_best_for_volume(candidates, mass_g, item_optimization, stores_by_id, size_key="size_g")
        else:
            best, qty, reason = pick_best_unit(candidates, item_optimization, stores_by_id)
        price = best.attributes.get("price", 0)
        selections.append({
            "id": best.entity_id, "name": best.name, "store_id": best.attributes.get("store_id"),
            "store_name": best.attributes.get("store_name"), "qty": qty, "price": price, "reason": reason,
        })
        total += price * qty
        # Quantity Optimization: a mixed-package combo (pick_best_for_volume)
        # returns additional package types beyond the primary one — each is
        # a real, separate line item, same shape as the primary selection.
        for extra_c, extra_qty in extra:
            extra_price = extra_c.attributes.get("price", 0)
            selections.append({
                "id": extra_c.entity_id, "name": extra_c.name, "store_id": extra_c.attributes.get("store_id"),
                "store_name": extra_c.attributes.get("store_name"), "qty": extra_qty, "price": extra_price, "reason": reason,
            })
            total += extra_price * extra_qty

    return {"feasible": True, "selections": selections, "total": round(total, 2), "optimization": optimization}


def diff_plans(baseline: dict, alternative: dict) -> dict:
    """Explain what would actually change between two simulate_plan results
    — which items changed store/product, and the total cost delta. This is
    the answer to "what would change", not just "here are two plans";
    without it a caller has to manually diff two selection lists to find
    out whether anything meaningful happened at all.
    """
    if not baseline.get("feasible") or not alternative.get("feasible"):
        return {
            "changed": True,
            "baseline_feasible": baseline.get("feasible", False),
            "alternative_feasible": alternative.get("feasible", False),
            "reason": alternative.get("reason") if not alternative.get("feasible") else baseline.get("reason"),
        }
    base_by_name = {s["name"]: s for s in baseline["selections"]}
    alt_by_name = {s["name"]: s for s in alternative["selections"]}
    item_changes = []
    for name in base_by_name:
        b, a = base_by_name.get(name), alt_by_name.get(name)
        if a is None:
            item_changes.append({"item": name, "change": "no longer available"})
        elif b["store_id"] != a["store_id"] or b["price"] != a["price"]:
            item_changes.append({
                "item": name,
                "from": {"store": b["store_name"], "price": b["price"]},
                "to": {"store": a["store_name"], "price": a["price"]},
            })
    return {
        "changed": bool(item_changes) or baseline["total"] != alternative["total"],
        "item_changes": item_changes,
        "total_delta": round(alternative["total"] - baseline["total"], 2),
    }


def compare_plans(kg, item_phrases: list[str], optimizations: list[str],
                   lactose_free: bool = False, rejected_keywords: frozenset = frozenset()) -> dict:
    """GS-1400: evaluate multiple candidate strategies (e.g. cost vs
    quality) for the SAME real world before committing to one — each is
    just simulate_plan with no overrides, run under a different
    optimization objective, so this is "which of several plans is best"
    rather than "what if the world were different" (that's what overrides
    on simulate_plan are for)."""
    plans = {opt: simulate_plan(kg, item_phrases, opt, lactose_free=lactose_free,
                                 rejected_keywords=rejected_keywords, force_optimization=True)
              for opt in optimizations}
    feasible = {opt: p for opt, p in plans.items() if p.get("feasible")}
    best_opt = min(feasible, key=lambda opt: feasible[opt]["total"]) if feasible else None
    return {"plans": plans, "recommended": best_opt}


_STORE_CLOSE_RE = re.compile(r"what if (.+?) (?:closes|closed|shuts down|shut down)", re.IGNORECASE)
_PRICE_CHANGE_RE = re.compile(
    r"what if (?:the )?(.+?) price (?:doubles|doubled|(?:goes|went) up|increases?|triples|tripled)",
    re.IGNORECASE,
)
_COMPARE_RE = re.compile(r"\bcompare\b|\bvs\.?\b|\bversus\b", re.IGNORECASE)


def parse_counterfactual(question: str, kg) -> dict | None:
    """GS-1401/1402: turn a "what if ..." question into structured
    overrides simulate_plan can act on, resolved against REAL entities in
    kg (not guessed IDs) — "Costco" only becomes an override if a store
    actually named that exists; an unrecognized name returns None rather
    than silently simulating nothing.

    Returns None if the question isn't a recognized counterfactual pattern
    — the caller (CounterfactualCapability) treats that as "not this kind
    of question" rather than an error, since new phrasings will always
    exist that this regex-based parser doesn't cover yet.
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    close_match = _STORE_CLOSE_RE.search(question)
    if close_match:
        # remainder = the question with the "what if X closes" clause cut
        # out, so the caller can extract the ACTUAL grocery item from
        # whatever's left ("...would I still get milk") instead of feeding
        # eligible_candidates the whole hypothetical clause as if it were
        # an item phrase, which never matches any real product.
        remainder = (question[:close_match.start()] + question[close_match.end():]).strip(" ,.")
        name_fragment = close_match.group(1).strip().lower()
        stores = [e for e in kg.entities_by_type(EntityType.ORGANIZATION)
                  if e.attributes.get("type") == "grocery_store"]
        target = next((s for s in stores if name_fragment in s.name.lower()), None)
        if target is None:
            return {"recognized": True, "resolved": False,
                    "reason": f"no store matching {name_fragment!r} found"}
        return {"recognized": True, "resolved": True, "kind": "store_closes",
                "overrides": {target.entity_id: {"is_open": False}}, "subject": target.name,
                "remainder": remainder}

    price_match = _PRICE_CHANGE_RE.search(question)
    if price_match:
        remainder = (question[:price_match.start()] + question[price_match.end():]).strip(" ,.")
        item_phrase = price_match.group(1).strip()
        return {"recognized": True, "resolved": True, "kind": "price_change",
                "item_phrase": item_phrase, "multiplier": 2.0,
                "remainder": remainder or item_phrase}

    return None


def find_borrowable(kg, item_phrase: str, exclude_owner_id: str | None = None) -> list:
    """GS-1500: PANTRY surplus someone else in the actor's own visible
    scope already owns and is willing to share — a fundamentally different
    pool from open_products' STORE inventory (attributes["pantry"] is True,
    not attributes["product"]). Only entities tagged shareable=True and
    with real available quantity (current quantity minus already-active
    loans, not just the raw quantity field) count — a fully-lent-out pantry
    item must not be offered twice.
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType
    kg.refresh()
    candidates = []
    # Performance certification (GS-6000): pantry/shareable entities are a
    # tiny fraction of ASSET-typed entities once the catalog is large (most
    # ASSET entities are ordinary store products) -- pre-filtering by the
    # keyword index before the pantry/shareable checks keeps this off the
    # catalog-scale pool the same way open_products' item_phrase does.
    request_kw = _keyword_surface_forms(_keywords(item_phrase))
    asset_pool = (
        [e for e in kg.entities_by_keywords(request_kw) if e.entity_type == EntityType.ASSET]
        if request_kw else kg.entities_by_type(EntityType.ASSET)
    )
    for e in asset_pool:
        if not e.attributes.get("pantry"):
            continue
        if not e.attributes.get("shareable"):
            continue
        if exclude_owner_id and e.attributes.get("owner_id") == exclude_owner_id:
            continue
        if _match_score(e.name, item_phrase) == 0:
            continue
        active_loans = sum(l.get("qty", 0) for l in e.attributes.get("loans", []))
        available = e.attributes.get("quantity", 0) - active_loans
        if available > 0:
            candidates.append(e)
    return candidates


def borrow_item(kg, item_id: str, borrower_id: str, qty: int, max_attempts: int = 5) -> tuple[bool, str]:
    """GS-1500: CAS-append a loan to item_id's `loans` list (same
    reservations-list pattern try_reserve uses — Level 7/12 already proved
    a single-slot design silently overwrites concurrent claims instead of
    summing them). Borrowing never decrements the owner's real quantity —
    it's still theirs, just temporarily held by someone else — so
    return_borrowed_item can hand it back exactly.
    """
    for _ in range(max_attempts):
        entity = kg.get_entity(item_id)
        if entity is None:
            return False, "not found"
        attrs = entity.attributes
        loans = attrs.get("loans", [])
        held = sum(l.get("qty", 0) for l in loans)
        available = attrs.get("quantity", 0) - held
        if available < qty:
            return False, f"insufficient shareable stock: {available} available, {qty} requested"
        new_loans = loans + [{"borrower_id": borrower_id, "qty": qty, "borrowed_at": time.time()}]
        version = kg.version_of(item_id)
        ok, _current = kg.compare_and_swap(item_id, version, {"loans": new_loans})
        if ok:
            return True, f"borrowed {qty} from {attrs.get('owner_id')}"
    return False, "too much contention, gave up"


def return_borrowed_item(kg, item_id: str, borrower_id: str, max_attempts: int = 5) -> tuple[bool, str]:
    """Hand a borrowed item back — removes borrower_id's loan entry,
    freeing that quantity for someone else to borrow again."""
    for _ in range(max_attempts):
        entity = kg.get_entity(item_id)
        if entity is None:
            return False, "not found"
        loans = entity.attributes.get("loans", [])
        mine = next((l for l in loans if l.get("borrower_id") == borrower_id), None)
        if mine is None:
            return False, "no active loan held by this borrower"
        remaining_loans = [l for l in loans if l.get("borrower_id") != borrower_id]
        version = kg.version_of(item_id)
        ok, _current = kg.compare_and_swap(item_id, version, {"loans": remaining_loans})
        if ok:
            return True, f"returned {mine.get('qty', 0)}"
    return False, "too much contention, gave up"


def _cas_adjust_balance(kg, wallet_id: str, delta: float, max_attempts: int = 20) -> bool:
    """Atomically adjust a wallet's balance by delta (negative to debit,
    positive to credit), retrying on a concurrent-write conflict — the
    same compare_and_swap retry pattern Level 34 established for
    PaymentCapability's wallet debit, reused here for peer-to-peer
    transfers so a genuinely concurrent negotiation can't lose an update
    either."""
    for _ in range(max_attempts):
        wallet = kg.get_entity(wallet_id)
        if wallet is None:
            return False
        current = wallet.attributes.get("balance", 0)
        version = kg.version_of(wallet_id)
        ok, _ = kg.compare_and_swap(wallet_id, version, {"balance": round(current + delta, 2)})
        if ok:
            return True
    return False


def _debit_store_accounts_for_refund(kg, order, refund_amount: float) -> None:
    """Debits each affected store's real receivable account (kernel/
    domains/commerce.py::onboard_merchant) by its proportional share of a
    refund — the symmetric reversal of PaymentCapability's own store-
    crediting (grocery.py::PaymentCapability._finalize_successful_payment).
    Store accounts allow both directions (credit on sale, debit on
    refund — and, outside this function's scope, whatever a store spends
    on restocking its own inventory) the same as any other real account;
    nothing here treats them as credit-only. Without this reversal, a
    refunded buyer got their money back but the store kept the full
    original credit, silently doubling the total in circulation.

    Scaled by refund_amount / the order's own paid_amount (not a flat
    per-item split), so a PARTIAL refund debits each store only its
    proportional share, and cancel_order()/refund_order() calling this
    multiple times across several partial refunds on the same order
    never debits a store more than it was ever actually credited for
    that order. Items whose product has since been discontinued are
    skipped — their original store can no longer be identified from the
    live catalog, the same real limitation cancel_order()'s own
    restocking logic already accepts rather than fabricating a store to
    debit.
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    items = order.attributes.get("items", [])
    paid_amount = order.attributes.get("paid_amount", order.attributes.get("total", 0))
    if not items or paid_amount <= 0 or refund_amount <= 0:
        return
    refund_ratio = min(1.0, refund_amount / paid_amount)

    totals_by_store: dict[str, float] = {}
    for item in items:
        pid = item.get("product_id")
        product = kg.get_entity(pid) if pid else None
        if product is None:
            continue
        store_id = product.attributes.get("store_id")
        if not store_id:
            continue
        totals_by_store[store_id] = totals_by_store.get(store_id, 0.0) + (item.get("price", 0) * item.get("qty", 1))

    for store_id, gross_value in totals_by_store.items():
        debit = round(gross_value * refund_ratio, 2)
        if debit <= 0:
            continue
        store_account = next(
            (a for a in kg.entities_by_type(EntityType.ACCOUNT) if a.attributes.get("store_id") == store_id),
            None,
        )
        if store_account is not None:
            _cas_adjust_balance(kg, store_account.entity_id, -debit)


def _seller_scoped_kg(kg, seller_id: str):
    """Level 50 (GS-5000): a peer seller's own wallet lives in THEIR OWN
    partition, not the buyer's — reachable from the buyer's kg instance
    only by constructing a fresh instance scoped to the seller's own
    person_id, the same "build a KG scoped to a DIFFERENT actor" pattern
    DelegationCheckCapability already uses for a delegator. Caught live:
    without this, _find_wallet(kg, seller_id) against the BUYER's own kg
    instance could only ever see accounts that instance had actually
    hydrated — in a genuine cross-household scenario that's never the
    seller's real wallet, and (before Level 50's _owned_by fix) it
    silently matched the BUYER's own household wallet instead, debiting
    and crediting the identical account while the real seller was never
    paid.

    Duck-typed to Neo4jBackedKnowledgeGraph's own constructor kwargs
    (_uri/_user/_password) — the plain in-memory KnowledgeGraph has no
    cross-instance concept at all (every actor sharing that ONE object
    already sees the same accounts, the convention every test before
    Level 48 used), so this returns kg itself unchanged for that
    backend.
    """
    uri = getattr(kg, "_uri", None)
    if uri is None:
        return kg
    return type(kg)(person_id=seller_id, uri=uri, user=getattr(kg, "_user", None), password=getattr(kg, "_password", None))


def buy_from_neighbor(kg, item_id: str, buyer_id: str, qty: int, agreed_price: float, max_attempts: int = 5) -> tuple[bool, str]:
    """GS-1501/Level 45 (GS-4500): a real peer-to-peer transaction once
    negotiation succeeds — permanently decrements the seller's pantry
    quantity (unlike borrow_item, this item doesn't come back) AND moves
    the real agreed total from the buyer's wallet to the seller's wallet.

    Caught live: this used to only touch inventory — a negotiated price
    was computed and reported ("bought 1 from carol at $6.14 each"), but
    no money ever actually moved between wallets, so the seller silently
    gave their food away for free while the system believed a real
    purchase had happened. Fails honestly, with NOTHING touched, if the
    buyer can't actually afford the agreed total or either party has no
    wallet to settle through — a peer negotiation is still a real
    transaction, not exempt from the affordability checks every other
    purchase in this codebase already enforces.

    The seller's own wallet is resolved through a seller-scoped KG
    (_seller_scoped_kg) rather than the buyer's own kg instance — see
    its docstring for the real cross-household bug this fixes.
    """
    total = round(agreed_price * qty, 2)
    buyer_wallet = _find_wallet(kg, buyer_id)
    if buyer_wallet is None:
        return False, f"{buyer_id} has no wallet to pay ${total:.2f} for this"
    if buyer_wallet.attributes.get("balance", 0) < total:
        return False, f"insufficient funds: {buyer_id} needs ${total:.2f} for this peer purchase"

    entity = kg.get_entity(item_id)
    if entity is None:
        return False, "not found"
    seller_id = entity.attributes.get("owner_id")
    seller_kg = _seller_scoped_kg(kg, seller_id)
    seller_wallet = _find_wallet(seller_kg, seller_id)
    if seller_wallet is None:
        return False, f"{seller_id} has no wallet to receive ${total:.2f} for this"

    for _ in range(max_attempts):
        entity = kg.get_entity(item_id)
        if entity is None:
            return False, "not found"
        attrs = entity.attributes
        available = attrs.get("quantity", 0)
        if available < qty:
            return False, f"insufficient stock: {available} available, {qty} requested"
        version = kg.version_of(item_id)
        ok, _current = kg.compare_and_swap(item_id, version, {"quantity": available - qty})
        if ok:
            if not _cas_adjust_balance(kg, buyer_wallet.entity_id, -total):
                return False, "payment failed: too much contention on buyer's wallet"
            _cas_adjust_balance(seller_kg, seller_wallet.entity_id, total)
            return True, f"bought {qty} from {seller_id} at ${agreed_price:.2f} each (${total:.2f} paid)"
    return False, "too much contention, gave up"


def negotiate_price(listed_price: float, min_seller_price: float, buyer_target_price: float,
                     max_rounds: int = 3) -> dict:
    """GS-1501: bounded split-the-difference bargaining, not a fixed
    take-it-or-leave-it price. Each round both sides move halfway toward
    the other; the seller's ask never drops below min_seller_price (their
    real floor) and the buyer's offer never exceeds listed_price. A deal is
    reached the moment the buyer's offer meets or exceeds the seller's ask,
    settling at their midpoint — not automatically at whichever number
    moved last. No deal is possible at all if the buyer's opening target is
    already below the seller's floor; that's reported immediately rather
    than run through rounds that could never converge.
    """
    if buyer_target_price < min_seller_price:
        return {"agreed": False, "price": None, "rounds": [],
                "reason": "buyer's target is below the seller's floor"}

    seller_ask, buyer_offer = listed_price, buyer_target_price
    rounds = []
    for i in range(1, max_rounds + 1):
        if buyer_offer >= seller_ask:
            price = round((buyer_offer + seller_ask) / 2, 2)
            rounds.append({"round": i, "seller_ask": round(seller_ask, 2),
                            "buyer_offer": round(buyer_offer, 2), "outcome": "deal"})
            return {"agreed": True, "price": price, "rounds": rounds}
        rounds.append({"round": i, "seller_ask": round(seller_ask, 2),
                        "buyer_offer": round(buyer_offer, 2), "outcome": "no deal yet"})
        gap = seller_ask - buyer_offer
        seller_ask = max(min_seller_price, seller_ask - gap / 2)
        buyer_offer = buyer_offer + gap / 2

    if buyer_offer >= seller_ask:
        price = round((buyer_offer + seller_ask) / 2, 2)
        return {"agreed": True, "price": price, "rounds": rounds}
    return {"agreed": False, "price": None, "rounds": rounds,
            "reason": f"no agreement within {max_rounds} rounds"}


def place_bid(kg, auction_id: str, actor_id: str, bid_price: float, qty: int, max_attempts: int = 5) -> tuple[bool, str]:
    """GS-1502: CAS-append a bid to auction_id's `bids` list (same pattern
    as try_reserve/place_bid's siblings) — a fresh bid from an actor who
    already bid supersedes their prior one rather than stacking a second
    entry, same convention try_reserve uses for reservations.
    """
    for _ in range(max_attempts):
        entity = kg.get_entity(auction_id)
        if entity is None:
            return False, "auction not found"
        bids = entity.attributes.get("bids", [])
        new_bids = [b for b in bids if b.get("actor_id") != actor_id]
        new_bids.append({"actor_id": actor_id, "bid_price": bid_price, "qty": qty, "bid_at": time.time()})
        version = kg.version_of(auction_id)
        ok, _current = kg.compare_and_swap(auction_id, version, {"bids": new_bids})
        if ok:
            return True, "bid placed"
    return False, "too much contention, gave up"


def resolve_auction(kg, auction_id: str) -> dict:
    """GS-1502: allocate the auctioned quantity to the highest bidders
    first (discriminatory-price: each winner pays their OWN bid, not a
    single clearing price), tie-broken by whoever bid earliest. A bidder
    whose quantity would only be partially covered by what's left gets
    exactly what's left, not zero and not their full ask — the same
    "use every unit, don't over- or under-allocate" principle Level 10's
    allocate_by_priority already established for scarcity.
    """
    kg.refresh()
    entity = kg.get_entity(auction_id)
    if entity is None:
        return {"resolved": False, "reason": "auction not found"}
    available = entity.attributes.get("quantity", 0)
    bids = sorted(entity.attributes.get("bids", []), key=lambda b: (-b["bid_price"], b["bid_at"]))
    allocation = {}
    remaining = available
    for b in bids:
        if remaining <= 0:
            break
        grant = min(b["qty"], remaining)
        allocation[b["actor_id"]] = {"qty": grant, "price": b["bid_price"]}
        remaining -= grant
    return {
        "resolved": True, "allocation": allocation, "remaining": remaining,
        "clearing_price": bids[0]["bid_price"] if bids else None,
    }


def household_autonomous_round(kg, actor_id: str, product_id: str, item_phrase: str = "milk") -> dict:
    """Level 18: one household's complete autonomous decision cycle for a
    single round — no human-authored question drives any of this. Composes
    primitives every prior level already built and proved correct:

      OBSERVE          -> form_belief (Level 13)
      FORM LOCAL GOAL   -> predict_demand, from THIS household's own real
                           purchase history (Level 14) — the "goal" is a
                           number this household derives from its own
                           state, not text typed by a person
      SHARE / COORDINATE -> find_borrowable/borrow_item (Level 16) tried
                           FIRST, before ever touching the shared scarce
                           pool — a household that can be served by a
                           neighbor's surplus never competes for it at all

    Deliberately stops short of claiming from the shared pool itself —
    resolving MANY households' simultaneous unmet need fairly (rather than
    a naive first-come race) needs to see every claim at once
    (allocate_fair_share, Level 10), which only the orchestrator calling
    this for the whole society can do. Returns claim_qty > 0 exactly when
    this household still needs that centralized step.
    """
    form_belief(kg, product_id)
    # Isolation fix: actor_id is right here in scope — predict_demand's
    # own docstring documents the real cross-actor demand-inflation bug
    # this exact opt-in parameter exists to prevent (Phase 5 stress
    # test), yet this call site never threaded it through, so this
    # household's own autonomous demand forecast still silently absorbed
    # every OTHER actor sharing this KG's concurrent purchases.
    demand = predict_demand(kg, product_id, actor_id=actor_id)
    needed = max(1, round(demand["predicted_daily_demand"])) if demand["predicted_daily_demand"] > 0 else 1

    borrowable = find_borrowable(kg, item_phrase, exclude_owner_id=actor_id)
    if borrowable:
        lender = max(
            borrowable,
            key=lambda e: e.attributes.get("quantity", 0) - sum(l.get("qty", 0) for l in e.attributes.get("loans", [])),
        )
        available = lender.attributes.get("quantity", 0) - sum(l.get("qty", 0) for l in lender.attributes.get("loans", []))
        borrow_qty = min(needed, available)
        ok, _msg = borrow_item(kg, lender.entity_id, actor_id, borrow_qty)
        if ok:
            remaining = needed - borrow_qty
            mode = "borrow" if remaining <= 0 else "partial_borrow"
            return {"needed": needed, "satisfied_via": mode, "claim_qty": max(0, remaining), "borrowed": borrow_qty}

    return {"needed": needed, "satisfied_via": "none", "claim_qty": needed, "borrowed": 0}


_MIN_LEARNED_PREFERENCE_PURCHASES = 2


_LEARNED_PREFERENCE_LOOKBACK_DAYS = 90.0


def learned_preference(kg, base_item: str, attribute: str, min_purchases: int = _MIN_LEARNED_PREFERENCE_PURCHASES,
                        lookback_days: float = _LEARNED_PREFERENCE_LOOKBACK_DAYS) -> tuple[str | None, dict]:
    """Level 19 (GS-1900/1901): infer a standing preference for `attribute`
    ("brand" or "type_keyword") from the actor's REAL past purchases of
    base_item, not a stored settings field — the preference IS the
    purchase history, the same "the belief is the evidence, not a label"
    principle Level 13/14 already established for belief and demand.

    Reads attribute values straight off each order's LINE ITEM (persisted
    by OrderCreationCapability at purchase time), not by re-looking-up the
    product entity now — a discontinued or renamed product must not erase
    what was actually bought historically.

    Requires min_purchases of the SAME value before calling it a
    preference — one purchase is just what happened to be picked, not a
    pattern; a genuine majority is. Returns (None, stats) when there's no
    clear majority, so a caller can tell "no real preference" from "found
    one" without inspecting the stats dict itself.

    Performance certification (GS-6000, "100K execution ticks: no
    unbounded latency increase"): this used to scan the actor's ENTIRE
    order history on every call — confirmed to grow linearly with
    lifetime order count (0.1ms at 100 orders -> 20ms at 20,000), which
    is genuinely unbounded for a "personal lifelong runtime" actor.
    lookback_days bounds the scan the same way predict_demand's own
    lookback window already does — a preference is a statement about
    RECENT behavior, not literally every purchase since the actor's
    first day; a purchase 6 months ago shouldn't out-vote the last
    dozen just because history happens to be longer now.

    Further performance certification finding: the lookback bound alone
    only shrunk the EXPENSIVE per-order work (the item loop / _keywords
    calls) — the cheap "read this order's timestamp and skip it if too
    old" still touched every order this actor has EVER placed, still
    growing linearly with lifetime order count (confirmed: 1ms at 1,000
    orders -> 8.6ms at 50,000, regardless of the window). Reads the same
    incremental per-actor aggregate update_order_stats() maintains
    (keyed by kg.person_id — this function has no actor_id parameter of
    its own, matching every existing caller's single-actor-per-KG usage)
    so the read is O(days in the retention window), never O(orders ever
    placed).
    """
    now = time.time()
    stats = kg.get_entity(_order_stats_id(kg.person_id))
    value_buckets = stats.attributes.get("pref_counts", {}).get(base_item, {}).get(attribute, {}) if stats else {}
    counts: dict[str, int] = {}
    total = 0
    for value, buckets in value_buckets.items():
        recent = _sum_recent_buckets(buckets, now, lookback_days)
        count = sum(recent.values())
        if count:
            counts[value] = count
            total += count

    if not counts:
        return None, {"total_purchases": total, "counts": counts}
    top_value, top_count = max(counts.items(), key=lambda kv: kv[1])
    stats = {"total_purchases": total, "counts": counts}
    if top_count >= min_purchases and top_count > total - top_count:
        return top_value, stats
    return None, stats


def resolve_preference_conflict(candidates, member_preferences: dict[str, dict]) -> tuple:
    """GS-1902: a shared household purchase where members want different
    things (alice: organic, bob: cheap) — picks the candidate that
    maximizes AVERAGE satisfaction across every member with a stated
    preference, not whichever member's preference happens to be checked
    first. A "compromise" pick (organic AND reasonably priced, if one
    exists) can genuinely score higher than either member's individual
    ideal, the same way a real household negotiation would land on
    something both people can live with rather than one person's exact
    preference winning outright.

    member_preferences: {actor_id: {"organic": True}} or
    {actor_id: {"optimize": "cost"}} — one dimension per member, kept
    simple since real conflicts are rarely more than "the thing I care
    about" per person. Returns (chosen_entity, {"average_satisfaction",
    "per_member"}) so the compromise is explainable, not just asserted.
    """
    if not candidates:
        return None, {}
    prices = [c.attributes.get("price", float("inf")) for c in candidates]
    lo, hi = min(prices), max(prices)

    scored = []
    for c in candidates:
        member_scores = {}
        for actor_id, pref in member_preferences.items():
            if "organic" in pref:
                member_scores[actor_id] = 1.0 if c.attributes.get("organic") == pref["organic"] else 0.0
            elif pref.get("optimize") == "cost":
                price = c.attributes.get("price", float("inf"))
                member_scores[actor_id] = 1.0 if hi == lo else max(0.0, 1.0 - (price - lo) / (hi - lo))
            else:
                member_scores[actor_id] = 0.5  # no dimension this candidate can be scored on for this member
        avg = sum(member_scores.values()) / len(member_scores) if member_scores else 0.0
        scored.append((avg, c, member_scores))

    scored.sort(key=lambda t: -t[0])
    best_avg, best, best_member_scores = scored[0]
    return best, {
        "average_satisfaction": round(best_avg, 2),
        "per_member": {k: round(v, 2) for k, v in best_member_scores.items()},
    }


def add_pantry_item(kg: Any, name: str, quantity: int = 0, owner_id: str | None = None,
                     household_members: list[str] | None = None, **item_attrs: Any) -> dict:
    """Adds a real pantry entity to a household's shared stock — the write
    side find_household_pantry_stock()/predict_household_stockout() below
    have always assumed exists (attributes["pantry"]/["household_stock"]),
    but nothing in this codebase previously created one; every pantry read
    only ever found entities a test had written directly via kg.add_entity.
    Same ownership convention _pantry_owned_by() already reads: an explicit
    owner_id, an explicit household_members list, or neither (unscoped).
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType
    import uuid

    entity_id = f"pantry_{uuid.uuid4().hex}"
    attributes = {
        "pantry": True, "household_stock": True, "quantity": quantity,
        **({"owner_id": owner_id} if owner_id is not None else {}),
        **({"household_members": household_members} if household_members is not None else {}),
        **item_attrs,
    }
    kg.add_entity(entity_id, EntityType.ASSET, name, attributes)
    return {"success": True, "pantry_id": entity_id, "name": name, "quantity": quantity}


def _pantry_owned_by(attrs: dict, actor_id: str | None) -> bool:
    """Whether actor_id's household can count this pantry entity as their
    OWN stock — the same ownership convention finance.py's _owned_by
    already established for shared wallets: an explicit owner_id, an
    explicit household_members list, or (only when NEITHER is set at
    all) an unscoped legacy entity nobody has claimed a household for,
    treated as belonging to whoever's asking — the single-actor-per-graph
    shape every pre-existing pantry test used. actor_id=None preserves
    the fully unscoped behavior for callers that haven't threaded an
    actor_id through yet.
    """
    if actor_id is None:
        return True
    owner_id = attrs.get("owner_id")
    if owner_id is not None:
        return owner_id == actor_id
    members = attrs.get("household_members")
    if members is not None:
        return actor_id in members
    return True


def find_household_pantry_stock(kg, item_phrase: str, actor_id: str | None = None):
    """Level 20/46 (GS-2000/2002/4600): THIS actor's OWN household's
    shared stock for item_phrase — what's actually at home right now,
    regardless of who in the household bought it. attributes
    ["household_stock"] is a DIFFERENT flag from Level 16's "shareable"/
    "for_sale" — a pantry entity meaning "this is our current stock" is a
    distinct concept from "this is surplus I'll lend or sell to a
    neighbor," even though both reuse the same pantry entity shape.
    Conflating them would make find_borrowable start treating ordinary
    household stock as available for a NEIGHBOR to borrow, which it was
    never meant to offer.

    Caught live: in a shared-world KG holding more than one actor's data
    (PlanetaryRuntime's convention), this matched ANY household's pantry
    stock for the item — Alice's request was marked pantry-fulfilled and
    reported goal_achieved=True using an entirely unrelated household's
    (Bob's) milk, while Alice herself received nothing at all and never
    even attempted a real purchase. actor_id scopes this the same way
    Level 2/35's fixes scoped wallets/memberships — see _pantry_owned_by.
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType
    kg.refresh()
    # Performance certification (GS-6000): same keyword pre-filter as
    # find_borrowable -- pantry entities are a tiny fraction of all
    # ASSET-typed entities once the catalog is large.
    request_kw = _keyword_surface_forms(_keywords(item_phrase))
    asset_pool = (
        [e for e in kg.entities_by_keywords(request_kw) if e.entity_type == EntityType.ASSET]
        if request_kw else kg.entities_by_type(EntityType.ASSET)
    )
    for e in asset_pool:
        if not e.attributes.get("pantry"):
            continue
        if not e.attributes.get("household_stock"):
            continue
        if not _pantry_owned_by(e.attributes, actor_id):
            continue
        if _match_score(e.name, item_phrase) > 0:
            return e
    return None


def predict_household_stockout(kg, pantry_entity, reference_product_id: str, urgency_days: float = 2.0,
                                actor_id: str | None = None) -> dict:
    """GS-2002: days remaining on the household's own pantry stock, using
    its REAL purchase-rate history against reference_product_id (the store
    product this household actually buys) as the consumption-rate proxy —
    reuses Level 14's predict_demand rather than inventing a second,
    parallel consumption-tracking mechanism just for pantry stock.

    Isolation fix: actor_id (opt-in, same convention predict_demand
    itself uses) scopes the underlying demand forecast to THIS
    household's own purchases — without it, "how many days will this
    last" was silently answered from every actor sharing this KG's
    combined purchase rate, not this household's real consumption.
    """
    demand = predict_demand(kg, reference_product_id, actor_id=actor_id)
    daily_rate = demand["predicted_daily_demand"]
    current_qty = pantry_entity.attributes.get("quantity", 0) if pantry_entity else 0
    if daily_rate <= 0:
        return {"days_remaining": None, "daily_rate": 0.0, "current_quantity": current_qty,
                "urgent": False, "reason": "no demand signal"}
    days_remaining = current_qty / daily_rate
    return {
        "days_remaining": round(days_remaining, 2), "daily_rate": daily_rate,
        "current_quantity": current_qty, "urgent": days_remaining <= urgency_days,
        "confidence": demand["confidence"],
    }


_DUPLICATE_PURCHASE_WINDOW_HOURS = 24.0


def check_recent_duplicate_purchase(kg, item_phrase: str, window_hours: float = _DUPLICATE_PURCHASE_WINDOW_HOURS,
                                     actor_id: str | None = None) -> dict | None:
    """Level 20/46 (GS-2001/4600): has ANYONE in THIS actor's household
    already bought item_phrase recently? Real orders are persisted in the
    BUYER's private scope (Level 14/19 rely on that — personal purchase
    history is what makes per-person demand prediction and brand/type
    preference learning correct; pooling it at the household level would
    blend every member's separate habits together). So duplicate
    detection can't just read orders — it reads a lightweight,
    HOUSEHOLD-scoped purchase-log marker OrderCreationCapability writes
    alongside every order specifically for this, keeping the
    private-order architecture untouched.

    This never gates a purchase — reordering is always allowed. It only
    tells HouseholdCognitionCapability what to inform the household about
    (who bought it, how long ago), including how much (purchased_qty/
    volume_ml/mass_g) for a richer notification.

    actor_id scopes to markers this actor's own household actually wrote
    (same _pantry_owned_by convention as find_household_pantry_stock) —
    without it, an unrelated household's purchase in a shared-world KG
    would be reported as "someone in YOUR household already bought this."
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType
    kg.refresh()
    now = time.time()
    cutoff = now - window_hours * 3600
    for e in kg.entities_by_type(EntityType.OTHER):
        if not e.attributes.get("purchase_log"):
            continue
        if not _pantry_owned_by(e.attributes, actor_id):
            continue
        purchased_at = e.attributes.get("purchased_at", 0)
        if purchased_at < cutoff:
            continue
        if _match_score(e.attributes.get("product_name", ""), item_phrase) > 0:
            return {
                "buyer": e.attributes.get("buyer_id"),
                "product": e.attributes.get("product_name"),
                "purchased_at": purchased_at,
                "hours_ago": round((now - purchased_at) / 3600, 1),
                "purchased_qty": e.attributes.get("purchased_qty", 1),
                "purchased_volume_ml": e.attributes.get("purchased_volume_ml"),
                "purchased_mass_g": e.attributes.get("purchased_mass_g"),
            }
    return None


_ITEM_VERB_PREFIX_RE = re.compile(r"^(?:buy|get|purchase|order)\s+", re.IGNORECASE)


def cooperative_shopping_split(coordination_engine, shared_goal: str, member_ids: list[str]) -> dict[str, str]:
    """Level 47 (GS-4700): splits a household's SHARED shopping goal
    across its members via real task assignment, instead of every member
    independently pursuing the FULL list — which would duplicate every
    purchase across the household (verified live: without this, two
    members both buying "milk, eggs, and bread" each buy all three,
    doubling the spend and the inventory drawn down for something the
    household only needed once).

    coordination_engine is any object exposing assign_task(task_id,
    task_description, assigned_to, assigned_by) — this domain module
    never imports SocietyRuntime/PlanetaryRuntime or CoordinationEngine
    itself (the same architectural boundary CognitiveActor's own
    docstring already draws), so the caller passes its own
    society-layer CoordinationEngine in by reference; this function only
    ever calls the one method it needs on it.

    Items are assigned round-robin, in the order they were requested, so
    the split is deterministic and reproducible rather than arbitrary.
    Returns {member_id: "buy <items assigned to them>"} — a real,
    per-member goal string each actor can pursue via add_goal(), plus
    the real TaskAssignment records this produced are left in the
    caller's coordination_engine for inspection (it doesn't return them
    separately — assign_task already returns each one and the engine
    itself is the durable record).
    """
    # _split_requested_items only strips the "buy"/"get"/etc verb via the
    # comma/"and" separators it splits on — the FIRST item in "buy milk,
    # eggs, and bread" still comes back as "buy milk", not "milk", since
    # the verb precedes the first separator. Left as-is, rejoining a
    # per-member goal ("buy " + "buy milk, bread") produces a malformed
    # "buy buy milk, bread" that fails to match the milk product at all —
    # caught live, it silently zeroed out that member's own assigned item.
    items = [_ITEM_VERB_PREFIX_RE.sub("", item).strip() for item in _split_requested_items(shared_goal)]
    if not items or not member_ids:
        return {}
    assignments: dict[str, list[str]] = {m: [] for m in member_ids}
    for i, item in enumerate(items):
        member = member_ids[i % len(member_ids)]
        assignments[member].append(item)
        coordination_engine.assign_task(
            task_id=f"shop-{item.replace(' ', '_')}-{i}",
            task_description=f"buy {item}",
            assigned_to=member, assigned_by="household",
        )
    return {member: f"buy {', '.join(assigned)}" for member, assigned in assignments.items() if assigned}


_COUPON_CODE_RE = re.compile(r"\b(?:coupon|code|promo)[\s:]+([A-Za-z0-9-]+)", re.IGNORECASE)


def parse_coupon_code(question: str) -> str | None:
    m = _COUPON_CODE_RE.search(question)
    return m.group(1).upper() if m else None


_COUPON_URGENT_HOURS = 24.0


def validate_coupon(kg, code: str, store_id: str) -> dict:
    """Level 21/23 (GS-2100/2301): a coupon is only real if it matches a
    coupon entity that ACTUALLY exists and was issued for THIS store — a
    user-supplied code with no matching entity at all, or one issued for a
    different store, is flagged as forged (fraud_suspected=True), not just
    "not found". An expired real coupon is a different, honest case: it
    really was issued, it's just no longer valid — not a forgery.

    A valid coupon also reports expires_in_hours/urgent (GS-2301) — real
    time-until-expiry computed from valid_until, not a guess, so a caller
    can flag "use this now, it's about to lapse" instead of treating every
    valid coupon identically regardless of how much runway it has left.

    No internal refresh() — called once per candidate from a discount-
    application loop, where refreshing per candidate would mean one full
    re-hydration per candidate. Callers needing fresher-than-hydration
    data as a standalone check should refresh the kg themselves first.
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType
    for e in kg.entities_by_type(EntityType.OTHER):
        if not e.attributes.get("coupon"):
            continue
        if e.attributes.get("code", "").upper() != code.upper():
            continue
        if e.attributes.get("store_id") not in (None, store_id):
            return {"valid": False, "fraud_suspected": True,
                    "reason": f"coupon {code} was not issued for this store"}
        valid_until = e.attributes.get("valid_until")
        if valid_until and time.time() > valid_until:
            return {"valid": False, "fraud_suspected": False, "reason": f"coupon {code} has expired"}
        expires_in_hours = round((valid_until - time.time()) / 3600, 1) if valid_until else None
        return {
            "valid": True, "fraud_suspected": False,
            "discount_amount": e.attributes.get("discount_amount"),
            "discount_percent": e.attributes.get("discount_percent"),
            "expires_in_hours": expires_in_hours,
            "urgent": expires_in_hours is not None and expires_in_hours <= _COUPON_URGENT_HOURS,
        }
    return {"valid": False, "fraud_suspected": True,
            "reason": f"no such coupon {code!r} exists — rejected as forged"}


def has_active_membership(kg, store_id: str, actor_id: str | None = None) -> bool:
    """Level 23/35 (GS-2302): does THIS actor hold a real membership entity
    for this store? A membership-gated discount only applies to someone
    who actually has one — unlike a coupon (any code holder can redeem
    it), membership pricing is per-person and can't be unlocked just by
    knowing a store offers it, or by sharing a SharedWorld KG with
    someone else who happens to have one.

    Caught live: in a shared-world KG holding more than one actor's data
    (PlanetaryRuntime's convention, same shape Level 2 already exercised
    for wallets), a membership entity was matched purely on
    (membership, store_id, active) with no ownership check at all — Bob,
    who held no membership anywhere, got Alice's 10% club discount for
    free simply because her membership entity existed somewhere in the
    same graph. actor_id=None preserves the original unscoped behavior
    for any existing single-actor-per-graph caller that hasn't threaded
    an actor_id through yet (matching _find_wallet/find_payment_sources'
    same fallback convention). A membership tagged attributes["household"]
    is True is shared by any household member, same as a shared wallet.
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType
    for e in kg.entities_by_type(EntityType.OTHER):
        if e.attributes.get("membership") \
                and e.attributes.get("store_id") == store_id and e.attributes.get("active", True):
            if actor_id is None:
                return True
            if e.attributes.get("person_id") == actor_id or e.attributes.get("household") is True:
                return True
    return False


def household_subsidy_programs(kg) -> list:
    """Level 30 (GS-3000): which government subsidy programs (e.g. "WIC",
    "SNAP") this household is REALLY enrolled in — a genuine KG fact
    (attributes["enrolled_programs"] on some real entity in scope), not
    self-reported in the request text, which would let anyone claim a
    subsidy just by asking for one.
    """
    for e in kg.entities:
        programs = e.attributes.get("enrolled_programs")
        if programs:
            return programs
    return []


def apply_subsidy(product, programs: list) -> dict:
    """Level 30 (GS-3000): a real government subsidy applies
    AUTOMATICALLY — no code needed, unlike Level 23's coupon — but only
    when the household is actually enrolled AND the specific product is
    eligible under that program (attributes["subsidy_eligible_programs"]
    on the product itself, a real per-product eligibility fact, not
    "every enrolled household gets everything discounted"), capped at
    that program's real per-unit subsidy amount.
    """
    if not programs:
        return {"applied": False, "reason": "not enrolled in any subsidy program"}
    eligible_programs = product.attributes.get("subsidy_eligible_programs", [])
    matching = [p for p in programs if p in eligible_programs]
    if not matching:
        return {"applied": False, "reason": f"not eligible under {', '.join(programs)}"}
    price = product.attributes.get("price", 0)
    subsidy_amount = min(price, product.attributes.get("subsidy_max_amount", price))
    return {"applied": True, "program": matching[0], "subsidy_amount": round(subsidy_amount, 2)}


def government_purchase_limit(product) -> int | None:
    """Level 30 (GS-3001): a real, PERMANENT regulatory purchase limit
    (attributes["gov_purchase_limit"]) on a controlled/regulated item —
    unlike Level 29's emergency rationing cap (temporary, shortage-
    triggered, based on a household's recent purchase history), this is a
    standing legal constraint every purchase must respect regardless of
    supply or demand.
    """
    return product.attributes.get("gov_purchase_limit")


def declare_emergency_rationing(kg, category: str, ration_limit_per_household: int) -> dict:
    """Level 30 (GS-3002): a government emergency declaration cascades
    automatically to every REAL product in the declared category —
    tagging each with Level 29's rationed/ration_limit_per_household
    attributes, rather than requiring every product to be hand-tagged one
    at a time. This is the government ACTION; Level 29's
    apply_rationing_cap/allocate_ethically is the mechanism it triggers —
    a real integration between the two levels, not a duplicate of either.
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType
    kg.refresh()
    affected = []
    for e in kg.entities_by_type(EntityType.ASSET):
        if e.attributes.get("product") and category in _keywords(e.name):
            kg.update_entity(e.entity_id, attributes={"rationed": True, "ration_limit_per_household": ration_limit_per_household})
            affected.append(e.entity_id)
    return {"category": category, "ration_limit_per_household": ration_limit_per_household, "affected_products": affected}


def apply_discounts_to_candidates(kg, candidates, coupon_code: str | None, actor_id: str | None = None) -> tuple[list, dict]:
    """Level 23/30 (GS-2300/2301/2302/3000): applies a valid coupon,
    active membership discount, AND a real automatic government subsidy
    to whichever candidates they actually apply to, BEFORE ranking — not
    after. Applying a discount only to whichever candidate already won on
    raw price (the Level 21 approach) means a discount can only ever
    sweeten an already-winning pick, never change WHICH candidate wins;
    "evaluate a $2 coupon (or subsidy) against a lower unit price"
    requires comparing the ADJUSTED price against every other real
    option, which means adjusting before pick_best_unit runs, not after.
    Returns (adjusted_candidates, notes) — notes records what was
    actually applied where and any rejection, so the outcome stays
    explainable even though the discount is now baked into the price
    ranking sees.
    """
    import copy
    subsidy_programs = household_subsidy_programs(kg)
    notes = {"coupon": None, "memberships_applied": [], "subsidies_applied": []}
    adjusted = []
    coupon_seen_valid = False
    for c in candidates:
        store_id = c.attributes.get("store_id", "")
        price = c.attributes.get("price", 0)
        discount = 0.0
        shadow = c

        if coupon_code:
            coupon = validate_coupon(kg, coupon_code, store_id)
            if coupon["valid"]:
                coupon_seen_valid = True
                notes["coupon"] = coupon
                discount += coupon.get("discount_amount") or (price * (coupon.get("discount_percent") or 0) / 100)
            elif notes["coupon"] is None:
                notes["coupon"] = coupon  # keep the rejection reason if nothing validates it anywhere

        if has_active_membership(kg, store_id, actor_id):
            member_discount_pct = (kg.get_entity(store_id).attributes.get("membership_discount_percent", 0)
                                    if kg.get_entity(store_id) else 0)
            if member_discount_pct:
                discount += price * member_discount_pct / 100
                notes["memberships_applied"].append({"store_id": store_id, "discount_percent": member_discount_pct})

        subsidy = apply_subsidy(c, subsidy_programs)
        if subsidy["applied"]:
            discount += subsidy["subsidy_amount"]
            notes["subsidies_applied"].append({"product": c.name, "program": subsidy["program"],
                                                "amount": subsidy["subsidy_amount"]})

        if discount > 0:
            shadow = copy.deepcopy(c)
            shadow.attributes["price"] = round(max(0.0, price - discount), 2)
            shadow.attributes["discount_applied"] = round(discount, 2)
        adjusted.append(shadow)

    if coupon_code and not coupon_seen_valid and notes["coupon"] is None:
        notes["coupon"] = {"valid": False, "fraud_suspected": True,
                            "reason": f"no such coupon {coupon_code!r} exists — rejected as forged"}
    return adjusted, notes


_MACRO_TARGET_RE = re.compile(r"(\d+(?:\.\d+)?)\s*g(?:rams)?\s+(?:of\s+)?(protein|fiber|calcium|iron)", re.IGNORECASE)


def parse_macro_target(question: str) -> tuple[str, float] | None:
    """Level 24 (GS-2400): "need 120g protein this week" -> (nutrient,
    target_grams). None if the question doesn't name a real macro target,
    so the caller can tell "not this kind of question" from "malformed
    target"."""
    m = _MACRO_TARGET_RE.search(question)
    return (m.group(2).lower(), float(m.group(1))) if m else None


def build_macro_shopping_list(all_products, nutrient: str, target_g: float) -> dict:
    """Level 24 (GS-2400): builds a real shopping list meeting a stated
    macro target from actual product nutrition data
    (attributes[f"{nutrient}_g"]) — a greedy, nutrient-per-dollar-first
    selection, not a full knapsack optimizer. That's a deliberate choice,
    not a shortcut: every other ranking decision in this domain (cost,
    quality, time) is a simple, explainable rule a person could verify by
    hand, and a real optimizer's output is much harder to justify in a
    GS-1600-style explanation than "always take the most nutrient-
    efficient dollar next." feasible=False with whatever the greedy pass
    could still gather (not an empty list) — a caller can see exactly how
    close it got and why, not just that it failed.
    """
    candidates = [p for p in all_products if p.attributes.get(f"{nutrient}_g", 0) > 0]
    if not candidates:
        return {"feasible": False, "target_g": target_g, "achieved_g": 0.0, "selected": [], "total_cost": 0.0,
                "reason": f"no products with {nutrient} data available"}

    ranked = sorted(candidates, key=lambda p: p.attributes.get("price", float("inf")) / p.attributes.get(f"{nutrient}_g", 1))
    selected = []
    total_nutrient = 0.0
    total_cost = 0.0
    for p in ranked:
        if total_nutrient >= target_g:
            break
        selected.append({
            "id": p.entity_id, "name": p.name, "store_name": p.attributes.get("store_name"),
            f"{nutrient}_g": p.attributes.get(f"{nutrient}_g"), "price": p.attributes.get("price"),
        })
        total_nutrient += p.attributes.get(f"{nutrient}_g", 0)
        total_cost += p.attributes.get("price", 0)

    return {
        "feasible": total_nutrient >= target_g, "target_g": target_g, "achieved_g": round(total_nutrient, 1),
        "selected": selected, "total_cost": round(total_cost, 2),
        "reason": "" if total_nutrient >= target_g else
                  f"only {round(total_nutrient, 1)}g of {target_g}g achievable from available {nutrient} sources",
    }


def detect_inventory_inconsistency(kg, product_id: str, tolerance: float = 0.2) -> dict:
    """Level 21 (GS-2101): cross-checks a store's SELF-REPORTED quantity
    against an independent, trusted verification
    (attributes["verified_quantity"] — a warehouse audit or physical
    scanner reading). Same "a second, higher-trust source can disagree
    with the first" pattern Level 13's reconcile_beliefs established for
    GS-1201, applied here to flag tampering rather than just pick a
    winner — a compromised store feed reporting stock that doesn't
    physically exist should be caught, not silently trusted because it's
    the only number available. No verified_quantity present at all means
    nothing to cross-check against — not suspicious by default, since most
    seed data was never meant to carry this field.

    Reads off the CURRENT kg snapshot without its own refresh() — called
    once per candidate from open_products' filter comprehension, where a
    refresh per product would mean one full re-hydration per product in
    the catalog. Callers that need this against fresher-than-hydration
    data (as a standalone check, not via open_products) should refresh
    the kg themselves first.
    """
    product = kg.get_entity(product_id)
    if product is None:
        return {"consistent": True, "reason": "product not found"}
    reported = product.attributes.get("quantity")
    verified = product.attributes.get("verified_quantity")
    if verified is None or reported is None:
        return {"consistent": True, "reason": "no independent verification available"}
    discrepancy = (float("inf") if reported > 0 else 0.0) if verified == 0 else abs(reported - verified) / verified
    suspicious = discrepancy > tolerance
    return {
        "consistent": not suspicious, "reported_quantity": reported, "verified_quantity": verified,
        "discrepancy": discrepancy if discrepancy == float("inf") else round(discrepancy, 2),
        "reason": f"reported {reported} vs verified {verified} — "
                  f"{'exceeds' if suspicious else 'within'} {tolerance:.0%} tolerance",
    }


_SUSPICIOUS_PRICE_RATIO = 0.5  # priced below half the group's median is suspicious
_SUSPICIOUS_MIN_ATTEMPTS = 3   # fewer real transactions than this counts as "no track record"


def is_suspicious_new_seller(candidate, all_candidates_for_item, stores_by_id) -> dict:
    """Level 21 (GS-2102): an "impossibly low" price ALONE isn't suspicious
    (real sales happen) and a store with no track record ALONE isn't
    suspicious (every real store started with zero transactions once) —
    it's the COMBINATION that should withhold automatic selection: a
    brand-new, unproven seller undercutting the market by more than half.
    Trust and reputation exist for exactly this: no need to prove fraud to
    justify caution, just decline to auto-select until there's a track
    record — the same spirit as Level 17's learned avoidance, applied to
    the ABSENCE of evidence rather than a bad one.
    """
    store = stores_by_id.get(candidate.attributes.get("store_id")) if stores_by_id else None
    if store is None:
        return {"suspicious": False}
    attempts = store.attributes.get("fulfilled_count", 0) + store.attributes.get("cancelled_count", 0)
    prices = sorted(c.attributes.get("price", float("inf")) for c in all_candidates_for_item)
    median_price = prices[len(prices) // 2] if prices else 0
    price = candidate.attributes.get("price", 0)
    is_new = attempts < _SUSPICIOUS_MIN_ATTEMPTS
    is_too_cheap = median_price > 0 and price < median_price * _SUSPICIOUS_PRICE_RATIO
    suspicious = is_new and is_too_cheap
    return {
        "suspicious": suspicious, "attempts": attempts, "price": price, "median_price": median_price,
        "reason": (f"new seller ({attempts} prior transactions) pricing ${price} vs market median "
                   f"${median_price} — withheld from automatic selection") if suspicious else "",
    }


from src.monkey_brain.kernel.domains.finance import (
    process_payment_with_fallback, _find_wallet, find_payment_sources,
    _available_funds, choose_payment_source, attempt_borrowing,
    check_monthly_cap, _PAYMENT_SOURCE_PRIORITY,
)


def resolve_store_with_fallback(kg, store_id: str, product_id: str) -> dict:
    """Level 22 (GS-2201): a store marked attributes["status"]=="timeout"
    (a transient infra failure — "we couldn't reach it just now", distinct
    from is_open, which is a real business fact) falls back to the actor's
    own cached belief (Level 13's get_belief) rather than failing outright.
    No cache at all means genuinely nothing usable is known about it right
    now, reported honestly rather than guessed.
    """
    store = kg.get_entity(store_id)
    if store is None or store.attributes.get("status") != "timeout":
        return {"available": True, "source": "live"}
    cached = get_belief(kg, product_id)
    if cached is not None:
        return {"available": True, "source": "cache", "cached_attrs": cached,
                "reason": f"{store.name} unreachable (timeout) — using last known cached data"}
    return {"available": False, "source": None,
            "reason": f"{store.name} unreachable (timeout) and no cached data exists — excluded"}


def resume_partial_checkout(kg, order_id: str, cart: list[dict]) -> dict:
    """Level 22 (GS-2202): idempotent, resumable multi-item checkout. Each
    cart item is committed via try_reserve+confirm_reservation (Level 7's
    real CAS primitives), one at a time; an item ALREADY marked confirmed
    on a prior partial attempt is skipped entirely on resume — a retry of
    the whole request never re-reserves or re-confirms a unit that already
    went through, which is what makes retrying actually safe instead of a
    silent double-decrement.

    The order acts as the "actor" reserving each item (not the person) —
    ties every reservation to THIS specific checkout attempt, so two
    different orders for the same product from the same person don't
    collide with each other's reservations.
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType
    order = kg.get_entity(order_id)
    if order is None:
        kg.add_entity(order_id, EntityType.EVENT, "Partial Checkout", {
            "order_id": order_id, "status": "partial",
            "item_status": {item["id"]: "pending" for item in cart},
        })
        order = kg.get_entity(order_id)

    item_status = dict(order.attributes.get("item_status", {}))
    results = {}
    for item in cart:
        pid = item["id"]
        if item_status.get(pid) == "confirmed":
            results[pid] = {"status": "already_confirmed", "skipped": True}
            continue
        ok, msg = try_reserve(kg, pid, order_id, item.get("qty", 1))
        if not ok:
            item_status[pid] = "failed"
            results[pid] = {"status": "failed", "reason": msg}
            continue
        ok2, msg2 = confirm_reservation(kg, pid, order_id)
        item_status[pid] = "confirmed" if ok2 else "failed"
        results[pid] = {"status": item_status[pid], "reason": msg2}

    all_confirmed = all(v == "confirmed" for v in item_status.values())
    version = kg.version_of(order_id)
    kg.compare_and_swap(order_id, version, {
        "item_status": item_status, "status": "complete" if all_confirmed else "partial",
    })
    return {"order_id": order_id, "complete": all_confirmed, "results": results, "item_status": item_status}


# Level 47/48 note: the identity/policy/governance/RBAC/ABAC/SoD/audit/
# runtime-integrity core (Levels 34-48, everything below this import
# through separation_of_duties_satisfied in the previous revision of this
# file) now lives in domain_security.py — it never actually reasoned about
# groceries, it operated on generic KG entities/accounts/orgs, so it moved
# out to be shared by whatever domain module comes after this one. This
# import re-exports every one of those names under their original names,
# so every existing call site in this file, in prompt.py, and in the
# already-built test suite continues to work completely unchanged.
from src.monkey_brain.kernel.domains.domain_security import (
    parse_target_payment_actor, authorize_payment_source, authorize_resource_access,
    check_delegation, parse_delegator,
    find_household_policy, consume_policy_exception,
    is_same_household, _CONSTITUTIONAL_PROTECTED_CATEGORIES,
    enterprise_purchase_authorized,
    authorize_abac_access, consume_purchase_request, separation_of_duties_satisfied,
)
from src.monkey_brain.kernel.domains import domain_security as _domain_security

_REQUIRED_SECURITY_CAPABILITIES = (
    "DelegationCheck", "SocietyQuery", "ProductSelection", "OrderConfirmation",
    "OrderCreation", "PaymentConfirmation", "Payment",
)


def verify_required_capabilities(bus, required_names=_REQUIRED_SECURITY_CAPABILITIES) -> dict:
    """Grocery-specific default preserved here: the generic core in
    domain_security.py takes no default (required_names is genuinely
    domain-specific and must always be explicit there); this thin wrapper
    keeps every existing call site in this file/prompt.py/the test suite
    working exactly as it did before the extraction, unchanged.
    """
    return _domain_security.verify_required_capabilities(bus, required_names)


class GroceryCapabilityBus(CommerceCapabilityBus):
    """Capability bus for the grocery domain.

    Implements discover(name) for ActionExecutor compatibility.
    """
    pass


_RECIPE_TRIGGER_RE = re.compile(
    r"\b(?:cook|make|prepare|preparing)\b\s+([a-zA-Z][a-zA-Z\s]*?)(?:\s+tonight|\s+for\s+dinner|\s+today)?$",
    re.IGNORECASE,
)


def parse_recipe_name(question: str) -> str | None:
    m = _RECIPE_TRIGGER_RE.search((question or "").strip())
    return m.group(1).strip() if m else None


def find_recipe(kg, name: str):
    """Level 25 (GS-2500): looks up a REAL recipe entity by name (keyword
    match), returning None if nothing matches — a request to cook a dish
    the household has no recipe for must fail honestly, not hallucinate a
    plausible-looking ingredient list."""
    from src.monkey_brain.kernel.knowledge_graph import EntityType
    kg.refresh()
    name_kws = _keywords(name)
    for e in kg.entities_by_type(EntityType.OTHER):
        if e.attributes.get("recipe") and _keywords(e.name) & name_kws:
            return e
    return None


class DelegationCheckCapability:
    """Level 39 (GS-3900/3901/3902): "on behalf of alice" must be verified
    before anything else in the pipeline reads the request, since a
    denied delegation means nothing downstream should run at all, not
    just that the eventual purchase fails. Verifies a REAL, currently-
    active KG delegation grant (check_delegation) — expired or revoked
    delegations are denied with the real reason, never silently treated
    as "acting for yourself".

    Does NOT actually run first, or at all, automatically — like every
    other capability in this file, it only executes when the LLM planner
    includes it as a plan step (registered in the bus, no fixed pipeline
    position anywhere in belief_runtime.py/action_executor.py; confirmed
    by grep).

    Real gaps this closes (both real, both confirmed live-blocking):
    (1) grant_delegation() (domain_security.py) had zero callers anywhere
    — no real path ever created the grant this capability checks for, so
    every "on behalf of X" request was denied unconditionally. Fixed via
    POST /actors/{delegator_id}/delegations (api/routes/actors.py).
    (2) parse_delegator(question) only ever extracts a raw, lowercase
    WORD from the question text ("on behalf of alice" -> "alice") — it
    was passed straight into check_delegation/Neo4jBackedKnowledgeGraph
    as if it were a real actor_id, with no resolution step at all, unlike
    every other actor-addressing capability in this file (AskActor/
    DelegateTask both resolve a short/partial name via reachable_
    colleagues' first-token match — this one never did). Fixed below:
    the parsed word is resolved against the DELEGATE's own reachable
    colleagues before check_delegation ever runs.

    When active, context["actor_id"] is left untouched — Bob remains the
    authenticated author of the request for audit purposes — but
    context["acting_as"] is set to the delegator's real id, which is what
    actually makes Bob's purchase pay from Alice's real wallet
    (_paying_actor_id, below, scopes every financial lookup to acting_as
    when set) and lets PaymentConfirmation's revocation re-check (GS-3902)
    know which grant to re-verify.

    Real gap this closed: an earlier version of this capability also
    REPLACED context["knowledge_graph"] with a fresh, per-actor
    Neo4jBackedKnowledgeGraph scoped to the delegator — on paper, "make
    Bob operate in Alice's grocery domain." Confirmed live: this
    deployment's real commerce data (stores/products/wallets) lives
    entirely in the shared, Redis-backed pr.knowledge_graph every other
    capability in this file already reads/writes — no capability here
    has ever used per-actor Neo4j partitions for commerce data (that
    class only backs a separate, unrelated per-actor graph used by
    routes/knowledge_graph.py). Swapping to one mid-plan silently handed
    every downstream capability an almost-empty graph, and a real
    delegated purchase failed at ProductSelection with "planner selected
    unknown product" the moment delegation succeeded — the exact
    "silently erase the delegator's real wallet/products/stores" failure
    this capability's own OLD offline-only comment already warned about,
    just never applied to the online branch too. Since every actor in one
    PlanetaryRuntime already shares the SAME pr.knowledge_graph instance,
    there was never anything to switch: acting_as alone is both necessary
    and sufficient.
    """
    name = "DelegationCheck"

    def handle(self, args: dict) -> dict:
        context = args.get("context", {})
        question = context.get("question", "")
        delegate_id = context.get("actor_id", "")

        parsed_name = parse_delegator(question)
        if parsed_name is None or parsed_name == delegate_id:
            return {"success": True, "delegated": False}

        # Resolve the raw parsed word into a real actor_id, same
        # first-token/reachable-colleagues pattern AskActorCapability/
        # DelegateTaskCapability already use — scoped to who the DELEGATE
        # (the one making this request) can actually reach, never a
        # global name search. An unresolved or ambiguous name is never
        # silently guessed at: it either falls through to the raw parsed
        # word (check_delegation then honestly reports "no delegation
        # exists" for it, the same real denial this always gave) or, if
        # genuinely ambiguous, is denied outright with the specific
        # ambiguity named.
        delegator_id = parsed_name
        pr = context.get("planetary_runtime")
        if pr is not None:
            from src.monkey_brain.kernel.affiliations.reachability import reachable_colleagues
            candidates = [
                c for c in reachable_colleagues(pr, delegate_id)
                if c["name"].strip().lower().split(" ")[0] == parsed_name
            ]
            if len(candidates) == 1:
                delegator_id = candidates[0]["actor_id"]
            elif len(candidates) > 1:
                names = ", ".join(c["name"] for c in candidates)
                return {"success": False, "error": f"{parsed_name!r} is ambiguous — could mean any of: {names}"}

        check = check_delegation(delegator_id, delegate_id, kg=context.get("knowledge_graph"))
        if not check["active"]:
            return {"success": False, "error": f"delegation denied: {check['reason']}"}

        context["acting_as"] = delegator_id
        return {"success": True, "delegated": True, "delegator": delegator_id, "reason": check["reason"]}


class RecipeExpansionCapability:
    """Level 25 (GS-2500/2501/2502): "cook lasagna tonight" -> a real
    ingredient list from an actual recipe entity, rewritten into
    context["question"] as a plain "buy X, Y, Z" request so every
    downstream capability (BeliefCheck, HouseholdCognition — which is what
    makes GS-2501's "already own cheese, don't buy again" work for free,
    SocialSourcing, ProductSelection) operates on the real ingredients
    without needing its own recipe awareness. Must run FIRST, before
    SocietyQuery even builds its candidate pool from the ORIGINAL question
    text — a query for "cook lasagna tonight" would match zero real
    product names.
    """
    name = "RecipeExpansion"

    def handle(self, args: dict) -> dict:
        context = args.get("context", {})
        kg = context.get("knowledge_graph")
        question = context.get("question", "")

        recipe_name = parse_recipe_name(question)
        if not recipe_name or kg is None:
            return {"success": True, "expanded": False}

        recipe = find_recipe(kg, recipe_name)
        if recipe is None:
            return {"success": False, "error": f"no recipe found for {recipe_name!r}"}

        ingredients = recipe.attributes.get("ingredients", [])
        context["question"] = "buy " + ", ".join(ingredients)
        context["recipe_substitutes"] = recipe.attributes.get("substitutes", {})
        context["recipe_name"] = recipe.name
        return {"success": True, "expanded": True, "recipe": recipe.name, "ingredients": ingredients}


class SocietyQueryCapability:
    """Queries the actor's KG for stores in the Grocery Store Society."""
    name = "SocietyQuery"

    def handle(self, args: dict) -> dict:
        context = args.get("context", {})
        kg = context.get("knowledge_graph")
        question = context.get("question") or args.get("parameters", {}).get("description", "")

        if kg is None:
            return {"success": False, "error": "no knowledge graph available"}

        from src.monkey_brain.kernel.knowledge_graph import EntityType
        # onboard_merchant() (kernel/domains/commerce.py) never sets
        # attributes["type"] on a store — only owner_id, plus whatever the
        # caller passes through **store_attrs, which nothing in this
        # codebase ever sets to "grocery_store". This filter matched zero
        # stores unconditionally, no matter how many real, open stores
        # existed. is_open is the same real signal open_products() itself
        # uses to decide whether a store is a live candidate at all.
        stores = [e for e in kg.entities_by_type(EntityType.ORGANIZATION)
                  if e.attributes.get("is_open", True) is not False]

        products = open_products(kg, item_phrase=question)

        return {
            "success": True,
            "stores_found": len(stores),
            "products_found": len(products),
            "stores": [{"id": e.entity_id, "name": e.name, **e.attributes} for e in stores],
            "products": [{"id": e.entity_id, "name": e.name, **e.attributes} for e in products],
        }


class CounterfactualCapability:
    """Level 15 — a standalone, single-step plan (not part of the buy
    pipeline): answers "what if" and "compare" questions using
    simulate_plan/compare_plans, entirely read-only. Routed to directly by
    LLMPlanner when the question matches a counterfactual/comparison
    pattern, instead of the 8-step purchase pipeline every other grocery
    question gets — asking "what if Costco closes" should never itself
    close Costco, reserve stock, or spend money.
    """
    name = "Counterfactual"

    def handle(self, args: dict) -> dict:
        context = args.get("context", {})
        kg = context.get("knowledge_graph")
        question = context.get("question", "")
        optimization = context.get("optimization", "cost")

        if kg is None:
            return {"success": False, "error": "no knowledge graph available"}

        lactose_free = wants_lactose_free(question)
        rejected_keywords = get_rejected_keywords(kg)

        if _COMPARE_RE.search(question):
            item_phrases = _split_requested_items(question)
            if not item_phrases:
                return {"success": False, "error": "no grocery item mentioned to compare plans for"}
            lowered = question.lower()
            opts = [o for o in ("cost", "quality", "time") if o in lowered]
            if len(opts) < 2:
                opts = ["cost", "quality", "time"]
            result = compare_plans(kg, item_phrases, opts, lactose_free, rejected_keywords)
            return {"success": True, "kind": "compare_plans", **result}

        parsed = parse_counterfactual(question, kg)
        if parsed is None:
            return {"success": False, "error": "not a recognized what-if question"}
        if not parsed.get("resolved"):
            return {"success": False, "error": parsed.get("reason", "could not resolve counterfactual")}

        item_phrases = _split_requested_items(parsed.get("remainder") or question)
        if not item_phrases:
            return {"success": False, "error": "no grocery item mentioned to evaluate"}

        baseline = simulate_plan(kg, item_phrases, optimization, overrides=None,
                                  lactose_free=lactose_free, rejected_keywords=rejected_keywords)

        if parsed["kind"] == "store_closes":
            overrides, subject = parsed["overrides"], parsed["subject"]
        else:  # price_change — apply to whatever the baseline plan actually
            # picked ("the milk price doubles" means the deal I was about
            # to get, not every milk product at every store).
            if not baseline.get("feasible"):
                return {"success": True, "kind": "what_if", "baseline": baseline,
                        "counterfactual": baseline, "diff": {"changed": False}}
            target = baseline["selections"][0]
            overrides = {target["id"]: {"price": target["price"] * parsed["multiplier"]}}
            subject = target["name"]

        counterfactual = simulate_plan(kg, item_phrases, optimization, overrides=overrides,
                                        lactose_free=lactose_free, rejected_keywords=rejected_keywords)
        diff = diff_plans(baseline, counterfactual)

        return {
            "success": True, "kind": "what_if", "subject": subject,
            "baseline": baseline, "counterfactual": counterfactual, "diff": diff,
        }


def _format_explanation(trace: dict) -> str:
    """GS-1600: the plain-language rendering of a decision trace — goal,
    every real candidate weighed, the objective, the decision, and a
    derived confidence, in that order. This is what actually separates an
    executor from a cognitive system: not that a decision was made, but
    that the reasoning behind it can be reconstructed and shown.
    """
    lines = [f"Goal:\n{trace['item']}", "", "Candidates:"]
    for c in trace["candidates"]:
        detail = f"\n{c['store']}\nPrice: ${c['price']:.2f}\nTrust: {c['trust']:.2f}"
        if "effective_cost" in c:
            unit = c.get("effective_cost_unit", "total")
            detail += f"\nEffective cost (trust-adjusted, {unit}): ${c['effective_cost']:.2f}"
        if "quality" in c:
            detail += f"\nQuality: {c['quality']:.1f}/5"
        if "predicted_minutes" in c:
            detail += f"\nPredicted delivery: {c['predicted_minutes']:.0f} min"
        lines.append(detail)
    lines += [
        "", f"Optimization:\n{trace['optimization_label']}",
        "", f"Decision:\n{trace['chosen']['store']}",
        "", f"Confidence:\n{trace['confidence']}",
    ]
    return "\n".join(lines)


class ExplainCapability:
    """GS-1600 — "Explain Your Decision": answers "why did you choose X"
    from the REAL decision trace a past purchase actually produced
    (persisted onto its order by OrderCreationCapability), not a fresh
    re-simulation that could disagree with reality if the world has
    changed since, and not a fabricated-sounding summary. A standalone,
    read-only single-step plan routed by the LLM planner,
    same as CounterfactualCapability — asking why a decision was made must
    never itself trigger a new purchase.
    """
    name = "Explain"

    def handle(self, args: dict) -> dict:
        context = args.get("context", {})
        kg = context.get("knowledge_graph")
        question = context.get("question", "")

        if kg is None:
            return {"success": False, "error": "no knowledge graph available"}

        from src.monkey_brain.kernel.knowledge_graph import EntityType
        kg.refresh()
        # Isolation fix: this used to scan EVERY actor's orders sharing
        # this KG with no ownership filter at all — a generic "why did
        # you buy milk" from any actor could return a completely
        # different actor's real decision trace (candidate stores,
        # prices, trust scores, and reasoning), since the KG this
        # capability reads is shared across every actor a PlanetaryRuntime
        # manages, not partitioned per actor. "why did YOU choose X" is
        # inherently first-person — scoped to buyer_id (the same field
        # OrderCreationCapability persists onto every order), the
        # authenticated actor's own purchases only, never a household or
        # global view.
        actor_id = context.get("actor_id")
        orders = [
            e for e in kg.entities_by_type(EntityType.EVENT)
            if e.attributes.get("decision_traces") and e.attributes.get("buyer_id") == actor_id
        ]
        if not orders:
            return {"success": False, "error": "no past decision found to explain"}

        question_kw = _keywords(question)
        matches = []
        for order in orders:
            order_ts = order.attributes.get("created_at", order.created_at)
            for trace in order.attributes.get("decision_traces", []):
                if not question_kw or _keywords(trace.get("item", "")) & question_kw:
                    matches.append((order_ts, trace))

        if not matches:
            return {"success": False, "error": "no past decision found matching that item"}

        matches.sort(key=lambda t: -t[0])
        _, trace = matches[0]

        return {"success": True, "explanation": _format_explanation(trace), **trace}


class NutritionCapability:
    """Level 24 (GS-2400) — "need 120g protein this week": a standalone,
    read-only single-step plan (routed here by the LLMPlanner, same pattern
    as Counterfactual/Explain) that builds a real shopping list from
    actual product nutrition data rather than triggering the normal
    single/multi-item cart flow — meeting a macro target is a fundamentally
    different task shape (build a SET achieving a total) than picking one
    best item per requested phrase.
    """
    name = "Nutrition"

    def handle(self, args: dict) -> dict:
        context = args.get("context", {})
        kg = context.get("knowledge_graph")
        question = context.get("question", "")

        if kg is None:
            return {"success": False, "error": "no knowledge graph available"}

        target = parse_macro_target(question)
        if target is None:
            return {"success": False, "error": "no macro target recognized in the question"}
        nutrient, target_g = target

        all_products = open_products(kg)
        result = build_macro_shopping_list(all_products, nutrient, target_g)
        return {"success": True, "nutrient": nutrient, **result}


class BeliefCheckCapability:
    """Level 13 (GS-1200/1202): runs between SocietyQuery (which surfaces
    candidate products) and ProductSelection (which picks one), so a stale
    belief is caught and corrected BEFORE it can influence which product
    gets picked — not after, the way OrderConfirmation catches staleness on
    the item that was ALREADY selected (Level 6).

    For each candidate product the actor has a standing belief about, this
    force-refreshes ground truth and compares. A divergence means the
    actor's last observation of that product is now wrong (GS-1200); it's
    corrected immediately (form_belief) and reported in belief_updates
    rather than left for downstream steps to fail confusingly against.
    Candidates with no prior belief are observed for the first time.
    """
    name = "BeliefCheck"

    def handle(self, args: dict) -> dict:
        context = args.get("context", {})
        kg = context.get("knowledge_graph")
        products = context.get("products", [])

        if kg is None:
            return {"success": True, "belief_updates": []}

        updates = []
        reconciliations = []
        for p in products:
            entity_id = p.get("id")
            if not entity_id:
                continue
            prior = get_belief(kg, entity_id)
            if prior is None:
                form_belief(kg, entity_id)
            else:
                diffs = detect_belief_divergence(kg, entity_id)
                if diffs:
                    form_belief(kg, entity_id)
                    updates.append({
                        "product": p.get("name", entity_id),
                        "believed": {k: v[0] for k, v in diffs.items()},
                        "observed": {k: v[1] for k, v in diffs.items()},
                    })

            # Level 40 (GS-1201): a store's self-reported quantity and an
            # independent warehouse/scanner reading (verified_quantity)
            # can genuinely disagree without crossing
            # detect_inventory_inconsistency's fraud threshold —
            # open_products already excludes the EXTREME case outright,
            # but a smaller, honest discrepancy within tolerance still
            # means two real sources disagree. reconcile_beliefs resolves
            # this per-attribute (highest trust wins — a real warehouse
            # scan outranks a store's own self-report) so the actor's
            # belief record reflects the more trustworthy number instead
            # of whichever one happened to be self-reported, with a real,
            # inspectable attribution of which source won. Previously
            # fully built but never called anywhere in the pipeline.
            truth = kg.get_entity(entity_id)
            if truth is not None:
                reported_qty = truth.attributes.get("quantity")
                verified_qty = truth.attributes.get("verified_quantity")
                if reported_qty is not None and verified_qty is not None and reported_qty != verified_qty:
                    now = time.time()
                    resolved, attribution = reconcile_beliefs(kg, entity_id, [
                        {"source": "store_reported", "trust": 0.7, "attrs": {"quantity": reported_qty}, "observed_at": now},
                        {"source": "warehouse_scan", "trust": 1.0, "attrs": {"quantity": verified_qty}, "observed_at": now},
                    ])
                    reconciliations.append({
                        "product": p.get("name", entity_id),
                        "reported_quantity": reported_qty, "verified_quantity": verified_qty,
                        "reconciled_quantity": resolved.get("quantity"), "attribution": attribution,
                    })

        return {"success": True, "belief_updates": updates, "reconciliations": reconciliations}


class HouseholdCognitionCapability:
    """Level 20 (GS-2000/2001/2002): runs between BeliefCheck and
    SocialSourcing — before even considering how to ACQUIRE an item
    (borrow, negotiate, buy), check whether the household already has
    enough of it at home (GS-2000). Items resolved here are marked
    context["pantry_fulfilled"], same pattern as SocialSourcing's
    socially_fulfilled, so ProductSelection skips them entirely.

    GS-2001 (someone in the household already bought this recently) is
    NOT a purchase gate — reordering is always allowed. It's surfaced as
    context["household_notifications"] instead, informing the household
    that a new purchase is being made despite a recent one, rather than
    silently blocking it.

    When the pantry DOES have stock, also surfaces a predicted stockout
    (GS-2002) as an informational note — not a purchase decision, just
    "here's when you'll likely need to buy this again," derived from the
    household's own real purchase-rate history.
    """
    name = "HouseholdCognition"

    def handle(self, args: dict) -> dict:
        context = args.get("context", {})
        kg = context.get("knowledge_graph")
        question = context.get("question", "")
        actor_id = _paying_actor_id(context)

        if kg is None:
            return {"success": True, "pantry_fulfilled": {}, "notes": []}

        item_phrases = _split_requested_items(question)
        fulfilled: dict[str, dict] = {}
        household_notifications: dict[str, dict] = {}
        notes: list[str] = []

        for item_phrase in item_phrases:
            pantry_item = find_household_pantry_stock(kg, item_phrase, actor_id)
            if pantry_item and pantry_item.attributes.get("quantity", 0) > 0:
                fulfilled[item_phrase] = {"mode": "pantry_sufficient", "quantity": pantry_item.attributes.get("quantity")}
                note = f"Already have {pantry_item.attributes.get('quantity')} {pantry_item.name} at home — skipping purchase"
                reference_id = pantry_item.attributes.get("reference_product_id")
                if reference_id:
                    stockout = predict_household_stockout(kg, pantry_item, reference_id, actor_id=actor_id)
                    if stockout.get("days_remaining") is not None:
                        note += f" (predicted to last ~{stockout['days_remaining']:.1f} more day(s))"
                notes.append(note)
                continue

            # GS-2001: a recent household purchase is information, not a
            # purchase gate -- reordering is always allowed. Rather than
            # silently skipping the buy, the household is informed that
            # someone already bought this recently, so a duplicate order
            # is a known, visible choice, not a surprise on the bill.
            duplicate = check_recent_duplicate_purchase(kg, item_phrase, actor_id=actor_id)
            if duplicate:
                household_notifications[item_phrase] = {
                    "buyer": duplicate["buyer"],
                    "product": duplicate["product"],
                    "hours_ago": duplicate["hours_ago"],
                }
                notes.append(f"{duplicate['buyer']} already bought {duplicate['product']} "
                             f"{duplicate['hours_ago']}h ago — proceeding with a new purchase; "
                             f"household notified")

        context["pantry_fulfilled"] = fulfilled
        context["household_notifications"] = household_notifications
        return {"success": True, "pantry_fulfilled": fulfilled,
                "household_notifications": household_notifications, "notes": notes}


class SocialSourcingCapability:
    """Level 16 (GS-1500/1501): before buying an item from a STORE, check
    whether the actor's own social scope already covers it more cheaply —
    borrowing a neighbor's shareable surplus for free (GS-1500), or
    negotiating a peer-to-peer purchase that beats every store's price
    (GS-1501). Runs between BeliefCheck and ProductSelection; anything it
    resolves is recorded in context["socially_fulfilled"] so
    ProductSelection skips buying that item from a store entirely, instead
    of both a borrow AND a store purchase happening for the same item.

    Only applies to plain, unspecified-quantity items ("buy milk", qty 1)
    — same reasoning as Level 14's stockout-driven qty bump: an explicit
    "2L of milk" states exactly how much is wanted, and partially covering
    that via a single borrowed/negotiated unit would silently under-deliver
    against what was actually asked for.
    """
    name = "SocialSourcing"

    def handle(self, args: dict) -> dict:
        context = args.get("context", {})
        kg = context.get("knowledge_graph")
        question = context.get("question", "")
        actor_id = context.get("actor_id")

        if kg is None:
            return {"success": True, "socially_fulfilled": {}, "actions": []}

        from src.monkey_brain.kernel.knowledge_graph import EntityType
        item_phrases = _split_requested_items(question)
        fulfilled: dict[str, dict] = {}
        actions: list[str] = []

        for item_phrase in item_phrases:
            if _parse_requested_volume_ml(item_phrase) or _parse_requested_mass_g(item_phrase):
                continue

            # GS-1500: borrow first — it's free, and doesn't touch anyone's
            # real store inventory or the actor's wallet at all.
            borrowable = find_borrowable(kg, item_phrase, exclude_owner_id=actor_id)
            if borrowable:
                lender = max(
                    borrowable,
                    key=lambda e: e.attributes.get("quantity", 0) - sum(l.get("qty", 0) for l in e.attributes.get("loans", [])),
                )
                ok, msg = borrow_item(kg, lender.entity_id, actor_id, 1)
                if ok:
                    fulfilled[item_phrase] = {"mode": "borrow", "from": lender.attributes.get("owner_id"), "item": lender.name}
                    actions.append(f"Borrowed {lender.name} from {lender.attributes.get('owner_id')} instead of buying — {msg}")
                    continue

            # GS-1501: no one to borrow from — check for a neighbor selling
            # the item peer-to-peer, and negotiate against the best real
            # store price before accepting anything.
            _for_sale_kw = _keyword_surface_forms(_keywords(item_phrase))
            _for_sale_pool = (
                [e for e in kg.entities_by_keywords(_for_sale_kw) if e.entity_type == EntityType.ASSET]
                if _for_sale_kw else kg.entities_by_type(EntityType.ASSET)
            )
            for_sale = [
                e for e in _for_sale_pool
                if e.attributes.get("pantry")
                and e.attributes.get("for_sale") and e.attributes.get("owner_id") != actor_id
                and _match_score(e.name, item_phrase) > 0 and e.attributes.get("quantity", 0) > 0
            ]
            if not for_sale:
                continue
            seller = min(for_sale, key=lambda e: e.attributes.get("price", float("inf")))
            store_candidates = open_products(kg, item_phrase=item_phrase)
            store_best = min((p.attributes.get("price", float("inf")) for p in store_candidates), default=None)
            listed = seller.attributes.get("price", 0)
            floor = seller.attributes.get("min_price", listed)
            target = round(store_best * 0.9, 2) if store_best else round(listed * 0.9, 2)
            deal = negotiate_price(listed, floor, target)
            if deal["agreed"] and (store_best is None or deal["price"] < store_best):
                ok, msg = buy_from_neighbor(kg, seller.entity_id, actor_id, 1, deal["price"])
                if ok:
                    fulfilled[item_phrase] = {
                        "mode": "negotiated_purchase", "from": seller.attributes.get("owner_id"),
                        "item": seller.name, "price": deal["price"], "rounds": len(deal["rounds"]),
                    }
                    store_note = f" (best store price was ${store_best:.2f})" if store_best else ""
                    actions.append(
                        f"Negotiated {seller.name} from {seller.attributes.get('owner_id')} at "
                        f"${deal['price']:.2f} after {len(deal['rounds'])} round(s){store_note} — {msg}"
                    )

        context["socially_fulfilled"] = fulfilled
        return {"success": True, "socially_fulfilled": fulfilled, "actions": actions}


class ProductSelectionCapability:
    """Expose product facts and execute an explicit planner selection.

    This capability never ranks products, applies discounts, chooses stores,
    or infers substitutions. Those are planner decisions. The legacy
    implementation remains below as a migration reference but is not used by
    the capability bus.
    """
    name = "ProductSelection"

    def handle(self, args: dict) -> dict:
        context = args.get("context", {})
        kg = context.get("knowledge_graph")
        if kg is None:
            return {"success": False, "error": "no knowledge graph available"}
        parameters = args.get("parameters", {}) or {}

        if parameters.get("retry_after_failure"):
            # Same-tick cross-provider recovery (Qualification Gap
            # Closure, Phase 4; domain-independent per "MAKE DOMAIN
            # ISOLATION AIRTIGHT"): ActionExecutor's one-shot retry hook
            # (action_executor.py::_build_recovery_action) re-invokes this
            # SAME capability with its own ORIGINAL parameters unchanged,
            # plus the generic retry_after_failure marker -- the executor
            # itself never computed or supplied an "excluded_ids" list (it
            # has no idea what a "selection" is). This capability derives
            # its own exclusion set from its own original `selection`
            # parameter, which it already owned before the retry. A real
            # re-grounding search (the same open_products() this
            # capability's own legacy search path already uses) picks the
            # first real, currently-available candidate that is NOT one of
            # the ids that just failed -- genuinely excluding whatever
            # just failed, not retrying the identical selection.
            original_selection = parameters.get("selection") or parameters.get("selected") or []
            if isinstance(original_selection, dict):
                original_selection = [original_selection]
            excluded_ids = {
                item.get("id") if isinstance(item, dict) else str(item)
                for item in original_selection
            }
            selected = []
            for item in original_selection:
                original_id = item.get("id") if isinstance(item, dict) else str(item)
                qty = item.get("qty", 1) if isinstance(item, dict) else 1
                original_entity = kg.get_entity(original_id)
                phrase = original_entity.name if original_entity is not None else ""
                candidates = open_products(kg, context.get("actor_permissions"),
                                            context.get("actor_attributes"), item_phrase=phrase)
                alternative = next(
                    (p for p in candidates if p.entity_id not in excluded_ids),
                    None,
                )
                if alternative is None:
                    return {
                        "success": False,
                        "error": f"recovery: no real alternative available for {phrase or original_id!r}",
                        "recoverable": False,
                    }
                selected.append({"id": alternative.entity_id, "name": alternative.name,
                                  "qty": qty, **alternative.attributes})
            return {"success": True, "selected": selected, "planner_decision": True, "recovered": True}

        decision = parameters.get("selection") or parameters.get("selected")
        if decision:
            if isinstance(decision, dict):
                decision = [decision]
            # Real gap this closes: kg.get_entity(product_id) below only
            # confirms the id is SOME real entity anywhere in the whole
            # catalog — not that it was actually one of THIS tick's
            # offered candidates. Confirmed live: grounding correctly
            # scoped to milk products only, the model still selected an
            # unrelated, ungrounded id (Cage-Free Eggs) and the purchase
            # went through with no error. Gated on the set being non-
            # empty — a caller with no real grounding this tick (e.g.
            # skip-replan's reused plan, or a legacy/test caller) isn't
            # broken by a check it never had the evidence to pass.
            offered_ids = context.get("_relevant_knowledge_ids")
            selected = []
            for item in decision:
                product_id = item.get("id") if isinstance(item, dict) else str(item)
                if offered_ids and product_id not in offered_ids:
                    return {
                        "success": False,
                        "error": f"planner selected {product_id!r}, which was never offered in this "
                                 "execution's grounding — refusing to substitute an ungrounded product",
                    }
                entity = kg.get_entity(product_id)
                if entity is None:
                    return {"success": False, "error": f"planner selected unknown product: {product_id}"}
                # Real gap this closes: nothing on this path ever checked
                # stock. Reproduced live: a product patched to quantity=0
                # was still selected, ordered, paid, and delivered —
                # 6/6 success. open_products()'s type-substitution helper
                # (~line 1990) filters zero-stock candidates, but the
                # planner's direct `selection` here bypasses that helper
                # entirely, so a product offered before it sold out (or a
                # replayed/reused plan) sails through unchecked. Missing
                # attribute defaults to available (1), matching the
                # existing opt-in convention (is_open, warehouse_id,
                # open_products's own quantity default at line 2032) —
                # only an explicit non-positive quantity is rejected.
                available_qty = entity.attributes.get("quantity", 1)
                if available_qty is not None and available_qty <= 0:
                    return {"success": False, "error": f"{entity.name} is out of stock"}
                selected.append({"id": entity.entity_id, "name": entity.name,
                                 "qty": item.get("qty", 1) if isinstance(item, dict) else 1,
                                 **entity.attributes})
            return {"success": True, "selected": selected, "planner_decision": True}

        question = context.get("question", "")
        # GS-1500/1501/GS-2000: an item SocialSourcing already borrowed or
        # negotiated peer-to-peer, or one the household already has enough
        # of at home (HouseholdCognition), must not also be OFFERED as a
        # store-purchase candidate — it's already fulfilled. _legacy_handle
        # (below, confirmed dead — zero callers) already applied this exact
        # filter; this, the actual active candidate-building path, never
        # did, so a socially/pantry-fulfilled item was still offered here
        # and could still be bought a second time from a store.
        socially_fulfilled = context.get("socially_fulfilled") or {}
        pantry_fulfilled = context.get("pantry_fulfilled") or {}
        candidates = []
        for phrase in _split_requested_items(question):
            if phrase in socially_fulfilled or phrase in pantry_fulfilled:
                continue
            products = open_products(kg, context.get("actor_permissions"),
                                     context.get("actor_attributes"), item_phrase=phrase)
            candidates.append({"request": phrase, "products": tuple(
                {"id": p.entity_id, "name": p.name, **p.attributes} for p in products)})
        return {"success": True, "decision_required": True, "candidates": tuple(candidates)}

    def _legacy_handle(self, args: dict) -> dict:
        context = args.get("context", {})
        kg = context.get("knowledge_graph")
        optimization = context.get("optimization", "cost")
        question = context.get("question", "")

        if kg is None:
            return {"success": False, "error": "no knowledge graph available"}

        from src.monkey_brain.kernel.knowledge_graph import EntityType
        # Level 35 (GS-3503): opts into the restricted-resource check —
        # a specially-flagged item (e.g. a society's emergency reserve)
        # is excluded from ordinary candidate pools unless the actor's
        # real, verified permissions grant it.
        #
        # Performance certification (GS-6000): this used to fetch the WHOLE
        # open catalog ONCE upfront and reuse it for every item — O(catalog
        # size) regardless of how many items were actually being resolved,
        # which cost ~2s per request at 1M products even for a one-item
        # "buy milk". Each item now fetches its OWN pre-filtered pool below
        # (open_products(..., item_phrase=...)), narrowed via the KG's real
        # keyword index before any business-rule check runs — a per-item
        # search is now O(matching keyword count), not O(catalog size).
        stores_by_id = {e.entity_id: e for e in kg.entities_by_type(EntityType.ORGANIZATION)}

        lactose_free = wants_lactose_free(question)
        low_sodium = wants_low_sodium(question)
        excluded_allergens = wants_allergen_free(question)
        budget = parse_budget(question)
        item_phrases = _split_requested_items(question)
        rejected_keywords = get_rejected_keywords(kg)

        # GS-1500/1501: an item SocialSourcing already borrowed or
        # negotiated peer-to-peer must not ALSO be bought from a store —
        # it's already fulfilled, just not via a store purchase.
        socially_fulfilled = context.get("socially_fulfilled") or {}
        # GS-2000: an item the household already has enough of at home
        # needs no purchase at all. GS-2001 (a recent duplicate purchase)
        # no longer excludes an item here -- reordering is always allowed;
        # HouseholdCognition only informs the household about it.
        pantry_fulfilled = context.get("pantry_fulfilled") or {}
        item_phrases = [p for p in item_phrases if p not in socially_fulfilled and p not in pantry_fulfilled]

        def item_total(item):
            return item.get("price", 0) * item.get("qty", 1)

        selections = []
        reasons = []
        # (candidates, req_score, item_optimization) per item, aligned with
        # item_phrases — kept around so the consolidation pass below can (a)
        # require the same specificity/type at each candidate store instead
        # of re-deriving eligibility from scratch, and (b) pick among a
        # store's own candidates by the SAME criterion this item actually
        # used (quality for "healthy milk", not always cheapest) — it used
        # to always pick cheapest regardless, so a quality-optimized item
        # silently reverted to a cost pick the moment consolidation applied.
        eligible_per_item = []
        for item_phrase in item_phrases:
            # GS-1901: a bare "buy milk" with no stated type inherits the
            # actor's LEARNED type preference (from real past purchases),
            # not just the generic substitution policy's fallback order —
            # appended only to the phrase used for candidate resolution,
            # never to item_phrase itself (volume/mass parsing, req_score,
            # and every downstream match against the ORIGINAL request text
            # must keep seeing what the actor actually typed).
            effective_phrase = item_phrase
            preference_note = ""
            base = _base_item(item_phrase)
            if base:
                policy = _SUBSTITUTION_POLICIES.get(base, ())
                explicit_type = any(t in _keywords(item_phrase) for t in policy)
                if not explicit_type:
                    learned_type, type_stats = learned_preference(kg, base, "type_keyword")
                    if learned_type:
                        effective_phrase = f"{item_phrase} {learned_type}"
                        preference_note += (f"(inferred {learned_type} {base} from "
                                             f"{type_stats['counts'][learned_type]} prior purchases) ")

            item_products = open_products(kg, context.get("actor_permissions"), context.get("actor_attributes"),
                                           item_phrase=effective_phrase)
            candidates, note_or_error = eligible_candidates(effective_phrase, item_products, lactose_free, rejected_keywords,
                                                             low_sodium=low_sodium, excluded_allergens=excluded_allergens)
            if candidates is None:
                # GS-2502: a recipe-derived ingredient with nothing matching
                # at all tries the RECIPE's own listed substitutes before
                # failing outright — a real substitution the recipe itself
                # names ("ground turkey" for "ground beef"), not a generic
                # guess the way _SUBSTITUTION_POLICIES covers dairy alone.
                recipe_substitutes = (context.get("recipe_substitutes") or {}).get(item_phrase.strip(), [])
                for sub_name in recipe_substitutes:
                    sub_products = open_products(kg, context.get("actor_permissions"), context.get("actor_attributes"),
                                                  item_phrase=sub_name)
                    sub_candidates, _sub_note = eligible_candidates(sub_name, sub_products, lactose_free, rejected_keywords,
                                                                     low_sodium=low_sodium, excluded_allergens=excluded_allergens)
                    if sub_candidates is not None:
                        candidates = sub_candidates
                        note_or_error = f"{item_phrase.strip()!r} unavailable, substituted {sub_name!r} — "
                        break
                if candidates is None:
                    return {"success": False, "error": note_or_error}

            # GS-1900: brand loyalty narrows the candidate pool to the
            # actor's established brand BEFORE normal ranking runs — cost/
            # quality/time optimization still picks the best WITHIN that
            # brand (which store, which size), but a cheaper different
            # brand is no longer in consideration at all, matching "choose
            # it over a cheaper generic" rather than merely favoring it.
            if base:
                brand_pref, brand_stats = learned_preference(kg, base, "brand")
                if brand_pref:
                    brand_candidates = [c for c in candidates if c.attributes.get("brand") == brand_pref]
                    if brand_candidates:
                        candidates = brand_candidates
                        preference_note += (f"(brand loyalty: {brand_pref}, "
                                             f"{brand_stats['counts'][brand_pref]} prior purchases) ")

            # GS-2102: withhold a suspiciously cheap, unproven new seller
            # from AUTOMATIC selection — same principle as rejected_keywords
            # filtering, but only when at least one non-suspicious candidate
            # remains (never leave nothing purchasable just because every
            # remaining option happens to look new).
            non_suspicious = [c for c in candidates if not is_suspicious_new_seller(c, candidates, stores_by_id)["suspicious"]]
            if non_suspicious and len(non_suspicious) < len(candidates):
                preference_note += f"(withheld {len(candidates) - len(non_suspicious)} suspiciously cheap new-seller offer(s)) "
                candidates = non_suspicious

            # GS-2300/2301/2302: coupon and membership discounts are baked
            # into candidate prices BEFORE ranking — so "evaluate a coupon
            # against a lower unit price" is a real comparison the normal
            # cost ranking already does correctly, not a discount bolted
            # on after whichever candidate already won without it.
            coupon_code = parse_coupon_code(question)
            candidates, discount_notes = apply_discounts_to_candidates(kg, candidates, coupon_code, _paying_actor_id(context))
            if discount_notes["coupon"]:
                c_info = discount_notes["coupon"]
                if c_info["valid"]:
                    urgency = f" (expires in {c_info['expires_in_hours']}h — use it now)" if c_info.get("urgent") else ""
                    preference_note += f"(coupon {coupon_code} applied to price comparison{urgency}) "
                else:
                    preference_note += f"(coupon {coupon_code} rejected: {c_info['reason']}) "
            for m in discount_notes["memberships_applied"]:
                preference_note += f"(membership discount applied: {m['discount_percent']}%) "
            for s in discount_notes["subsidies_applied"]:
                preference_note += f"({s['program']} subsidy applied automatically: -${s['amount']:.2f}) "

            substitution_note = preference_note + note_or_error
            req_score = max(_match_score(c.name, item_phrase) for c in candidates)

            # GS-0400: this item's own preference wins if it states one
            # ("healthy milk"), else it inherits the request-level default
            # ("cheap bread" alongside it still resolves to cost, not
            # whatever milk's preference was).
            item_optimization = detect_optimization(item_phrase) or optimization
            eligible_per_item.append((candidates, req_score, item_optimization))

            volume_ml = _parse_requested_volume_ml(item_phrase)
            mass_g = _parse_requested_mass_g(item_phrase) if volume_ml is None else None
            extra_packages = []
            if volume_ml:
                best, qty, reason, extra_packages = pick_best_for_volume(candidates, volume_ml, item_optimization, stores_by_id)
            elif mass_g:
                best, qty, reason, extra_packages = pick_best_for_volume(candidates, mass_g, item_optimization, stores_by_id, size_key="size_g")
            else:
                best, qty, reason = pick_best_unit(candidates, item_optimization, stores_by_id)

            # GS-1301: only for a plain, unspecified-quantity request ("buy
            # milk", qty 1) — a volume/mass request already states exactly
            # how much the actor wants, and silently buying MORE than a
            # stated "2L" would be a surprising, unrequested cost increase,
            # not a helpful prediction. Applies before selections/reasons so
            # budget enforcement and consolidation below see the real qty
            # being bought, not a number they never accounted for.
            if volume_ml is None and mass_g is None:
                reorder_qty, stockout = recommend_reorder_quantity(kg, best.entity_id, qty, actor_id=context.get("actor_id"))
                # Inventory Constraints: recommend_reorder_quantity's own
                # cap (_MAX_REORDER_MULTIPLE x requested_qty) only guards
                # against its runaway-feedback-loop failure mode — it has
                # no notion of how much stock actually exists. Capping here
                # to on_hand stock is the same fix pick_best_for_volume
                # already applies to its own multi-unit picks; without it,
                # a demand-driven bulk buy could recommend more units than
                # the shelf holds, the exact oversell class Level 4 closed
                # for volume/mass requests, reopened here via a different
                # (unspecified-quantity) code path.
                on_hand = best.attributes.get("quantity")
                if on_hand is not None:
                    reorder_qty = min(reorder_qty, max(0, on_hand))
                if reorder_qty != qty:
                    reason += (f"; predicted stockout in {stockout['days_remaining']}d at "
                               f"~{stockout['daily_rate']}/day demand — buying {reorder_qty} "
                               f"instead of {qty} to cover it")
                    qty = reorder_qty

            # GS-3001: a real, permanent regulatory purchase limit is the
            # FINAL cap — applied after any stockout-driven bump, since a
            # legal ceiling on a controlled/regulated item must override
            # even a legitimate reason to want more, not just a starting
            # point other logic can push past.
            gov_limit = government_purchase_limit(best)
            if gov_limit is not None and qty > gov_limit:
                reason += f"; capped at {gov_limit} per purchase (regulatory limit), was requesting {qty}"
                qty = gov_limit

            selections.append({"id": best.entity_id, "name": best.name, "qty": qty, **best.attributes})
            # Quantity Optimization: pick_best_for_volume's mixed-package
            # combo returns additional package types beyond the primary
            # one — each is a real, separate line item (own id/qty/price),
            # the same shape OrderConfirmation/OrderCreation/Delivery
            # already expect for every entry in the selections list.
            for extra_c, extra_qty in extra_packages:
                selections.append({"id": extra_c.entity_id, "name": extra_c.name, "qty": extra_qty, **extra_c.attributes})
            reasons.append(f"{substitution_note}{best.name}: {reason}")

        # GS-0301: does splitting the cart across each item's own cheapest
        # store actually beat consolidating to one store that carries
        # everything, once delivery fees are counted? Naive per-item
        # optimization (what the loop above does on its own) ignores that a
        # second, third, fourth store each add their own delivery fee —
        # "cheaper item, more stops" isn't automatically the better plan.
        # Only runs when more than one store is actually in play; skipped
        # entirely for a single-store cart, since there's nothing to
        # consolidate.
        stores_used = {s.get("store_id") for s in selections if s.get("store_id")}
        if len(stores_used) > 1:
            # A store only qualifies if it carries something at the SAME
            # (candidates, req_score) this item already resolved to above —
            # reusing eligible_per_item, not re-deriving eligibility from
            # all_products, is what keeps this consistent with the
            # non-consolidated plan. Two bugs came from skipping that: (1)
            # plain _matches_request let a store with only Skim Milk count
            # as "carrying" a "whole milk" request; (2) recomputing
            # eligibility from scratch had no notion of the substitution
            # policy, so a generic "milk" item could get consolidated to
            # Skim Milk — cheaper, but never a candidate under the policy
            # (reduced -> whole -> oat -> almond) the main loop was honoring.
            candidate_store_ids = None
            for candidates, _req_score, _opt in eligible_per_item:
                # candidates is already resolved (dietary + specificity/
                # substitution) by eligible_candidates — no need to
                # re-check req_score here, every entry already satisfies it.
                stores_for_item = {c.attributes.get("store_id") for c in candidates}
                candidate_store_ids = stores_for_item if candidate_store_ids is None else candidate_store_ids & stores_for_item

            best_store_id, best_total, best_items = None, float("inf"), None
            for store_id in (candidate_store_ids or ()):
                fee = stores_by_id[store_id].attributes.get("delivery_fee", 0)
                store_items = []
                total = fee
                ok = True
                for i, phrase in enumerate(item_phrases):
                    candidates, _req_score, item_optimization = eligible_per_item[i]
                    at_store = [c for c in candidates if c.attributes.get("store_id") == store_id]
                    if not at_store:
                        ok = False
                        break
                    # Pick among THIS store's own candidates by the item's
                    # own preference, not always cheapest — a "healthy milk"
                    # (quality) item was silently reverting to the cheapest
                    # milk at the consolidated store, discarding the whole
                    # point of asking for quality. "time" has no further
                    # tie-breaker within a single store (every candidate here
                    # is already at the same distance), so it falls back to
                    # cost like the un-optimized default.
                    if item_optimization == "quality":
                        chosen = max(at_store, key=lambda e: e.attributes.get("quality", 0))
                    else:
                        chosen = min(at_store, key=lambda e: _unit_cost(e, stores_by_id))
                    # A volume request (e.g. "2L of milk") needs qty
                    # recomputed against THIS store's own size_ml, not
                    # copied from the original per-item pick — a different
                    # store can carry a different bottle size for the same
                    # product.
                    volume_ml = _parse_requested_volume_ml(phrase)
                    size = chosen.attributes.get("size_ml")
                    needed_qty = -(-volume_ml // size) if volume_ml and size else selections[i].get("qty", 1)
                    # Partial Inventory: "consolidate everything to one
                    # store" is only a real option if that store actually
                    # HAS enough of every item — without this check, a
                    # store with 2 units in stock could "win" consolidation
                    # by being quoted for however many units the request
                    # needed, oversold exactly like Level 4/16 already
                    # fixed for the non-consolidated path.
                    on_hand = chosen.attributes.get("quantity")
                    if on_hand is not None and needed_qty > on_hand:
                        ok = False
                        break
                    qty = needed_qty
                    store_items.append({"id": chosen.entity_id, "name": chosen.name, "qty": qty, **chosen.attributes})
                    total += chosen.attributes.get("price", 0) * qty
                if ok and total < best_total:
                    best_total, best_store_id, best_items = total, store_id, store_items

            if best_store_id is not None:
                naive_fees = sum(stores_by_id[sid].attributes.get("delivery_fee", 0) for sid in stores_used)
                naive_total = sum(item_total(s) for s in selections) + naive_fees
                if best_total < naive_total:
                    store_name = stores_by_id[best_store_id].name
                    best_fee = stores_by_id[best_store_id].attributes.get("delivery_fee", 0)
                    # Replace the per-item reasons, not append to them — they
                    # described the pre-consolidation picks, which this plan
                    # no longer uses; keeping them alongside the new picks
                    # would report a different store than what's now selected.
                    item_desc = "; ".join(f"{it['name']}: ${it['price']:.2f}" for it in best_items)
                    reasons = [
                        item_desc,
                        f"Consolidated to {store_name}: ${best_total:.2f} total (incl. ${best_fee:.2f} delivery) "
                        f"beats splitting across {len(stores_used)} stores: ${naive_total:.2f} total (incl. ${naive_fees:.2f} delivery)",
                    ]
                    selections = best_items

        # GS-0200: a stated budget is a hard cap, not a suggestion — if the
        # cost-optimal cart still exceeds it, drop the costliest item(s)
        # until it fits rather than silently completing an order over what
        # the actor said they were willing to spend.
        if budget is not None:
            cart_total = sum(item_total(i) for i in selections)
            dropped = []
            while cart_total > budget and len(selections) > 1:
                costliest = max(selections, key=item_total)
                selections.remove(costliest)
                dropped.append(costliest["name"])
                cart_total = sum(item_total(i) for i in selections)
            if cart_total > budget:
                return {
                    "success": False,
                    "error": f"cannot fit any item within budget ${budget:.2f} "
                             f"(cheapest is {selections[0]['name']} at ${item_total(selections[0]):.2f})",
                }
            if dropped:
                reasons.append(
                    f"Budget ${budget:.2f}: dropped {', '.join(dropped)} to fit "
                    f"(cart now ${cart_total:.2f})"
                )

        # GS-1600: built from the FINAL selections (post-consolidation,
        # post-budget-drop), matched back to each item's real candidate
        # pool by product id rather than by list position — consolidation
        # preserves per-item order and count, but a budget drop removes
        # items and shifts every later index, so position alone would
        # silently pair a trace with the wrong item. A selection that
        # can't be matched (shouldn't happen, but explanations must never
        # guess) is simply skipped rather than given a fabricated trace.
        decision_traces = []
        for sel in selections:
            for candidates, _req_score, item_optimization in eligible_per_item:
                if any(c.entity_id == sel.get("id") for c in candidates):
                    decision_traces.append(build_decision_trace(sel.get("name", ""), candidates, item_optimization, stores_by_id, sel))
                    break
        context["decision_traces"] = decision_traces

        return {
            "success": True,
            "selected": selections,
            "reason": "; ".join(reasons),
            "optimization": optimization,
            "decision_traces": decision_traces,
        }


class OrderConfirmationCapability:
    """Confirms an order — re-validates every item against a FRESH read of
    the world (not the snapshot from when the request started), replanning
    around anything that's gone stale instead of either blindly trusting an
    outdated pick or just failing.

    GS-0500/0501/0502: the world can change between when an item was
    selected and when the order is actually confirmed — sold out, price
    changed, the store closed. The old version compared a product's price
    against kg.get_entity(product["id"]) — but that reads the SAME
    in-memory snapshot the price came from in the first place, so it could
    only ever agree with itself; it could never detect a real concurrent
    change, only catch an internal-consistency bug. kg.refresh()
    (Neo4jBackedKnowledgeGraph) re-hydrates from Neo4j first so this check
    is against what's actually true right now.

    Fails closed only when no live alternative exists at all for an item —
    a partially-valid cart is not confirmed for the items that happen to
    check out while silently dropping the rest (that was the "milk and
    bread" bug, and the same principle applies to replanning: report what
    changed, don't just disappear it).
    """
    name = "OrderConfirmation"

    def handle(self, args: dict) -> dict:
        context = args.get("context", {})
        kg = context.get("knowledge_graph")
        products = context.get("selected_product", [])
        if isinstance(products, dict):
            products = [products]

        # A planner step that reaches OrderConfirmation without ever
        # running OrderCreation first (e.g. picking this action BY NAME
        # for "confirm the order" when it meant to create one) used to
        # report {"success": true, "status": "confirmed"} anyway — this
        # only ever re-validates cart freshness, it never checks that a
        # real, persisted Order actually exists. That's a false
        # "confirmed" for an order nothing downstream can find, pay for,
        # or ship. Same no-op convention as the empty-cart case below for
        # the one legitimate case nothing needs confirming at all.
        if not context.get("order"):
            if context.get("socially_fulfilled") or context.get("pantry_fulfilled"):
                return {"success": True, "note": "nothing to confirm — already fulfilled", "confirmed": []}
            return {"success": False, "error": "no order to confirm — OrderCreation has not run yet"}

        # Real gap this closes: a plan whose Payment step returned
        # requires_payment_confirmation (pending UPI authorization, never a
        # real charge) still let OrderConfirmation — and, unguarded the
        # same way, Delivery right after it — proceed and report success.
        # Confirmed live: the planner had silently dropped every
        # depends_on in the purchase chain (a real, separate planner-
        # reliability bug also being fixed alongside this), so nothing
        # blocked these steps on Payment's real outcome at the dependency-
        # graph level — but even with a correct dependency graph, nothing
        # here independently verified the money actually moved before
        # confirming and shipping the order. context["order"] is a stale
        # OrderCreation-time snapshot (never updated after Payment runs —
        # its own projector only ever sets it once, on "order_id" in
        # result), so this can't check IT; a fresh kg.get_entity(order_id)
        # read is required, same real "payment_status" == "paid" gate
        # CancelOrder/ReturnOrder/ApproveReturn/RefundOrder already use.
        order_id = context["order"].get("order_id")
        if order_id and kg is not None:
            fresh_order = kg.get_entity(order_id)
            if fresh_order is not None and fresh_order.attributes.get("payment_status") != "paid":
                return {"success": False,
                        "error": f"order {order_id!r} has not been paid for yet — cannot confirm"}

        if not products:
            # Level 16/20: an empty cart is only a REAL failure if nothing
            # was sourced any other way either. If SocialSourcing borrowed
            # or negotiated everything, or HouseholdCognition found the
            # pantry already covers it / a member already bought it, there's
            # genuinely nothing left to confirm from a store — a clean
            # no-op, not an error.
            if context.get("socially_fulfilled") or context.get("pantry_fulfilled"):
                return {"success": True, "note": "nothing to confirm — already fulfilled", "confirmed": []}
            return {"success": False, "error": "no product selected to confirm"}
        for product in products:
            if not product or not product.get("id"):
                return {"success": False, "error": "no product selected to confirm"}
            price = product.get("price")
            if price is None or price <= 0:
                return {"success": False, "error": f"invalid price for {product.get('name', product.get('id'))}: {price}"}

        if kg is not None and hasattr(kg, "refresh"):
            kg.refresh()

        from src.monkey_brain.kernel.knowledge_graph import EntityType
        # Performance certification (GS-6000): every product being
        # reconfirmed here is ALREADY a known id from ProductSelection's own
        # pick — this only needs to re-verify those SPECIFIC products still
        # pass the open-catalog business rules (is_open, supply chain,
        # policy, etc.), not re-scan the whole catalog. Each product's own
        # name is guaranteed to keyword-match itself, so combining all cart
        # item names into one item_phrase narrows the keyword-index lookup
        # to (at most) the cart's own items instead of every product that
        # exists, while still catching a real "used to qualify, now
        # doesn't" change exactly like the old full scan did.
        combined_names = " ".join(p.get("name", "") for p in products if p.get("name"))
        fresh_products = (open_products(kg, item_phrase=combined_names) if kg is not None else [])
        fresh_by_id = {p.entity_id: p for p in fresh_products}
        lactose_free = wants_lactose_free(context.get("question", ""))
        request_optimization = context.get("optimization", "cost")
        stores_by_id = {e.entity_id: e for e in kg.entities_by_type(EntityType.ORGANIZATION)} if kg is not None else {}
        rejected_keywords = get_rejected_keywords(kg) if kg is not None else frozenset()
        coupon_code = parse_coupon_code(context.get("question", ""))

        # Human approval / pause-resume (Qualification Gap Closure, Phase
        # 3): a generic opt-in, planner-supplied via context (matching
        # required_permission's own precedent -- the LLM/planner sets this
        # when the user's own prompt says "ask me first," never hardcoded
        # per-domain here). Fully backward compatible: every existing
        # caller that never sets this flag gets byte-for-byte the same
        # auto-replan behavior as before this phase. approval_decision/
        # approval_pending_candidate are threaded in by ActionExecutor.
        # execute() only on a resumed tick whose pending approval has
        # already been decided (kernel/pipeline/approval_store.py).
        approval_required = bool(context.get("approval_required_for_substitution")) or \
            wants_approval_before_substitution(context.get("question", ""))
        approval_decision = context.get("approval_decision")
        pending_candidate = context.get("approval_pending_candidate") or {}

        confirmed = []
        replans = []
        for product in products:
            live = fresh_by_id.get(product["id"])
            # Discounts & Coupons: ProductSelection's own price already
            # reflects any coupon/membership/subsidy adjustment
            # (apply_discounts_to_candidates) applied to a DEEP-COPIED
            # shadow entity — the real KG entity's price is never written
            # to, by design (the discount is per-request, not a genuine
            # price change). Comparing against the RAW live price here
            # meant every discounted purchase looked exactly like a Level
            # 6 dynamic-price change and silently lost the discount (or,
            # before Level 6's fix, failed outright with "no alternative
            # available"). Re-deriving the live price through the same
            # discount function before comparing is what actually
            # distinguishes "the discount you were quoted still applies"
            # from "the underlying price genuinely moved."
            live_priced = live
            if live is not None:
                adjusted, _notes = apply_discounts_to_candidates(kg, [live], coupon_code, _paying_actor_id(context))
                live_priced = adjusted[0] if adjusted else live
            stale_reason = None
            if kg is None:
                pass  # no KG available at all — nothing to re-validate against, trust the pick
            elif live is None:
                original = kg.get_entity(product["id"])
                if original is None:
                    stale_reason = f"{product['name']} no longer exists"
                else:
                    store = kg.get_entity(original.attributes.get("store_id"))
                    stale_reason = f"{store.name if store else product.get('store_name', 'the store')} has closed"
            elif live.attributes.get("quantity", 1) == 0:
                stale_reason = f"{product['name']} sold out at {product.get('store_name', 'the store')}"
            elif live_priced.attributes.get("price") != product.get("price"):
                stale_reason = (
                    f"{product['name']} price changed: was ${product.get('price'):.2f}, "
                    f"now ${live_priced.attributes.get('price'):.2f}"
                )

            if stale_reason is None:
                confirmed.append(product)
                continue

            # Dynamic Pricing: a price change alone (the item is still the
            # SAME live entity, still in stock, at the SAME store — just
            # costs a different amount now) is not unavailability. The
            # generic replan path below excludes this exact entity_id from
            # its own candidate search (by design, for genuinely-gone
            # items), which for a price-only change meant the only store
            # selling an item failed the whole order with "no alternative
            # available" the moment its price moved at all — a plain price
            # fluctuation should never behave like a stockout. Re-price the
            # SAME item from the live entity and proceed; only a real
            # stockout or store closure (handled above) falls through to
            # search for a genuine alternative below.
            if live is not None and live.attributes.get("quantity", 1) != 0 and live_priced.attributes.get("price") != product.get("price"):
                repriced = {**product, **live_priced.attributes, "id": live.entity_id, "name": live.name}
                confirmed.append(repriced)
                replans.append(f"{stale_reason} — repriced to ${live_priced.attributes.get('price', 0):.2f}, same item")
                continue

            # Resuming after a real human decision on THIS exact product's
            # substitution: commit exactly the candidate that was proposed
            # (never recompute — the world may have moved again since the
            # human was asked, and approving what was actually shown is
            # the only honest contract), or fail the whole order if
            # rejected (same fail-closed philosophy this capability's own
            # docstring already commits to for a partially-valid cart —
            # a rejected substitution is not silently dropped while the
            # rest of the order proceeds).
            if pending_candidate.get("original_id") == product["id"] and approval_decision is not None:
                if approval_decision:
                    confirmed.append(dict(pending_candidate["replacement"]))
                    replans.append(f"{stale_reason} — approved substitution to {pending_candidate['replacement'].get('name', '?')}")
                    continue
                return {"success": False, "error": f"{stale_reason} — substitution for {product['name']} was not approved"}

            # Replan: re-resolve this item fresh, excluding whatever just
            # went stale (and any now-closed store, via open_products
            # above), using the product's own name as the search phrase —
            # good enough for a repeat search ("Whole Milk 1L" still
            # matches "whole" + "milk"), without needing to thread the
            # original free-text item phrase all the way to this capability.
            remaining = [p for p in fresh_products if p.entity_id != product["id"]]
            candidates, note_or_error = eligible_candidates(product["name"], remaining, lactose_free, rejected_keywords)
            if candidates is None:
                return {"success": False, "error": f"{stale_reason} — no alternative available"}
            item_optimization = detect_optimization(product["name"]) or request_optimization
            best, qty, _reason = pick_best_unit(candidates, item_optimization, stores_by_id)
            replacement = {"id": best.entity_id, "name": best.name, "qty": product.get("qty", qty), **best.attributes}

            if approval_required:
                # Generic pause point (kernel/pipeline/action_executor.py
                # reads requires_approval/proposed_action/reason off any
                # capability's result — nothing grocery-specific about
                # the contract, this is just the first real capability
                # that opts into it). The tick stops here; no substitution
                # happens until a real human decision resumes it.
                return {
                    "success": False,
                    "requires_approval": True,
                    "proposed_action": {
                        "original_id": product["id"], "original_name": product["name"],
                        "replacement": replacement,
                    },
                    "reason": (
                        f"{stale_reason} — proposing to substitute {best.name} at "
                        f"{best.attributes.get('store_name', '?')} (${best.attributes.get('price', 0):.2f})"
                    ),
                }

            confirmed.append(replacement)
            replans.append(
                f"{stale_reason} — replanned to {best.name} at {best.attributes.get('store_name', '?')} "
                f"(${best.attributes.get('price', 0):.2f})"
            )

        # ActionExecutor's generic context wiring only threads
        # ProductSelection's "selected" key into context["selected_product"]
        # — nothing wires THIS capability's output back in. Mutating
        # context directly is what actually propagates a replan to
        # OrderCreation/Delivery instead of it silently reading the
        # original, now-stale cart.
        context["selected_product"] = confirmed

        # Level 17 write side: the other half of cancel_order()'s
        # fulfilled=False wiring — a cart that reaches a real confirmation
        # (payment already succeeded, per the canonical plan order this
        # step runs after Payment) is a genuine positive outcome for
        # every store it's actually fulfilling from, the same signal
        # has_learned_to_avoid()'s docstring describes a store needing to
        # climb back above the avoidance threshold.
        if kg is not None:
            confirmed_store_ids = {p.get("store_id") for p in confirmed if p.get("store_id")}
            for store_id in confirmed_store_ids:
                record_order_outcome(kg, store_id, fulfilled=True)

        result = {"success": True, "status": "confirmed", "product": confirmed}
        if replans:
            result["replanned"] = replans
        return result


class InventoryReserveCapability:
    """MB-3100 True Multi-Actor Coordination: reserves inventory for the
    most recently placed, not-yet-reserved order — wraps the real,
    already-production try_reserve() (the same CAS/no-oversell reservation
    POST /inventory/reserve calls), not new reservation logic.

    Deliberately does NOT take a planner-specified order/product — this
    is the real action an Inventory Society's own actor (e.g. an
    Inventory Robot) takes when coordinated by a real OrderCreated
    propagation event: a self-directed react-tick has no per-request
    context handed to it beyond its own goal text (goal-driven planning
    has no way to know "reserve inventory for THIS SPECIFIC order" — it
    only knows its own goal, e.g. "reserve_inventory"), so this looks up
    the real order that actually needs reserving from the KG itself,
    the same way DeliveryCapability/select_delivery_riders() resolve
    real KG state rather than trusting planner-supplied specifics.
    """
    name = "InventoryReserve"

    def handle(self, args: dict) -> dict:
        context = args.get("context", {})
        kg = context.get("knowledge_graph")
        if kg is None:
            return {"success": False, "error": "no knowledge graph available"}

        from src.monkey_brain.kernel.knowledge_graph import EntityType
        all_events = kg.entities_by_type(EntityType.EVENT)
        orders = [
            e for e in all_events
            if e.attributes.get("order_id") and e.attributes.get("status") == "confirmed"
            and not e.attributes.get("inventory_reserved")
        ]
        if not orders:
            # Diagnostic detail, not decoration: this branch was hit live
            # against a real order confirmed to exist via GET /orders/{id}
            # (same kg.get_entity() by id) — the cause wasn't understood
            # yet, so report exactly what the type index itself saw rather
            # than a bare "not found" that would hide the real state.
            statuses = [
                (e.entity_id, e.attributes.get("status"), bool(e.attributes.get("inventory_reserved")))
                for e in all_events if e.attributes.get("order_id")
            ]
            return {
                "success": False,
                "error": f"no pending order to reserve inventory for (event_entities={len(all_events)}, orders_seen={statuses})",
            }
        order = max(orders, key=lambda e: e.attributes.get("created_at", 0))

        reserved = []
        reasons = []
        for item in order.attributes.get("items", []):
            product_id = item.get("product_id")
            if not product_id:
                continue
            ok, reason = try_reserve(kg, product_id, order.attributes.get("buyer_id", ""), item.get("qty", 1))
            if ok:
                reserved.append(product_id)
            else:
                reasons.append(f"{product_id}: {reason}")

        if not reserved:
            return {"success": False, "error": f"inventory reservation failed for every item on the order: {reasons}"}

        kg.add_entity(order.entity_id, order.entity_type, order.name, {
            **order.attributes, "inventory_reserved": True,
        })
        return {"success": True, "order_id": order.entity_id, "reserved": reserved}


class InventoryReleaseCapability:
    """MB-3102 True Multi-Actor Coordination: releases any active
    reservation — the real reaction an Inventory Society's own actor
    takes when a PaymentDeclined event means a hold that was placed
    (whether by InventoryReserveCapability's own order-driven flow, or
    pre-existing — e.g. via the real POST /inventory/reserve API,
    independent of any specific order) will never be confirmed. Wraps
    release_reservation() (this module, mirrors try_reserve's real CAS
    mechanism), not new logic. Deliberately scans every ASSET product's
    live reservations rather than resolving through a specific order —
    a PaymentDeclined reaction has no per-request context connecting it
    to which order failed, the same real constraint documented on
    InventoryReserveCapability above; a world with multiple concurrent
    unpaid orders would need a real correlation id this demo doesn't
    model."""
    name = "InventoryRelease"

    def handle(self, args: dict) -> dict:
        context = args.get("context", {})
        kg = context.get("knowledge_graph")
        if kg is None:
            return {"success": False, "error": "no knowledge graph available"}

        import time as _time
        from src.monkey_brain.kernel.knowledge_graph import EntityType

        now = _time.time()
        released = []
        for product in kg.entities_by_type(EntityType.ASSET):
            active = [r for r in product.attributes.get("reservations", []) if r.get("until", 0) > now]
            for r in active:
                ok, _reason = release_reservation(kg, product.entity_id, r.get("actor_id", ""))
                if ok:
                    released.append(product.entity_id)

        # Shared multi-agent budget (Qualification Gap Closure, Phase 5):
        # the identical reactive release, generalized — a budget entity
        # (EntityType.ACCOUNT, attrs["reservations"]) is drawn against with
        # the exact same try_reserve/confirm_reservation/release_reservation
        # CAS primitives as inventory (see create_shared_budget), so the
        # SAME release_reservation() call restores it. A real actor wallet
        # (also EntityType.ACCOUNT) simply has no "reservations" attribute
        # and yields an empty list here — genuinely inert for every
        # non-budget account, not a new special case.
        for account in kg.entities_by_type(EntityType.ACCOUNT):
            active = [r for r in account.attributes.get("reservations", []) if r.get("until", 0) > now]
            for r in active:
                ok, _reason = release_reservation(kg, account.entity_id, r.get("actor_id", ""))
                if ok:
                    released.append(account.entity_id)

        if not released:
            return {"success": False, "error": "no active reservation to release"}

        return {"success": True, "released": released}


class LoyaltyAwardCapability:
    """MB-3105 True Multi-Actor Coordination: awards real, persisted
    loyalty points — wraps finance.py's award_points() (MB-3049), no new
    loyalty logic. Same "look up the real thing needing action, don't
    trust planner-supplied specifics" design as InventoryReserveCapability:
    a reactive react-tick has no per-request context connecting it to
    which order just completed, so this finds the most recently
    completed order itself. award_points() is already idempotent per
    order_id (finance.py), so a retry here is always safe."""
    name = "LoyaltyAward"

    def handle(self, args: dict) -> dict:
        context = args.get("context", {})
        kg = context.get("knowledge_graph")
        if kg is None:
            return {"success": False, "error": "no knowledge graph available"}

        from src.monkey_brain.kernel.knowledge_graph import EntityType
        from src.monkey_brain.kernel.domains.finance import award_points

        completed_orders = [
            e for e in kg.entities_by_type(EntityType.EVENT)
            if e.attributes.get("order_id") and e.attributes.get("status") == "completed"
        ]
        if not completed_orders:
            return {"success": False, "error": "no completed order to award points for"}
        order = max(completed_orders, key=lambda e: e.attributes.get("completed_at", 0))
        buyer_id = order.attributes.get("buyer_id", "")
        if not buyer_id:
            return {"success": False, "error": "completed order has no buyer_id"}
        return award_points(kg, buyer_id, order.entity_id)


class AnswerQuestionCapability:
    """Natural Language Actor-to-Actor Communication: an actor
    independently reasoning about a question a colleague asked it,
    grounded in real KG facts, and replying in real natural language —
    not a structured business-field dict like every other capability
    here returns.

    Reuses the SAME keyword-matched entity lookup
    ContextConstructionEngine._explore_knowledge() already uses for
    planning (kernel/pipeline/planning/context_engine.py), so "what
    facts is this actor grounded in" isn't reinvented here — only
    formatted with full real attributes (quantity, status, etc.), not
    just the truncated id/price PlanningContext.relevant_knowledge
    carries, since a conversational reply benefits from more complete
    facts than a plan step's decision contract does. The reply itself
    is a real LLM call (get_backend().complete(), the same backend
    LLMPlanner uses) — never a template, never scripted."""
    name = "AnswerQuestion"

    async def handle(self, args: dict) -> dict:
        context = args.get("context", {})
        parameters = args.get("parameters", {}) or {}
        question = context.get("question") or parameters.get("question")
        if not question:
            return {"success": False, "error": "no question provided"}

        kg = context.get("knowledge_graph")
        actor_id = context.get("actor_id", "")
        actor_role = context.get("actor_role", "")

        facts = self._gather_facts(kg, actor_id, question)

        # Game-Theoretic Reasoning: a negotiation reply should reflect
        # THIS actor's own real constraints (a Driver's real delivery
        # capacity, a Warehouse's real throughput preference), not just
        # KG entity facts — without this, "Driver negotiates an
        # alternative schedule" degrades to a generic reply with
        # nothing actually strategic behind it.
        strategy = _actor_strategy_profile(context)
        strategy_facts = []
        if strategy.get("preferences"):
            strategy_facts.append(
                "Your preferences: " + ", ".join(f"{k}={v}" for k, v in strategy["preferences"].items())
            )
        if strategy.get("resources"):
            strategy_facts.append(
                "Your resources: " + ", ".join(f"{k}={v}" for k, v in strategy["resources"].items())
            )
        if strategy.get("risk_tolerance") is not None:
            strategy_facts.append(f"Your risk tolerance: {strategy['risk_tolerance']}")
        if strategy.get("negotiation_policy"):
            strategy_facts.append(f"Your negotiation stance: {strategy['negotiation_policy']}")

        from src.monkey_brain.kernel.execute.provider.model_backend import get_backend

        facts_text = "\n".join(f"- {f}" for f in facts) if facts else "(no specific facts found for this question)"
        if strategy_facts:
            facts_text += "\n" + "\n".join(f"- {f}" for f in strategy_facts)
        system = (
            "You are a real worker inside an operational system, replying "
            "to a colleague or customer in natural language. Answer using "
            "ONLY the facts listed below — if a fact isn't listed, say you "
            "don't know rather than inventing one. If your own preferences, "
            "resources, or negotiation stance are listed, let them genuinely "
            "shape your answer (e.g. propose a real alternative that fits "
            "your real constraints instead of just agreeing or refusing). "
            "Reply in 1-3 short, plain, conversational sentences. No "
            "markdown, no bullet points, no headers."
        )
        prompt = (
            f"You are: {actor_role or 'a colleague'}\n\n"
            f"Facts you know:\n{facts_text}\n\n"
            f"A colleague asks you: \"{question}\"\n\n"
            f"Your natural-language reply:"
        )
        answer = (await get_backend().complete(prompt, system=system)).strip()
        return {"success": True, "answer": answer, "facts_used": facts}

    @staticmethod
    def _gather_facts(kg: Any, actor_id: str, question: str) -> list[str]:
        if kg is None:
            return []
        keywords = [w for w in re.findall(r"[a-zA-Z]+", question.lower()) if len(w) > 2]
        if not keywords:
            return []
        facts: list[str] = []
        for entity in kg.entities_by_keywords(keywords):
            # Found live (actor_chat's own KG lookup surfaced these):
            # EpisodicTrace (an actor's own recorded conversations/
            # experiences — MemoryManager's KG-indexed mirror, see
            # kernel/learn/memory/manager.py) and purchase_log marker
            # entities are memory bookkeeping, not real facts about the
            # world — the exact same class of contamination
            # context_engine.py::_may_explore_entity and
            # observations.py::WorldPollingProvider.observe already
            # exclude for planning grounding/beliefs, but this separate
            # KG lookup (shared by /ask and the actor chat) never got
            # that same fix. EVENT entities (e.g. "Grocery Order") share
            # one literal name across every instance any actor ever
            # created, so a keyword match here would misleadingly
            # attribute one actor's order to whichever question asked
            # about "order" — same reasoning as observations.py's own
            # EVENT exclusion.
            if entity.attributes.get("label") == "EpisodicTrace" or entity.attributes.get("purchase_log"):
                continue
            if getattr(entity.entity_type, "value", entity.entity_type) == "event":
                continue
            if len(facts) >= 8:
                break
            attrs = ", ".join(f"{k}={v}" for k, v in entity.attributes.items() if v is not None)
            facts.append(f"{entity.name} ({entity.entity_type.value}, id={entity.entity_id}): {attrs}")
            for connected_id in kg.connected_entities(entity.entity_id)[:3]:
                connected = kg.get_entity(connected_id)
                if connected is not None:
                    facts.append(f"{entity.name} is connected to {connected.name} ({connected.entity_type.value})")
        return facts


async def _run_delegated_tasks(
    pr: Any, actor_id: str, actor_role: str, tasks: list, shared_budget_id: str | None = None,
    verified_delegation: dict | None = None, delegation_chain: tuple = (),
) -> dict:
    """Real dispatch shared by subscribe_actor_inbox's NATS path and
    DelegateTaskCapability's in-process fallback below -- exactly one real
    execution path, not two. Runs the TARGET actor's own capability chain
    through the same, single, shared ActionExecutor
    (pr._execution_engine, kernel/society/integration.py:351) every other
    real request in this system already goes through: real Action tuples,
    real depends_on chaining, real capability discovery/error handling,
    and (for free) every existing execution hook (world-mutation/
    fault-injection/checkpoint-resume) -- never a second, parallel
    capability-invocation path invented for this feature.

    `tasks`: list of {"capability": str, "parameters": dict,
    "depends_on": [int, ...]} entries -- a real, potentially multi-step
    chain, not just a single call."""
    if not tasks:
        return {"success": False, "error": "delegated_task requires a non-empty tasks list"}
    from src.monkey_brain.kernel.pipeline.execution import Action

    actions = tuple(
        Action(
            action_id=f"{actor_id}_delegated_{i}", capability=t.get("capability", ""),
            step_index=i, depends_on=tuple(t.get("depends_on", ()) or ()),
            parameters=t.get("parameters", {}) or {},
        )
        for i, t in enumerate(tasks)
    )
    context = {
        "knowledge_graph": pr.knowledge_graph, "actor_id": actor_id,
        "actor_role": actor_role, "question": "", "planetary_runtime": pr,
    }
    if shared_budget_id:
        # Threaded from the asker's own tick (DelegateTaskCapability
        # below) -- the delegated actor's own sub-context otherwise has
        # no "question" text of its own to derive one from (set to "" a
        # few lines up), so a real shared budget can only ever reach here
        # by being passed explicitly.
        context["shared_budget_id"] = shared_budget_id
    if verified_delegation is not None:
        # The ONLY trusted-injection seam action_executor.py reads
        # (context["verified_delegation"], populated exclusively from an
        # ALREADY-verified chain -- see kernel/edge/delegation_message.py
        # and action_executor.py's own "Portable Delegation integration
        # point" comment). Never populated from anything agent-claimed.
        context["verified_delegation"] = verified_delegation
        # Raw parsed chain for the EDGE/local decision path
        # (LocalGovernanceEvaluator.evaluate(), which independently
        # re-verifies it) -- a separate consumer from verified_delegation
        # above (OPA's shaped input), not a duplicate of it.
        context["delegation_chain"] = delegation_chain
    exec_result = await pr._execution_engine.execute(actions, context)
    return {
        "success": exec_result.goal_achieved,
        "success_count": exec_result.success_count, "failure_count": exec_result.failure_count,
        "actions": [
            {"capability": a.capability, "success": o.success, "result": o.result, "error": o.error}
            for a, o in zip(actions, exec_result.actions)
        ],
    }


async def subscribe_actor_inbox(pr: Any, actor_id: str, actor_role: str) -> bool:
    """Multi-Actor Execution Handoff: real per-actor NATS inbox, the
    receiving half of AskActorCapability's real point-to-point
    request/reply (below). Subject monkeybrain.actor.{actor_id}.inbox —
    on a real message, answers it through the exact same
    AnswerQuestionCapability the in-process version used to call
    directly, then replies via NATS's own auto-generated reply-subject
    (msg.respond), which is what makes this real request/reply rather
    than a second hand-rolled correlation-id scheme.

    Called from both real actor-registration call sites (api/routes/
    actors.py, kernel/society/runtime.py) via asyncio.create_task — those
    are synchronous call sites today (register_actor isn't async), and
    turning them async is out of scope here; fire-and-forget subscription
    setup, same non-fatal-if-NATS-is-down convention every other real
    NATS use in this codebase already follows (kernel/society/
    context_stream.py's own publish()). Returns False (never raises)
    when there's no NATS connection to subscribe on."""
    nc = getattr(pr, "_nats_client", None)
    if nc is None:
        return False

    async def _on_message(msg: Any) -> None:
        import json
        # Runtime Approval Gate: this NATS callback runs in its own task,
        # not the asking actor's request context -- nothing previously
        # bound a principal here, so any governed capability this handler
        # goes on to execute (AnswerQuestionCapability, a delegated task's
        # own capability steps) picked up whatever TrustedAuthEvidence
        # happened to be ambient (the asker's, if the asyncio context was
        # inherited, or unauthenticated_evidence() otherwise) instead of
        # the RESPONDING actor's own identity. Same pattern as
        # actor_runtime.py's per-Pod POST /execute entrypoint: the actor
        # answering here authenticates as itself, never by inheriting (or
        # merely trusting) whichever actor's message triggered this reply.
        #
        # SPIFFE/SPIRE workload identity layer: prefer a REAL, verified
        # workload identity over the plain service-name evidence above.
        # NO_UNAUTHENTICATED_AGENT_COMMUNICATION: when SPIFFE identity is
        # required (production, or an explicit opt-in) and no real SVID is
        # available, this communication is refused outright here -- the
        # Communication Boundary itself -- rather than silently degrading
        # to a self-asserted service name. This is the narrowest point
        # every inbound actor-to-actor message (AskActor question,
        # DelegateTask, broadcast) actually passes through, so it is the
        # correct place to enforce this, not a downstream capability.
        from src.monkey_brain.kernel.trusted_auth import (
            bind_trusted_auth, evidence_for_service, evidence_from_spiffe, unauthenticated_evidence,
        )
        from src.monkey_brain.kernel.workload_identity import get_workload_identity_provider
        from src.monkey_brain.kernel.production_gates import production_mode_enabled

        def _spiffe_required_for_agent_communication() -> bool:
            import os as _os
            explicit = _os.getenv("COGNITIVEOS_REQUIRE_SPIFFE_AGENT_IDENTITY", "").strip().lower() in (
                "true", "1", "yes", "on",
            )
            return explicit or production_mode_enabled()

        identity = await get_workload_identity_provider().get_current_identity()
        if identity is not None:
            bind_trusted_auth(evidence_from_spiffe(identity))
        elif _spiffe_required_for_agent_communication():
            bind_trusted_auth(unauthenticated_evidence())
            logger.warning(
                "subscribe_actor_inbox: refusing message for actor %s -- "
                "no verified SPIFFE workload identity and SPIFFE is required "
                "(production mode or COGNITIVEOS_REQUIRE_SPIFFE_AGENT_IDENTITY)",
                actor_id,
            )
            if msg.reply:
                try:
                    await msg.respond(json.dumps({
                        "success": False,
                        "error": "unauthenticated agent communication refused: no verified workload identity",
                    }).encode())
                except Exception:
                    logger.debug("subscribe_actor_inbox: refusal reply failed for actor %s", actor_id, exc_info=True)
            return
        else:
            bind_trusted_auth(evidence_for_service(f"actor-runtime:{actor_id}"))
        try:
            payload = json.loads(msg.data.decode())
        except Exception:
            payload = {}
        # Delegated Transactions: a real msg_type dispatch. Anything else
        # -- including today's existing {"question": ...} shape, and any
        # payload with no msg_type at all -- keeps answering via
        # AnswerQuestionCapability exactly as before this branch existed;
        # this is purely additive, not a replacement.
        if payload.get("msg_type") == "delegated_task":
            # Edge gap-closure (Section 4/6): extract+verify a delegation
            # chain riding on this message at the narrowest trusted
            # boundary -- right after this handler has bound ITS OWN
            # verified identity (SPIFFE/service evidence above), before
            # any capability executes. `actor_id` (this actor, the one
            # actually about to act) is passed as authenticated_delegate,
            # never a value read out of the message -- that is the
            # identity-binding check. A chain that fails verification is
            # rejected outright (never silently dropped, never let through
            # ungoverned) since the caller explicitly supplied one; no
            # chain at all is the normal, unauthenticated-authority case
            # and falls through unchanged.
            from src.monkey_brain.kernel.edge.delegation_message import extract_and_verify_delegation

            extraction = extract_and_verify_delegation(payload, authenticated_delegate=actor_id)
            if extraction.present and not extraction.verified:
                result = {
                    "success": False,
                    "error": f"delegation chain rejected: {extraction.denial_reason}",
                }
            else:
                result = await _run_delegated_tasks(
                    pr, actor_id, actor_role, payload.get("tasks", []),
                    shared_budget_id=payload.get("shared_budget_id"),
                    verified_delegation=extraction.verified_delegation,
                    delegation_chain=extraction.chain,
                )
        elif payload.get("msg_type") == "broadcast":
            # Real gap this closes: BroadcastToAffiliationCapability
            # (below) used to only ever call SocietyRuntime.
            # broadcast_message(), which queues into _message_queue --
            # a real, correctly-permission-checked recipient list with
            # NO real consumer anywhere in the live pipeline (its only
            # reader, cognitive_os/cognitive_os.py::get_messages_for's
            # caller, has zero external callers itself). A broadcast
            # need not be answered or executed (unlike a question or a
            # delegated task) -- the honest, minimal real effect is that
            # the recipient genuinely KNOWS about it, recorded as real
            # episodic state they can recall later, the same way
            # AskActor already records BOTH sides of a question/answer.
            memory_manager = getattr(pr, "memory_manager", None)
            if memory_manager is not None:
                try:
                    memory_manager.record_experience(
                        actor_id, kind="broadcast", text=payload.get("message", ""),
                        metadata={
                            "timestamp": time.time(),
                            "from_actor_id": payload.get("from_actor_id", ""),
                            "from_actor_name": payload.get("from_actor_name", ""),
                            "correlation_id": payload.get("correlation_id", ""),
                        },
                    )
                except Exception:
                    logger.debug("subscribe_actor_inbox: record_experience(kind=broadcast) failed for %s", actor_id, exc_info=True)
            result = {"success": True, "received": True}
        else:
            result = await AnswerQuestionCapability().handle({"context": {
                "knowledge_graph": pr.knowledge_graph, "actor_id": actor_id,
                "actor_role": actor_role, "question": payload.get("question", ""),
                "planetary_runtime": pr,
            }})
        # Echo the asker's correlation_id back so the round-trip (test:
        # "a response retains the same correlation_id") is verifiable from
        # either side, not just asserted by the asker's own bookkeeping.
        correlation_id = payload.get("correlation_id", "")
        if correlation_id:
            result = {**result, "correlation_id": correlation_id}
        if msg.reply:
            try:
                await msg.respond(json.dumps(result).encode())
            except Exception:
                logger.debug("subscribe_actor_inbox: reply failed for actor %s", actor_id, exc_info=True)

    try:
        await nc.subscribe(f"monkeybrain.actor.{actor_id}.inbox", cb=_on_message)
        return True
    except Exception:
        logger.debug("subscribe_actor_inbox: subscribe failed for actor %s", actor_id, exc_info=True)
        return False


class AskActorCapability:
    """Autonomous Dialogue: the ASKING actor's own LLM planner decides,
    as a real plan-step parameter, that it lacks information and WHO to
    ask and WHAT to ask them — this class only carries out that
    decision, it never makes it. No orchestrator script chooses the
    target or writes the question; both come verbatim from
    parameters["target_actor"]/parameters["question"], which the
    planner wrote (see llm_planner.py's AskActor prompt guidance).

    Multi-Actor Execution Handoff: real NATS point-to-point request/reply
    against the TARGET actor's own inbox (subscribe_actor_inbox above),
    not the in-process direct call this capability used to make. That
    in-process design existed specifically to dodge a real, confirmed
    self-deadlock: a literal HTTP call back to this same server, from
    inside a capability's handle() already running on the single asyncio
    event loop a single-worker uvicorn process uses, can never be
    accepted because the loop is busy running this very call. NATS
    request/reply doesn't have that problem — awaiting a reply from an
    EXTERNAL process (nats-server) is ordinary async I/O, not a
    self-referential HTTP call, so the loop stays free to run the actual
    subscriber callback that answers it (which is a plain
    AnswerQuestionCapability().handle() call, same logic as before,
    just invoked from the inbox subscriber instead of from here).
    Falls back to the old in-process call (never fails outright) if this
    actor's PlanetaryRuntime has no live NATS connection — same
    non-fatal-degrade convention as every other real NATS use in this
    codebase."""
    name = "AskActor"

    async def handle(self, args: dict) -> dict:
        context = args.get("context", {})
        parameters = args.get("parameters", {}) or {}
        target_name = parameters.get("target_actor")
        question = parameters.get("question")
        if not target_name or not question:
            return {"success": False, "error": "AskActor requires parameters.target_actor and parameters.question"}

        pr = context.get("planetary_runtime")
        if pr is None:
            return {"success": False, "error": "no planetary_runtime available to resolve target actor"}

        # actor_id match first — the real, unambiguous identifier the
        # "Reachable colleagues" prompt section (llm_planner.py) now gives
        # the model directly. Name match stays as a fallback (role-style
        # addressing like "Driver", or a caller that still sent a name) —
        # this is strictly additive, no existing name-based caller breaks.
        target_sr, target_state = None, None
        target_name_norm = str(target_name).strip().lower()
        for sr in pr.all_societies():
            for state in sr.active_actors():
                if state.actor_id == target_name:
                    target_sr, target_state = sr, state
                    break
                if target_state is None and state.profile.identity.name.strip().lower() == target_name_norm:
                    target_sr, target_state = sr, state
            if target_state:
                break
        if target_state is None:
            # Real gap this closes: neither an exact actor_id nor an
            # exact full-name match — confirmed live, a genuinely correct
            # AskActor step failed outright on "Raj" when the real actor
            # is registered as "Raj Sharma". Falls back to the SAME
            # reachable-colleagues set the "Reachable colleagues" prompt
            # section is built from (kernel/affiliations/reachability.py)
            # — scoped to who the ASKER can actually reach, never a
            # global name search — and matches on the first token of
            # each candidate's real name (so "Raj" resolves against "Raj
            # Sharma" but not against an unrelated "Raj Patel" the asker
            # has no connection to at all, since that name would never
            # appear in their own reachable-colleagues list to begin
            # with). Exactly one match resolves silently; more than one
            # is a real, reported ambiguity — never a guess.
            from src.monkey_brain.kernel.affiliations.reachability import reachable_colleagues
            asker_id_for_fallback = context.get("actor_id", "")
            candidates = [
                c for c in reachable_colleagues(pr, asker_id_for_fallback)
                if c["name"].strip().lower().split(" ")[0] == target_name_norm
            ]
            if len(candidates) == 1:
                match = candidates[0]
                for sr in pr.all_societies():
                    state = sr.get_actor(match["actor_id"])
                    if state is not None:
                        target_sr, target_state = sr, state
                        break
            elif len(candidates) > 1:
                names = ", ".join(c["name"] for c in candidates)
                return {"success": False, "error": f"{target_name!r} is ambiguous — could mean any of: {names}"}
        if target_state is None:
            # Deployment Architecture (Section 8 / Top 10 item 4): every
            # search above only ever looks at pr.all_societies() — actors
            # genuinely resident in THIS process's memory. An actor
            # registered on a DIFFERENT node was previously reported "no
            # actor named X found" even when it genuinely existed — the
            # durable Actor Registry (locate_actor), not this process's
            # in-memory set, is the real source of truth for "does this
            # actor exist." locate_actor() is a cheap, no-side-effect
            # registry read (no cognition reconstruction), tried only as a
            # last resort after the exact-id and reachable-colleagues name
            # fallbacks above, so this never changes behavior for the
            # overwhelmingly common same-process case.
            registry_entry = pr.locate_actor(target_name)
            if registry_entry is None or not registry_entry.actor_id:
                return {"success": False, "error": f"no actor named {target_name!r} found"}
            target_id = registry_entry.actor_id
            target_display_name = registry_entry.name or target_id
            # No goals available from the lightweight registry record (by
            # design — locate_actor never reconstructs cognition) — the
            # role description degrades to just the name rather than
            # fabricating responsibilities this process has no way to know.
            target_role = target_display_name
        else:
            target_id = target_state.actor_id
            # Real gap this closes: target_name is whatever the planner wrote
            # in parameters.target_actor — since the "Reachable colleagues"
            # prompt section (llm_planner.py) now correctly leads the model to
            # write the exact actor_id there (confirmed live) rather than a
            # display name, target_name itself is now often a raw id string.
            # Everywhere a HUMAN-READABLE name is needed below uses the real
            # resolved name instead — target_name/target_id stay as the
            # original parameter/resolved id for lookup and error-message
            # purposes only.
            target_display_name = target_state.profile.identity.name
            target_goals = list(target_state.profile.goals) if target_state.profile.goals else []
            target_role = (
                f"{target_display_name}, whose responsibilities include: {', '.join(target_goals)}"
                if target_goals else target_display_name
            )
        asker_id = context.get("actor_id", "")
        # actor_role context isn't reliably populated on this real
        # pipeline invocation path (confirmed live: always empty here,
        # silently falling back to the raw asker_id) — resolve the
        # asker's own real name the same way target_state was resolved
        # above, instead of trusting an unreliable context field.
        asker_display_name = asker_id
        for asker_sr in pr.all_societies():
            asker_state = asker_sr.get_actor(asker_id)
            if asker_state is not None:
                asker_display_name = asker_state.profile.identity.name
                break

        # This AskActor request is its own logical operation with no
        # upstream execution_id passed down into the capability context —
        # mint once here and thread it through resolve -> NATS request ->
        # reply -> the resulting INTERACTION ContextEvent.
        correlation_id = context.get("correlation_id") or new_correlation_id()

        start = time.monotonic()
        decision = pr.resolve_communication(asker_id, target_id, correlation_id=correlation_id)
        elapsed_ms = (time.monotonic() - start) * 1000
        societies_traversed = 1 if decision.society_id else 2
        _obs.gauge("communication.average_routing_time_ms", elapsed_ms)
        _obs.counter("communication.societies_traversed", societies_traversed)
        if decision.affiliation_id:
            _obs.counter("communication.messages_per_affiliation", affiliation_id=decision.affiliation_id)
        if not decision.allowed:
            return {
                "success": False, "denied": True, "error": decision.reason,
                "sender_affiliations": list(decision.sender_affiliations),
                "recipient_affiliations": list(decision.recipient_affiliations),
            }

        nc = getattr(pr, "_nats_client", None)
        if nc is not None:
            import json
            try:
                msg = await nc.request(
                    f"monkeybrain.actor.{target_id}.inbox",
                    json.dumps({"question": question, "correlation_id": correlation_id}).encode(),
                    # Real gap this closed: the subscriber side answers by
                    # running AnswerQuestionCapability, a real LLM call
                    # (get_backend().complete()) -- model_backend.py's own
                    # httpx client budgets 120s for exactly that call.
                    # Confirmed live: a real local-model answer routinely
                    # takes 20-40s+, so 10s failed a genuinely-in-progress,
                    # correct answer as an honest-looking "timeout" that
                    # was actually just impatience.
                    timeout=90.0,
                )
                result = json.loads(msg.data.decode())
            except Exception as exc:
                # nats.errors.TimeoutError (no subscriber answered in
                # time) and any transport error both land here — an
                # honest failure, never a guessed/fabricated answer.
                return {"success": False, "error": f"{target_display_name} did not respond within 90s ({exc})"}
        elif target_state is not None:
            result = await AnswerQuestionCapability().handle({"context": {
                "knowledge_graph": pr.knowledge_graph, "actor_id": target_id,
                "actor_role": target_role, "question": question,
                "planetary_runtime": pr,
            }})
        else:
            # No NATS connection AND the target isn't resident in this
            # process — there is no way to reach it. The in-process
            # AnswerQuestionCapability fallback above is only correct when
            # the target genuinely lives in THIS process's memory
            # (target_state is not None); calling it for a registry-only,
            # remote target_id would silently answer using whatever local
            # actor_id happens to match, or fail confusingly deep inside
            # AnswerQuestionCapability instead of here, honestly, at the
            # one place that actually knows why.
            return {
                "success": False,
                "error": f"{target_display_name} is on a different node and no NATS connection "
                         "is available to reach it",
            }
        if not result.get("success"):
            return {"success": False, "error": result.get("error", f"{target_display_name} could not answer")}

        try:
            from src.monkey_brain.kernel.society.context_stream import ContextEvent, ContextEventType
            pr.context_stream.publish(ContextEvent(
                event_type=ContextEventType.INTERACTION,
                # actor_id must be the asker, not the target: the read side
                # (api/routes/actors.py::get_execution_conversation) filters
                # context_stream.replay(actor_id=<the actor whose execution
                # debugger this is>) -- AskActor is a step in the ASKER's
                # own plan, so that's whose execution this interaction
                # belongs to. Tagging it with target_id instead meant this
                # event could only ever be found by replaying the TARGET's
                # stream, which has no execution of its own to scope it to
                # -- confirmed live: every real ask/answer exchange was
                # invisible in the initiating actor's own Conversations
                # panel, indistinguishable from "nothing was ever asked."
                actor_id=asker_id,
                description=f"{asker_display_name} asked {target_display_name}: {question}",
                payload={
                    "from_actor_id": asker_id, "from_actor_name": asker_display_name,
                    "to_actor_id": target_id, "to_actor_name": target_display_name,
                    "society_id": target_sr.society.society_id if target_sr else "",
                    "society_name": target_sr.society.name if target_sr else "",
                    "question": question, "answer": result.get("answer", ""),
                },
                correlation_id=correlation_id,
                causation_id=decision.decision_id,
            ))
        except Exception:
            logger.debug("handle: suppressed exception", exc_info=True)

        # Real gap this closes: api/routes/actors.py's POST /actors/{id}/ask
        # HTTP route already records kind="conversation" memory for a
        # human-initiated ask (see that route's own "Real gap this closes"
        # comment) — but THIS is the separate code path a plan's own
        # AskActor step actually runs through, and it only ever published
        # a context_stream INTERACTION event, never a retrievable
        # conversation memory. Confirmed live: a real ask/answer exchange
        # during planning never showed up in the debugger's Conversations
        # panel, indistinguishable from "no conversation happened at all."
        # Recorded under BOTH participants, same convention as the /ask
        # route (search_episodic() filters strictly by actor_id, so an
        # entry recorded under only one side is unreachable by the other).
        memory_manager = getattr(pr, "memory_manager", None)
        if memory_manager is not None:
            timestamp = time.time()
            for participant_id in (asker_id, target_id):
                if not participant_id:
                    continue
                try:
                    memory_manager.record_experience(
                        participant_id, kind="conversation", text=question,
                        metadata={"timestamp": timestamp, "speaker": asker_display_name, "correlation_id": correlation_id},
                    )
                    memory_manager.record_experience(
                        participant_id, kind="conversation", text=result.get("answer", ""),
                        metadata={"timestamp": timestamp, "speaker": target_display_name, "correlation_id": correlation_id},
                    )
                except Exception:
                    logger.debug("handle: record_experience(kind=conversation) failed for %s", participant_id, exc_info=True)

        return {
            "success": True, "target_actor": target_name,
            "question": question, "answer": result.get("answer", ""),
            "affiliation_id": decision.affiliation_id,
            "society_id": decision.society_id,
            "reason": decision.reason,
            "correlation_id": correlation_id,
            "causation_id": decision.decision_id,
        }


class DelegateTaskCapability:
    """Delegated Transactions: AskActorCapability's sibling for real
    actions instead of free-text questions — a planner decides it needs
    another actor to actually DO something (e.g. an Inventory Society
    actor reserving stock, see InventoryReserveCapability/
    InventoryReleaseCapability's own MB-3100 "True Multi-Actor
    Coordination" precedent), not just answer a question.

    Same target-resolution/communication-permission/NATS-with-in-process-
    fallback/INTERACTION-event shape as AskActorCapability, deliberately —
    this is a second capability, not a second pattern. The only real
    differences: parameters["tasks"] (a real, possibly multi-step chain of
    {"capability", "parameters", "depends_on"} entries) instead of
    parameters["question"]; the wire payload carries msg_type
    "delegated_task" so subscribe_actor_inbox's _on_message dispatches to
    _run_delegated_tasks instead of AnswerQuestionCapability; the return is
    a structured transaction result (success/success_count/failure_count/
    actions), never a single free-text answer — because what actually
    happened at each step (a real capability outcome) is what a caller of
    a delegated TRANSACTION needs, not a summary."""
    name = "DelegateTask"

    @staticmethod
    def _find_actor_by_id_or_name(pr, target: str, asker_id: str = ""):
        """Real gap this closed: this only ever matched by name, even
        though llm_planner.py's own DelegateTask prompt guidance (added
        alongside AskActorCapability's identical fix) tells the model to
        pass a real actor_id in target_actor — confirmed live, a
        correctly id-shaped target_actor value failed every single time
        with "no actor named '<id>' found" since no real actor's .name
        field is ever an id string. Same actor_id-first-then-name-
        fallback order as AskActorCapability.handle() (this capability's
        own docstring already claims "Same target-resolution... shape as
        AskActorCapability" — this makes that claim true again).

        Real gap this closes too: the exact same "Raj" vs "Raj Sharma"
        partial-name failure AskActorCapability.handle() confirmed live
        and fixed via reachable_colleagues' first-token fallback was never
        ported here, despite this method's own docstring (above) claiming
        target-resolution parity with AskActorCapability — a DelegateTask
        step with a short/partial name (exactly what the "Reachable
        colleagues" prompt section can lead a smaller model to write
        anyway, same as AskActor's own confirmed failure) failed outright
        instead of resolving, while the identical AskActor call succeeded."""
        target_norm = str(target).strip().lower()
        for sr in pr.all_societies():
            for state in sr.active_actors():
                if state.actor_id == target:
                    return sr, state, None
                if state.profile.identity.name.strip().lower() == target_norm:
                    return sr, state, None

        if not asker_id:
            return None, None, None
        from src.monkey_brain.kernel.affiliations.reachability import reachable_colleagues
        candidates = [
            c for c in reachable_colleagues(pr, asker_id)
            if c["name"].strip().lower().split(" ")[0] == target_norm
        ]
        if len(candidates) == 1:
            match_id = candidates[0]["actor_id"]
            for sr in pr.all_societies():
                state = sr.get_actor(match_id)
                if state is not None:
                    return sr, state, None
        elif len(candidates) > 1:
            names = ", ".join(c["name"] for c in candidates)
            return None, None, f"{target!r} is ambiguous — could mean any of: {names}"
        return None, None, None

    @staticmethod
    def _find_alternative_delegate(pr, original_state, excluded_ids: set, asker_id: str):
        """Same-tick cross-agent recovery (Qualification Gap Closure,
        Phase 4 extension): "ask another agent; if they cannot complete,
        find another way" needs a real DIFFERENT actor to delegate the
        SAME tasks to, not a different product/provider — this is the
        delegation-level sibling of ProductSelectionCapability's own
        retry_after_failure search, generalized the same way (capability
        owns what "another real option" means, the executor only knows
        "retry once"). Prefers another real, active actor whose own
        stated goals genuinely overlap the original target's (the same
        real signal target_role above already surfaces to the LLM) —
        falls back to any other real active actor in the SAME society as
        the ASKER (a genuine, inspectable "someone else I can actually
        reach", which is what matters for whether a delegation can even
        be attempted — the original target's own society is irrelevant
        to whether the ASKER can reach a replacement) when the original
        target declared no goals to match against."""
        original_goals = {g.strip().lower() for g in (original_state.profile.goals or ())}
        asker_society_ids = {
            sr.society.society_id for sr in pr.all_societies() if sr.get_actor(asker_id) is not None
        }
        by_goal_overlap = None
        by_same_society = None
        for sr in pr.all_societies():
            for state in sr.active_actors():
                if state.actor_id == original_state.actor_id or state.actor_id in excluded_ids or state.actor_id == asker_id:
                    continue
                if by_goal_overlap is None and original_goals and {
                    g.strip().lower() for g in (state.profile.goals or ())
                } & original_goals:
                    by_goal_overlap = (sr, state)
                if by_same_society is None and sr.society.society_id in asker_society_ids:
                    by_same_society = (sr, state)
        return by_goal_overlap or by_same_society or (None, None)

    async def handle(self, args: dict) -> dict:
        context = args.get("context", {})
        parameters = args.get("parameters", {}) or {}
        target_name = parameters.get("target_actor")
        tasks = parameters.get("tasks")
        if not target_name or not tasks:
            return {"success": False, "error": "DelegateTask requires parameters.target_actor and parameters.tasks"}

        pr = context.get("planetary_runtime")
        if pr is None:
            return {"success": False, "error": "no planetary_runtime available to resolve target actor"}

        asker_id = context.get("actor_id", "")
        original_sr, original_state, ambiguity_error = self._find_actor_by_id_or_name(pr, target_name, asker_id)
        if original_state is None:
            return {"success": False, "error": ambiguity_error or f"no actor named {target_name!r} found"}

        if parameters.get("retry_after_failure"):
            # Domain-independent per "MAKE DOMAIN ISOLATION AIRTIGHT": the
            # executor supplies only the generic retry_after_failure
            # marker on a verbatim copy of this action's own original
            # parameters -- it never computes or injects an
            # "excluded_ids" list (it has no idea what a "delegate" is).
            # The one real actor that must never be picked again is
            # derivable entirely from this capability's own already-
            # resolved original_state, with no input from the executor.
            excluded_ids = {original_state.actor_id}
            target_sr, target_state = self._find_alternative_delegate(
                pr, original_state, excluded_ids, context.get("actor_id", ""),
            )
            if target_state is None:
                return {
                    "success": False,
                    "error": f"delegation recovery: no real alternative to {target_name!r} available",
                    "recoverable": False,
                }
            target_name = target_state.profile.identity.name
        else:
            target_sr, target_state = original_sr, original_state

        target_id = target_state.actor_id
        target_goals = list(target_state.profile.goals) if target_state.profile.goals else []
        target_role = (
            f"{target_name}, whose responsibilities include: {', '.join(target_goals)}"
            if target_goals else target_name
        )
        asker_name = str(context.get("actor_role", "")).split(",")[0] or asker_id

        correlation_id = context.get("correlation_id") or new_correlation_id()

        start = time.monotonic()
        decision = pr.resolve_communication(asker_id, target_id, correlation_id=correlation_id)
        elapsed_ms = (time.monotonic() - start) * 1000
        societies_traversed = 1 if decision.society_id else 2
        _obs.gauge("communication.average_routing_time_ms", elapsed_ms)
        _obs.counter("communication.societies_traversed", societies_traversed)
        if decision.affiliation_id:
            _obs.counter("communication.messages_per_affiliation", affiliation_id=decision.affiliation_id)
        # Same-tick cross-agent recovery (Qualification Gap Closure, Phase
        # 4 extension): this attempt is itself already a retry
        # (_find_alternative_delegate above already picked a DIFFERENT
        # real actor) -- a second failure genuinely means no real
        # alternative worked, so it's reported honestly exhausted
        # (recoverable=False), never marked retryable again. The
        # executor's own one-retry structure would ignore a second True
        # anyway, but an explicit False here keeps this capability's own
        # contract honest on its face, matching ProductSelectionCapability's
        # identical convention.
        is_retry = bool(parameters.get("retry_after_failure"))

        if not decision.allowed:
            return {
                "success": False, "denied": True, "error": decision.reason,
                "sender_affiliations": list(decision.sender_affiliations),
                "recipient_affiliations": list(decision.recipient_affiliations),
                "recoverable": not is_retry,
            }

        nc = getattr(pr, "_nats_client", None)
        if nc is not None:
            import json
            try:
                msg = await nc.request(
                    f"monkeybrain.actor.{target_id}.inbox",
                    json.dumps({
                        "msg_type": "delegated_task", "tasks": tasks, "correlation_id": correlation_id,
                        "shared_budget_id": context.get("shared_budget_id"),
                    }).encode(),
                    # Same real fix as AskActorCapability's identical
                    # nc.request call above -- a delegated task can run a
                    # real capability (possibly its own LLM-driven step),
                    # so it needs the same realistic budget, not a
                    # 10s window a genuine local-model round trip
                    # routinely exceeds.
                    timeout=90.0,
                )
                result = json.loads(msg.data.decode())
            except Exception as exc:
                # nats.errors.TimeoutError (no subscriber answered in
                # time) and any transport error both land here — an
                # honest failure, never a fabricated transaction result.
                return {
                    "success": False, "error": f"{target_name} did not respond within 90s ({exc})",
                    "recoverable": not is_retry,
                }
        else:
            result = await _run_delegated_tasks(pr, target_id, target_role, tasks, shared_budget_id=context.get("shared_budget_id"))

        try:
            from src.monkey_brain.kernel.society.context_stream import ContextEvent, ContextEventType
            pr.context_stream.publish(ContextEvent(
                event_type=ContextEventType.INTERACTION,
                # Same fix as AskActorCapability's identical publish above:
                # actor_id must be the delegator (asker_id), not target_id —
                # DelegateTask is a step in the DELEGATOR's own plan, and
                # the read side filters by the actor whose execution is
                # being viewed.
                actor_id=asker_id,
                description=f"{asker_name} delegated {len(tasks)} task(s) to {target_name}",
                payload={
                    "from_actor_id": asker_id, "from_actor_name": asker_name,
                    "to_actor_id": target_id, "to_actor_name": target_name,
                    "society_id": target_sr.society.society_id if target_sr else "",
                    "society_name": target_sr.society.name if target_sr else "",
                    "tasks": tasks, "result": result,
                },
                correlation_id=correlation_id,
                causation_id=decision.decision_id,
            ))
        except Exception:
            logger.debug("handle: suppressed exception", exc_info=True)

        # Merge the structured transaction result (success/success_count/
        # failure_count/actions) regardless of overall success — a caller
        # delegating a multi-step transaction needs to know exactly which
        # step(s) failed, not just that "something" did. A genuine
        # delegated-transaction failure (the target actor couldn't
        # complete it) is the third real recovery trigger, alongside
        # denial and no-response above.
        merged = {
            **result,
            "target_actor": target_name,
            "affiliation_id": decision.affiliation_id,
            "society_id": decision.society_id,
            "reason": decision.reason,
            "correlation_id": correlation_id,
            "causation_id": decision.decision_id,
        }
        if not merged.get("success", True):
            merged["recoverable"] = not is_retry
        return merged


class BroadcastToAffiliationCapability:
    """The UNDIRECTED counterpart to AskActor: an actor request with no
    single named colleague ("can someone help pack Order 123", "everyone
    in Warehouse A stop operations") is a genuine plan-step choice the
    LLM planner makes, not a fallback when AskActor's target lookup
    fails. Recipient computation is real: SocietyRuntime.
    broadcast_message() resolves eligible recipients through the same
    AffiliationCommunicationRouter AskActor uses (shared affiliation, or
    society membership including temporary presence-driven
    participants); nothing here invents a recipient list. Scoped to the
    sender's own home society — there is no cross-society broadcast.

    Real gap this closes: broadcast_message() only ever queued into
    SocietyRuntime._message_queue, which has no live reader anywhere in
    the running pipeline (its one real consumer,
    cognitive_os/cognitive_os.py::get_messages_for's caller, is itself
    dead code with zero external callers) — a sender got an honest-
    looking delivered_count for a message that reached literally no
    one. Now genuinely delivered per recipient, same NATS-with-in-
    process-fallback shape AskActor/DelegateTask already use — publish
    (fire-and-forget; a broadcast needs no synchronous reply the way a
    question or delegated task does), to each recipient's own inbox,
    handled by subscribe_actor_inbox's new msg_type=="broadcast" branch."""
    name = "BroadcastToAffiliation"

    async def handle(self, args: dict) -> dict:
        context = args.get("context", {})
        parameters = args.get("parameters", {}) or {}
        message = parameters.get("message")
        if not message:
            return {"success": False, "error": "BroadcastToAffiliation requires parameters.message"}

        pr = context.get("planetary_runtime")
        sender_id = context.get("actor_id", "")
        if pr is None or not sender_id:
            return {"success": False, "error": "no planetary_runtime/actor_id available to broadcast from"}

        sender_sr = None
        sender_state = None
        for sr in pr.all_societies():
            state = sr.get_actor(sender_id)
            if state is not None:
                sender_sr, sender_state = sr, state
                break
        if sender_sr is None:
            return {"success": False, "error": f"sender {sender_id!r} has no home society to broadcast from"}
        sender_display_name = sender_state.profile.identity.name if sender_state else sender_id

        correlation_id = context.get("correlation_id") or new_correlation_id()

        start = time.monotonic()
        recipients = sender_sr.eligible_recipients(sender_id)
        # Still real, still the source of truth for WHO is eligible and
        # WHY (affiliation/society permission) — this permission-checked
        # queueing keeps happening exactly as before, feeding the same
        # audit trail and Lemon metrics below. Actual delivery to each
        # recipient is the separate step right after this.
        delivered = sender_sr.broadcast_message(sender_id, "broadcast", {"message": message}, correlation_id=correlation_id)
        elapsed_ms = (time.monotonic() - start) * 1000
        _obs.gauge("communication.average_routing_time_ms", elapsed_ms)
        _obs.counter("communication.societies_traversed", 1)
        for decision in sender_sr.communication_audit()[-len(recipients):] if recipients else ():
            if decision.affiliation_id:
                _obs.counter("communication.messages_per_affiliation", affiliation_id=decision.affiliation_id)

        nc = getattr(pr, "_nats_client", None)
        memory_manager = getattr(pr, "memory_manager", None)
        received_by: list[str] = []
        for recipient_id in recipients:
            wire_payload = {
                "msg_type": "broadcast", "message": message,
                "from_actor_id": sender_id, "from_actor_name": sender_display_name,
                "correlation_id": correlation_id,
            }
            if nc is not None:
                try:
                    import json
                    await nc.publish(f"monkeybrain.actor.{recipient_id}.inbox", json.dumps(wire_payload).encode())
                    received_by.append(recipient_id)
                    continue
                except Exception:
                    logger.debug("BroadcastToAffiliation: NATS publish failed for %s, falling back in-process", recipient_id, exc_info=True)
            # No live NATS connection (or publish failed) — same
            # non-fatal in-process fallback AskActor/DelegateTask use,
            # applied here as the identical real effect
            # subscribe_actor_inbox's broadcast branch has: record it
            # directly into the recipient's own episodic memory.
            if memory_manager is not None:
                try:
                    memory_manager.record_experience(
                        recipient_id, kind="broadcast", text=message,
                        metadata={
                            "timestamp": time.time(), "from_actor_id": sender_id,
                            "from_actor_name": sender_display_name, "correlation_id": correlation_id,
                        },
                    )
                    received_by.append(recipient_id)
                except Exception:
                    logger.debug("BroadcastToAffiliation: in-process fallback failed for %s", recipient_id, exc_info=True)

        try:
            from src.monkey_brain.kernel.society.context_stream import ContextEvent, ContextEventType
            pr.context_stream.publish(ContextEvent(
                event_type=ContextEventType.INTERACTION,
                actor_id=sender_id,
                description=f"{sender_display_name} broadcast to {len(received_by)} recipient(s): {message}",
                payload={
                    "from_actor_id": sender_id, "from_actor_name": sender_display_name,
                    "participants": [sender_id, *received_by],
                    "society_id": sender_sr.society.society_id,
                    "society_name": sender_sr.society.name,
                    "message": message, "recipients": list(recipients), "received_by": received_by,
                },
                correlation_id=correlation_id,
            ))
        except Exception:
            logger.debug("handle: suppressed exception", exc_info=True)
        if memory_manager is not None:
            try:
                memory_manager.record_experience(
                    sender_id, kind="broadcast", text=message,
                    metadata={"timestamp": time.time(), "sent": True, "correlation_id": correlation_id},
                )
            except Exception:
                logger.debug("BroadcastToAffiliation: sender memory record failed", exc_info=True)

        return {
            "success": True, "message": message,
            "recipients": list(recipients), "delivered_count": delivered,
            "received_by": received_by, "correlation_id": correlation_id,
        }


class RespondToInquiryCapability:
    """Autonomous Dialogue's termination signal. The actor's own LLM
    selects this once it decides it has gathered enough real information
    (from its own facts and/or a prior AskActor reply) to conclude —
    parameters["answer"] is the natural-language answer the planner
    itself already wrote; this class doesn't generate or alter it. No
    extra LLM call is needed here: the "conversion to natural language"
    already happened when the planner wrote this plan step's string
    parameter. An orchestrator watches for this action name to know the
    conversation is over — it never decides that itself."""
    name = "RespondToInquiry"

    def handle(self, args: dict) -> dict:
        parameters = args.get("parameters", {}) or {}
        answer = parameters.get("answer")
        if not answer:
            return {"success": False, "error": "RespondToInquiry requires parameters.answer"}
        return {"success": True, "answer": answer}


def _actor_strategy_profile(context: dict) -> dict:
    """Game-Theoretic Reasoning: an actor's real strategy metadata
    (preferences/resources/risk_tolerance/negotiation_policy), set at
    actor-creation time (POST /actors body.metadata["strategy"]) and
    read back verbatim via the real actor registry — never invented
    here. Empty dict for an actor with no strategy profile set."""
    pr = context.get("planetary_runtime")
    actor_id = context.get("actor_id", "")
    if pr is None or not actor_id:
        return {}
    for sr in pr.all_societies():
        state = sr.get_actor(actor_id)
        if state is not None:
            return dict(state.profile.metadata.get("strategy", {}))
    return {}


class EvaluateStrategyCapability:
    """Game-Theoretic Reasoning: computes real, deterministic utility
    numbers for real candidate options the actor's own planning
    perceived — the explainable "Utility Evaluation" a negotiation
    trace exposes, not an LLM's free-text assertion that it weighed
    tradeoffs. Ranks; never chooses. Same round-boundary discipline as
    AskActor/RespondToInquiry: this is a fact-gathering step — the
    actor sees the real numbers as a fact on ITS NEXT round before
    committing to a strategy, not within the same plan that requested
    the evaluation."""
    name = "EvaluateStrategy"

    def handle(self, args: dict) -> dict:
        context = args.get("context", {})
        parameters = args.get("parameters", {}) or {}
        candidates = parameters.get("candidates")
        if not candidates or not isinstance(candidates, list):
            return {"success": False, "error": "EvaluateStrategy requires parameters.candidates (a non-empty list)"}

        preferences = _actor_strategy_profile(context).get("preferences") or {}
        if not preferences:
            return {"success": False, "error": "actor has no strategy preferences to evaluate candidates against"}

        from src.monkey_brain.kernel.domains.negotiation import evaluate_candidates
        evaluations = evaluate_candidates(preferences, candidates)
        return {
            "success": True, "evaluations": evaluations,
            "best": evaluations[0]["name"] if evaluations else None,
        }


class CompeteForResourceCapability:
    """Game-Theoretic Reasoning: real scarce-resource competition —
    wraps try_reserve (CAS, no oversell, proven under contention),
    never a new allocation algorithm. The real win/lose outcome and,
    on a loss, the real reason (try_reserve's own CAS failure message,
    e.g. "insufficient stock: 0 available, 1 requested") are returned
    as facts; this capability never writes a natural-language
    explanation itself — the actor's OWN next-round reasoning
    (RespondToInquiry) explains the real outcome, keeping "avoid
    hardcoded outcomes" honest: the ALLOCATION is deterministic CAS,
    only the WORDING is the actor's own real reasoning. hold_seconds
    is generous (5 minutes, not try_reserve's 5-second default) since
    a real multi-round LLM negotiation can easily take that long
    end-to-end, and a hold expiring mid-negotiation would silently
    undo a real win."""
    name = "CompeteForResource"

    def handle(self, args: dict) -> dict:
        context = args.get("context", {})
        parameters = args.get("parameters", {}) or {}
        resource_id = parameters.get("resource_id")
        if not resource_id:
            return {"success": False, "error": "CompeteForResource requires parameters.resource_id"}
        try:
            qty = int(parameters.get("qty", 1))
        except (TypeError, ValueError):
            qty = 1

        kg = context.get("knowledge_graph")
        actor_id = context.get("actor_id", "")
        if kg is None or not actor_id:
            return {"success": False, "error": "no knowledge graph or actor_id available"}

        won, reason = try_reserve(kg, resource_id, actor_id, qty, hold_seconds=300.0)
        return {"success": True, "won": won, "resource_id": resource_id, "qty": qty, "reason": reason}


class RecordAgreementCapability:
    """Game-Theoretic Reasoning: persists a real negotiated agreement
    as durable world state (kernel/domains/negotiation.py::record_agreement,
    CAS-append — same template place_bid already uses), not an
    in-memory-only result. parameters["agreement"] is the structured
    deal the actor's own LLM wrote once a negotiation concluded (e.g.
    {"with": "Driver", "terms": "afternoon delivery", "price": 54.99})
    — this capability persists it verbatim, it doesn't compose or
    validate the deal's content."""
    name = "RecordAgreement"

    def handle(self, args: dict) -> dict:
        context = args.get("context", {})
        parameters = args.get("parameters", {}) or {}
        entity_id = parameters.get("entity_id")
        agreement = parameters.get("agreement")
        if not entity_id or not isinstance(agreement, dict):
            return {"success": False, "error": "RecordAgreement requires parameters.entity_id and parameters.agreement (a dict)"}

        kg = context.get("knowledge_graph")
        if kg is None:
            return {"success": False, "error": "no knowledge graph available"}

        from src.monkey_brain.kernel.domains.negotiation import record_agreement
        ok, msg = record_agreement(kg, entity_id, agreement)
        return {"success": ok, "entity_id": entity_id, "agreement": agreement, "message": msg}


class GetAgreementsCapability:
    """Game-Theoretic Reasoning: real read path for RecordAgreement's own
    persisted data. Real gap this closes: RecordAgreement's docstring
    claims a recorded agreement is "durable world state future reasoning
    can read back... via the same KG fact-lookup every capability
    already uses" — but no such fact-lookup existed for it. Unlike
    place_bid's `bids` (consumed by resolve_auction) or NegotiatePrice's
    deal (threaded into context["negotiated_prices"] for
    OrderCreationCapability), a recorded agreement was write-only: the
    context_stream event at record time is the only place it was ever
    visible, and only for that one tick — an actor (or its counterparty)
    in a LATER round, or a different actor checking "did we agree on
    this?", had no way to ever see it again. This is a plain read of
    entity_id's real, persisted `agreements` list, nothing computed or
    inferred."""
    name = "GetAgreements"

    def handle(self, args: dict) -> dict:
        context = args.get("context", {})
        parameters = args.get("parameters", {}) or {}
        entity_id = parameters.get("entity_id")
        if not entity_id:
            return {"success": False, "error": "GetAgreements requires parameters.entity_id"}

        kg = context.get("knowledge_graph")
        if kg is None:
            return {"success": False, "error": "no knowledge graph available"}

        entity = kg.get_entity(entity_id)
        if entity is None:
            return {"success": False, "error": f"entity {entity_id!r} not found"}

        agreements = list(entity.attributes.get("agreements", []))
        return {"success": True, "entity_id": entity_id, "agreements": agreements, "count": len(agreements)}


class ReportWorldPerturbationCapability:
    """Context Grounding: the real replacement for "external world events"
    (weather/traffic/NOAA feeds — none of which exist in this codebase and
    would be fabricated data, not real evidence). A perturbation reported
    through this capability is real in the two ways that matter: it
    genuinely mutates the affected entity's real attributes via
    kg.update_entity (attributes=..., the same merge-not-replace mechanism
    every other real world mutation in this file already uses — see
    OrderCreationCapability's own inventory-decrement call a few hundred
    lines up), so a reported "Warehouse A out of stock"
    (impact_attributes={"quantity": 0}) genuinely makes the next
    ProductSelection skip it — not text claiming it did. It also publishes
    a real WORLD_UPDATE ContextEvent (source="external_perturbation" in
    its payload) through the shared SocietyContextStream, so
    ContextConstructionEngine._retrieve_context_stream picks it up as real
    grounding for whichever actor's next planning tick runs after it.

    Callable two ways, same pattern AskActorCapability already
    establishes for AnswerQuestionCapability: as a plan step (an actor's
    own LLM decides to report something, e.g. "the store I'm at just
    caught fire"), or invoked directly from an admin route
    (POST /planet/perturbations, api/routes/planet.py) for an
    operator-triggered event — same handler either way, no duplicated
    logic."""
    name = "ReportWorldPerturbation"

    def handle(self, args: dict) -> dict:
        context = args.get("context", {})
        parameters = args.get("parameters", {}) or {}
        entity_id = parameters.get("entity_id")
        description = parameters.get("description") or ""
        impact_attributes = parameters.get("impact_attributes")
        if not entity_id or not description or not isinstance(impact_attributes, dict) or not impact_attributes:
            return {
                "success": False,
                "error": "ReportWorldPerturbation requires parameters.entity_id, "
                         "parameters.description, and a non-empty parameters.impact_attributes dict",
            }

        kg = context.get("knowledge_graph")
        if kg is None:
            return {"success": False, "error": "no knowledge graph available"}
        entity = kg.update_entity(entity_id, attributes=impact_attributes)
        if entity is None:
            return {"success": False, "error": f"entity {entity_id!r} not found"}

        reported_by = context.get("actor_id") or "system"
        pr = context.get("planetary_runtime")
        published = False
        if pr is not None:
            try:
                from src.monkey_brain.kernel.society.context_stream import ContextEvent, ContextEventType
                pr.context_stream.publish(ContextEvent(
                    event_type=ContextEventType.WORLD_UPDATE, actor_id=reported_by,
                    description=description,
                    payload={
                        "entity_id": entity_id, "impact_attributes": impact_attributes,
                        "source": "external_perturbation",
                    },
                ))
                published = True
            except Exception:
                published = False
            # Real-Time World Changes refactor: also queue this as a
            # World Perturbation for the next Planetary Tick to reconcile
            # into SharedWorld and consider for Deja Vu (kernel/society/
            # integration.py::_run_cycle) -- additive to, not instead of,
            # the immediate kg.update_entity above (that mutation already
            # happened; this only makes it visible to SharedWorld/Deja Vu
            # too, on the same reconciled-once-per-cycle cadence every
            # other World Perturbation follows).
            try:
                from src.monkey_brain.kernel.society.perturbation_queue import WorldPerturbation
                pr.perturbation_queue.publish(WorldPerturbation(
                    entity_id=entity_id, description=description,
                    attributes=dict(impact_attributes), source=reported_by,
                ))
            except Exception:
                pass

        return {
            "success": True, "entity_id": entity_id, "description": description,
            "impact_attributes": impact_attributes, "entity": entity.to_dict(),
            "context_event_published": published,
        }


class PrepareOrderCapability:
    """Multi-Actor Execution Handoff: the real capability a Warehouse-side
    actor invokes in reaction to a real OrderCreated/InventoryReserved
    domain event (kernel/society/integration.py::_propagate_coordination
    ticks that actor's own real cognitive loop with a "World event(s)
    occurred" broadcast_context — this is the capability its own LLM plan
    can then choose). Genuinely mutates the real order entity via
    kg.update_entity (same merge-not-replace mechanism every other real
    mutation in this file uses), then publishes OrderPrepared
    (CAPABILITY_DOMAIN_EVENTS below) so the NEXT propagation round can
    carry the cascade further (e.g. to a Logistics Society)."""
    name = "PrepareOrder"

    def handle(self, args: dict) -> dict:
        context = args.get("context", {})
        parameters = args.get("parameters", {}) or {}
        order_id = parameters.get("order_id") or parameters.get("entity_id")
        if not order_id:
            return {"success": False, "error": "PrepareOrder requires parameters.order_id"}

        kg = context.get("knowledge_graph")
        if kg is None:
            return {"success": False, "error": "no knowledge graph available"}
        entity = kg.update_entity(order_id, attributes={"status": "prepared"})
        if entity is None:
            return {"success": False, "error": f"order {order_id!r} not found"}
        return {"success": True, "order_id": order_id, "status": "prepared"}


class AcceptDeliveryCapability:
    """Multi-Actor Execution Handoff: the real capability a Driver/
    Logistics-side actor invokes in reaction to a real ShipmentCreated
    domain event (see PrepareOrderCapability's own docstring — same
    propagation mechanism, different subscribed event). Genuinely mutates
    the real order/shipment entity's status via kg.update_entity, then
    publishes OutForDelivery."""
    name = "AcceptDelivery"

    def handle(self, args: dict) -> dict:
        context = args.get("context", {})
        parameters = args.get("parameters", {}) or {}
        entity_id = parameters.get("shipment_id") or parameters.get("order_id") or parameters.get("entity_id")
        if not entity_id:
            return {"success": False, "error": "AcceptDelivery requires parameters.shipment_id or parameters.order_id"}

        kg = context.get("knowledge_graph")
        if kg is None:
            return {"success": False, "error": "no knowledge graph available"}
        entity = kg.update_entity(entity_id, attributes={"status": "out_for_delivery"})
        if entity is None:
            return {"success": False, "error": f"entity {entity_id!r} not found"}
        return {"success": True, "entity_id": entity_id, "status": "out_for_delivery"}


class NegotiatePriceCapability:
    """Game-Theoretic Reasoning: wraps negotiate_price (this file,
    GS-1501, already production-proven for peer-to-peer bargaining)
    directly — a real bounded bilateral price bargain, not an
    LLM-invented number. The negotiated price can never violate the
    seller's real floor or exceed the listed price, because the math
    enforces that, not a prompt instruction. The actor's own LLM
    supplies the three real inputs (listed price, its own floor/target)
    as plan-step parameters; this capability only runs the bargain.

    Real gap this closes: a real, bounded deal used to be computed here
    and then go nowhere — nothing downstream ever read it, so a
    negotiated price never actually changed what OrderCreation/Payment
    charged. parameters.product_id (optional, planner-supplied once a
    specific product has been selected) links a successful deal to the
    real cart item it was for; project_action_result_to_context (below)
    threads an agreed deal into context["negotiated_prices"], which
    OrderCreationCapability applies to that item's price before pricing
    the order. Omitting product_id preserves the old standalone
    behavior — the deal is still computed and returned, just not linked
    to anything to apply it to.
    """
    name = "NegotiatePrice"

    def handle(self, args: dict) -> dict:
        parameters = args.get("parameters", {}) or {}
        try:
            listed_price = float(parameters.get("listed_price"))
            min_seller_price = float(parameters.get("min_seller_price"))
            buyer_target_price = float(parameters.get("buyer_target_price"))
        except (TypeError, ValueError):
            return {"success": False, "error": (
                "NegotiatePrice requires numeric parameters.listed_price, "
                "parameters.min_seller_price, parameters.buyer_target_price"
            )}
        max_rounds = int(parameters.get("max_rounds", 3) or 3)
        deal = negotiate_price(listed_price, min_seller_price, buyer_target_price, max_rounds=max_rounds)
        result = {"success": True, **deal}
        product_id = parameters.get("product_id")
        if product_id:
            result["product_id"] = product_id
        return result


class NegotiateTermsCapability:
    """Game-Theoretic Reasoning: wraps
    kernel/domains/negotiation.py::negotiate_terms directly — a real
    bounded bilateral bargain over a single numeric term (a delivery
    delay in hours, a priority score, anything real-valued), the exact
    same proven shape as NegotiatePrice generalized past price. The
    actor's own LLM supplies the three real inputs as plan-step
    parameters; this capability only runs the bargain, never invents
    the outcome."""
    name = "NegotiateTerms"

    def handle(self, args: dict) -> dict:
        parameters = args.get("parameters", {}) or {}
        try:
            high_side_opening = float(parameters.get("high_side_opening"))
            high_side_floor = float(parameters.get("high_side_floor"))
            low_side_opening = float(parameters.get("low_side_opening"))
        except (TypeError, ValueError):
            return {"success": False, "error": (
                "NegotiateTerms requires numeric parameters.high_side_opening, "
                "parameters.high_side_floor, parameters.low_side_opening"
            )}
        max_rounds = int(parameters.get("max_rounds", 3) or 3)

        from src.monkey_brain.kernel.domains.negotiation import negotiate_terms
        deal = negotiate_terms(high_side_opening, high_side_floor, low_side_opening, max_rounds=max_rounds)
        return {"success": True, **deal}


class OrderCreationCapability:
    """Creates an order in the actor's KG covering every item in the cart —
    one order, multiple line items, subtotal/tax/total summed across all of
    them (not just the first or cheapest)."""
    name = "OrderCreation"

    def handle(self, args: dict) -> dict:
        context = args.get("context", {})
        kg = context.get("knowledge_graph")
        products = context.get("selected_product", [])
        if isinstance(products, dict):
            products = [products]

        # Real gap this closes: NegotiatePriceCapability computed a
        # genuine, bounded deal that never affected what actually got
        # charged. A real agreed negotiation (product_id-linked, via
        # project_action_result_to_context above) overrides that item's
        # listed price here, before subtotal/tax/total are ever computed
        # from it — the same "apply it before pricing, not after" timing
        # the mid-plan substitution fix above already established.
        negotiated_prices = context.get("negotiated_prices") or {}
        if negotiated_prices:
            products = [
                {**p, "price": negotiated_prices[p["id"]], "negotiated": True}
                if p.get("id") in negotiated_prices else p
                for p in products
            ]

        def store_name(store_id: str) -> str:
            # A product carries store_id, never store_name — p.get(
            # "store_name", ...) always missed and silently fell back to
            # the PRODUCT's own name (or "" per line item), so an order's
            # "store" field showed things like "Frozen Cheese Pizza"
            # instead of "Whole Foods". Resolve the real store entity.
            if kg is None or not store_id:
                return ""
            entity = kg.get_entity(store_id)
            return entity.name if entity is not None else ""

        # Performance certification (GS-6000, "memory leak: none"): found
        # live while profiling — with an empty cart (ProductSelection
        # found nothing to buy, e.g. everything out of stock), this used
        # to fall straight through and persist a real, permanent Order
        # entity anyway: 0 items, $0 total, status "confirmed" — a fake
        # successful order for a purchase that never happened, one new
        # garbage entity per failed attempt, forever. Mirrors
        # OrderConfirmationCapability's own existing check (same
        # socially_fulfilled/pantry_fulfilled no-op convention) — an
        # empty cart because everything was already covered elsewhere is
        # a genuine success with nothing to persist; an empty cart for
        # any OTHER reason is an honest failure, not a fabricated order.
        if not products:
            if context.get("socially_fulfilled") or context.get("pantry_fulfilled"):
                return {"success": True, "note": "nothing to order — already fulfilled", "order_id": None}
            return {"success": False, "error": "no products selected — nothing to order"}

        belief_refreshed = []
        backordered = []
        substitutions = []

        tax_rate = 0.08
        subtotal = round(sum(p.get("price", 0) * p.get("qty", 1) for p in products), 2)
        tax = round(subtotal * tax_rate, 2)
        # Taxes & Fees: a store's delivery_fee (attributes["delivery_fee"])
        # already factors into WHERE to buy — ProductSelection's
        # consolidation comparison weighs it when deciding whether one
        # store beats splitting the cart across several — but the fee
        # itself was never actually added to what the customer is
        # charged, here or in PaymentCapability (which just charges
        # OrderConfirmation's "total"). Summed once per distinct store in
        # the cart, not per line item, so a 3-item order from one store
        # pays that store's delivery fee once, not three times.
        # Delivery vs Pickup: a delivery fee is a delivery cost — an actor
        # who explicitly asked to pick the order up themselves never
        # incurs it, the same fulfillment choice DeliveryCapability itself
        # honors by skipping rider assignment entirely.
        store_ids = {p.get("store_id") for p in products if p.get("store_id")}
        delivery_fee = 0.0 if wants_pickup(context.get("question", "")) else round(sum(
            (kg.get_entity(sid).attributes.get("delivery_fee", 0) if kg is not None and kg.get_entity(sid) else 0)
            for sid in store_ids
        ), 2)
        total = round(subtotal + tax + delivery_fee, 2)
        store_names = sorted({name for name in (store_name(p.get("store_id")) for p in products) if name})

        # Level 36 (GS-3601): a household "spending_limit" policy blocks
        # the order OUTRIGHT (no entity is even written) if its real total
        # exceeds the declared per-order ceiling — checked here, the first
        # point the actual post-tax total exists, and before anything is
        # persisted. An owner-approved one-time exception (Level 37,
        # GS-3702) unblocks exactly this one order; the policy re-applies
        # to the next one regardless.
        if kg is not None:
            spending_policy = find_household_policy(kg, "spending_limit")
            if spending_policy is not None:
                max_per_order = spending_policy.attributes.get("params", {}).get("max_per_order")
                if max_per_order is not None and total > max_per_order and not consume_policy_exception(kg, "spending_limit"):
                    return {"success": False, "error": (
                        f"policy denied: order total ${total:.2f} exceeds household spending "
                        f"limit of ${max_per_order:.2f}")}

        # A budget stated IN THIS REQUEST ("under $20", "I have exactly
        # $20") is a real spending cap regardless of whether the household
        # ever configured a standing spending_limit policy. The planner
        # can only reason about item subtotal when picking products — tax
        # and delivery fee aren't known until this point — so a plan that
        # looked affordable at selection time can still land over budget
        # here; this is the first (and only) point the actual total the
        # customer would be charged exists, checked before anything is
        # persisted or paid, same as the policy check above.
        # Shared multi-agent budget (Qualification Gap Closure, Phase 5):
        # context["shared_budget_id"] supersedes (never adds to) the
        # stated-in-this-request budget check — a shared budget already IS
        # the real spending cap for this transaction. The actual reserve
        # happens further below, once order_id exists (it needs a real,
        # per-order holder key, the same convention product reservations
        # already use) — this only decides which check applies.
        stated_budget = parse_budget(context.get("question", ""))
        shared_budget_id = context.get("shared_budget_id")
        if not shared_budget_id and stated_budget is not None and total > stated_budget:
            return {"success": False, "error": (
                f"order total ${total:.2f} exceeds the ${stated_budget:.2f} budget stated in the request")}

        # Level 43 (GS-4300): interrupt/resume safety. A caller that knows
        # it's RETRYING a checkout that may have already gone through (a
        # crash or lost response, not a fresh request) passes the SAME
        # order_id it used before via context["resume_order_id"] instead
        # of letting this capability mint a brand-new random one every
        # call — without this, a genuinely completed-and-paid order that
        # gets retried (because the caller never saw the success response)
        # would silently create a SECOND real order and charge a SECOND
        # time, since nothing tied the retry back to what already happened.
        # If that order already reached a real successful charge
        # (payment_status == "paid", written by PaymentCapability), this
        # returns its already-settled details as a genuine no-op success —
        # nothing is re-reserved, nothing is charged again.
        resume_order_id = context.get("resume_order_id")
        if resume_order_id and kg is not None:
            existing_order = kg.get_entity(resume_order_id)
            if existing_order is not None and existing_order.attributes.get("payment_status") == "paid":
                context["already_paid"] = True
                return {
                    "success": True, "order_id": resume_order_id, "resumed": True,
                    "status": "already completed in a prior attempt — no charge or reservation repeated",
                    "store": existing_order.attributes.get("store", ""),
                    "items": existing_order.attributes.get("items", []),
                    "subtotal": existing_order.attributes.get("subtotal", 0),
                    "tax_rate": tax_rate, "tax": existing_order.attributes.get("tax", 0),
                    "delivery_fee": existing_order.attributes.get("delivery_fee", 0),
                    "total": existing_order.attributes.get("total", 0),
                    "belief_refreshed": [],
                }

        # Second-precision timestamps alone collide whenever two orders are
        # created within the same wall-clock second (two household members
        # buying at once, or just fast sequential calls) -- MERGE on the
        # same entity_id then silently overwrites the first order instead
        # of creating a second one. A random suffix makes this actually
        # unique regardless of timing -- MB-3036 (Black Friday, 10,000
        # simultaneous orders) found live that a SHORT 6-hex-char suffix
        # (~16.7M values) collides often enough under real concurrent
        # volume within the same second to matter (verified: ~5 collisions
        # per 10,000 concurrent orders), and a collision here means one
        # customer's order silently overwrites another's
        # (KnowledgeGraph.add_entity's documented overwrite-on-reuse
        # behavior) -- a real order vanishing, not just a cosmetic ID
        # clash. The full uuid4().hex (32 hex chars, 128 bits) makes that
        # practically impossible at any realistic order volume. Not used
        # when resuming a known, not-yet-paid order_id -- that ID is
        # reused as-is so a LATER retry of THIS same attempt can
        # recognize it next time.
        import uuid
        order_id = resume_order_id or f"ORD-{int(time.time())}-{uuid.uuid4().hex}"

        # Shared multi-agent budget (Qualification Gap Closure, Phase 5):
        # reserved via the SAME real, already-proven try_reserve() CAS
        # discipline products use two lines below (this module, ~line
        # 1349) — a shared budget entity is just another KG entity with a
        # real "quantity" (remaining budget in dollars); try_reserve never
        # assumed "quantity" means physical units, so it generalizes here
        # for free, no new concurrency primitive invented. Concurrent
        # actors drawing against the SAME budget id can therefore never
        # jointly commit more than its real ceiling — the identical
        # no-oversell guarantee try_reserve already gives inventory,
        # confirmed live this session for both. order_id (not actor_id) is
        # the holder key, matching product reservations below — a shared
        # budget is drawn against per ORDER, so the same actor placing two
        # concurrent orders holds two separate reservations rather than
        # one superseding the other.
        if shared_budget_id and kg is not None:
            won, reason = try_reserve(kg, shared_budget_id, order_id, total)
            if not won:
                return {"success": False, "error": f"shared budget: {reason}"}

        line_items = [
            {"product": p.get("name", ""), "qty": p.get("qty", 1), "unit_price": p.get("price", 0), "store": store_name(p.get("store_id"))}
            for p in products
        ]

        if kg:
            from src.monkey_brain.kernel.knowledge_graph import EntityType
            order_ts = time.time()
            order_items = [
                {
                    "product_id": p.get("id", ""), "qty": p.get("qty", 1), "price": p.get("price", 0),
                    # Level 19 (GS-1900/1901): captured off the product
                    # AS BOUGHT, not looked up again later — a
                    # discontinued/renamed product must not erase what
                    # this order actually shows was purchased.
                    "product_name": p.get("name", ""),
                    "brand": p.get("brand"),
                    "type_keyword": _type_keyword_of(p.get("name", "")),
                }
                for p in products
            ]
            kg.add_entity(order_id, EntityType.EVENT, "Grocery Order", {
                "order_id": order_id,
                "items": order_items,
                "subtotal": subtotal, "tax": tax, "delivery_fee": delivery_fee, "total": total, "status": "confirmed",
                # GS-1300: explicit, persisted purchase timestamp -- Entity's
                # own created_at field is NOT written to Neo4j by
                # _write_entity, so every fresh KG instance re-hydrates it
                # to hydration time, not the real order time. Storing it in
                # attributes (which DOES round-trip through attributes_json)
                # is what makes real historical demand prediction possible.
                "created_at": order_ts,
                # GS-1600: the REAL decision trace(s) ProductSelection just
                # built, persisted onto the order they actually produced —
                # "why did you choose X" answers from what this specific
                # purchase actually weighed, not a fresh re-simulation that
                # could disagree with reality if the world changed since.
                "decision_traces": context.get("decision_traces", []),
                # Phase 5 stress (GS-5100): who actually placed this order —
                # the same buyer_id convention the purchase_log marker below
                # already uses. Without this, predict_demand had no way to
                # scope demand prediction to THIS actor's own purchases (see
                # its docstring for the real cross-actor inflation this fixes).
                "buyer_id": context.get("actor_id", kg.person_id),
            })
            # Real gap this closes: nothing in the grocery domain ever
            # called KnowledgeGraph.add_relationship (confirmed by
            # grepping the whole domain — the only real callers anywhere
            # were the generic /knowledge-graph admin route and world
            # seeding), so a real order left behind zero traversable KG
            # edges — ContextConstructionEngine._explore_knowledge had
            # nothing to find, and the debugger's Relationships card was
            # permanently 0 for any execution whose grounding depended on
            # this order. Actor -[POSSESSES]-> Order, and each purchased
            # Product -[PART_OF]-> Order — real entities that already
            # exist (the actor and the just-selected products), not new
            # ones minted for this.
            from src.monkey_brain.kernel.knowledge_graph import RelationshipType
            buyer_id = context.get("actor_id", kg.person_id)
            if buyer_id and kg.get_entity(buyer_id) is not None:
                kg.add_relationship(buyer_id, order_id, RelationshipType.POSSESSES)
            for item in order_items:
                product_id = item.get("product_id")
                if product_id and kg.get_entity(product_id) is not None:
                    kg.add_relationship(product_id, order_id, RelationshipType.PART_OF)
            # Performance certification (GS-6000): incrementally update the
            # per-actor order-stats aggregate predict_demand/learned_preference
            # now read instead of re-scanning every order this actor has
            # ever placed — see update_order_stats' own docstring.
            update_order_stats(kg, context.get("actor_id", kg.person_id), order_items, order_ts)
            # Level 20 (GS-2001): a lightweight, HOUSEHOLD-scoped marker per
            # item, separate from the order itself — orders stay in the
            # buyer's private scope (Level 14/19 need that for per-person
            # demand/preference signals), but duplicate detection needs
            # every household member to see that SOMEONE bought this
            # recently, which the private order alone can't provide.
            buyer_id = context.get("actor_id", kg.person_id)
            for p in products:
                # Performance certification (GS-6000): deterministic per
                # (buyer, product) id, NOT per order -- check_recent_
                # duplicate_purchase only ever needs the MOST RECENT
                # purchase of a given product (no other code reads
                # historical markers), so a repeat purchase overwriting
                # the same marker entity (add_entity's own documented
                # overwrite behavior) keeps the count of these markers
                # bounded by distinct (buyer, product) pairs ever bought,
                # not by total order count -- the same unbounded-scan
                # class of problem update_order_stats' docstring covers,
                # fixed here by making there be nothing to accumulate.
                marker_id = f"purchase_log_{buyer_id}_{p.get('id', '')}"
                qty = p.get("qty", 1)
                marker_attrs = {
                    "purchase_log": True,
                    "product_name": p.get("name", ""),
                    "buyer_id": buyer_id,
                    "purchased_at": time.time(),
                    "household": True,
                    # How much was actually bought, in whatever unit the
                    # product is sized in -- lets a later duplicate-purchase
                    # check tell "already bought some" apart from "already
                    # bought enough" instead of treating every repeat
                    # request as fully covered regardless of quantity.
                    "purchased_qty": qty,
                }
                if p.get("size_ml"):
                    marker_attrs["purchased_volume_ml"] = qty * p["size_ml"]
                if p.get("size_g"):
                    marker_attrs["purchased_mass_g"] = qty * p["size_g"]
                # Performance certification (GS-6000): EntityType.OTHER, not
                # EVENT -- check_recent_duplicate_purchase's own scan (below)
                # needs ONLY these markers, but entities_by_type(EVENT) would
                # also walk every real Order entity ever created (which keeps
                # growing with total order count), making the deterministic-
                # marker-id fix above pointless: the outer scan would still
                # touch every order this actor has ever placed just to skip
                # past it. A separate type keeps this bounded by (OTHER-typed
                # entities: markers + coupons + memberships + rejections +
                # order-stats aggregates), not by total order count.
                kg.add_entity(marker_id, EntityType.OTHER, f"Purchase log: {p.get('name', '')}", marker_attrs)

            # Inventory Constraints / Reservation Before Payment: an order
            # is a real claim on real stock, made via the existing CAS
            # primitives (try_reserve/confirm_reservation) — the same ones
            # resume_partial_checkout already uses for the async/resumable
            # case. The order_id is used as the reservation holder (not the
            # buyer), matching resume_partial_checkout's own convention, so
            # two different orders from the same person never collide.
            #
            # HOLD only, here — never confirm yet. An earlier version
            # called confirm_reservation (a permanent decrement)
            # immediately, before PaymentConfirmation/Payment even run:
            # verified live that a payment which fails downstream (e.g.
            # insufficient balance) still left the shelf one unit short
            # for a purchase that was never actually paid for. try_reserve
            # itself still fails the order honestly if the stock isn't
            # really there (unchanged); confirm_reservation — the actual
            # commit — now only happens in PaymentCapability, after a
            # real successful charge. An unconfirmed hold lapses on its
            # own (try_reserve's hold_seconds) if payment never completes,
            # so a failed or abandoned checkout releases the stock instead
            # of consuming it.
            # Level 40 (GS-1202): refresh this actor's belief about each
            # product immediately before committing a reservation against
            # it — a belief formed back at BeliefCheck/ProductSelection may
            # be seconds old by the time execution reaches here, and
            # try_reserve's own CAS loop only protects the reservation
            # itself, not whether anything downstream even notices the
            # world moved in between. Purely informational (try_reserve
            # still independently fails honestly on real unavailability) —
            # this is what actually exercises refresh_belief_before_action,
            # previously fully built (GS-1202) but never called anywhere.
            for p in products:
                pid = p.get("id")
                if not pid:
                    continue
                updated, diffs = refresh_belief_before_action(kg, pid)
                if updated and diffs:
                    belief_refreshed.append({
                        "product": p.get("name", pid),
                        "believed": {k: v[0] for k, v in diffs.items()},
                        "observed": {k: v[1] for k, v in diffs.items()},
                    })

            # MB-3031 Backorder: inventory unavailable for an item no
            # longer fails the WHOLE order — it's queued via
            # place_backorder() (MB-3016), keyed to buyer_id (the same
            # actor-scoped convention MB-3016/MB-3030 already use for
            # backorders, deliberately NOT order_id — try_reserve's
            # order_id-as-holder convention above is scoped to this one
            # order's temporary hold, but a backorder is a claim the
            # actor holds regardless of which order it came from, and
            # create_partial_shipments() (MB-3030) looks backorders up
            # by buyer_id, not order_id). The order still completes for
            # whatever actually reserved; fulfill_backorders() delivers
            # the rest later, once it's actually back in stock.
            #
            # Real mid-plan self-healing: before backordering, try a live
            # substitute right now, in the same tick — the same search
            # OrderConfirmationCapability already performs, just run early
            # enough here to actually change what gets billed, instead of
            # arriving after PaymentConfirmation/Payment have already
            # charged for an item this order already knows isn't there.
            #
            # External world events (warehouse fire / supply-chain
            # outage): try_reserve alone only checks a product's own
            # `quantity` — it has no idea the STORE selling it can't
            # actually fulfill anything right now (open_products'/
            # OrderConfirmation's real supply_chain_ok gate, elsewhere in
            # this file). Without this check here, a warehouse failure
            # injected between ProductSelection and this step sailed
            # through OrderCreation's own reservation (the product's
            # `quantity` attribute is untouched by a warehouse going
            # down) and only got caught one stage later at
            # OrderConfirmation — still before Payment, so no overcharge
            # risk, but one full stage later than every other kind of
            # unavailability this loop already self-heals from. all_orgs
            # is computed once up front (not lazily) since this check now
            # runs for every item, not just ones whose reservation fails.
            all_orgs = {e.entity_id: e for e in kg.entities_by_type(EntityType.ORGANIZATION)}
            lactose_free = None
            rejected_keywords = None
            coupon_code = None
            request_optimization = None
            for idx, p in enumerate(products):
                pid = p.get("id")
                qty = p.get("qty", 1)
                if not pid or qty <= 0:
                    continue

                store_id = p.get("store_id")
                if store_id and not supply_chain_ok(all_orgs, store_id):
                    ok, msg = False, f"{store_name(store_id)}'s supply chain is down"
                else:
                    ok, msg = try_reserve(kg, pid, order_id, qty)
                if ok:
                    continue

                if lactose_free is None:
                    lactose_free = wants_lactose_free(context.get("question", ""))
                    rejected_keywords = get_rejected_keywords(kg)
                    coupon_code = parse_coupon_code(context.get("question", ""))
                    request_optimization = context.get("optimization", "cost")

                substituted = False
                # Human approval / pause-resume: this self-healing
                # substitution runs BEFORE OrderConfirmationCapability's own
                # approval-gated substitution ever gets a chance to see the
                # item was stale -- this loop silently resolved it first,
                # every time, regardless of the SAME
                # approval_required_for_substitution/"ask me before buying
                # a substitute" contract OrderConfirmation already honors
                # (test_human_approval.py's own real, live-only finding).
                # When approval is required, skip the auto-substitute
                # attempt here (falling through to backorder below) and
                # leave `products[idx]` referencing the ORIGINAL item --
                # OrderConfirmation's independent freshness re-check then
                # discovers the same staleness itself and pauses for a real
                # human decision, exactly as it already does when nothing
                # upstream silently resolved it.
                approval_required_here = bool(context.get("approval_required_for_substitution")) or \
                    wants_approval_before_substitution(context.get("question", ""))
                fresh_catalog = [] if approval_required_here else open_products(kg, item_phrase=p.get("name", ""))
                remaining = [e for e in fresh_catalog if e.entity_id != pid]
                candidates, _note = eligible_candidates(p.get("name", ""), remaining, lactose_free, rejected_keywords)
                if candidates:
                    item_optimization = detect_optimization(p.get("name", "")) or request_optimization
                    best, _unit_qty, _reason = pick_best_unit(candidates, item_optimization, all_orgs)
                    adjusted, _notes = apply_discounts_to_candidates(kg, [best], coupon_code, buyer_id)
                    priced = adjusted[0] if adjusted else best
                    sub_ok, sub_msg = try_reserve(kg, best.entity_id, order_id, qty)
                    if sub_ok:
                        products[idx] = {**p, **priced.attributes, "id": best.entity_id, "name": best.name, "qty": qty}
                        substitutions.append(
                            f"{p.get('name', pid)} unavailable ({msg}) — substituted {best.name} at "
                            f"{store_name(priced.attributes.get('store_id'))} (${priced.attributes.get('price', 0):.2f})"
                        )
                        substituted = True

                if not substituted:
                    backorder = place_backorder(kg, pid, buyer_id, qty)
                    backordered.append({
                        "product": p.get("name", pid), "product_id": pid, "qty": qty,
                        "reason": msg, "backorder_id": backorder["backorder_id"],
                    })

            # A substitution above changed what's actually in the cart
            # after subtotal/tax/total/line_items/order_items were already
            # computed and persisted (above) from the ORIGINAL cart — this
            # recomputes and re-persists them from the now-corrected
            # `products`, using the exact same formula as the original
            # computation. Deliberately left as its own pass rather than
            # refactored into the earlier computation: it only ever runs
            # on the rare substitution path, and keeps the well-tested
            # original computation completely untouched.
            if substitutions:
                subtotal = round(sum(p.get("price", 0) * p.get("qty", 1) for p in products), 2)
                tax = round(subtotal * tax_rate, 2)
                store_ids = {p.get("store_id") for p in products if p.get("store_id")}
                delivery_fee = 0.0 if wants_pickup(context.get("question", "")) else round(sum(
                    (kg.get_entity(sid).attributes.get("delivery_fee", 0) if kg.get_entity(sid) else 0)
                    for sid in store_ids
                ), 2)
                total = round(subtotal + tax + delivery_fee, 2)
                store_names = sorted({name for name in (store_name(p.get("store_id")) for p in products) if name})
                line_items = [
                    {"product": p.get("name", ""), "qty": p.get("qty", 1), "unit_price": p.get("price", 0), "store": store_name(p.get("store_id"))}
                    for p in products
                ]
                order_items = [
                    {
                        "product_id": p.get("id", ""), "qty": p.get("qty", 1), "price": p.get("price", 0),
                        "product_name": p.get("name", ""), "brand": p.get("brand"),
                        "type_keyword": _type_keyword_of(p.get("name", "")),
                    }
                    for p in products
                ]
                kg.update_entity(order_id, attributes={
                    "items": order_items, "subtotal": subtotal, "tax": tax,
                    "delivery_fee": delivery_fee, "total": total,
                })
                context["selected_product"] = products

        result = {
            "success": True,
            "order_id": order_id,
            "store": ", ".join(store_names),
            "items": line_items,
            "subtotal": subtotal, "tax_rate": tax_rate, "tax": tax, "delivery_fee": delivery_fee, "total": total,
            "belief_refreshed": belief_refreshed,
            "backordered": backordered,
        }
        if substitutions:
            result["replanned"] = substitutions
        return result


def _paying_actor_id(context: dict) -> str | None:
    """Whose accounts should actually be charged for this request?

    context["actor_id"] is always the AUTHENTICATED requester (see
    DelegationCheckCapability's docstring — it's deliberately left
    untouched for audit purposes even when acting under delegation).
    But a delegate has no accounts of their own to find: the whole point
    of "buy milk on behalf of alice" is that BOB is the authenticated
    actor while ALICE's real wallet pays. Every financial lookup
    (find_payment_sources/_find_wallet/enterprise_purchase_authorized/
    separation_of_duties_satisfied) must scope to context["acting_as"]
    (the delegator) when a delegation is active, or it silently finds no
    account at all and PaymentCapability falls through with wallet=None —
    a phantom "success" that charges nobody. Caught live: a fully-verified,
    active delegation still resulted in payment reporting
    "status": "completed" while the delegator's real wallet balance never
    moved, because every call site here was scoped to the delegATE's
    identity instead.
    """
    return context.get("acting_as") or context.get("actor_id")


class PaymentConfirmationCapability:
    """Confirms payment is authorized to proceed — checks the order total
    against the actor's wallet balance instead of always succeeding."""
    name = "PaymentConfirmation"

    def handle(self, args: dict) -> dict:
        context = args.get("context", {})
        kg = context.get("knowledge_graph")
        order = context.get("order", {})
        total = context.get("total", order.get("total", 0))

        if not order or not order.get("order_id"):
            return {"success": False, "error": "no order to confirm payment for"}

        # Level 43 (GS-4300): OrderCreation already found this a resumed
        # order that reached a real successful charge in a prior attempt —
        # confirming it again would just be re-litigating a purchase that
        # already happened.
        if context.get("already_paid"):
            return {"success": True, "status": "already paid (resumed order)", "order_id": order.get("order_id"), "total": total}

        # Payment integrity: OrderCreation may have already discovered, in
        # this same tick, that some cart items couldn't be reserved and
        # backordered them (MB-3031) — but its own "total" still includes
        # those items' full price, since partial-fulfillment billing isn't
        # implemented. Confirming payment on that total would charge the
        # actor for stock they don't have. Fail closed until an order can
        # actually itemize what was reserved vs backordered.
        if order.get("backordered"):
            names = ", ".join(b.get("product", "?") for b in order["backordered"])
            return {"success": False, "error": (
                f"cannot confirm payment: {len(order['backordered'])} item(s) could not be "
                f"reserved and are backordered ({names})"
            )}

        # Level 35 (GS-3501/3502): checked against the AUTHENTICATED
        # actor_id (the JWT's verified sub claim, threaded through
        # context by the /prompt route — never a request-supplied
        # field), BEFORE any balance/affordability check runs. Asking to
        # pay from someone else's personal account must be denied
        # regardless of whether that account could afford it.
        target_actor = parse_target_payment_actor(context.get("question", ""))
        if target_actor is not None:
            authz = authorize_payment_source(context.get("actor_id", ""), target_actor)
            if not authz["authorized"]:
                return {"success": False, "error": f"authorization denied: {authz['reason']}"}

        # Level 39 (GS-3902): a delegation checked as active back at the
        # START of the pipeline (DelegationCheckCapability) is
        # RE-VERIFIED here, right before payment — the closest honest
        # analog this request/response architecture has to "revoked
        # mid-execution, pause, reauthorization required" (no genuine
        # async pause/resume exists here, the same honest boundary
        # Level 18/26 already drew for "delay"/"autonomous"). If Alice
        # revoked it in the moments between plan-start and this step,
        # THIS check catches it and payment is refused, not silently
        # completed on stale authority.
        acting_as = context.get("acting_as")
        if acting_as:
            recheck = check_delegation(acting_as, context.get("actor_id", ""), kg=context.get("knowledge_graph"))
            if not recheck["active"]:
                return {"success": False,
                        "error": f"reauthorization required: delegation from {acting_as} is no longer active ({recheck['reason']})"}

        if total <= 0:
            # Level 16/20: a $0 total with zero line items is only a
            # legitimate no-op when something ELSE actually fulfilled the
            # request (borrowed, negotiated, already in the pantry, already
            # bought by someone else) — checked via the SAME fulfillment
            # flags SocialSourcing/HouseholdCognition set, not just "the
            # order happens to be empty". An empty order with NEITHER flag
            # set means ProductSelection genuinely failed upstream (e.g. no
            # matching product exists at all) and must surface as the real
            # error it is, not get silently relabeled "already fulfilled".
            if not order.get("items") and (context.get("socially_fulfilled") or context.get("pantry_fulfilled")):
                return {"success": True, "status": "no payment needed — already fulfilled",
                        "order_id": order.get("order_id"), "total": 0}
            return {"success": False, "error": f"invalid order total: {total}"}

        # MB-3014 Fraud Detection: checked BEFORE any balance/account
        # lookup below, same principle as Level 35's authorize_payment_
        # source running before any affordability check — a high-risk
        # transaction is held for review regardless of whether the
        # account could actually afford it, and the response never
        # reaches the balance-lookup code path that would otherwise leak
        # real account/balance details to what might be a stolen-card
        # test. Real signals only (kernel/domains/finance.py::
        # assess_transaction_risk, from this actor's actual persisted
        # order history) — never a hardcoded score.
        if kg is not None:
            from src.monkey_brain.kernel.domains.finance import assess_transaction_risk
            risk = assess_transaction_risk(kg, _paying_actor_id(context), total)
            if risk["high_risk"]:
                return {
                    "success": False,
                    "error": "transaction held for fraud review",
                    "fraud_review": True,
                    "risk_assessment": risk,
                }

        if kg is not None:
            paying_actor_id = _paying_actor_id(context)
            accounts = find_payment_sources(kg, paying_actor_id)
            # Level 26 (GS-2600/2601/2602) only engages when at least one
            # account actually declares a real account_type from the new
            # taxonomy (food_assistance/cash/credit) — a household with
            # merely MORE THAN ONE account isn't necessarily using it.
            # Caught live: alice's real household has always had a
            # personal "Alice Wallet" alongside the shared household
            # wallet (predating Level 26, both using the older
            # attributes["type"]="wallet" convention, neither tagged with
            # a real account_type) — len(accounts) > 1 alone falsely
            # engaged this branch, found no recognized account_type on
            # either real account, and rejected a payment real funds could
            # actually cover.
            has_typed_accounts = any(a.attributes.get("account_type") in _PAYMENT_SOURCE_PRIORITY for a in accounts)
            if has_typed_accounts:
                import datetime
                month_start = datetime.datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
                cap_account = next((a for a in accounts if a.attributes.get("monthly_cap")), None)
                if cap_account is not None:
                    cap_check = check_monthly_cap(kg, cap_account.attributes["monthly_cap"], total, month_start)
                    if not cap_check["within_cap"]:
                        return {"success": False, "error": cap_check["reason"], "monthly_cap_check": cap_check}

                choice = choose_payment_source(accounts, total)
                if choice["chosen"] is not None:
                    # Level 40 (GS-4000/4001): the account CHOSEN to pay,
                    # checked here for the same reason Level 35's
                    # authorize_payment_source runs before any balance
                    # check — a role failure must block the purchase
                    # regardless of whether the account could afford it.
                    ent_check = enterprise_purchase_authorized(kg, context.get("actor_id", ""), choice["chosen"], total)
                    if not ent_check["authorized"]:
                        return {"success": False, "error": f"RBAC denied: {ent_check['reason']}"}
                    # Level 43 (GS-4300/4301): checked IN ADDITION TO the
                    # RBAC rank check above — even a procurement_manager
                    # cannot unilaterally clear their own large purchase.
                    sod_check = separation_of_duties_satisfied(kg, context.get("actor_id", ""), choice["chosen"], total, order)
                    if not sod_check["authorized"]:
                        return {"success": False, "error": f"separation of duties denied: {sod_check['reason']}"}
                    context["chosen_payment_source"] = choice["chosen"].entity_id
                    return {"success": True, "status": "confirmed", "order_id": order.get("order_id"),
                            "total": total, "payment_source": choice["account_type"]}

                # GS-2601: nothing alone covers it -- try a real, bounded
                # emergency extension before failing outright.
                credit_account = next((a for a in accounts if a.attributes.get("account_type") == "credit"), None)
                max_available = max((_available_funds(a) for a in accounts), default=0.0)
                shortfall = round(total - max_available, 2)
                borrow = attempt_borrowing(credit_account, shortfall)
                if borrow["approved"]:
                    context["chosen_payment_source"] = credit_account.entity_id
                    context["emergency_borrowing"] = borrow
                    return {"success": True, "status": "confirmed via emergency credit extension",
                            "order_id": order.get("order_id"), "total": total, "borrowing": borrow}
                return {"success": False,
                        "error": f"insufficient funds across all {len(accounts)} payment sources ({borrow['reason']})"}

            wallet = _find_wallet(kg, paying_actor_id)
            if wallet is None:
                return {"success": False, "error": "no wallet account found for payment"}
            ent_check = enterprise_purchase_authorized(kg, context.get("actor_id", ""), wallet, total)
            if not ent_check["authorized"]:
                return {"success": False, "error": f"RBAC denied: {ent_check['reason']}"}
            sod_check = separation_of_duties_satisfied(kg, context.get("actor_id", ""), wallet, total, order)
            if not sod_check["authorized"]:
                return {"success": False, "error": f"separation of duties denied: {sod_check['reason']}"}
            # UPI Reserve Pay (kernel/domains/payment_provider.py): a
            # wallet entity explicitly tagged account_type=="upi_reserve_pay"
            # has no meaningful local "balance" to check here at all — the
            # real affordability determination happens at the payer's own
            # bank, when they approve (or don't) the reserve request in
            # their UPI app, which this backend has no visibility into
            # ahead of time. Every other account_type (including no tag at
            # all, the default for every existing wallet) keeps this exact
            # check unchanged.
            if wallet.attributes.get("account_type") != "upi_reserve_pay":
                balance = wallet.attributes.get("balance", 0)
                if balance < total:
                    return {
                        "success": False,
                        "error": f"insufficient balance: {wallet.name} has ${balance:.2f}, order total is ${total:.2f}",
                    }

        return {"success": True, "status": "confirmed", "order_id": order.get("order_id"), "total": total}


class PaymentCapability:
    """Processes payment using wallet/bank/processor from the actor's KG."""
    name = "Payment"

    def handle(self, args: dict) -> dict:
        context = args.get("context", {})
        kg = context.get("knowledge_graph")
        total = context.get("total", 0)
        order_id = context.get("order", {}).get("order_id", "")

        def _finalize_successful_payment(
            wallet, wallet_name: str, bank_name: str, processor_name: str,
            payment_attempts: list, balance_before, balance_after, account_type,
        ) -> dict:
            """Shared tail for EVERY real successful charge, wallet-debit
            or UPI Reserve Pay alike: confirms the stock/budget holds
            OrderCreation only placed (Reservation Before Payment — see
            below), records what was actually paid on the order, builds
            the honest result shape, applies the delegate-privacy
            redaction, and writes the audit event. Extracted so UPI's
            real capture()-success path doesn't have to duplicate this —
            every one of these side effects applies identically regardless
            of which payment rail actually landed the charge."""
            # Reservation Before Payment: the actual stock commit — see
            # OrderCreationCapability's comment. OrderCreation only placed a
            # HOLD (try_reserve); confirming it (a permanent decrement) is
            # deferred to here, the point payment has actually, successfully
            # landed. A payment that fails anywhere above this line never
            # reaches this call, so its held stock is never consumed — it
            # just lapses on its own once the hold expires.
            if kg:
                for p in context.get("selected_product") or []:
                    pid = p.get("id")
                    qty = p.get("qty", 1)
                    if pid and qty > 0:
                        confirm_reservation(kg, pid, order_id)
                # Shared multi-agent budget (Qualification Gap Closure, Phase
                # 5): the SAME hold-then-confirm-on-real-payment sequencing as
                # product reservations directly above — OrderCreation only
                # placed a hold; converting it into a permanent commit against
                # the shared ceiling is deferred to here, the point payment has
                # actually, successfully landed.
                shared_budget_id = context.get("shared_budget_id")
                if shared_budget_id:
                    confirm_reservation(kg, shared_budget_id, order_id)
                # Goal Cancellation: cancel_order() needs to know WHICH account
                # to refund and WHAT was actually paid — "status": "confirmed"
                # alone (written by OrderCreationCapability, before payment
                # even runs) doesn't mean payment succeeded, so it can't be
                # trusted as "this order was really paid for". Recording the
                # real paid wallet/amount here, at the point of a genuine
                # successful charge, is the only honest signal of that.
                if wallet is not None and order_id:
                    kg.update_entity(order_id, attributes={
                        "paid_wallet_id": wallet.entity_id, "paid_amount": total, "payment_status": "paid",
                    })

                # Credit the selling side of the transaction — see
                # kernel/domains/commerce.py::onboard_merchant's own
                # comment: before this, nothing anywhere credited a
                # store's account at all; Payment only ever debited the
                # buyer, and the amount simply vanished rather than
                # landing anywhere. Splits by each line item's own
                # store_id (a cart can span multiple stores) and credits
                # only the goods subtotal (price*qty), not tax/delivery
                # fee, which aren't the store's own revenue. Uses the
                # SAME CAS-safe increment _cas_adjust_balance already
                # provides for peer-to-peer payment (Level 50) — a store
                # can be credited from many concurrent buyers at once,
                # the identical concurrency hazard the buyer-side CAS
                # loop above already guards against.
                from src.monkey_brain.kernel.knowledge_graph import EntityType as _EntityType
                totals_by_store: dict[str, float] = {}
                for p in context.get("selected_product") or []:
                    sid = p.get("store_id")
                    if not sid:
                        continue
                    totals_by_store[sid] = totals_by_store.get(sid, 0.0) + (p.get("price", 0) * p.get("qty", 1))
                for sid, sale_amount in totals_by_store.items():
                    if sale_amount <= 0:
                        continue
                    store_account = next(
                        (a for a in kg.entities_by_type(_EntityType.ACCOUNT) if a.attributes.get("store_id") == sid),
                        None,
                    )
                    if store_account is not None:
                        _cas_adjust_balance(kg, store_account.entity_id, round(sale_amount, 2))

            result = {
                "success": True,
                "payment_id": f"PAY-{int(time.time())}",
                "amount": total,
                "wallet": wallet_name,
                "payment_source": account_type,
                "bank": bank_name,
                "processor": processor_name,
                "payment_attempts": payment_attempts,
                "wallet_balance_before": balance_before,
                "wallet_balance_after": balance_after,
                "status": "completed",
            }

            # Level 38 (GS-3800/3801): a delegate acting for someone OUTSIDE
            # their own real household (the common delegation case, Level 39)
            # needs to know the payment succeeded, not the delegator's exact
            # account identity or balance — a privacy boundary, distinct from
            # authorization (Level 39's delegation check already decided this
            # payment itself is legitimate). A delegate who IS a real household
            # member of the delegator, or the delegator acting for themselves,
            # sees the full detail unchanged.
            acting_as = context.get("acting_as")
            if acting_as and not is_same_household(acting_as, context.get("actor_id", "")):
                result["wallet"] = "[redacted]"
                result["bank"] = "[redacted]"
                result["wallet_balance_before"] = None
                result["wallet_balance_after"] = None

            from src.monkey_brain.kernel.pipeline.audit_trail import record_decision_event
            record_decision_event(
                "payment_completed", actor_id=context.get("actor_id", ""),
                execution_id=context.get("execution_id", ""),
                reason=f"Charged ${total:.2f} via {processor_name}",
                metadata={"payment_id": result["payment_id"], "order_id": order_id, "amount": total},
            )

            return result

        def _handle_upi_reserve_pay(wallet, account_type: str) -> dict:
            """Real UPI Reserve Pay (kernel/domains/payment_provider.py::
            RazorpayUPIProvider via get_default_provider() — the same
            singleton the webhook route, api/routes/payments.py, resolves
            authorizations through). Only reached for a wallet entity
            explicitly tagged account_type=="upi_reserve_pay" — every
            other account type keeps using the wallet-debit path below,
            completely unchanged.

            Two-phase, spanning TWO separate handle() calls: the first
            reserves and PAUSES (ActionExecutor persists a PendingPayment
            and the tick stops here — see action_executor.py); the SECOND
            is the resumed call, after a real webhook confirmed the payer's
            decision, and either captures for real or reports the honest
            refusal. context["payment_decision"] (injected by
            ActionExecutor on resume, never set on a first attempt) is
            what tells these two calls apart — the same signal
            approval_decision/negotiation_decision already use for their
            own pause/resume capabilities.
            """
            from src.monkey_brain.kernel.domains.payment_provider import sync_call
            from src.monkey_brain.kernel.domains.razorpay_upi_provider import get_default_provider

            provider = get_default_provider()
            payer_id = _paying_actor_id(context)

            # RBAC/SoD re-verified here regardless of which pass this is —
            # the SAME "verify directly against real current state, don't
            # trust a prior check" principle every other check in this
            # function already follows; a resumed capture happens after a
            # real-world delay (the payer approving in their own app), so
            # re-checking is the MORE correct application of that
            # principle here, not a redundant one.
            ent_check = enterprise_purchase_authorized(kg, context.get("actor_id", ""), wallet, total)
            if not ent_check["authorized"]:
                return {"success": False, "error": f"RBAC denied: {ent_check['reason']}"}
            sod_check = separation_of_duties_satisfied(kg, context.get("actor_id", ""), wallet, total, context.get("order", {}))
            if not sod_check["authorized"]:
                return {"success": False, "error": f"separation of duties denied: {sod_check['reason']}"}

            decision = context.get("payment_decision")
            if decision is not None:
                # Resumed: a real decision already exists — capture (or
                # honor a real refusal), never reserve again.
                if decision is not True:
                    return {"success": False, "error": "UPI payment was not approved"}
                reservation_id = context.get("payment_reservation_id", "")
                cap = sync_call(provider.capture(reservation_id, f"{order_id}:capture"))
                if not cap.success:
                    return {"success": False, "error": cap.reason}
                # The real capture succeeded — the money DID actually
                # move at the PSP. This wallet's own tracked balance is
                # the local ledger everything else in this system
                # (seeding, refunds, store crediting) already treats as
                # real, so it must be debited here too, the same
                # CAS-safe way the non-UPI wallet-debit path already is
                # -- without this, a UPI purchase credited the store
                # (see _finalize_successful_payment below) while the
                # buyer's own tracked balance never moved, silently
                # inflating the total in circulation exactly the way
                # crediting a store with no matching refund debit did.
                current = kg.get_entity(wallet.entity_id)
                balance_before = current.attributes.get("balance", 0) if current else 0
                if balance_before < total:
                    return {"success": False,
                            "error": f"insufficient balance: {wallet.name} has ${balance_before:.2f}, charge is ${total:.2f}"}
                if not _cas_adjust_balance(kg, wallet.entity_id, -total):
                    return {"success": False, "error": "payment failed: too much contention on wallet, please retry"}
                balance_after = round(balance_before - total, 2)
                # Level 43's single-use consumption, deferred to HERE (not
                # the reserve pass below) — for UPI, capture is the true
                # "money moved" point, the direct analog of the CAS wallet
                # debit above; consuming a single-use approval before the
                # payer had even approved the UPI collect request would
                # burn it on a purchase that might never actually happen.
                if "request_id" in sod_check:
                    consume_purchase_request(kg, sod_check["request_id"])
                return _finalize_successful_payment(
                    wallet, wallet.name, "UPI", provider.name, [],
                    balance_before=balance_before, balance_after=balance_after, account_type=account_type,
                )

            # First attempt: reserve (hold funds) — real money is not yet
            # moved. reserve_idempotency_key is the order_id itself
            # (already unique and, per OrderCreationCapability's own
            # resume_order_id, stable across a resumed tick), so a retried
            # first attempt replays the SAME reservation instead of
            # creating a second real order.
            r = sync_call(provider.reserve(total, payer_id, order_id))
            if not r.success:
                return {"success": False, "error": r.reason}
            # Demo-mode only (UPI_AUTO_APPROVE_SECONDS — see its own
            # docstring): with UPI now every actor's sole payment account,
            # a real checkout has no debit fallback to pause on instead —
            # this stands in for the payer actually approving the UPI
            # collect request, so checkout completes on its own instead of
            # pausing forever with nothing to ever resolve it.
            from src.monkey_brain.kernel.domains.razorpay_upi_provider import schedule_auto_approval
            schedule_auto_approval(r.reservation_id, r.amount)
            return {
                "success": False,
                "requires_payment_confirmation": True,
                "provider_name": provider.name,
                "reservation_id": r.reservation_id,
                "payer_ref": payer_id,
                "amount": r.amount,
                "reserve_idempotency_key": order_id,
                "status": r.status.value,
                "reason": "awaiting UPI approval",
            }

        # Level 43 (GS-4300): OrderCreation already found this a resumed
        # order that reached a real successful charge in a prior attempt —
        # charging it again would be a genuine double-charge, exactly the
        # failure mode interrupt/resume safety exists to prevent.
        if context.get("already_paid"):
            return {"success": True, "status": "already paid (resumed order)", "amount": 0}

        # Level 16/20: nothing to charge for (everything was borrowed/
        # negotiated socially, or already covered at home) is a real,
        # successful outcome — don't fabricate a $0 "payment". But a $0
        # total with NEITHER fulfillment flag set means ProductSelection
        # genuinely found nothing at all (e.g. GS-3700: a regional outage
        # with no surviving store anywhere) — PaymentConfirmationCapability
        # already refuses this exact case ("invalid order total"); Payment
        # itself did not, and fell through to a real wallet, where a $0
        # charge trivially passes every affordability/CAS check and reports
        # a fabricated "completed" payment for a purchase that never
        # happened. Reproduced live: every store closed, ProductSelection
        # and OrderConfirmation both correctly failed, yet Payment still
        # returned {"success": True, "status": "completed", "amount": 0.0"}.
        if total <= 0:
            if context.get("socially_fulfilled") or context.get("pantry_fulfilled"):
                return {"success": True, "status": "no payment needed — already fulfilled", "amount": 0}
            return {"success": False, "error": f"invalid order total: {total}"}

        # MB-3014 Fraud Detection: re-verified here too, the SAME "verify
        # directly, don't trust a prior check" principle the RBAC/SoD
        # re-checks below already follow — PaymentConfirmation's own
        # fraud-review rejection doesn't stop ActionExecutor from running
        # this step anyway, so a high-risk transaction must be caught
        # HERE too, at the actual charge point, not only at Confirmation.
        if kg:
            from src.monkey_brain.kernel.domains.finance import assess_transaction_risk
            risk = assess_transaction_risk(kg, _paying_actor_id(context), total)
            if risk["high_risk"]:
                return {
                    "success": False,
                    "error": "transaction held for fraud review",
                    "fraud_review": True,
                    "risk_assessment": risk,
                }

        wallet = None
        bank = None
        payment_result = {"success": False, "processor": None, "attempts": []}
        account_type = None

        if kg:
            from src.monkey_brain.kernel.knowledge_graph import EntityType
            # Level 26 (GS-2600): charge the SPECIFIC account
            # PaymentConfirmation actually chose (food assistance, cash,
            # or credit) when one was chosen — never re-guess with
            # _find_wallet and silently charge a different account than
            # the one that was confirmed.
            chosen_id = context.get("chosen_payment_source")
            wallet = kg.get_entity(chosen_id) if chosen_id else _find_wallet(kg, _paying_actor_id(context))
            account_type = wallet.attributes.get("account_type") if wallet else None

            # UPI Reserve Pay dispatch: a wallet explicitly tagged this way
            # never goes through the simulated payment_processor/CAS-balance
            # machinery below at all — real money moves (or is refused) via
            # a real PSP instead. Every other account_type (including no
            # tag, the default for every existing wallet) falls through to
            # the unchanged code beneath this block.
            if wallet is not None and account_type == "upi_reserve_pay":
                return _handle_upi_reserve_pay(wallet, account_type)

            orgs = [e for e in kg.entities_by_type(EntityType.ORGANIZATION)]
            bank = next((e for e in orgs if "bank" in e.name.lower() or e.attributes.get("type") == "bank"), None)
            # GS-2200: try every processor, retrying a transiently-down one
            # before falling back to the next — not just the first one found.
            payment_result = process_payment_with_fallback(kg, total)

        # Same "verify directly, don't trust a prior check" principle as
        # the RBAC/SoD/balance re-checks below — PaymentConfirmation's own
        # "no wallet account found" rejection doesn't stop ActionExecutor
        # from running this step anyway (by design). Without this,
        # wallet=None fell all the way through to the unconditional
        # "success": True at the bottom, fabricating a completed payment
        # that charged nothing — confirmed live for a delegated purchase
        # scoped to the wrong actor_id (see _paying_actor_id).
        if kg and wallet is None:
            return {"success": False, "error": "no wallet account found for payment"}

        if kg and not payment_result["success"]:
            return {"success": False, "error": payment_result["reason"], "attempts": payment_result["attempts"]}

        # Level 40 (GS-4000/4001): the SAME "verify directly, don't trust a
        # prior check" principle the balance re-check right below already
        # follows — PaymentConfirmation's RBAC check doesn't stop
        # ActionExecutor from running this step anyway, so it's re-verified
        # here too, against the actual account about to be charged.
        if wallet is not None:
            ent_check = enterprise_purchase_authorized(kg, context.get("actor_id", ""), wallet, total)
            if not ent_check["authorized"]:
                return {"success": False, "error": f"RBAC denied: {ent_check['reason']}"}
            # Level 43 (GS-4300/4301/4302): re-verified here too, and this
            # is the actual charge point — the approved request is
            # CONSUMED here (single-use), not at PaymentConfirmation,
            # since that step can run without the charge actually landing.
            sod_check = separation_of_duties_satisfied(kg, context.get("actor_id", ""), wallet, total, context.get("order", {}))
            if not sod_check["authorized"]:
                return {"success": False, "error": f"separation of duties denied: {sod_check['reason']}"}
            if "request_id" in sod_check:
                consume_purchase_request(kg, sod_check["request_id"])

        wallet_name = wallet.name if wallet else "Unknown Wallet"
        bank_name = bank.name if bank else "Unknown Bank"
        processor_name = payment_result["processor"] or "Unknown Processor"
        balance_before = wallet.attributes.get("balance", 0) if wallet else 0

        # Never trust that PaymentConfirmationCapability already gate-kept
        # this — ActionExecutor runs every pipeline step regardless of an
        # earlier step's failure (by design, for every OTHER capability),
        # which meant an actual insufficient-balance rejection from
        # Confirmation never stopped Payment from charging anyway. Caught
        # live: a runaway quantity bug (now fixed separately) drove a
        # wallet to -$20M because this check was missing here — the same
        # "verify directly against real current state, don't trust a
        # prior check" principle every CAS primitive in this file already
        # follows.
        #
        # A credit account's "balance" is DEBT OWED, not funds on hand, so
        # affordability means available credit (credit_limit - balance),
        # PLUS any emergency extension PaymentConfirmation already
        # approved for this specific transaction (GS-2601) — not the raw
        # balance comparison every other account type uses.
        #
        # Household-shared-wallet concurrency (GS-3400): two household
        # members charging the SAME wallet at genuinely the same time is
        # a real, plain read-modify-write race — balance_before/after here
        # were computed from a single stale read and written with a bare
        # kg.update_entity, exactly the unprotected pattern
        # try_reserve/confirm_reservation already exists to avoid for
        # inventory. Reproduced live under real OS-thread concurrency
        # (not asyncio.gather, which can't preempt mid-call): two
        # concurrent $7.55/$4.85 charges against one $100 household
        # wallet BOTH reported success, but the final balance reflected
        # only one deduction — the other was silently overwritten. Fixed
        # by charging via the same compare_and_swap primitive inventory
        # already uses, re-reading the real current balance and retrying
        # on conflict instead of trusting the value read once at the top
        # of this function.
        def _apply_balance_change(current: float) -> float:
            return round(current + total, 2) if account_type == "credit" else round(current - total, 2)

        if wallet:
            for _ in range(20):
                current = kg.get_entity(wallet.entity_id)
                current_balance = current.attributes.get("balance", 0)
                expected_version = kg.version_of(wallet.entity_id)
                if account_type == "credit":
                    extension = (context.get("emergency_borrowing") or {}).get("extension_used", 0)
                    available = _available_funds(current) + extension
                    if available < total:
                        return {"success": False,
                                "error": f"insufficient credit: {wallet_name} has ${available:.2f} available "
                                         f"(incl. any approved extension), charge is ${total:.2f}"}
                elif current_balance < total:
                    return {"success": False,
                            "error": f"insufficient balance: {wallet_name} has ${current_balance:.2f}, charge is ${total:.2f}"}
                balance_after = _apply_balance_change(current_balance)
                ok, _ = kg.compare_and_swap(wallet.entity_id, expected_version, {"balance": balance_after})
                if ok:
                    balance_before = current_balance
                    break
            else:
                return {"success": False, "error": "payment failed: too much contention on wallet, please retry"}
        else:
            balance_after = _apply_balance_change(balance_before)

        return _finalize_successful_payment(
            wallet, wallet_name, bank_name, processor_name, payment_result["attempts"],
            balance_before, balance_after, account_type,
        )


_SHIPPED_ORDER_STATUSES = frozenset({"delivered", "completed", "return_requested", "returned"})
"""MB-3029: statuses that mean an order has already shipped (or is
already mid-return) — cancel_order() refuses all of these, since
return_order() (MB-3025/MB-3026) is the correct path once an order has
actually gone out."""


def cancel_order(kg, order_id: str, actor_id: str | None = None) -> dict:
    """Goal Cancellation / MB-3029 Cancel Order: reverses a paid order
    BEFORE it ships — refunds the real wallet that paid for it, restocks
    the reserved quantity back onto each product still in the catalog,
    and marks the order cancelled. A clean synchronous undo: unlike
    mid-flight interruption (which this request/response architecture
    has no real mechanism for — the same honest boundary already drawn
    for "delay"/"autonomous" requests elsewhere in this domain),
    cancelling an order that has ALREADY finished executing needs no
    deferred-execution machinery at all — everything it touches (wallet
    balance, product quantity, the order record) is a real,
    already-persisted KG fact it can update directly.

    Refuses an order that's already shipped (MB-3021's "delivered",
    MB-3022's "completed") or is already mid-return
    (MB-3025's "return_requested", MB-3026's "returned") — the symmetric
    complement of return_order()'s own "must have shipped" guard.
    Cancellation is for stopping an order before it goes out; once it
    has, return_order() is the only correct path back.

    Only ever refunds/restocks an order this function can prove was
    actually paid: "payment_status" == "paid", written by PaymentCapability
    at the point of a genuine successful charge — an order's "status"
    field alone is set at CREATION time (OrderCreationCapability), before
    payment even runs, so it can't be trusted to mean "this was paid for".
    Restocking silently skips (rather than failing the whole cancellation)
    any line item whose product has since been discontinued — there's
    nothing real left to restock, but the refund and cancellation are
    still genuine and should still go through.
    """
    order = kg.get_entity(order_id)
    if order is None:
        return {"success": False, "error": f"no such order {order_id!r}"}
    status = order.attributes.get("status")
    if status == "cancelled":
        return {"success": False, "error": f"order {order_id!r} is already cancelled"}
    if status in _SHIPPED_ORDER_STATUSES:
        return {
            "success": False,
            "error": f"order {order_id!r} is {status!r} — it has already shipped, "
                     f"use return_order instead of cancel_order",
        }
    if order.attributes.get("payment_status") != "paid":
        return {"success": False, "error": f"order {order_id!r} was never successfully paid for — nothing to reverse"}

    wallet_id = order.attributes.get("paid_wallet_id")
    wallet = kg.get_entity(wallet_id) if wallet_id else None
    if wallet is None:
        return {"success": False, "error": f"paid wallet {wallet_id!r} for order {order_id!r} no longer exists — cannot refund"}

    # MB-3027: never blindly refund the original paid_amount — a prior
    # partial/goodwill refund_order() call may have already returned
    # some of it; only what's actually still outstanding gets refunded
    # here, so a partial refund followed by a full cancellation can
    # never double-refund the same money.
    already_refunded = order.attributes.get("total_refunded", 0)
    refund_amount = round(order.attributes.get("paid_amount", order.attributes.get("total", 0)) - already_refunded, 2)
    account_type = wallet.attributes.get("account_type")
    balance = wallet.attributes.get("balance", 0)
    # A credit account's balance is debt owed (Level 26) — refunding a
    # credit purchase REDUCES the debt, the same direction Payment's own
    # charge/credit asymmetry already established.
    new_balance = round(balance - refund_amount, 2) if account_type == "credit" else round(balance + refund_amount, 2)
    kg.update_entity(wallet.entity_id, attributes={"balance": new_balance})
    _debit_store_accounts_for_refund(kg, order, refund_amount)

    restocked = []
    stores_affected = set()
    for item in order.attributes.get("items", []):
        pid = item.get("product_id")
        qty = item.get("qty", 1)
        product = kg.get_entity(pid) if pid else None
        if product is None:
            continue  # discontinued since purchase — nothing real to restock
        kg.update_entity(pid, attributes={"quantity": product.attributes.get("quantity", 0) + qty})
        restocked.append({"product_id": pid, "qty": qty})
        store_id = product.attributes.get("store_id")
        if store_id:
            stores_affected.add(store_id)

    kg.update_entity(order_id, attributes={
        "status": "cancelled", "cancelled_at": time.time(), "cancelled_by": actor_id,
        "total_refunded": round(already_refunded + refund_amount, 2),
    })

    # Level 17 write side: has_learned_to_avoid() (this module) reads
    # trust/fulfilled_count/cancelled_count off each store entity, but
    # until now nothing in the real order pipeline ever wrote them — a
    # store could fail every order forever and never be deprioritized.
    # A cancelled, previously-paid order is the clearest "this store's
    # order did not work out" signal already in this codebase; one
    # outcome per distinct store in the order, not per line item, so a
    # multi-item order from a single store counts as one data point.
    for store_id in stores_affected:
        record_order_outcome(kg, store_id, fulfilled=False)

    return {
        "success": True, "order_id": order_id, "refunded": refund_amount,
        "wallet_id": wallet.entity_id, "restocked": restocked,
    }


class CancelOrderCapability:
    """Discoverable capability wrapper for cancel_order — an on-demand
    action, not part of the fixed checkout plan sequence every other
    capability here participates in."""
    name = "CancelOrder"

    def handle(self, args: dict) -> dict:
        context = args.get("context", {})
        kg = context.get("knowledge_graph")
        parameters = args.get("parameters", {}) or {}
        selection = parameters.get("selection")
        if isinstance(selection, dict):
            selection = [selection]
        # Same planner-decision path ProductSelectionCapability already
        # supports (parameters.selection) — found live: a cancellation
        # request is its own fresh /prompt call, with no order_id ever
        # in context (that only exists within the SAME context an
        # OrderCreation just populated it in); "Grocery Order" keyword-
        # matches a "cancel my order" goal, so the planner CAN see and
        # supply the real order id, but nothing here ever read it before.
        order_id = (
            context.get("order_id")
            or context.get("order", {}).get("order_id")
            or parameters.get("order_id")
            or (selection[0].get("id") if selection else None)
        )
        if kg is None or not order_id:
            return {"success": False, "error": "no knowledge graph or order_id provided"}
        return cancel_order(kg, order_id, actor_id=context.get("actor_id"))


def return_order(kg, order_id: str, actor_id: str | None = None, reason: str = "") -> dict:
    """MB-3025 Return Request: files a return REQUEST for a DELIVERED
    order — the customer's ask, not an automatic refund. Approval
    (MB-3026's approve_return()) is a separate, deliberate step that
    actually reverses payment and restocks inventory; requesting a
    return only records that the customer wants one and moves the order
    to "return_requested", pending review — the same request/approve
    split real return workflows use, rather than letting a customer
    unilaterally trigger a refund the moment they ask.

    cancel_order() has no delivery-status check at all — it's for
    stopping an order that hasn't been fulfilled yet; a return is the
    opposite case, and only accepts an order that actually reached
    "delivered" or "completed" (MB-3021/MB-3022's own terminal
    statuses). An order that was never delivered isn't returnable, it's
    cancellable — this refuses it and says so, rather than silently
    doing a cancellation under a different name. Same "only for what
    was actually paid for" guarantee as cancel_order(): requires
    payment_status == "paid".
    """
    order = kg.get_entity(order_id)
    if order is None:
        return {"success": False, "error": f"no such order {order_id!r}"}
    status = order.attributes.get("status")
    if status not in ("delivered", "completed"):
        return {
            "success": False,
            "error": f"order {order_id!r} is {status!r}, cannot request a return — it was never delivered "
                     f"(use cancel_order for an order that hasn't shipped)",
        }
    if order.attributes.get("payment_status") != "paid":
        return {"success": False, "error": f"order {order_id!r} was never successfully paid for — nothing to return"}

    kg.update_entity(order_id, attributes={
        "status": "return_requested",
        "return_requested_at": time.time(),
        "return_requested_by": actor_id,
        "return_reason": reason,
    })
    return {"success": True, "order_id": order_id, "status": "return_requested", "reason": reason}


def approve_return(kg, order_id: str, approved_by: str | None = None, now: float | None = None) -> dict:
    """MB-3026 Return Approval: the deliberate step that actually acts on
    a return_order() (MB-3025) request — refunds the real wallet that
    paid for the order and restocks each item, the same mechanics as
    cancel_order()'s undo. Only ever acts on an order actually in
    "return_requested" — there's nothing to approve for an order nobody
    asked to return, and an already-approved/returned order can't be
    approved twice. Re-checks payment_status == "paid" independently
    rather than trusting return_order()'s own check still holds by the
    time approval happens. Restocking silently skips any line item whose
    product has since been discontinued, same honest-skip behavior as
    cancel_order().
    """
    now = now if now is not None else time.time()
    order = kg.get_entity(order_id)
    if order is None:
        return {"success": False, "error": f"no such order {order_id!r}"}
    if order.attributes.get("status") != "return_requested":
        return {
            "success": False,
            "error": f"order {order_id!r} is {order.attributes.get('status')!r}, "
                     f"no pending return request to approve",
        }
    if order.attributes.get("payment_status") != "paid":
        return {"success": False, "error": f"order {order_id!r} was never successfully paid for — nothing to reverse"}

    wallet_id = order.attributes.get("paid_wallet_id")
    wallet = kg.get_entity(wallet_id) if wallet_id else None
    if wallet is None:
        return {"success": False, "error": f"paid wallet {wallet_id!r} for order {order_id!r} no longer exists — cannot refund"}

    # MB-3027: same "never double-refund" guard as cancel_order() — only
    # what's actually still outstanding, in case a prior partial/goodwill
    # refund_order() call already returned some of it.
    already_refunded = order.attributes.get("total_refunded", 0)
    refund_amount = round(order.attributes.get("paid_amount", order.attributes.get("total", 0)) - already_refunded, 2)
    account_type = wallet.attributes.get("account_type")
    balance = wallet.attributes.get("balance", 0)
    # Same credit/debit refund-direction asymmetry as cancel_order().
    new_balance = round(balance - refund_amount, 2) if account_type == "credit" else round(balance + refund_amount, 2)
    kg.update_entity(wallet.entity_id, attributes={"balance": new_balance})
    _debit_store_accounts_for_refund(kg, order, refund_amount)

    restocked = []
    for item in order.attributes.get("items", []):
        pid = item.get("product_id")
        qty = item.get("qty", 1)
        product = kg.get_entity(pid) if pid else None
        if product is None:
            continue  # discontinued since purchase — nothing real to restock
        kg.update_entity(pid, attributes={"quantity": product.attributes.get("quantity", 0) + qty})
        restocked.append({"product_id": pid, "qty": qty})

    kg.update_entity(order_id, attributes={
        "status": "returned", "returned_at": now, "approved_by": approved_by,
        "total_refunded": round(already_refunded + refund_amount, 2),
    })
    return {
        "success": True, "order_id": order_id, "refunded": refund_amount,
        "wallet_id": wallet.entity_id, "restocked": restocked,
    }


class ReturnOrderCapability:
    """Discoverable capability wrapper for return_order — same on-demand
    convention as CancelOrderCapability, but for a customer requesting a
    return on an order they've already received rather than stopping
    one before it ships."""
    name = "ReturnOrder"

    def handle(self, args: dict) -> dict:
        context = args.get("context", {})
        kg = context.get("knowledge_graph")
        parameters = args.get("parameters", {}) or {}
        # Same real gap CancelOrderCapability's own fix already closed
        # (see its docstring): a return request is its own fresh /prompt
        # call, with no order_id ever in the ambient context — only the
        # planner-controlled parameters field can carry one. That fix was
        # never extended to this capability, so a real "return my order"
        # request could never actually work, only a step chained
        # immediately after another step that happened to populate
        # context["order_id"] itself.
        order_id = (
            context.get("order_id")
            or context.get("order", {}).get("order_id")
            or parameters.get("order_id")
        )
        if kg is None or not order_id:
            return {"success": False, "error": "no knowledge graph or order_id provided"}
        return return_order(
            kg, order_id, actor_id=context.get("actor_id"),
            reason=parameters.get("reason") or context.get("return_reason", ""),
        )


class ApproveReturnCapability:
    """Discoverable capability wrapper for approve_return — same
    on-demand convention as ReturnOrderCapability, but for the merchant
    side sign-off that actually triggers the refund/restock."""
    name = "ApproveReturn"

    def handle(self, args: dict) -> dict:
        context = args.get("context", {})
        kg = context.get("knowledge_graph")
        parameters = args.get("parameters", {}) or {}
        # Same real gap CancelOrderCapability's own fix already closed —
        # see ReturnOrderCapability's identical comment just above.
        order_id = (
            context.get("order_id")
            or context.get("order", {}).get("order_id")
            or parameters.get("order_id")
        )
        if kg is None or not order_id:
            return {"success": False, "error": "no knowledge graph or order_id provided"}
        return approve_return(kg, order_id, approved_by=context.get("actor_id"))


def refund_order(kg, order_id: str, amount: float | None = None, reason: str = "",
                  refunded_by: str | None = None, now: float | None = None) -> dict:
    """MB-3027 Refund: a standalone, partial-capable refund — credits the
    real wallet that paid for an order WITHOUT restocking inventory or
    changing the order's status to cancelled/returned. For the cases
    where the customer keeps the goods: a damaged-item credit, a price
    adjustment, a goodwill gesture — genuinely different from
    cancel_order()'s (full reversal + restock + cancels) or
    approve_return()'s (full reversal + restock + returns) undo.

    Tracks attributes["total_refunded"] cumulatively on the order — the
    SAME field cancel_order() and approve_return() now also read before
    refunding, so a partial refund issued here and a later full
    cancellation/return can never double-refund the same money; both
    only ever refund what's actually still outstanding.

    amount defaults to the full remaining refundable balance when not
    given (a full, but non-restocking, refund). Refuses an amount
    that's non-positive or exceeds what's actually still outstanding.
    """
    now = now if now is not None else time.time()
    order = kg.get_entity(order_id)
    if order is None:
        return {"success": False, "error": f"no such order {order_id!r}"}
    if order.attributes.get("payment_status") != "paid":
        return {"success": False, "error": f"order {order_id!r} was never successfully paid for — nothing to refund"}

    paid_amount = order.attributes.get("paid_amount", order.attributes.get("total", 0))
    already_refunded = order.attributes.get("total_refunded", 0)
    remaining = round(paid_amount - already_refunded, 2)
    if remaining <= 0:
        return {"success": False, "error": f"order {order_id!r} has already been fully refunded"}

    refund_amount = remaining if amount is None else amount
    if refund_amount <= 0:
        return {"success": False, "error": f"refund amount must be positive, got {refund_amount!r}"}
    if refund_amount > remaining:
        return {
            "success": False,
            "error": f"refund amount {refund_amount} exceeds the {remaining} still refundable on order {order_id!r}",
        }

    wallet_id = order.attributes.get("paid_wallet_id")
    wallet = kg.get_entity(wallet_id) if wallet_id else None
    if wallet is None:
        return {"success": False, "error": f"paid wallet {wallet_id!r} for order {order_id!r} no longer exists — cannot refund"}

    account_type = wallet.attributes.get("account_type")
    balance = wallet.attributes.get("balance", 0)
    # Same credit/debit refund-direction asymmetry as cancel_order().
    new_balance = round(balance - refund_amount, 2) if account_type == "credit" else round(balance + refund_amount, 2)
    kg.update_entity(wallet.entity_id, attributes={"balance": new_balance})
    _debit_store_accounts_for_refund(kg, order, refund_amount)

    new_total_refunded = round(already_refunded + refund_amount, 2)
    refund_record = {"amount": refund_amount, "reason": reason, "refunded_by": refunded_by, "refunded_at": now}
    refund_history = list(order.attributes.get("refund_history") or [])
    refund_history.append(refund_record)
    kg.update_entity(order_id, attributes={
        "total_refunded": new_total_refunded,
        "refund_history": refund_history,
    })
    return {
        "success": True, "order_id": order_id, "refunded": refund_amount,
        "total_refunded": new_total_refunded, "wallet_id": wallet.entity_id,
    }


class RefundOrderCapability:
    """Discoverable capability wrapper for refund_order — same on-demand
    convention as CancelOrderCapability/ReturnOrderCapability, but for a
    standalone partial/goodwill refund that leaves the order and
    inventory untouched."""
    name = "RefundOrder"

    def handle(self, args: dict) -> dict:
        context = args.get("context", {})
        kg = context.get("knowledge_graph")
        parameters = args.get("parameters", {}) or {}
        # Same real gap CancelOrderCapability's own fix already closed —
        # see ReturnOrderCapability's identical comment above.
        order_id = (
            context.get("order_id")
            or context.get("order", {}).get("order_id")
            or parameters.get("order_id")
        )
        if kg is None or not order_id:
            return {"success": False, "error": "no knowledge graph or order_id provided"}
        return refund_order(
            kg, order_id,
            amount=parameters.get("amount", context.get("refund_amount")),
            reason=parameters.get("reason") or context.get("refund_reason", ""),
            refunded_by=context.get("actor_id"),
        )


class DeliveryCapability:
    """Assigns a rider and resolves pickup/delivery addresses from the KG.

    A cart can span multiple stores (e.g. cheapest milk at one store,
    cheapest bread at another) — pickup_addresses lists every distinct
    store involved rather than assuming a single pickup stop.

    GS-0700: when the request states a deadline ("before dinner", "by
    6pm"), rider choice switches from "highest rated" to "fastest that
    still meets the deadline" — picking the top-rated rider regardless of
    ETA had no way to even represent "will this arrive in time," let alone
    act on it. Reports whether the deadline is actually met, and if no
    rider can make it, picks the fastest available anyway (better than
    refusing outright) but says so honestly rather than silently reporting
    "scheduled" as if the deadline were satisfied.
    """
    name = "Delivery"

    def handle(self, args: dict) -> dict:
        context = args.get("context", {})
        kg = context.get("knowledge_graph")
        products = context.get("selected_product", [])
        if isinstance(products, dict):
            products = [products]

        if kg is None:
            return {"success": False, "error": "no knowledge graph available"}
        if not products:
            # Level 16/20: borrowed/negotiated items are handed over by the
            # lender/neighbor directly, and pantry-covered/duplicate items
            # were never going to be delivered again — nothing here to
            # schedule is correct, not a failure.
            if context.get("socially_fulfilled") or context.get("pantry_fulfilled"):
                return {"success": True, "note": "nothing to deliver — already fulfilled"}
            return {"success": False, "error": "no items to deliver"}

        # Real gap this closes: same real bug as OrderConfirmationCapability
        # (its own docstring explains the full finding — a planner-dropped
        # depends_on chain let this run even though Payment never actually
        # succeeded) — a rider was assigned and a real Shipment entity
        # created for an order that was never paid for. Same fresh-KG-read
        # "payment_status" == "paid" gate; context["order"] is a stale
        # OrderCreation-time snapshot, never updated after Payment runs.
        order_id = (context.get("order") or {}).get("order_id")
        if order_id:
            fresh_order = kg.get_entity(order_id)
            if fresh_order is not None and fresh_order.attributes.get("payment_status") != "paid":
                return {"success": False,
                        "error": f"order {order_id!r} has not been paid for yet — cannot arrange delivery"}

        from src.monkey_brain.kernel.knowledge_graph import EntityType

        store_ids = sorted({p.get("store_id", "") for p in products if p.get("store_id")})
        pickup_addresses = []
        pickup_minutes = 0.0
        pickup_methods = []
        for store_id in store_ids:
            store = kg.get_entity(store_id)
            store_name = store.name if store else "Unknown Store"
            address = store.attributes.get("address") if store else None
            if not address:
                return {"success": False, "error": f"no address on file for store {store_name!r}"}
            pickup_addresses.append(f"{store_name}, {address}")
            # GS-2800: a multi-store cart isn't ready until EVERY pickup
            # is — the binding constraint is whichever store takes longest,
            # not the first one checked.
            store_pickup_minutes, method = estimate_pickup_minutes(store)
            pickup_minutes = max(pickup_minutes, store_pickup_minutes)
            pickup_methods.append({"store": store_name, "method": method, "minutes": store_pickup_minutes})

        # Delivery vs Pickup: an explicit self-pickup request skips rider
        # assignment entirely — the actor collects the order themselves,
        # so there's no delivery address to resolve, no rider to find, and
        # (OrderCreationCapability) no delivery fee to charge. Every one
        # of those steps below this point is delivery-specific; nothing
        # here is skipped SILENTLY — the returned status/fulfillment_method
        # says exactly which path was taken.
        if wants_pickup(context.get("question", "")):
            return {
                "success": True,
                "fulfillment_method": "pickup",
                "pickup_addresses": pickup_addresses,
                "pickup_minutes": pickup_minutes,
                "pickup_methods": pickup_methods,
                "status": "ready_for_pickup",
            }

        # Scoped to the recipient (_paying_actor_id: the delegator when
        # acting under delegation, else the requester) -- this used to
        # scan every ADDRESS entity in the whole knowledge graph with no
        # actor filter at all, so in any world with more than one actor's
        # address on file, delivery could silently resolve to a stranger's
        # address (whichever entity happened to be first/is_primary
        # across ALL actors), never the requester's own.
        recipient_id = _paying_actor_id(context)
        addresses = [
            e for e in kg.entities_by_type(EntityType.ADDRESS)
            if e.attributes.get("actor_id") == recipient_id
        ]
        delivery_entity = next(
            (a for a in addresses if a.attributes.get("is_primary", True)),
            addresses[0] if addresses else None,
        )
        if delivery_entity is None:
            return {"success": False, "error": "no delivery address on file for actor"}
        delivery_address = _format_address(delivery_entity)

        persons = [e for e in kg.entities_by_type(EntityType.PERSON)
                   if e.attributes.get("status") == "available"]
        if not persons:
            return {"success": False, "error": "no riders available"}

        deadline_minutes = parse_deadline_minutes(context.get("question", ""))
        item_optimization = context.get("optimization", "cost")
        assignment = select_delivery_riders(persons, products, item_optimization, deadline_minutes)
        if not assignment["success"]:
            return assignment

        assignments = assignment["assignments"]
        deadline_met = assignment["deadline_met"]

        # Real capacity + tracking (closes the gap create_shipment()'s
        # own docstring already names: "DeliveryCapability schedules a
        # rider and returns a delivery_id, but that id is never
        # persisted"). Each assigned rider gets a real Shipment record
        # (logistics.py, MB-3019) and is marked unavailable for further
        # assignment (mark_rider_assigned) until mark_shipment_delivered
        # releases them back — without this, every rider stayed
        # "available" forever regardless of how many deliveries they
        # already carried, since nothing ever recorded an assignment
        # anywhere a later call could see it. order_id may be empty for
        # a caller that never threaded a real order through context
        # (e.g. a standalone scheduling probe) — shipments/capacity
        # tracking need a real order to attach to, so this is skipped
        # rather than fabricating one.
        order_id = context.get("order", {}).get("order_id", "")
        shipment_ids: list[str] = []
        if order_id and kg:
            packages = [{"product_id": p.get("id"), "qty": p.get("qty", 1)} for p in products]
            for a in assignments:
                rider = a["rider"]
                shipment = create_shipment(kg, order_id, packages, rider_id=rider.entity_id)
                if shipment.get("success"):
                    shipment_ids.append(shipment["shipment_id"])
                mark_rider_assigned(kg, rider.entity_id)

        if len(assignments) == 1:
            # Single rider (the common case) — same result shape Level 8
            # always returned, unchanged.
            rider = assignments[0]["rider"]
            rider_attrs = rider.attributes
            result = {
                "success": True,
                "delivery_id": f"DEL-{int(time.time())}",
                "rider_id": rider.entity_id,
                "rider_name": rider.name,
                "rating": rider_attrs.get("rating", 0),
                "vehicle": rider_attrs.get("vehicle", ""),
                "pickup_addresses": pickup_addresses,
                "pickup_minutes": pickup_minutes,
                "pickup_methods": pickup_methods,
                "delivery_address": delivery_address,
                "estimated_minutes": rider_attrs.get("estimated_minutes", 30),
                "status": "scheduled",
            }
            if shipment_ids:
                result["shipment_id"] = shipment_ids[0]
            if assignment["cold_chain"]:
                result["cold_chain_verified"] = True
            if deadline_minutes is not None:
                result["deadline_minutes"] = round(deadline_minutes, 1)
                result["deadline_met"] = deadline_met
                if not deadline_met:
                    result["warning"] = (
                        f"deadline in {deadline_minutes:.0f} min, but fastest available rider "
                        f"({rider.name}) takes {rider_attrs.get('estimated_minutes', 30)} min — will be late"
                    )

            # Schedule Conflict: flags a real calendar overlap with the
            # estimated arrival window (pickup time + transit) — it does
            # not reschedule anything (see find_schedule_conflict's
            # docstring for why), just reports it honestly instead of
            # silently scheduling a delivery for the middle of a real
            # commitment.
            now = time.time()
            window_start = now + pickup_minutes * 60
            window_end = window_start + rider_attrs.get("estimated_minutes", 30) * 60
            conflict = find_schedule_conflict(kg, window_start, window_end)
            if conflict is not None:
                result["schedule_conflict"] = conflict
                result["warning"] = (
                    f"estimated arrival overlaps {conflict['title']!r} on your calendar"
                    + (f"; {result['warning']}" if "warning" in result else "")
                )
            return result

        # GS-2702: the order didn't fit in one vehicle — a real multi-rider
        # split, each carrying their own share, reported as a distinct
        # "riders" plan rather than forcing one rider's result shape to
        # awkwardly represent several.
        riders_report = [
            {
                "rider_id": a["rider"].entity_id, "rider_name": a["rider"].name,
                "qty_assigned": a["qty"], "vehicle": a["rider"].attributes.get("vehicle", ""),
                "estimated_minutes": a["rider"].attributes.get("estimated_minutes", 30),
            }
            for a in assignments
        ]
        result = {
            "success": True,
            "delivery_id": f"DEL-{int(time.time())}",
            "split_across_vehicles": True,
            "riders": riders_report,
            "pickup_addresses": pickup_addresses,
            "pickup_minutes": pickup_minutes,
            "pickup_methods": pickup_methods,
            "delivery_address": delivery_address,
            "status": "scheduled",
        }
        if shipment_ids:
            result["shipment_ids"] = shipment_ids
        if assignment["cold_chain"]:
            result["cold_chain_verified"] = True
        if deadline_minutes is not None:
            result["deadline_minutes"] = round(deadline_minutes, 1)
            result["deadline_met"] = deadline_met
        return result


def _build_vertical_runtime() -> "VerticalRuntime":
    from src.monkey_brain.kernel.domains.vertical_router import VerticalRuntime
    from src.monkey_brain.kernel.pipeline.llm_planner import LLMPlanner
    from src.monkey_brain.kernel.pipeline.plan_validator import PlanValidator
    return VerticalRuntime(
        bus=build_default_capability_bus(),
        context_projector=project_action_result_to_context,
        integrity_check=verify_required_capabilities,
        # The vertical owns capabilities and world facts, never business
        # planning rules. LLMPlanner is the single planning authority.
        planner=LLMPlanner(),
        plan_validator=PlanValidator(max_cost=10.0, min_confidence=0.0, max_risk=1.0),
        domain_event_resolver=resolve_domain_event,
        pre_execute_hook=ensure_shared_budget_from_question,
        propose_transition=_propose_transition,
    )


from src.monkey_brain.kernel.domains.vertical_router import VerticalRuntime, register_vertical  # noqa: E402
register_vertical("grocery", _build_vertical_runtime)
