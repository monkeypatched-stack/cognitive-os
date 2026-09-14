"""Finance domain — payment/wallet/account mechanics, shared across verticals.

Extracted from the grocery vertical (kernel/domains/grocery.py) on the
same premise as domain_security.py and logistics.py: nothing here
reasons about groceries specifically. It operates on generic accounts,
wallets, and payment processors — the Grocery vertical composes this
domain the same way any future vertical could.

This is the financial MECHANICS only — account lookup, source selection,
processor fallback, spending caps, credit extension. The authorization
question ("is this actor allowed to use this account", RBAC/SoD approval
chains) already lives in domain_security.py; this module only ever
answers "given an authorized action, how does the money actually move."

Sections:
  - Accounts & wallets
  - Payment source selection
  - Processing & spending limits
"""

from __future__ import annotations

from src.monkey_brain.kernel.domains.commerce import DomainCapability


class FinanceCapability(DomainCapability):
    """Discoverable finance competency, independent of any vertical."""

    name = "finance"

    def __init__(self):
        super().__init__(
            {
                "find_payment_sources": find_payment_sources,
                "choose_payment_source": choose_payment_source,
                "process_payment": process_payment_with_fallback,
                "assess_transaction_risk": assess_transaction_risk,
                "award_points": award_points,
                "redeem_points": redeem_points,
                "get_points_balance": get_points_balance,
            }
        )


# ── Accounts & wallets ────────────────────────────────────────────────


def _owned_by(kg, account, actor_id: str) -> bool:
    """Whether actor_id can pay from this account: their own personal
    wallet (attributes["owner"]), a wallet listing them as a co-owner
    (attributes["owners"], the multi-owner household convention), or an
    explicitly household-tagged account (attributes["household"] is True
    — a household-wide account with no owner/owners list at all, shared
    by convention rather than explicit membership).

    Level 50 (GS-5000): "household is True" alone does NOT prove
    actor_id is actually a member of THIS account's household — caught
    live in a genuine multi-household, cross-runtime scenario: Bob's own
    KG instance (which legitimately hydrates his OWN Smith household
    wallet) was asked whether an unrelated actor (Carol, a different
    household entirely) owned that SAME wallet, and this returned True
    just because it was tagged household=True, with no check that Carol
    had anything to do with the Smith household at all. A real
    peer-to-peer payment settlement (buy_from_neighbor) used this to
    "find the seller's wallet" and got the BUYER's own household wallet
    back instead — debiting and crediting the identical account, net
    zero, while the real seller was never paid.

    Neo4jBackedKnowledgeGraph tracks which real partition (_household_id)
    each hydrated entity actually came from (_entity_scope) — when kg
    exposes that (a real cross-instance scenario), a household=True
    account only counts if it was hydrated from THIS kg's own configured
    household_id, i.e. genuinely the actor's own household instance, not
    just any household-tagged account this kg's cache happens to hold.
    The plain in-memory KnowledgeGraph has no such concept at all — every
    actor sharing that ONE object IS the household by construction (the
    shared-Python-object convention every test before Level 48 used) —
    so household=True there is trusted exactly as before.
    """
    attrs = account.attributes
    if attrs.get("owner") == actor_id:
        return True
    if actor_id in (attrs.get("owners") or ()):
        return True
    if attrs.get("household") is not True:
        return False
    entity_scope = getattr(kg, "_entity_scope", None)
    if entity_scope is None:
        return True
    kg_household_id = getattr(kg, "_household_id", None)
    return (
        kg_household_id is not None
        and entity_scope.get(account.entity_id) == kg_household_id
    )


