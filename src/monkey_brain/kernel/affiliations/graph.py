"""AffiliationGraph — the single authoritative implementation of actor-to-
actor communication eligibility (the Affiliation Graph's role per the
coordination spec: "determines communication topology... eligible
participants").

Supersedes AffiliationCommunicationRouter's own shared-third-party-only
check (kernel/society/communication.py), which incorrectly denied every
correctly-mirrored PAIRWISE relationship (roommate<->roommate, customer<->
store) once either party had ANY affiliation on file — diagnosed live via
a smoke test of TransactionCoordinator's negotiation feature. The router
now delegates here; this is the ONLY place communication-eligibility
semantics are decided, so growing the rule set (new relationship types,
richer policy, a real Enterprise entity) never again means touching the
router.

Nine eligibility patterns, evaluated in this precedence — first match
authorizes communication:
    1. direct affiliation          sender -> recipient
    2. reverse affiliation         recipient -> sender
    3. bidirectional affiliation   sender <-> recipient (type-declared)
    4. shared organization         sender -> org <- recipient
    5. shared society membership
    6. enterprise policies         (PART_OF/BELONGS_TO chain, one hop up)
    7. authorization policies      (SocietyGovernanceEngine.authorize)
    8. delegated authority         (DelegationRegistry)
    9. inherited organizational relationship (MANAGES/SUPERIOR/REPRESENTS,
       one intermediate actor)

Rules 6-9 are honest, narrow implementations against what data actually
exists today (there is no separate Enterprise entity, no communicate-
shaped governance grants populated anywhere yet, and delegation grants
permissions rather than communication rights specifically) — they will
get richer as those subsystems grow, rather than fabricating behavior
those subsystems don't yet support.

Trust plays NO role here — eligibility only. TransactionCoordinator ranks
the eligible set by trust_level afterward
(kernel/society/transaction.py::_eligible_affiliates), exactly as the
architecture requires: Affiliation Graph -> Eligible Participants ->
Trust Network Ranking.
"""

from __future__ import annotations

from typing import Any

_NON_NEGOTIABLE_TYPES = frozenset({"member_of"})
"""Affiliation types that represent structural bookkeeping (mirrored
society membership — see PlanetaryRuntime._mirror_membership_affiliation)
rather than a real actor-to-actor relationship; never a basis for
communication eligibility on their own."""

_ORG_HIERARCHY_TYPES = frozenset({"part_of", "belongs_to"})
_CHAIN_RELATIONSHIP_TYPES = frozenset({"manages", "superior", "represents"})


