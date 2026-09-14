"""Affiliation-governed actor communication.

The router is intentionally independent of natural-language generation. It
answers only the social question: may this sender reach that participant? The
existing actor/planner pipeline remains responsible for message content and
responses.

Communication-eligibility SEMANTICS live in kernel/affiliations/graph.py::
AffiliationGraph, not here — this router is orchestration only (actor
lookup, active-recipient filtering, decision auditing/metrics). See
AffiliationGraph's module docstring for the full precedence of eligibility
patterns it evaluates.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable
from uuid import uuid4

from src.monkey_brain.kernel.compile import _obs


@dataclass(frozen=True)
class CommunicationDecision:
    allowed: bool
    sender_id: str
    recipient_id: str
    sender_affiliations: tuple[str, ...] = ()
    recipient_affiliations: tuple[str, ...] = ()
    society_id: str = ""
    reason: str = ""
    affiliation_id: str = ""
    decision_id: str = field(default_factory=lambda: uuid4().hex)
    correlation_id: str = field(default_factory=lambda: uuid4().hex)
    """Id of the logical end-to-end operation this decision belongs to.
    Self-mints by default so every decision is traceable even from callers
    that don't yet pass one in; resolve() overrides it with a caller-
    supplied id (e.g. an execution_id/transaction_id) when available."""
    causation_id: str = ""
    """Id of whatever immediately triggered this decision (e.g. an inbound
    message/event id). Left empty rather than fabricated when nothing
    concrete produced this check."""


class AffiliationCommunicationRouter:
    """Resolve recipients through affiliations and a society boundary."""

    def __init__(
        self,
        actor_lookup: Callable[[str], Any],
        active_lookup: Callable[[], Iterable[Any]],
        society_id: str,
        *,
        metric_sink: Any = None,
        affiliation_graph: Any = None,
    ) -> None:
        self._actor_lookup = actor_lookup
        self._active_lookup = active_lookup
        self.society_id = society_id
        self.metric_sink = metric_sink
        self.audit: list[CommunicationDecision] = []
        self.affiliation_graph = affiliation_graph
        """Optional kernel/affiliations/graph.py::AffiliationGraph — the
        authoritative eligibility source once this router is attached to a
        PlanetaryRuntime (see PlanetaryRuntime._attach_society, which sets
        this post-construction the same way it already shares
        _context_engine/_relationships rather than rebuilding them per
        society). None only for a standalone SocietyRuntime with no
        PlanetaryRuntime (tests/demo scripts) — resolve() falls back to
        the original shared-affiliation/same-society check in that case,
        unchanged from before this refactor."""

    def sender_affiliations(self, actor_id: str) -> tuple[str, ...]:
        return self._affiliation_ids(self._actor_lookup(actor_id))

    def eligible_recipients(
        self,
        sender_id: str,
        *,
        affiliation_id: str = "",
        correlation_id: str = "",
        causation_id: str = "",
    ) -> tuple[str, ...]:
        recipients: list[str] = []
        for state in self._active_lookup():
            recipient_id = getattr(state, "actor_id", "")
            if not recipient_id or recipient_id == sender_id:
                continue
            decision = self.resolve(
                sender_id,
                recipient_id,
                affiliation_id=affiliation_id,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
            if decision.allowed:
                recipients.append(recipient_id)
        return tuple(recipients)

    def resolve(
        self,
        sender_id: str,
        recipient_id: str,
        *,
        affiliation_id: str = "",
        correlation_id: str = "",
        causation_id: str = "",
    ) -> CommunicationDecision:
        sender = self._actor_lookup(sender_id)
        recipient = self._actor_lookup(recipient_id)
        if sender is None or recipient is None:
            decision = CommunicationDecision(
                allowed=False,
                sender_id=sender_id,
                recipient_id=recipient_id,
                society_id=self.society_id,
                reason="sender or recipient is not registered",
            )
        elif not getattr(recipient, "is_active", True):
            decision = CommunicationDecision(
                allowed=False,
                sender_id=sender_id,
                recipient_id=recipient_id,
                society_id=self.society_id,
                reason="recipient is not active",
            )
        elif self.affiliation_graph is not None:
            decision = self.affiliation_graph.can_communicate(sender_id, recipient_id)
            if not decision.society_id:
                decision = dataclasses.replace(decision, society_id=self.society_id)
            if affiliation_id and decision.affiliation_id != affiliation_id:
                decision = dataclasses.replace(
                    decision,
                    allowed=False,
                    reason=f"eligible via {decision.reason!r} but not via the requested affiliation_id",
                )
        else:
            decision = self._legacy_resolve(sender, sender_id, recipient, recipient_id, affiliation_id)
        # Uniform override regardless of which branch above produced the
        # decision (early-return, AffiliationGraph delegate, or legacy) —
        # same dataclasses.replace pattern already used for society_id.
        if correlation_id:
            decision = dataclasses.replace(decision, correlation_id=correlation_id)
        if causation_id:
            decision = dataclasses.replace(decision, causation_id=causation_id)
        self.audit.append(decision)
        _obs.counter(
            "communication.affiliation_resolutions",
            allowed=str(decision.allowed).lower(),
        )
        _obs.counter("communication.routing_decisions")
        if not decision.allowed:
            _obs.counter("communication.denied_communications")
        return decision

    def _legacy_resolve(
        self,
        sender: Any,
        sender_id: str,
        recipient: Any,
        recipient_id: str,
        affiliation_id: str,
    ) -> CommunicationDecision:
        """Pre-AffiliationGraph behavior: only reachable when this router
        has no affiliation_graph attached (a standalone SocietyRuntime with
        no owning PlanetaryRuntime — tests/demo scripts). Preserved
        unchanged so those call sites see no behavior change from this
        refactor."""
        sender_affiliations = self._affiliation_ids(sender)
        recipient_affiliations = self._affiliation_ids(recipient)
        shared = set(sender_affiliations).intersection(recipient_affiliations)
        if affiliation_id:
            shared.intersection_update({affiliation_id})
        if shared:
            allowed, reason = True, "shared affiliation permits communication"
        elif (
            self.society_id
            and self._same_society(sender, recipient)
            and (not sender_affiliations or not recipient_affiliations)
        ):
            # Society membership is itself the canonical organizational
            # affiliation; temporary participants are included because they
            # are present in this runtime's active participant set.
            allowed, reason = True, "society affiliation permits communication"
            shared = {self.society_id}
        else:
            allowed, reason = False, "no shared affiliation or permitted society route"
        return CommunicationDecision(
            allowed=allowed,
            sender_id=sender_id,
            recipient_id=recipient_id,
            sender_affiliations=sender_affiliations,
            recipient_affiliations=recipient_affiliations,
            society_id=self.society_id,
            reason=reason,
            affiliation_id=next(iter(sorted(shared)), ""),
        )

    @staticmethod
    def _affiliation_ids(state: Any) -> tuple[str, ...]:
        if state is None:
            return ()
        actor = getattr(state, "actor", state)
        manager = getattr(actor, "affiliations", None) or getattr(actor, "_affiliations", None)
        if manager is None:
            return ()
        result: list[str] = []
        for affiliation in manager.active() if hasattr(manager, "active") else manager.all():
            target = getattr(affiliation, "target_id", "")
            if target:
                result.append(target)
        return tuple(sorted(set(result)))

    @staticmethod
    def _same_society(sender: Any, recipient: Any) -> bool:
        return bool(sender and recipient)