def _find_wallet(kg, actor_id: str | None = None):
    """The wallet ACCOUNT entity that pays for groceries, or None. Shared by
    Payment and PaymentConfirmation so both capabilities agree on which
    account pays.

    actor_id scopes candidates to accounts that actor actually owns (see
    _owned_by) — a KnowledgeGraph can hold MULTIPLE actors' independent
    accounts at once (PlanetaryRuntime's Shared World, not the one-actor-
    per-graph shape every pre-existing test used), and without this scope
    payment silently drew from whichever account sorted first by
    entity_id in the WHOLE graph, regardless of who was actually paying.
    actor_id=None preserves the original unscoped behavior for callers
    that haven't threaded an actor_id through yet.

    A household-tagged wallet (attributes.household == True) wins over a
    personal one — an actor can have both a personal wallet and a shared
    household wallet in their KG, and groceries are a household expense.
    Falls back to any wallet-ish account, then the first account at all.

    UPI Reserve Pay (kernel/domains/payment_provider.py) accounts
    (account_type=="upi_reserve_pay") are only picked when they're the
    ONLY real payment account an actor has. Every actor is now UPI-only
    (scripts/seed_world.py::ensure_wallet no longer creates a regular
    debit/cash wallet at all) — but preferring a non-UPI account WHEN one
    genuinely exists still matters for any transitional/mixed state (a
    household with an unmigrated legacy wallet, or a typed food_assistance/
    credit account from Level 26): without that preference, an actor with
    both would have their charge routed to whichever account happens to
    sort first by entity_id — real money (or a real, asynchronous UPI
    pause) decided by tie-break order, not by anyone's actual choice or
    the system's own UPI-first policy.
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    # kg.entities' order isn't guaranteed (Neo4j's MATCH has no ORDER BY,
    # see process_payment_with_fallback's Level 22 fix) -- sorted by
    # entity_id so which account wins a tie (e.g. two real household=True
    # accounts) is deterministic, not an accident of graph-read order.
    accounts = sorted(
        (e for e in kg.entities if e.entity_type == EntityType.ACCOUNT),
        key=lambda e: e.entity_id,
    )
    if actor_id is not None:
        accounts = [a for a in accounts if _owned_by(kg, a, actor_id)]
    non_upi = [
        a for a in accounts if a.attributes.get("account_type") != "upi_reserve_pay"
    ]
    # Prefer a non-UPI account if one genuinely exists (see docstring);
    # otherwise UPI Reserve Pay is the only account there is, and IS the
    # correct default now, not something to exclude down to nothing.
    accounts = non_upi or accounts
    household = next(
        (e for e in accounts if e.attributes.get("household") is True), None
    )
    if household is not None:
        return household
    return next(
        (
            e
            for e in accounts
            if e.attributes.get("type") in ("wallet", None)
            and "wallet" in e.name.lower()
        ),
        accounts[0] if accounts else None,
    )


def find_payment_sources(kg, actor_id: str | None = None) -> list:
    """Level 26 (GS-2600): every real payment ACCOUNT available to the
    household — not just the single wallet _find_wallet picks. A
    household with only one account (every existing household in this
    codebase, before Level 26) still works exactly as before; this is
    additive, only engaged when genuinely more than one account exists.

    actor_id scopes to accounts that actor owns — see _find_wallet's
    docstring for why this matters the moment a KnowledgeGraph holds more
    than one actor's accounts. actor_id=None preserves the original
    unscoped behavior.
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    accounts = [e for e in kg.entities if e.entity_type == EntityType.ACCOUNT]
    if actor_id is not None:
        accounts = [a for a in accounts if _owned_by(kg, a, actor_id)]
    return accounts


def _available_funds(account) -> float:
    if account.attributes.get("account_type") == "credit":
        return max(
            0.0,
            account.attributes.get("credit_limit", 0)
            - account.attributes.get("balance", 0),
        )
    return account.attributes.get("balance", 0)


# ── Payment source selection ──────────────────────────────────────────

_PAYMENT_SOURCE_PRIORITY = ("food_assistance", "cash", "credit")


