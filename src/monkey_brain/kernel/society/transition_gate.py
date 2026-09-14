"""TransitionGate — the one authoritative pre-commit decision point for a
shared/world-state mutation.

The bug this closes: a capability (OrderCreationCapability, PaymentCapability,
...) could go straight from "actor wants X" to "state committed" with no
step in between that asked "does this transition need another actor's
agreement first?" Negotiation infrastructure already existed
(kernel/society/transaction.py::TransactionCoordinator) but as a fully
parallel system nothing on the real purchase path ever called.

This module is domain-agnostic (no import of grocery.py or any vertical) —
a vertical builds a ProposedTransition from its own action/context shape and
hands it to this gate; the gate only knows about generic KG entity
attributes (owner_id, constraints, requires_consent_from, reservations).

Two independent things this gate reports, deliberately kept separate
(spec's "contention vs negotiation-required" distinction, and its own
worked example in the observability section shows them as independent
booleans):

- ``contention``: a live claim by another actor is currently visible on the
  resource at evaluation time (an active, unexpired reservation held by
  someone else). This is PURE OBSERVABILITY. It does NOT by itself force a
  pause — a plain capacity race between two actors who both have an
  equally legitimate claim is already correctly arbitrated by the existing
  compare-and-swap reservation primitives (try_reserve/confirm_reservation,
  kernel/domains/grocery.py) under real concurrency (proven by
  tests/scenarios/test_shared_budget.py::test_budget004) — re-litigating
  that as a negotiation would just be theater around a decision the CAS
  loop already made honestly and atomically. Forcing negotiation onto
  every multi-owner shared resource would also be wrong: two actors
  legitimately co-spending within one shared budget is NOT contention
  (test_budget001) — the spec's own §4 "shared state, no contention"
  example.

- ``requires_negotiation``: the proposed transition conflicts with an
  EXPLICIT, declared claim/constraint on the resource — a genuinely
  incompatible requirement (e.g. a buyer's max_price below a store's
  min_price) or a resource explicitly flagged as needing another named
  actor's consent before this class of transition may commit. This is what
  actually pauses the tick.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ProposedTransition:
    """What an actor wants to change, before anything is committed."""

    actor_id: str
    resource_ids: tuple[str, ...]
    mutation_kind: str  # "reserve" | "confirm" | "release"
    capability: str = ""
    action_id: str = ""
    magnitude: float = 0.0
    constraints: dict[str, Any] = field(default_factory=dict)
    """Explicit constraints THIS proposal declares (e.g. {"max_price": 10.0}).
    Only ever compared against a resource's own EXPLICITLY declared
    constraints — never inferred from mere co-ownership."""
    self_holder_ids: tuple[str, ...] = ()
    """Additional reservation-holder identities that are really THIS same
    actor's own prior claim, not another actor's. grocery.py's
    try_reserve/confirm_reservation key a hold by order_id (generated
    inside OrderCreationCapability), not by actor_id — by the time Payment
    proposes a "confirm" transition against a hold it already placed, the
    real order_id (context["order"]["order_id"]) is this actor's own
    holder identity, and must not be mistaken for a live claim by someone
    else."""
    required_consent_from: tuple[str, ...] = ()
    """Counterparty ids the PROPOSING vertical already knows must consent,
    independent of anything declared on the resource entity itself. Order/
    Payment never set this — a store product's seller isn't a live
    negotiation party, only an explicitly-opted-in resource attribute
    (requires_consent_from) is. But a vertical whose whole domain meaning
    IS "this resource is another actor's own property" (e.g. grocery.py's
    SocialSourcing, borrowing/buying directly from a named peer) knows its
    counterparty without needing a separate opt-in flag on every peer's
    entity — this field lets it say so, still through the ONE gate, still
    resolved through the SAME negotiation_store/negotiate route Order/
    Payment already use, just from a second, proposer-declared source
    instead of only a resource-declared one."""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["resource_ids"] = list(self.resource_ids)
        return d


@dataclass(frozen=True)
class GateDecision:
    """The gate's answer for one ProposedTransition."""

    allow: bool
    requires_negotiation: bool = False
    contention: bool = False
    counterparties: tuple[str, ...] = ()
    reason: str = ""
    evaluated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["counterparties"] = list(self.counterparties)
        return d


