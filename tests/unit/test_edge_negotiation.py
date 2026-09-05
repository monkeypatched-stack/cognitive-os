"""Local negotiation tests (kernel/edge/negotiation.py) -- the central
invariant under test throughout: negotiation != authorization, and a
successful negotiation can never itself grant authority or substitute
for human approval."""
from __future__ import annotations

import time

import pytest

from src.monkey_brain.kernel.delegation import DelegationScope, issue_delegation
from src.monkey_brain.kernel.edge.negotiation import (
    Agreement,
    AgreementStatus,
    LocalNegotiationEngine,
    NegotiationError,
    NegotiationKind,
    agreement_is_executable_now,
)


@pytest.fixture()
def engine():
    return LocalNegotiationEngine()


class TestSuccessfulLocalNegotiation:
    def test_propose_then_accept_produces_a_ready_agreement(self, engine):
        proposed = engine.propose(
            kind=NegotiationKind.RESOURCE_REQUEST, initiator="robot-a", counterparty="robot-b",
            capability="ReserveDock", resource="dock-1", terms={"max_minutes": 10},
        )
        assert proposed.status == AgreementStatus.PROPOSED
        accepted = engine.accept(proposed)
        assert accepted.status == AgreementStatus.ACCEPTED
        ok, reason = agreement_is_executable_now(accepted)
        assert ok is True


class TestRejectedNegotiation:
    def test_reject_marks_agreement_rejected_and_not_executable(self, engine):
        proposed = engine.propose(
            kind=NegotiationKind.RESERVATION_NEGOTIATION, initiator="a", counterparty="b",
            capability="Reserve", resource="r1", terms={},
        )
        rejected = engine.reject(proposed, reason="counterparty declined")
        assert rejected.status == AgreementStatus.REJECTED
        ok, reason = agreement_is_executable_now(rejected)
        assert ok is False

    def test_expired_agreement_is_not_executable_even_if_accepted(self, engine):
        proposed = engine.propose(
            kind=NegotiationKind.RESOURCE_REQUEST, initiator="a", counterparty="b",
            capability="X", resource="r1", terms={}, ttl_seconds=0.01,
        )
        accepted = engine.accept(proposed)
        time.sleep(0.02)
        ok, reason = agreement_is_executable_now(accepted)
        assert ok is False
        assert "expired" in reason


class TestAttenuationConstraintsPreserved:
    def test_terms_within_delegated_constraints_are_accepted(self, engine):
        delegation = issue_delegation(
            issuer="A", delegate="robot-a", capabilities=("grocery.purchase",),
            scope=DelegationScope(resources=("order-1",), actions=("create",)),
            constraints={"max_amount": 100, "region": "IN"},
        )
        agreement = engine.propose(
            kind=NegotiationKind.DELEGATION_AWARE_NEGOTIATION, initiator="robot-a", counterparty="robot-b",
            capability="grocery.purchase", resource="order-1",
            terms={"max_amount": 50, "region": "IN"}, delegation=delegation,
        )
        assert agreement.delegation_id == delegation.delegation_id

    def test_terms_exceeding_delegated_constraints_are_rejected(self, engine):
        delegation = issue_delegation(
            issuer="A", delegate="robot-a", capabilities=("grocery.purchase",),
            constraints={"max_amount": 100},
        )
        with pytest.raises(NegotiationError):
            engine.propose(
                kind=NegotiationKind.DELEGATION_AWARE_NEGOTIATION, initiator="robot-a", counterparty="robot-b",
                capability="grocery.purchase", resource="order-1",
                terms={"max_amount": 500}, delegation=delegation,
            )

    def test_terms_widening_a_scoped_constraint_are_rejected(self, engine):
        delegation = issue_delegation(
            issuer="A", delegate="robot-a", capabilities=("grocery.purchase",),
            constraints={"region": "IN"},
        )
        with pytest.raises(NegotiationError):
            engine.propose(
                kind=NegotiationKind.DELEGATION_AWARE_NEGOTIATION, initiator="robot-a", counterparty="robot-b",
                capability="grocery.purchase", resource="order-1",
                terms={"region": "ANY"}, delegation=delegation,
            )


class TestNegotiationCannotGrantAuthority:
    def test_terms_cannot_carry_an_authorization_shaped_key(self, engine):
        for forbidden_key in ("authorized", "approved", "allow", "opa_allow", "approval_mode"):
            with pytest.raises(NegotiationError):
                engine.propose(
                    kind=NegotiationKind.CAPABILITY_NEGOTIATION, initiator="a", counterparty="b",
                    capability="X", resource="r1", terms={forbidden_key: True},
                )

    def test_agreement_object_has_no_execution_capability(self):
        """Structural guarantee, not just a runtime check: Agreement is a
        plain frozen dataclass with no method that could invoke a
        capability or call ensure_governed -- negotiation has no path to
        a side effect at all, so it literally cannot bypass governance."""
        agreement = Agreement()
        executable_members = [
            name for name in dir(agreement)
            if not name.startswith("_") and callable(getattr(agreement, name))
        ]
        assert executable_members == []


class TestNegotiationCannotSubstituteForHumanApproval:
    def test_an_accepted_agreement_is_not_itself_an_approval_artifact(self, engine):
        proposed = engine.propose(
            kind=NegotiationKind.EXECUTION_COMMITMENT, initiator="a", counterparty="b",
            capability="Payment", resource="order-1", terms={"amount": 10},
        )
        accepted = engine.accept(proposed)
        # An Agreement has no approval_mode/approving_principal fields at
        # all -- consuming it for execution still requires an independent
        # ensure_governed decision (local or central), which is where
        # HUMAN_APPROVAL_REQUIRED would actually be enforced.
        assert not hasattr(accepted, "approval_mode")
        assert not hasattr(accepted, "approving_principal")