def choose_payment_source(
    accounts: list, total: float, is_grocery_eligible: bool = True
) -> dict:
    """Level 26/31 (GS-2600): which real account should pay, and why.
    Prefers food_assistance first when the purchase is grocery-eligible —
    a real household spends restricted-use benefits before unrestricted
    cash, since unused benefits are typically forfeited ("use it or lose
    it"), not saved. Credit is last: it's debt, not funds the household
    already has. An account only qualifies if it can cover the FULL
    total — this doesn't attempt to split one purchase automatically
    across multiple accounts, a materially bigger feature than "pick the
    best single source."

    Multi-Wallet Optimization: a household can genuinely hold MORE THAN
    ONE account of the same type (e.g. checking + savings, both tagged
    "cash") — grouping by type into a single dict entry per type used to
    keep only the LAST account of each type seen, silently hiding every
    other same-type account from consideration. Verified live: Savings
    ($50, cash) added before Checking ($5, cash) meant a $20 purchase was
    reported as unaffordable ("no source found") even though Savings
    alone could clearly cover it — the dict collision had already
    discarded it. Within each priority tier, every real account of that
    type is now considered, and the one with the most available funds
    among those that can actually cover the total is chosen (a
    deterministic tie-break by entity_id when two accounts of the same
    type have identical available funds, matching this codebase's
    existing convention — see _find_wallet).
    """
    by_type: dict[str, list] = {}
    for a in accounts:
        by_type.setdefault(a.attributes.get("account_type"), []).append(a)
    order = [
        t
        for t in _PAYMENT_SOURCE_PRIORITY
        if t != "food_assistance" or is_grocery_eligible
    ]
    for account_type in order:
        candidates = by_type.get(account_type) or []
        affordable = [a for a in candidates if _available_funds(a) >= total]
        if not affordable:
            continue
        account = max(affordable, key=lambda a: (_available_funds(a), a.entity_id))
        return {
            "chosen": account,
            "account_type": account_type,
            "available": _available_funds(account),
        }
    return {"chosen": None, "account_type": None, "available": 0.0}


def attempt_borrowing(credit_account, shortfall: float) -> dict:
    """Level 26 (GS-2601): when every real payment source combined still
    can't cover the total, check for a genuinely BOUNDED emergency
    extension (attributes["emergency_extension_limit"]) rather than
    letting credit go arbitrarily negative — Level 22 already proved
    unbounded overspend is a real, serious bug (a runaway quantity
    inflated one household's wallet to -$20M), not an acceptable
    fallback. A shortfall beyond the extension limit is an honest
    failure, reported with the real numbers, never silently absorbed.

    Cumulative, not per-call: emergency_extension_limit bounds how far
    PAST credit_limit this account may EVER sit at once, not how much
    can be borrowed in any single transaction — the limit itself is
    static seed data, so the only honest way to know how much
    headroom is actually left is to read how much of it is already
    outstanding (balance already past credit_limit) on THIS call.
    Without this, a limit "used" by one purchase was fully available
    again on the very next one (nothing ever persists usage against
    it), reintroducing exactly the unbounded-overspend failure mode
    this function's own docstring says was already proven real — just
    one layer up, at the emergency-extension cap instead of the plain
    balance.
    """
    if credit_account is None:
        return {"approved": False, "reason": "no credit account available to extend"}
    limit = credit_account.attributes.get("emergency_extension_limit", 0)
    credit_limit = credit_account.attributes.get("credit_limit", 0)
    balance = credit_account.attributes.get("balance", 0)
    already_used = max(0.0, balance - credit_limit)
    remaining = round(limit - already_used, 2)
    if shortfall <= remaining:
        return {
            "approved": True,
            "extension_used": round(shortfall, 2),
            "remaining_extension": round(remaining - shortfall, 2),
        }
    return {
        "approved": False,
        "reason": f"shortfall ${shortfall:.2f} exceeds the ${remaining:.2f} remaining emergency extension "
        f"(${limit:.2f} limit, ${already_used:.2f} already in use)",
    }


# ── Processing & spending limits ──────────────────────────────────────


def process_payment_with_fallback(
    kg, total: float, max_retries_per_processor: int = 2
) -> dict:
    """Level 22 (GS-2200): tries every payment processor in the KG in
    order, retrying a transiently-down one before moving to the next. A
    processor marked attributes["status"]=="down" fails EVERY attempt
    (simulating a real outage, not a coin flip), so this also proves the
    FALLBACK actually engages when exhausted, not just that retries look
    like they ran. Returns which processor ultimately succeeded and the
    full attempt trace, so a real outage is visible, not silently papered
    over.
    """
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    # kg.entities' order reflects Neo4j's MATCH return order, which has no
    # ORDER BY and is NOT guaranteed stable across queries — "try the
    # primary before falling back to backup" needs a real, explicit
    # ordering, not an accident of graph-read order (caught live: this
    # silently returned Backup Gateway before ever attempting the real
    # down Primary Gateway once Neo4j happened to return them in a
    # different order). attributes["priority"] (lower tries first) makes
    # fallback order real seed data instead of implicit; every existing
    # processor never set one and defaults to 0, tie-broken by entity_id
    # for full determinism.
    processors = sorted(
        (
            e
            for e in kg.entities
            if e.entity_type == EntityType.ORGANIZATION
            and e.attributes.get("type") == "payment_processor"
        ),
        key=lambda e: (e.attributes.get("priority", 0), e.entity_id),
    )
    # No processor entity configured at all is a seed-data gap, not an
    # outage — existing households never modeled this before Level 22 and
    # still need payment to work exactly as it always did. A processor
    # that EXISTS and is marked down is the real GS-2200 scenario.
    if not processors:
        return {"success": True, "processor": "Unknown Processor", "attempts": []}
    attempts_log = []
    for processor in processors:
        down = processor.attributes.get("status") == "down"
        for attempt in range(1, max_retries_per_processor + 1):
            if down:
                attempts_log.append(
                    {
                        "processor": processor.name,
                        "attempt": attempt,
                        "result": "failed (processor down)",
                    }
                )
                continue
            attempts_log.append(
                {"processor": processor.name, "attempt": attempt, "result": "succeeded"}
            )
            return {
                "success": True,
                "processor": processor.name,
                "attempts": attempts_log,
            }
    return {
        "success": False,
        "processor": None,
        "attempts": attempts_log,
        "reason": "every payment processor unavailable",
    }