def _incompatible_constraint(proposed: dict[str, Any], declared: dict[str, Any]) -> str:
    """The one, deliberately narrow, deterministic incompatibility rule:
    a buyer's declared max_price below the resource's own declared
    min_price. Additive — no existing entity declares either attribute,
    so this never fires for anything that isn't opted in by a test or a
    vertical that explicitly wants this check."""
    max_price = proposed.get("max_price")
    min_price = declared.get("min_price")
    if max_price is not None and min_price is not None and max_price < min_price:
        return f"buyer max_price {max_price} < resource min_price {min_price}"
    return ""


def _active_other_holders(attrs: dict[str, Any], self_ids: tuple[str, ...]) -> tuple[str, ...]:
    """Read-only: which OTHER holder_ids currently have a live (unexpired)
    reservation on this resource, excluding every identity in self_ids
    (the proposing actor's own id, plus any known holder ids that are
    really this same actor's own prior claim — see
    ProposedTransition.self_holder_ids). Never mutates — this is what
    makes it safe to call purely as an observation ahead of the real
    commit."""
    now = time.time()
    reservations = attrs.get("reservations", []) or []
    others = {
        r.get("actor_id")
        for r in reservations
        if r.get("until", 0) > now and r.get("actor_id") and r.get("actor_id") not in self_ids
    }
    return tuple(sorted(others))


class TransitionGate:
    """The single authoritative decision point. Reused unmodified by every
    vertical/capability that mutates shared state — no capability should
    implement its own negotiation-requirement logic (spec §3)."""

    def evaluate(self, transition: ProposedTransition, kg: Any) -> GateDecision:
        if kg is None or not transition.resource_ids:
            return GateDecision(allow=True, reason="no shared resource to evaluate")

        contention = False
        contention_holders: tuple[str, ...] = ()

        for resource_id in transition.resource_ids:
            entity = kg.get_entity(resource_id)
            if entity is None:
                continue
            attrs = entity.attributes

            # 1. Explicit, incompatible constraint (spec §4 "conflicting
            # constraints") — the one case that genuinely must pause the
            # tick before anything is reserved or committed.
            declared_constraints = attrs.get("constraints") or {}
            conflict = _incompatible_constraint(transition.constraints, declared_constraints)
            if conflict:
                owner = attrs.get("owner_id", "")
                counterparties = tuple(c for c in (owner,) if c and c != transition.actor_id)
                return GateDecision(
                    allow=False,
                    requires_negotiation=True,
                    contention=True,
                    counterparties=counterparties,
                    reason=f"constraint conflict on {resource_id}: {conflict}",
                )

            # 2. Resource explicitly requires another named actor's
            # consent before this transition may commit (spec §4
            # "authority/permission conflict") — union of what the
            # resource itself declares AND what the proposing vertical
            # already knows (transition.required_consent_from, e.g. a
            # peer-owned resource's real owner). Either source alone is
            # sufficient to require negotiation; this never REMOVES a
            # requirement either side names.
            consent_from = tuple(attrs.get("requires_consent_from") or ()) + transition.required_consent_from
            others_required = tuple(dict.fromkeys(a for a in consent_from if a and a != transition.actor_id))
            if others_required:
                return GateDecision(
                    allow=False,
                    requires_negotiation=True,
                    contention=True,
                    counterparties=others_required,
                    reason=f"{resource_id} requires consent from {others_required}",
                )

            # 3. Pure observability: is another actor's live claim visible
            # right now? Never gates the transition by itself (see module
            # docstring) — attached to the decision so the trace is honest
            # about what was visible at evaluation time, even when the
            # answer is "proceed."
            self_ids = (transition.actor_id,) + tuple(transition.self_holder_ids)
            other_holders = _active_other_holders(attrs, self_ids)
            if other_holders:
                contention = True
                contention_holders = tuple(sorted(set(contention_holders) | set(other_holders)))

        if contention:
            return GateDecision(
                allow=True,
                requires_negotiation=False,
                contention=True,
                counterparties=contention_holders,
                reason=f"another actor holds a live claim on this resource: {contention_holders}; "
                f"arbitrated by the existing reservation CAS, no negotiation pause needed",
            )
        return GateDecision(allow=True, reason="no conflicting claim")