class AffiliationGraph:
    """Cross-actor communication-eligibility authority. One instance per
    PlanetaryRuntime (constructed in PlanetaryRuntime.__init__, threaded
    into each SocietyRuntime's AffiliationCommunicationRouter via
    _attach_society — mirrors how _context_engine/_relationships are
    already shared, not rebuilt, per society)."""

    def __init__(self, planetary: Any) -> None:
        self._planetary = planetary

    # ── Public entry point ──────────────────────────────────────────────

    def can_communicate(self, sender_id: str, recipient_id: str) -> Any:
        from src.monkey_brain.kernel.society.communication import CommunicationDecision

        sender_targets = tuple(sorted(self._target_set(sender_id)))
        recipient_targets = tuple(sorted(self._target_set(recipient_id)))
        checks = (
            self._direct_affiliation,
            self._reverse_affiliation,
            self._bidirectional_affiliation,
            self._shared_organization,
            self._shared_society,
            self._enterprise_policy,
            self._authorization_policy,
            self._delegated_authority,
            self._inherited_relationship,
        )
        for check in checks:
            result = check(sender_id, recipient_id)
            if result is not None:
                reason, extra = result
                return CommunicationDecision(
                    allowed=True,
                    sender_id=sender_id,
                    recipient_id=recipient_id,
                    sender_affiliations=sender_targets,
                    recipient_affiliations=recipient_targets,
                    reason=reason,
                    **extra,
                )
        return CommunicationDecision(
            allowed=False,
            sender_id=sender_id,
            recipient_id=recipient_id,
            sender_affiliations=sender_targets,
            recipient_affiliations=recipient_targets,
            reason="no eligible communication pattern",
        )

    # ── Shared helpers ───────────────────────────────────────────────────

    def _affiliations_for(self, actor_id: str) -> Any | None:
        pr = self._planetary
        for sr in pr._societies_for(actor_id):
            state = sr.get_actor(actor_id)
            if state is not None:
                return getattr(state.actor_runtime, "affiliations", None)
        return None

    def _negotiable(self, affiliations: Any, target_id: str) -> list[Any]:
        if affiliations is None:
            return []
        return [
            a
            for a in affiliations.by_target(target_id)
            if getattr(a, "affiliation_type", "") not in _NON_NEGOTIABLE_TYPES
        ]

    def _target_set(self, actor_id: str) -> set[str]:
        affiliations = self._affiliations_for(actor_id)
        if affiliations is None:
            return set()
        return {
            a.target_id
            for a in affiliations.active()
            if a.target_id and getattr(a, "affiliation_type", "") not in _NON_NEGOTIABLE_TYPES
        }

    def _shared_societies(self, sender_id: str, recipient_id: str) -> list[Any]:
        pr = self._planetary
        recipient_societies = list(pr._societies_for(recipient_id))
        return [sr for sr in pr._societies_for(sender_id) if sr in recipient_societies]

    # ── 1-3: pairwise affiliation ────────────────────────────────────────

    def _direct_affiliation(self, sender_id: str, recipient_id: str):
        matches = self._negotiable(self._affiliations_for(sender_id), recipient_id)
        if matches:
            return "direct affiliation permits communication", {"affiliation_id": matches[0].affiliation_id}
        return None

    def _reverse_affiliation(self, sender_id: str, recipient_id: str):
        matches = self._negotiable(self._affiliations_for(recipient_id), sender_id)
        if matches:
            return "reverse affiliation permits communication", {"affiliation_id": matches[0].affiliation_id}
        return None

    def _bidirectional_affiliation(self, sender_id: str, recipient_id: str):
        # A one-sided record (either direction) whose TYPE declares
        # bidirectional=True is sufficient even when the mirroring bridge
        # never ran (e.g. bootstrap-seeded data) — covers real relationships
        # without requiring a perfectly mirrored copy on both sides.
        for source_id, target_id in (
            (sender_id, recipient_id),
            (recipient_id, sender_id),
        ):
            for a in self._negotiable(self._affiliations_for(source_id), target_id):
                type_info = getattr(a, "type_info", None)
                if type_info is not None and type_info.bidirectional:
                    return (
                        "bidirectional affiliation permits communication",
                        {"affiliation_id": a.affiliation_id},
                    )
        return None

    # ── 4: shared third-party target (the original, only pre-existing rule) ──

    def _shared_organization(self, sender_id: str, recipient_id: str):
        shared = self._target_set(sender_id) & self._target_set(recipient_id)
        if shared:
            return (
                "shared organizational affiliation permits communication",
                {"affiliation_id": next(iter(sorted(shared)))},
            )
        return None

    # ── 5: shared society ────────────────────────────────────────────────

    def _shared_society(self, sender_id: str, recipient_id: str):
        shared = self._shared_societies(sender_id, recipient_id)
        if shared:
            return (
                "shared society membership permits communication",
                {"society_id": shared[0].society.society_id},
            )
        return None

    # ── 6: enterprise (PART_OF/BELONGS_TO, one hop) ─────────────────────

    def _enterprise_policy(self, sender_id: str, recipient_id: str):
        sender_affiliations = self._affiliations_for(sender_id)
        recipient_affiliations = self._affiliations_for(recipient_id)
        if sender_affiliations is None or recipient_affiliations is None:
            return None
        sender_org = {a.target_id for a in sender_affiliations.affiliated_entities(_ORG_HIERARCHY_TYPES)}
        recipient_org = {a.target_id for a in recipient_affiliations.affiliated_entities(_ORG_HIERARCHY_TYPES)}
        shared = sender_org & recipient_org
        if shared:
            return (
                "shared enterprise/organizational affiliation permits communication",
                {"affiliation_id": next(iter(sorted(shared)))},
            )
        return None

    # ── 7: governance authorization ──────────────────────────────────────

    def _authorization_policy(self, sender_id: str, recipient_id: str):
        for sr in self._shared_societies(sender_id, recipient_id):
            governance = getattr(sr, "governance", None)
            if governance is None:
                continue
            try:
                allowed = governance.authorize(sender_id, resource=f"actor:{recipient_id}", action="communicate")
            except Exception:
                continue
            if allowed:
                return (
                    "governance policy authorizes communication",
                    {"society_id": sr.society.society_id},
                )
        return None

    # ── 8: delegated authority ───────────────────────────────────────────

    def _delegated_authority(self, sender_id: str, recipient_id: str):
        pr = self._planetary
        registry = getattr(pr, "delegation_registry", None)
        membership_registry = getattr(pr, "membership_registry", None)
        if registry is None or membership_registry is None:
            return None
        delegations = getattr(registry, "_delegations", {})
        for delegate_id, owner_id in (
            (sender_id, recipient_id),
            (recipient_id, sender_id),
        ):
            for delegation in delegations.values():
                if delegation.delegate_actor_id != delegate_id:
                    continue
                if not registry.is_valid(delegation.delegation_id):
                    continue
                membership = membership_registry.get_membership(delegation.membership_id)
                if membership is not None and membership.actor_id == owner_id:
                    return "delegated authority permits communication", {}
        return None

    # ── 9: inherited organizational relationship (one intermediate hop) ──

    def _inherited_relationship(self, sender_id: str, recipient_id: str):
        sender_affiliations = self._affiliations_for(sender_id)
        if sender_affiliations is None:
            return None
        for a in sender_affiliations.affiliated_entities(_CHAIN_RELATIONSHIP_TYPES):
            intermediate_id = a.target_id
            if not intermediate_id or intermediate_id in (sender_id, recipient_id):
                continue
            intermediate_affiliations = self._affiliations_for(intermediate_id)
            if self._negotiable(intermediate_affiliations, recipient_id):
                return "inherited organizational relationship permits communication", {}
        return None