def monthly_spending(kg, since_ts: float) -> float:
    """Level 26 (GS-2602): real spending this month, summed from ACTUAL
    persisted order totals — not a separately tracked counter that could
    drift from what really happened."""
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    kg.refresh()
    return round(
        sum(
            e.attributes.get("total", 0)
            for e in kg.entities
            if e.entity_type == EntityType.EVENT
            and e.attributes.get("order_id")
            and e.attributes.get("created_at", 0) >= since_ts
        ),
        2,
    )


def check_monthly_cap(
    kg, monthly_cap: float, additional_charge: float, since_ts: float
) -> dict:
    """Level 26 (GS-2602): "planner delays purchase" — this architecture
    is request/response, not a scheduler (same honest boundary Level 18
    drew for autonomy), so "delay" means refusing NOW with a clear
    recommendation to retry later, not silently queuing a future charge.
    """
    spent = monthly_spending(kg, since_ts)
    would_be = round(spent + additional_charge, 2)
    within = would_be <= monthly_cap
    return {
        "within_cap": within,
        "spent_this_month": spent,
        "would_be": would_be,
        "cap": monthly_cap,
        "reason": (
            ""
            if within
            else f"this purchase would bring monthly spending to ${would_be:.2f}, over the ${monthly_cap:.2f} cap — recommend delaying"
        ),
    }


# ── Fraud detection ─────────────────────────────────────────────────────

_FRAUD_VELOCITY_WINDOW_SECONDS = 3600.0
_FRAUD_VELOCITY_THRESHOLD = 10
"""10 or more of this actor's own orders inside the window is treated as
suspicious — a real household rarely places that many separate orders
within an hour; a stolen-card tester frequently does. Raised from 3:
that threshold was tripping on ordinary live/demo usage (a handful of
real purchase walkthroughs in quick succession looks identical to this
signal as actual card-testing at n=3) — 10 keeps the same detection
intent for genuine rapid-fire abuse while giving real interactive usage
realistic headroom."""
_FRAUD_AMOUNT_MULTIPLE = 5.0
"""A total more than 5x this actor's own historical average order is
treated as suspicious — only evaluated when real history exists."""
_FRAUD_HIGH_RISK_SCORE = 0.5
"""Either signal alone is enough to hold a transaction for review — these
are independent red flags, not required to co-occur."""


