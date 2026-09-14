"""Logistics domain — delivery/fulfillment mechanics, shared across verticals.

Extracted from the grocery vertical (kernel/domains/grocery.py) on the
same premise as domain_security.py and the rest of the domain/vertical
split: nothing here reasons about groceries specifically. It operates on
generic delivery deadlines, riders/couriers, pickup timing, and address
formatting — the Grocery vertical composes this domain the same way any
future vertical (retail, manufacturing, healthcare) could.

Sections:
  - Deadlines (parsing a stated delivery deadline from free text)
  - Delivery prediction & rider assignment
  - Pickup timing (human vs. autonomous-cart picking)
  - Fulfillment verification (shelf-scan) & address formatting
"""

from __future__ import annotations

import datetime
import re
import time

from src.monkey_brain.kernel.domains.commerce import DomainCapability


class LogisticsCapability(DomainCapability):
    """Discoverable delivery and fulfillment competency."""

    name = "logistics"

    def __init__(self):
        super().__init__(
            {
                "estimate_delivery": predict_delivery_delay,
                "assign_carrier": select_delivery_riders,
                "estimate_pickup": estimate_pickup_minutes,
                "assign_picker": assign_picker,
                "pack_order": pack_order,
                "create_shipment": create_shipment,
                "get_shipment": get_shipment,
                "mark_shipment_in_transit": mark_shipment_in_transit,
                "mark_shipment_delivered": mark_shipment_delivered,
                "track_order": track_order,
                "confirm_receipt": confirm_receipt,
                "create_partial_shipments": create_partial_shipments,
                "mark_shipment_lost": mark_shipment_lost,
                "issue_replacement_shipment": issue_replacement_shipment,
                "report_shipment_delay": report_shipment_delay,
            }
        )


# ── Deadlines ─────────────────────────────────────────────────────────

_DEADLINE_PHRASES = {
    "dinner": (18, 0),
    "lunch": (12, 30),
    "breakfast": (8, 0),
}
_DEADLINE_TIME_RE = re.compile(
    r"\b(?:by|before)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", re.IGNORECASE
)


def parse_deadline_minutes(question: str, now: float | None = None) -> float | None:
    """Minutes from now until a stated deadline ('before dinner', 'by
    6pm'), or None if the request states no deadline at all.

    GS-0700: without this, "need it before dinner" carried no notion of a
    deadline whatsoever — DeliveryCapability picked a rider purely by
    rating, with no way to even ask "will this actually arrive in time."
    Meal-name phrases resolve to a fixed clock time (a real assumption,
    not a computed one — there's no data on when THIS household eats
    dinner); an explicit "by HH[:MM][am/pm]" is parsed directly. Rolls to
    the next day if the named time has already passed today.
    """
    now = now if now is not None else time.time()
    now_dt = datetime.datetime.fromtimestamp(now)
    q = (question or "").lower()

    hour = minute = None
    for phrase, (h, m) in _DEADLINE_PHRASES.items():
        if phrase in q:
            hour, minute = h, m
            break
    if hour is None:
        m = _DEADLINE_TIME_RE.search(q)
        if not m:
            return None
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        ampm = (m.group(3) or "").lower()
        if ampm == "pm" and hour != 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0

    target = now_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target < now_dt:
        target += datetime.timedelta(days=1)
    return (target - now_dt).total_seconds() / 60


# ── Schedule conflicts ────────────────────────────────────────────────