def assess_transaction_risk(
    kg, actor_id: str, total: float, now: float | None = None
) -> dict:
    """Real-time fraud risk assessment for one transaction (MB-3014 Fraud
    Detection), using signals computed from this actor's ACTUAL persisted
    order history (EntityType.EVENT entities with attributes["buyer_id"]/
    ["total"]/["created_at"] — the exact same real data monthly_spending()/
    check_monthly_cap() already read) — never a hardcoded score.

    Two signals, each independently sufficient to flag high_risk:
      - velocity: _FRAUD_VELOCITY_THRESHOLD or more of this actor's own
        orders within the last _FRAUD_VELOCITY_WINDOW_SECONDS — a classic
        stolen-card-testing pattern (many rapid purchase attempts).
      - amount anomaly: total exceeds _FRAUD_AMOUNT_MULTIPLE times this
        actor's own historical average order total. Only evaluated when
        real history exists — a brand-new actor's very first order has
        nothing to compare against and is never flagged on this signal
        alone (a new actor's first order being unusually large is not,
        by itself, evidence of anything).

    risk_score is the fraction of the two signals that fired (0.0, 0.5,
    or 1.0); high_risk is risk_score >= _FRAUD_HIGH_RISK_SCORE. Purely
    advisory and read-only — never raises, never touches the
    transaction itself; it's the caller's job (PaymentConfirmation/
    Payment) to act on the assessment.
    """
    import time as _time
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    now = now if now is not None else _time.time()
    kg.refresh()
    own_orders = [
        e
        for e in kg.entities
        if e.entity_type == EntityType.EVENT
        and e.attributes.get("order_id")
        and e.attributes.get("buyer_id") == actor_id
    ]

    reasons: list[str] = []
    signals_fired = 0
    total_signals = 2

    # Qualification Gap Closure, Phase 6: velocity_cooldown_until is
    # purely informational, derived from the SAME real order history the
    # velocity signal above already reads — not a new, separately-tracked
    # state that could drift from it. Before this, a caller with no
    # access to this actor's own order history had no way to tell
    # "genuinely suspicious activity" from "I am legitimately retrying
    # rapidly (e.g. qualification re-testing) and will stop being flagged
    # once my own oldest recent order ages out of the window" — both
    # looked identical from the outside (a bare "held for fraud review"
    # error). None when the velocity signal isn't currently firing.
    velocity_cooldown_until: float | None = None
    recent = [
        o
        for o in own_orders
        if o.attributes.get("created_at", 0) >= now - _FRAUD_VELOCITY_WINDOW_SECONDS
    ]
    if len(recent) >= _FRAUD_VELOCITY_THRESHOLD:
        signals_fired += 1
        reasons.append(
            f"{len(recent)} orders from this actor in the last "
            f"{_FRAUD_VELOCITY_WINDOW_SECONDS / 60:.0f} minutes (threshold {_FRAUD_VELOCITY_THRESHOLD})"
        )
        # The count only drops back below the threshold once enough of
        # the OLDEST recent orders age out of the window -- the critical
        # one is at ascending index (len(recent) - threshold): once IT
        # ages out too, exactly `threshold - 1` orders remain in-window.
        recent_ts_ascending = sorted(o.attributes.get("created_at", 0) for o in recent)
        critical_ts = recent_ts_ascending[
            len(recent_ts_ascending) - _FRAUD_VELOCITY_THRESHOLD
        ]
        velocity_cooldown_until = critical_ts + _FRAUD_VELOCITY_WINDOW_SECONDS

    if own_orders:
        historical_avg = sum(o.attributes.get("total", 0) for o in own_orders) / len(
            own_orders
        )
        if historical_avg > 0 and total > historical_avg * _FRAUD_AMOUNT_MULTIPLE:
            signals_fired += 1
            reasons.append(
                f"order total ${total:.2f} is {total / historical_avg:.1f}x this actor's "
                f"historical average (${historical_avg:.2f})"
            )

    risk_score = round(signals_fired / total_signals, 2)
    return {
        "high_risk": risk_score >= _FRAUD_HIGH_RISK_SCORE,
        "risk_score": risk_score,
        "reasons": tuple(reasons),
        "velocity_cooldown_until": velocity_cooldown_until,
    }


# ── Loyalty program ──────────────────────────────────────────────────

_POINTS_PER_DOLLAR = 1
"""MB-3049: points earned per dollar of a completed order's real total."""

_POINTS_TO_DOLLAR_RATE = 100
"""MB-3049: points required to redeem for $1 of real wallet credit."""


def _loyalty_account_id(actor_id: str) -> str:
    return f"loyalty_{actor_id}"


def get_points_balance(kg, actor_id: str) -> dict:
    """Look up an actor's current loyalty points balance."""
    account = kg.get_entity(_loyalty_account_id(actor_id)) if kg is not None else None
    return {
        "actor_id": actor_id,
        "points_balance": account.attributes.get("points_balance", 0) if account else 0,
    }


def award_points(kg, actor_id: str, order_id: str, now: float | None = None) -> dict:
    """MB-3049 Loyalty Program: awards real, persisted loyalty points
    for a completed order — _POINTS_PER_DOLLAR point per dollar of the
    order's real total. Requires the order to have actually reached
    "completed" (MB-3022's confirm_receipt() terminal status) — points
    for an order that's still in progress, was cancelled, or was
    returned would be points for a purchase that never actually stuck.

    Refuses to award points for the SAME order_id twice — idempotent,
    the same "only what actually happened, once" discipline
    leave_review()'s verified-purchase check already follows — a
    retried/duplicated award call must never double-credit one
    purchase.

    The per-actor balance lives on its own EntityType.OTHER marker
    entity (deterministic id, same convention as grocery.py's
    purchase_log markers) — deliberately NOT an EntityType.ACCOUNT, so
    it can never be mistaken for a spendable wallet by
    _find_wallet()/find_payment_sources().
    """
    import time as _time
    from src.monkey_brain.kernel.knowledge_graph import EntityType

    now = now if now is not None else _time.time()
    order = kg.get_entity(order_id)
    if order is None:
        return {"success": False, "error": f"no such order {order_id!r}"}
    if order.attributes.get("status") != "completed":
        return {
            "success": False,
            "error": f"order {order_id!r} is {order.attributes.get('status')!r}, "
            f"points are only awarded for a 'completed' order",
        }

    account_id = _loyalty_account_id(actor_id)
    account = kg.get_entity(account_id)
    awarded_order_ids = (
        set(account.attributes.get("awarded_order_ids", [])) if account else set()
    )
    if order_id in awarded_order_ids:
        return {
            "success": False,
            "error": f"points already awarded for order {order_id!r}",
        }

    points_earned = int(order.attributes.get("total", 0) * _POINTS_PER_DOLLAR)
    current_balance = account.attributes.get("points_balance", 0) if account else 0
    new_balance = current_balance + points_earned
    awarded_order_ids.add(order_id)

    kg.add_entity(
        account_id,
        EntityType.OTHER,
        f"Loyalty Points: {actor_id}",
        {
            "loyalty_account": True,
            "actor_id": actor_id,
            "points_balance": new_balance,
            "awarded_order_ids": sorted(awarded_order_ids),
            "updated_at": now,
        },
    )
    return {
        "success": True,
        "actor_id": actor_id,
        "order_id": order_id,
        "points_earned": points_earned,
        "points_balance": new_balance,
    }


def redeem_points(kg, actor_id: str, points: int) -> dict:
    """MB-3049 Loyalty Program: redeems real points for real wallet
    credit. Per explicit design choice ("direct wallet credit"): points
    become spendable cash immediately (_POINTS_TO_DOLLAR_RATE points =
    $1), credited to the SAME wallet _find_wallet() already resolves
    for this actor for checkout — the same credit/debit
    direction asymmetry cancel_order()/approve_return()/refund_order()
    already use. Refuses a redemption that exceeds the actor's real
    points balance — points can't go negative, same "never overdraw"
    discipline as every balance check elsewhere in this domain.
    """
    if points <= 0:
        return {"success": False, "error": f"points must be positive, got {points!r}"}

    account_id = _loyalty_account_id(actor_id)
    account = kg.get_entity(account_id)
    current_balance = account.attributes.get("points_balance", 0) if account else 0
    if points > current_balance:
        return {
            "success": False,
            "error": f"only {current_balance} points available, cannot redeem {points}",
        }

    wallet = _find_wallet(kg, actor_id)
    if wallet is None:
        return {
            "success": False,
            "error": f"no wallet found for actor {actor_id!r} to credit",
        }

    dollar_value = round(points / _POINTS_TO_DOLLAR_RATE, 2)
    account_type = wallet.attributes.get("account_type")
    balance = wallet.attributes.get("balance", 0)
    new_wallet_balance = (
        round(balance - dollar_value, 2)
        if account_type == "credit"
        else round(balance + dollar_value, 2)
    )
    kg.update_entity(wallet.entity_id, attributes={"balance": new_wallet_balance})

    new_points_balance = current_balance - points
    kg.update_entity(account_id, attributes={"points_balance": new_points_balance})
    return {
        "success": True,
        "actor_id": actor_id,
        "points_redeemed": points,
        "dollar_value": dollar_value,
        "wallet_id": wallet.entity_id,
        "points_balance": new_points_balance,
    }