def find_schedule_conflict(kg, window_start: float, window_end: float) -> dict | None:
    """Whether the actor has a real calendar commitment overlapping
    [window_start, window_end] (a planned delivery/pickup window), or
    None if there's no conflict.

    A calendar event is a real KG fact (entity_type EVENT,
    attributes["calendar_event"] is True, with "start"/"end" unix
    timestamps) — the same "the belief is the evidence" principle other
    checks in this domain already follow (e.g. learned_preference reading
    real order history rather than a settings field), not a self-reported
    claim in the request text.

    This only ever FLAGS a conflict — it does not reschedule the delivery
    window or pick a different one. Real rescheduling would mean the
    system committing to a delivery time other than "as soon as
    possible," which requires deferred/future execution this
    request/response architecture doesn't have (the same honest boundary
    already drawn elsewhere in this domain for "delay"/"autonomous"
    requests) — reporting the conflict honestly is what a synchronous
    checkout can actually do today.
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    # Performance certification (GS-6000): entities_by_type(EVENT), not a
    # full kg.entities scan -- this used to walk every entity in the whole
    # graph (catalog products, accounts, everything) just to find calendar
    # events, on every delivery step.
    for e in kg.entities_by_type(EntityType.EVENT):
        if not e.attributes.get("calendar_event"):
            continue
        start = e.attributes.get("start")
        end = e.attributes.get("end")
        if start is None or end is None:
            continue
        if start < window_end and end > window_start:
            return {
                "title": e.attributes.get("title", "a scheduled commitment"),
                "start": start,
                "end": end,
            }
    return None


# ── Delivery prediction & rider assignment ───────────────────────────


def predict_delivery_delay(store, base_minutes_per_mile: float = 3.0) -> float:
    """GS-1302: predicted delivery time in minutes for a store — distance is
    real (distance_miles, already used for plain "nearest store" time
    optimization), traffic_factor is the new predictive signal: a
    multiplier > 1 on an otherwise-closer store (heavy congestion on that
    route) can make a farther store the actually-faster choice once real
    conditions are accounted for, instead of pure straight-line distance.
    Missing traffic_factor defaults to 1.0 (no adjustment) so every store
    seeded before this level keeps behaving exactly as it did under plain
    distance ranking.
    """
    distance = (
        store.attributes.get("distance_miles", float("inf")) if store else float("inf")
    )
    traffic_factor = store.attributes.get("traffic_factor", 1.0) if store else 1.0
    return distance * base_minutes_per_mile * traffic_factor


def select_delivery_riders(
    persons: list,
    products: list,
    item_optimization: str,
    deadline_minutes: float | None,
) -> dict:
    """Level 27: real multi-factor rider assignment over the ACTUAL rider
    pool and cart, not a single unconditional pick.

    GS-2701: a cart containing a cold-chain item (attributes["cold_chain"]
    on the product) can ONLY be assigned a refrigerated rider — a hard
    requirement, same tier as Level 24's allergy exclusion, never silently
    substituted with a regular vehicle that could spoil the order. No
    eligible rider is an honest failure, not a fallback.

    GS-2702: if the cart's total quantity exceeds one rider's real vehicle
    capacity (attributes["capacity"], unconstrained if unset — opt-in,
    not retroactively imposed on every existing rider), the order is
    split across as many eligible riders as it takes rather than silently
    assigning one rider an impossible load.

    GS-2700/GS-0700: within the eligible, capacity-checked pool, riders
    are still chosen fastest-first when that's the actual optimization
    ("fastest courier"), deadline-aware when a deadline was stated
    (Level 8's existing behavior — prefer the best-rated rider among
    those who can still make it, fall back to fastest-available if none
    can), or highest-rated by default — the exact same three branches
    Level 8 already had.
    """
    needs_cold_chain = any(p.get("cold_chain") is True for p in products)
    eligible = [
        p
        for p in persons
        if not needs_cold_chain or p.attributes.get("refrigerated") is True
    ]
    if not eligible:
        reason = (
            "no refrigerated courier available for this order's cold-chain items"
            if needs_cold_chain
            else "no riders available"
        )
        return {"success": False, "error": reason}

    deadline_met = None
    if item_optimization == "time" and deadline_minutes is None:
        ranked = sorted(
            eligible, key=lambda p: p.attributes.get("estimated_minutes", 30)
        )
    elif deadline_minutes is not None:
        within = [
            p
            for p in eligible
            if p.attributes.get("estimated_minutes", 30) <= deadline_minutes
        ]
        if within:
            ranked = sorted(within, key=lambda p: -p.attributes.get("rating", 0))
            deadline_met = True
        else:
            ranked = sorted(
                eligible, key=lambda p: p.attributes.get("estimated_minutes", 30)
            )
            deadline_met = False
    else:
        ranked = sorted(eligible, key=lambda p: -p.attributes.get("rating", 0))

    total_qty = sum(p.get("qty", 1) for p in products)
    default_capacity = float("inf")
    assignments = []
    remaining = total_qty
    for rider in ranked:
        if remaining <= 0:
            break
        capacity = rider.attributes.get("capacity", default_capacity)
        take = min(remaining, capacity)
        if take <= 0:
            continue
        assignments.append({"rider": rider, "qty": take})
        remaining -= take

    if remaining > 0:
        return {
            "success": False,
            "error": f"no combination of available riders can carry the full order "
            f"({total_qty - remaining:.0f}/{total_qty:.0f} units coverable)",
        }
    return {
        "success": True,
        "assignments": assignments,
        "deadline_met": deadline_met,
        "cold_chain": needs_cold_chain,
    }


def onboard_rider(kg, name: str, **rider_attrs) -> dict:
    """Creates a real Rider (PERSON) entity in the commerce KG — mirrors
    commerce.py's onboard_merchant() for the same reason: DeliveryCapability
    (grocery.py) and select_delivery_riders() above only ever see riders
    that already exist as EntityType.PERSON entities with attributes
    ["status"] == "available"; before this, kg.add_entity() called
    directly (a test fixture, e.g. tests/scenarios/test_mb3060_*) was the
    only way one ever got created — no production API did. status
    defaults to "available" since onboarding a new rider who ISN'T
    available yet is the unusual case, not the default one; capacity
    deliberately has no default here — passing 0.0 would cap every rider
    at zero units carried (see RiderCreateRequest's own note), so a
    caller that doesn't care about capacity should simply not pass it,
    same as select_delivery_riders() already treats a missing key as
    unconstrained.
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType
    import uuid

    rider_id = f"rider_{uuid.uuid4().hex}"
    attributes = {"status": "available", **rider_attrs}
    kg.add_entity(rider_id, EntityType.PERSON, name, attributes)
    return {"success": True, "rider_id": rider_id, "name": name}


def mark_rider_assigned(kg, rider_id: str, max_attempts: int = 5) -> bool:
    """Real capacity enforcement: a rider select_delivery_riders() picks
    must stop being a candidate for the NEXT delivery until this one is
    actually done. Before this existed, attributes["status"] was set to
    "available" once at onboard_rider() and never touched again anywhere
    in this codebase — every rider was "available" for every delivery
    simultaneously, no matter how many were already assigned, since
    DeliveryCapability (grocery.py) never persisted an assignment
    anywhere a later call could see it (see create_shipment()'s own
    docstring for the sibling half of this same gap).

    CAS-protected the same way try_reserve() (grocery.py) protects
    inventory — two concurrent DeliveryCapability calls reading the same
    "available" rider and both trying to assign them is the identical
    race try_reserve() already exists to prevent for stock, just never
    extended to riders."""
    for _ in range(max_attempts):
        rider = kg.get_entity(rider_id)
        if rider is None:
            return False
        version = kg.version_of(rider_id)
        ok, _ = kg.compare_and_swap(rider_id, version, {"status": "assigned"})
        if ok:
            return True
    return False


def release_rider(kg, rider_id: str, max_attempts: int = 5) -> bool:
    """The other half of mark_rider_assigned() — called from
    mark_shipment_delivered() once a shipment this rider carried
    actually completes, freeing them for the next assignment. Silently
    no-ops if the rider no longer exists (never raises for a rider
    removed since assignment) — releasing something that's already gone
    is an honest no-op, not an error."""
    for _ in range(max_attempts):
        rider = kg.get_entity(rider_id)
        if rider is None:
            return False
        version = kg.version_of(rider_id)
        ok, _ = kg.compare_and_swap(rider_id, version, {"status": "available"})
        if ok:
            return True
    return False


# ── Pickup timing ─────────────────────────────────────────────────────

_DEFAULT_HUMAN_PICK_MINUTES = 15.0
_DEFAULT_ROBOT_PICK_MINUTES = 5.0


def estimate_pickup_minutes(store) -> tuple[float, str]:
    """Level 28 (GS-2800): an autonomous in-store picking robot
    (attributes["has_autonomous_cart"]) prepares an order faster and more
    consistently than a human picker — a real time difference that
    affects when an order is actually ready for a rider to collect, not
    just a label on the store. Missing the attribute defaults to a human
    picker (opt-in, not retroactively imposed on every existing store).
    """
    if store is None:
        return _DEFAULT_HUMAN_PICK_MINUTES, "human picker"
    if store.attributes.get("has_autonomous_cart"):
        return (
            store.attributes.get("robot_pick_minutes", _DEFAULT_ROBOT_PICK_MINUTES),
            "autonomous cart",
        )
    return (
        store.attributes.get("human_pick_minutes", _DEFAULT_HUMAN_PICK_MINUTES),
        "human picker",
    )


def assign_picker(kg, store_id: str) -> dict:
    """MB-3017 Warehouse Picking: assign a SPECIFIC picker (a person, or
    the store's own autonomous cart) to prepare an order at store_id —
    the real-assignment counterpart to estimate_pickup_minutes()'s
    generic time/label estimate, mirroring select_delivery_riders()'s
    "assign a specific rider, not just a label" design for the delivery
    side.

    An autonomous cart, when the store has one (attributes["has_
    autonomous_cart"]), is always preferred: it's not a person competing
    for availability, the store owns it outright, so it's never
    unavailable the way a specific human employee can be. Otherwise, the
    fastest AVAILABLE human picker actually assigned to this store
    (attributes["role"] == "picker", attributes["store_id"] == store_id,
    attributes["status"] == "available") is chosen, ranked by
    pick_rate_minutes (lower is faster) — the same "fastest first"
    default select_delivery_riders() uses when there's no other
    deadline/rating signal to break ties on.

    No eligible picker (no autonomous cart, no available human picker at
    this store) is an honest failure, not a silent fallback to a generic
    estimate.
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    store = kg.get_entity(store_id) if kg is not None else None
    if store is None:
        return {"success": False, "error": f"store {store_id!r} not found"}

    if store.attributes.get("has_autonomous_cart"):
        return {
            "success": True,
            "picker_type": "autonomous_cart",
            "picker_id": store.attributes.get("cart_id", f"{store_id}-cart"),
            "picker_name": "Autonomous Picking Cart",
            "estimated_minutes": store.attributes.get(
                "robot_pick_minutes", _DEFAULT_ROBOT_PICK_MINUTES
            ),
        }

    candidates = [
        e
        for e in kg.entities_by_type(EntityType.PERSON)
        if e.attributes.get("role") == "picker"
        and e.attributes.get("store_id") == store_id
        and e.attributes.get("status") == "available"
    ]
    if not candidates:
        return {
            "success": False,
            "error": f"no available picker for store {store_id!r}",
        }

    chosen = min(
        candidates,
        key=lambda e: e.attributes.get(
            "pick_rate_minutes", _DEFAULT_HUMAN_PICK_MINUTES
        ),
    )
    return {
        "success": True,
        "picker_type": "human",
        "picker_id": chosen.entity_id,
        "picker_name": chosen.name,
        "estimated_minutes": chosen.attributes.get(
            "pick_rate_minutes", _DEFAULT_HUMAN_PICK_MINUTES
        ),
    }


_DEFAULT_BOX_CAPACITY = 12
"""Items per standard box, when a box doesn't declare its own capacity."""


def pack_order(products: list[dict], box_capacity: int = _DEFAULT_BOX_CAPACITY) -> dict:
    """MB-3018 Packing: split an order's line items into real packages
    ready for a rider to carry, mirroring select_delivery_riders()'s
    cold-chain/capacity model on the packing side of the same pipeline
    (Pick -> Pack -> Deliver). Cold-chain items are NEVER packed
    alongside non-cold-chain items in the same box — a hard requirement,
    same tier as the cold-chain rider requirement, never silently mixed
    to save a box.

    Items are packed first-fit into as few boxes as it takes, each
    capped at box_capacity units, cold-chain and standard items always
    segregated into their own boxes. Returns the full packing plan —
    every box, its type, and its exact contents — plus the total
    package count a rider will actually need to carry.
    """
    cold_chain_items = [p for p in products if p.get("cold_chain") is True]
    standard_items = [p for p in products if not p.get("cold_chain")]

    def _pack(items: list[dict], box_type: str) -> list[dict]:
        boxes: list[dict] = []
        current_box: list[dict] = []
        current_qty = 0
        for item in items:
            remaining = item.get("qty", 1)
            name = item.get("name", item.get("id", "item"))
            while remaining > 0:
                space = box_capacity - current_qty
                if space <= 0:
                    boxes.append(
                        {"box_type": box_type, "items": current_box, "qty": current_qty}
                    )
                    current_box, current_qty = [], 0
                    space = box_capacity
                take = min(space, remaining)
                current_box.append(
                    {"product_id": item.get("id"), "name": name, "qty": take}
                )
                current_qty += take
                remaining -= take
        if current_box:
            boxes.append(
                {"box_type": box_type, "items": current_box, "qty": current_qty}
            )
        return boxes

    packages = _pack(cold_chain_items, "insulated") + _pack(standard_items, "standard")
    return {
        "success": True,
        "packages": packages,
        "package_count": len(packages),
        "cold_chain_packages": sum(1 for p in packages if p["box_type"] == "insulated"),
    }


# ── Shipping (shipment lifecycle) ─────────────────────────────────────

_SHIPMENT_STATUSES = ("created", "in_transit", "delivered")
"""Strict, linear lifecycle — a shipment can only ever move forward one
step at a time (created -> in_transit -> delivered), never skip ahead
or move backward."""


def create_shipment(
    kg,
    order_id: str,
    packages: list[dict],
    rider_id: str | None = None,
    carrier: str | None = None,
    now: float | None = None,
) -> dict:
    """MB-3019 Shipping: persist a real, trackable Shipment record — the
    next stage of the same Pick (MB-3017) -> Pack (MB-3018) -> Ship
    pipeline. Neither assign_picker() nor pack_order() persists anything
    to the KG; they're pure computations. DeliveryCapability
    (grocery.py) schedules a rider and returns a delivery_id, but that
    id is never persisted either — there is nothing in the KG today that
    can be looked up later by tracking number or have its status
    queried or advanced. This closes that gap: a Shipment entity with a
    generated tracking number, starting at status "created", carrying
    the packages produced by pack_order() and (optionally) the rider
    assigned by assign_picker()/select_delivery_riders().
    """
    import uuid
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    now = now if now is not None else time.time()
    shipment_id = f"shipment_{uuid.uuid4().hex}"
    tracking_number = f"TRK-{uuid.uuid4().hex[:10].upper()}"
    kg.add_entity(
        shipment_id,
        EntityType.OTHER,
        f"Shipment: {order_id}",
        {
            "shipment": True,
            "order_id": order_id,
            "packages": packages,
            "rider_id": rider_id,
            "carrier": carrier,
            "tracking_number": tracking_number,
            "status": "created",
            "created_at": now,
            "history": [{"status": "created", "at": now}],
        },
    )
    return {
        "success": True,
        "shipment_id": shipment_id,
        "tracking_number": tracking_number,
        "order_id": order_id,
        "status": "created",
    }


def get_shipment(kg, shipment_id: str) -> dict:
    """Look up a shipment's current tracking status by id."""
    shipment = kg.get_entity(shipment_id) if kg is not None else None
    if shipment is None or not shipment.attributes.get("shipment"):
        return {"success": False, "error": f"shipment {shipment_id!r} not found"}
    return {
        "success": True,
        "shipment_id": shipment_id,
        "tracking_number": shipment.attributes.get("tracking_number"),
        "order_id": shipment.attributes.get("order_id"),
        "status": shipment.attributes.get("status"),
        "history": shipment.attributes.get("history", []),
    }


def _advance_shipment_status(
    kg, shipment_id: str, from_status: str, to_status: str, now: float | None = None
) -> dict:
    """Shared transition guard for the strict created -> in_transit ->
    delivered lifecycle: refuses to skip a step, move backward, or
    re-apply the same transition twice, always with an honest error
    naming the shipment's actual current status rather than silently
    overwriting it."""
    now = now if now is not None else time.time()
    shipment = kg.get_entity(shipment_id) if kg is not None else None
    if shipment is None or not shipment.attributes.get("shipment"):
        return {"success": False, "error": f"shipment {shipment_id!r} not found"}

    current = shipment.attributes.get("status")
    if current != from_status:
        return {
            "success": False,
            "error": f"shipment {shipment_id!r} is {current!r}, cannot move to {to_status!r} "
            f"(must be {from_status!r} first)",
            "status": current,
        }

    history = list(shipment.attributes.get("history", []))
    history.append({"status": to_status, "at": now})
    kg.update_entity(shipment_id, attributes={"status": to_status, "history": history})
    return {"success": True, "shipment_id": shipment_id, "status": to_status}


def mark_shipment_in_transit(kg, shipment_id: str, now: float | None = None) -> dict:
    """Advance a shipment from "created" to "in_transit"."""
    return _advance_shipment_status(kg, shipment_id, "created", "in_transit", now)


_SHIPMENT_STATUS_ORDER = {"created": 0, "in_transit": 1, "delivered": 2}


def mark_shipment_delivered(kg, shipment_id: str, now: float | None = None) -> dict:
    """MB-3021 Delivery: advance a shipment from "in_transit" to
    "delivered" — a shipment must actually be in transit first; it can't
    be marked delivered straight from "created" — and, when this closes
    out the LAST outstanding shipment for its order (track_order()'s own
    overall_status reaches "delivered"), mark the order entity itself
    delivered too. A multi-shipment order is never marked delivered
    until every shipment that makes it up actually is — reuses
    track_order()'s existing "least advanced status wins" rule rather
    than re-deriving it. Silently leaves the order alone if it doesn't
    exist in this KG (create_shipment() never required one to).
    """
    now = now if now is not None else time.time()
    result = _advance_shipment_status(kg, shipment_id, "in_transit", "delivered", now)
    if not result["success"]:
        return result

    shipment = kg.get_entity(shipment_id)
    order_id = shipment.attributes.get("order_id") if shipment is not None else None
    order_delivered = False
    if order_id and kg.get_entity(order_id) is not None:
        overall = track_order(kg, order_id)
        if overall["success"] and overall["status"] == "delivered":
            kg.update_entity(
                order_id, attributes={"status": "delivered", "delivered_at": now}
            )
            order_delivered = True
    result["order_id"] = order_id
    result["order_delivered"] = order_delivered

    # Real capacity release (mark_rider_assigned()'s own docstring): THIS
    # shipment's rider is free the moment THIS shipment lands, regardless
    # of whether the whole (possibly multi-shipment) order is fully
    # delivered yet — a rider who dropped off their own package doesn't
    # stay artificially unavailable waiting on a different rider's
    # separate shipment for the same order.
    rider_id = shipment.attributes.get("rider_id") if shipment is not None else None
    if rider_id:
        release_rider(kg, rider_id)

    return result


def track_order(kg, order_id: str) -> dict:
    """MB-3020 Shipment Tracking: answer "Where is my order?" — a
    customer knows their order_id, not a shipment_id, so get_shipment()
    alone can't answer this; this finds every shipment persisted against
    that order_id (an order can ship in more than one package/shipment)
    and reports each one's status, plus a single overall_status: the
    LEAST advanced status among them, since an order isn't "delivered"
    until every shipment is, and isn't meaningfully "in_transit" if even
    one piece is still sitting at "created".
    """
    kg.refresh()
    shipments = [
        e
        for e in kg.entities
        if e.attributes.get("shipment") and e.attributes.get("order_id") == order_id
    ]
    if not shipments:
        return {"success": False, "error": f"no shipment found for order {order_id!r}"}

    shipments_report = [
        {
            "shipment_id": s.entity_id,
            "tracking_number": s.attributes.get("tracking_number"),
            "status": s.attributes.get("status"),
        }
        for s in shipments
    ]
    overall_status = min(
        shipments_report, key=lambda s: _SHIPMENT_STATUS_ORDER.get(s["status"], 0)
    )["status"]
    return {
        "success": True,
        "order_id": order_id,
        "shipment_count": len(shipments_report),
        "shipments": shipments_report,
        "status": overall_status,
    }


def confirm_receipt(
    kg, order_id: str, actor_id: str | None = None, now: float | None = None
) -> dict:
    """MB-3022 Delivery Confirmation: the customer's own sign-off that
    closes an order's lifecycle — system delivery (MB-3021's
    mark_shipment_delivered) alone doesn't finish the transaction, the
    customer confirming receipt does. Requires the order to already be
    "delivered" (which itself only happens once every shipment covering
    it is, per mark_shipment_delivered()'s multi-shipment guard) before
    advancing it to a new terminal "completed" status. Refuses on an
    order that's still in progress, was cancelled, or was already
    confirmed — completing an order is a one-way, one-time transition,
    same discipline as the shipment lifecycle's own guards.
    """
    now = now if now is not None else time.time()
    order = kg.get_entity(order_id) if kg is not None else None
    if order is None:
        return {"success": False, "error": f"no such order {order_id!r}"}

    current = order.attributes.get("status")
    if current != "delivered":
        return {
            "success": False,
            "error": f"order {order_id!r} is {current!r}, cannot confirm receipt (must be 'delivered' first)",
            "status": current,
        }

    kg.update_entity(
        order_id,
        attributes={
            "status": "completed",
            "completed_at": now,
            "confirmed_by": actor_id,
        },
    )
    return {
        "success": True,
        "order_id": order_id,
        "status": "completed",
        "confirmed_by": actor_id,
    }


def create_partial_shipments(
    kg,
    order_id: str,
    actor_id: str,
    products: list[dict],
    box_capacity: int = _DEFAULT_BOX_CAPACITY,
    now: float | None = None,
) -> dict:
    """MB-3030 Partial Shipment: splits an order into multiple
    independent shipments when not everything can ship together —
    whatever's actually available ships NOW, packed via pack_order()
    and persisted via create_shipment() same as any other order; any
    line item with a currently PENDING backorder (attributes
    ["backorder"]/["status"] == "pending", the same marker place_backorder()
    persists and fulfill_backorders() clears) for this actor ships
    LATER, once that backorder is actually fulfilled — it gets no
    shipment record yet, because there's nothing real to ship for it.

    "Ready" is decided against the SAME backorder marker
    fulfill_backorders() itself already tracks, not a second,
    independently-computed availability check that could disagree with
    it. Whatever's ready never waits on whatever isn't — a single
    out-of-stock line item no longer holds an entire order's shipment
    hostage.
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    now = now if now is not None else time.time()
    kg.refresh()
    pending_backordered_ids = {
        e.attributes.get("product_id")
        for e in kg.entities_by_type(EntityType.OTHER)
        if e.attributes.get("backorder")
        and e.attributes.get("actor_id") == actor_id
        and e.attributes.get("status") == "pending"
    }

    ready_items = [p for p in products if p.get("id") not in pending_backordered_ids]
    pending_items = [p for p in products if p.get("id") in pending_backordered_ids]

    shipments = []
    if ready_items:
        packed = pack_order(ready_items, box_capacity=box_capacity)
        shipments.append(create_shipment(kg, order_id, packed["packages"], now=now))

    return {
        "success": True,
        "order_id": order_id,
        "shipments": shipments,
        "shipped_item_ids": [p.get("id") for p in ready_items],
        "pending_item_ids": [p.get("id") for p in pending_items],
        "partial": bool(pending_items) and bool(ready_items),
    }


def mark_shipment_lost(
    kg, shipment_id: str, reported_by: str | None = None, now: float | None = None
) -> dict:
    """MB-3043 Lost Package: the carrier reports a shipment lost — only
    ever from "in_transit" (a package can't be lost before it's even
    shipped, and once "delivered" it's not lost, it's a delivery
    dispute — a different problem). Reuses _advance_shipment_status()'s
    same guarded transition machinery as mark_shipment_in_transit()/
    mark_shipment_delivered() (MB-3019) — no skipping, no re-applying,
    same discipline. issue_replacement_shipment() (MB-3044) is the
    recovery path once a shipment reaches this state.
    """
    now = now if now is not None else time.time()
    result = _advance_shipment_status(kg, shipment_id, "in_transit", "lost", now)
    if result["success"] and reported_by is not None:
        kg.update_entity(shipment_id, attributes={"lost_reported_by": reported_by})
    return result


def issue_replacement_shipment(
    kg, lost_shipment_id: str, now: float | None = None
) -> dict:
    """MB-3044 Replacement Shipment: issues a brand-new shipment
    covering the SAME order and packages as a lost one (MB-3043) — the
    recovery path once mark_shipment_lost() actually confirms a
    shipment is lost, not before (issuing a replacement for a shipment
    that was never lost would be a duplicate order, not a recovery).
    The new shipment starts its own fresh created -> in_transit ->
    delivered lifecycle (create_shipment()), linked back to the
    original via replaces_shipment_id/replaced_by_shipment_id — a
    traceable substitution, not a silent do-over that erases what
    actually happened to the first one.
    """
    now = now if now is not None else time.time()
    lost = kg.get_entity(lost_shipment_id)
    if lost is None or not lost.attributes.get("shipment"):
        return {"success": False, "error": f"shipment {lost_shipment_id!r} not found"}
    if lost.attributes.get("status") != "lost":
        return {
            "success": False,
            "error": f"shipment {lost_shipment_id!r} is {lost.attributes.get('status')!r}, "
            f"not 'lost' — nothing to replace",
        }

    replacement = create_shipment(
        kg,
        lost.attributes.get("order_id"),
        lost.attributes.get("packages", []),
        rider_id=lost.attributes.get("rider_id"),
        carrier=lost.attributes.get("carrier"),
        now=now,
    )
    if not replacement["success"]:
        return replacement

    kg.update_entity(
        replacement["shipment_id"],
        attributes={"replaces_shipment_id": lost_shipment_id},
    )
    kg.update_entity(
        lost_shipment_id,
        attributes={"replaced_by_shipment_id": replacement["shipment_id"]},
    )
    return {**replacement, "replaces_shipment_id": lost_shipment_id}


def report_shipment_delay(
    kg,
    shipment_id: str,
    reason: str,
    new_eta: float | None = None,
    now: float | None = None,
) -> dict:
    """MB-3045 Carrier Delay: records a real delay on a shipment
    already in transit (e.g. weather) — doesn't change its status
    (still genuinely "in_transit", just running late), only appends a
    delay record and updates the estimated arrival when a new one is
    given. Only valid on a shipment that's actually "in_transit" — a
    delay reported before a shipment has even shipped, or after it's
    already delivered/lost, doesn't mean anything.
    """
    now = now if now is not None else time.time()
    shipment = kg.get_entity(shipment_id)
    if shipment is None or not shipment.attributes.get("shipment"):
        return {"success": False, "error": f"shipment {shipment_id!r} not found"}
    if shipment.attributes.get("status") != "in_transit":
        return {
            "success": False,
            "error": f"shipment {shipment_id!r} is {shipment.attributes.get('status')!r}, "
            f"cannot report a delay (must be 'in_transit')",
        }

    delays = list(shipment.attributes.get("delays", []))
    delays.append({"reason": reason, "reported_at": now, "new_eta": new_eta})
    updates = {"delays": delays}
    if new_eta is not None:
        updates["estimated_arrival"] = new_eta
    kg.update_entity(shipment_id, attributes=updates)
    return {
        "success": True,
        "shipment_id": shipment_id,
        "delay_count": len(delays),
        "new_eta": new_eta,
    }


# ── Fulfillment verification & address formatting ────────────────────


def robot_shelf_scan(kg, product_id: str, physical_count: int) -> dict:
    """Level 28 (GS-2801): records a real shelf-scanning robot's physical
    count as attributes["verified_quantity"] — the same field Level 21's
    detect_inventory_inconsistency already treats as an independent,
    trusted source. This is what gives that field genuine provenance (a
    robot actually scanned the shelf) instead of being a magic pre-seeded
    number nothing produced. physical_count is supplied by the CALLER
    (the robot's own sensor reading) — this function's job is recording
    and reporting a discrepancy, not fabricating what the robot "found".
    """
    product = kg.get_entity(product_id)
    if product is None:
        return {"scanned": False, "reason": "product not found"}
    reported = product.attributes.get("quantity", 0)
    kg.update_entity(product_id, attributes={"verified_quantity": physical_count})
    return {
        "scanned": True,
        "product_id": product_id,
        "reported_quantity": reported,
        "scanned_quantity": physical_count,
        "discrepancy_found": physical_count != reported,
    }


def _format_address(entity) -> str:
    """Render an ADDRESS entity's attributes as a single-line string."""
    attrs = entity.attributes
    if attrs.get("full_address"):
        return attrs["full_address"]
    parts = [
        attrs.get("street", ""),
        attrs.get("city", ""),
        attrs.get("state", ""),
        attrs.get("zip_code", ""),
    ]
    formatted = ", ".join(p for p in parts if p)
    return formatted or entity.name
