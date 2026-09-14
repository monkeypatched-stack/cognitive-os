"""The Approval Decision Contract — kernel/approval.py::ApprovalDecision.

Defines and tests the canonical type sitting between trusted policy
evaluation and the runtime execution gate:

    authenticated request -> authorization -> OPA/governance policy
        -> ApprovalDecision -> ApprovalArtifact/HITL handoff
        -> runtime execution gate -> execution

This file does NOT wire ApprovalDecision into any runtime call site
(capability_bus.py, action_executor.py, grocery.py's AskActor/
DelegateTask, or any route handler) -- per this task's explicit scope,
that requires separate authorization. Every test here exercises the type
itself and its pure helper methods/constructors in isolation.
"""

from __future__ import annotations

import dataclasses
import time

import pytest

from src.monkey_brain.kernel.approval import (
    ApprovalDecision,
    ApprovalDecisionError,
    ApprovalMode,
)


def make_decision(**overrides) -> ApprovalDecision:
    fields = dict(
        mode=ApprovalMode.AUTO_APPROVE,
        operation_id="op-1",
        principal="user:alice",
        operation="capability.ProductSelection",
        scope="milk",
        policy_rule="default_allow",
        reason="",
        risk_level="LOW",
    )
    fields.update(overrides)
    return ApprovalDecision(**fields)


class TestCanonicalDecisionType:
    """Section 2/3: exactly three terminal modes, no ambiguous states."""

    def test_only_three_modes_exist(self):
        assert {m.value for m in ApprovalMode} == {
            "AUTO_APPROVE",
            "HUMAN_APPROVAL_REQUIRED",
            "DENY",
        }

    def test_mode_must_be_a_real_approval_mode_enum(self):
        with pytest.raises(ApprovalDecisionError):
            make_decision(mode="AUTO_APPROVE")  # a bare string, not the enum

    def test_missing_operation_id_rejected(self):
        with pytest.raises(ApprovalDecisionError):
            make_decision(operation_id="")

    def test_missing_principal_rejected(self):
        with pytest.raises(ApprovalDecisionError):
            make_decision(principal="")

    def test_missing_operation_rejected(self):
        with pytest.raises(ApprovalDecisionError):
            make_decision(operation="")

    def test_empty_scope_is_permitted(self):
        """Scope can legitimately be empty (an operation with no
        resource-level distinction) -- only identity fields are required."""
        make_decision(scope="")

    def test_has_no_execution_permitted_field(self):
        """Section 15: decision != execution permission. This field must
        never exist on the type -- its presence would invite exactly the
        conflation the contract forbids."""
        decision = make_decision()
        assert not hasattr(decision, "execution_permitted")
        assert "execution_permitted" not in decision.to_dict()

    def test_has_no_approve_or_grant_method(self):
        """Section 10/11: a decision cannot transition itself into a
        grant -- there must be no method that does so."""
        decision = make_decision(mode=ApprovalMode.HUMAN_APPROVAL_REQUIRED)
        assert not hasattr(decision, "approve")
        assert not hasattr(decision, "grant")


class TestImmutability:
    """Section 9: once created, a decision must not be mutable."""

    def test_frozen_dataclass_rejects_field_assignment(self):
        decision = make_decision()
        with pytest.raises(dataclasses.FrozenInstanceError):
            decision.mode = ApprovalMode.DENY  # type: ignore[misc]

    def test_deny_cannot_become_auto_approve_by_mutation(self):
        denied = make_decision(mode=ApprovalMode.DENY, risk_level="CRITICAL")
        with pytest.raises(dataclasses.FrozenInstanceError):
            denied.mode = ApprovalMode.AUTO_APPROVE  # type: ignore[misc]
        assert denied.mode == ApprovalMode.DENY

    def test_a_new_decision_requires_a_new_object(self):
        """The only way to get a different decision is a new policy
        evaluation producing a new object -- proven by construction, not
        by any transition method (none exists)."""
        first = make_decision(mode=ApprovalMode.DENY)
        second = make_decision(mode=ApprovalMode.AUTO_APPROVE)
        assert first is not second
        assert first.mode == ApprovalMode.DENY
        assert second.mode == ApprovalMode.AUTO_APPROVE


class TestDecisionVsArtifactSeparation:
    """Section 5/10/11: ApprovalDecision answers a different question than
    ApprovalArtifact, and HUMAN_APPROVAL_REQUIRED must never itself
    constitute a human approval."""

    def test_auto_approve_decision_is_not_an_approved_by_system_artifact(self):
        decision = make_decision(mode=ApprovalMode.AUTO_APPROVE)
        # The decision has no "approved_by" concept at all -- that belongs
        # to ApprovalArtifact (approving_principal), a separate object
        # this decision does not create by existing.
        assert not hasattr(decision, "approved_by")
        assert not hasattr(decision, "approving_principal")

    def test_human_approval_required_decision_is_not_itself_an_approval(self):
        decision = make_decision(mode=ApprovalMode.HUMAN_APPROVAL_REQUIRED)
        # Nothing about this object represents a granted human approval --
        # it is a requirement, not a grant. No field claims otherwise.
        assert decision.mode == ApprovalMode.HUMAN_APPROVAL_REQUIRED
        assert not hasattr(decision, "approved")
        assert not hasattr(decision, "human_approved")


class TestFromPolicyResultAdapter:
    """Section 27: from_policy_result() adapts the EXISTING
    GovernanceEngine.evaluate()/_authorize() dict shape -- it must not
    trust agent-reachable content inside that dict for identity fields."""

    def test_adapts_auto_approve_result(self):
        result = {
            "allowed": True,
            "reason": "policy_permit",
            "approval_mode": "AUTO_APPROVE",
            "risk_level": "LOW",
            "policy_rule": "default_allow",
            "requires_hitl": False,
        }
        decision = ApprovalDecision.from_policy_result(
            operation_id="op-42",
            principal="user:alice",
            operation="capability.ProductSelection",
            scope="milk",
            result=result,
        )
        assert decision.mode == ApprovalMode.AUTO_APPROVE
        assert decision.operation_id == "op-42"
        assert decision.principal == "user:alice"
        assert decision.risk_level == "LOW"
        assert decision.policy_rule == "default_allow"

    def test_adapts_human_approval_required_result(self):
        result = {
            "allowed": True,
            "reason": "policy_requires_human_approval",
            "approval_mode": "HUMAN_APPROVAL_REQUIRED",
            "risk_level": "MEDIUM",
            "policy_rule": "high_risk_action",
            "requires_hitl": True,
        }
        decision = ApprovalDecision.from_policy_result(
            operation_id="op-43",
            principal="agent:processor",
            operation="capability.Payment",
            scope="order:1",
            result=result,
        )
        assert decision.mode == ApprovalMode.HUMAN_APPROVAL_REQUIRED

    def test_adapts_deny_result(self):
        result = {
            "allowed": False,
            "reason": "runtime_blocked",
            "approval_mode": "DENY",
            "risk_level": "CRITICAL",
        }
        decision = ApprovalDecision.from_policy_result(
            operation_id="op-44",
            principal="user:mallory",
            operation="capability.DeleteAccount",
            scope="account:1",
            result=result,
        )
        assert decision.mode == ApprovalMode.DENY

    def test_missing_approval_mode_falls_back_to_allowed_flag(self):
        """A policy that has never defined approval_mode (today's default,
        pre-Rego-extension state) must still resolve deterministically."""
        allow_decision = ApprovalDecision.from_policy_result(
            operation_id="op-45",
            principal="user:alice",
            operation="plan",
            scope="",
            result={"allowed": True, "reason": ""},
        )
        deny_decision = ApprovalDecision.from_policy_result(
            operation_id="op-46",
            principal="user:alice",
            operation="plan",
            scope="",
            result={"allowed": False, "reason": "denied"},
        )
        assert allow_decision.mode == ApprovalMode.AUTO_APPROVE
        assert deny_decision.mode == ApprovalMode.DENY

    def test_agent_poisoned_result_dict_cannot_override_identity_fields(self):
        """Section 4/7: even if a result dict somehow carried
        attacker-shaped keys named like decision identity fields, they
        must never leak into the decision -- only the explicit keyword
        arguments a trusted caller supplies do."""
        poisoned_result = {
            "allowed": True,
            "approval_mode": "AUTO_APPROVE",
            # An agent cannot smuggle a different principal/operation_id
            # through the dict this adapter reads policy fields from.
            "principal": "user:root",
            "operation_id": "op-attacker-chosen",
            "operation": "capability.DeleteEverything",
        }
        decision = ApprovalDecision.from_policy_result(
            operation_id="op-real-47",
            principal="user:alice",
            operation="capability.ProductSelection",
            scope="milk",
            result=poisoned_result,
        )
        assert decision.operation_id == "op-real-47"
        assert decision.principal == "user:alice"
        assert decision.operation == "capability.ProductSelection"

    def test_policy_revision_absent_by_default_not_fabricated(self):
        decision = ApprovalDecision.from_policy_result(
            operation_id="op-48",
            principal="user:alice",
            operation="plan",
            scope="",
            result={"allowed": True},
        )
        assert decision.policy_revision == ""


class TestDenyConstructor:
    """Section 14: explicit fail-closed constructor for the failure matrix."""

    def test_deny_produces_deny_mode_and_critical_risk(self):
        decision = ApprovalDecision.deny(
            operation_id="op-50",
            principal="unknown",
            operation="capability.Payment",
            reason="authentication failure",
        )
        assert decision.mode == ApprovalMode.DENY
        assert decision.risk_level == "CRITICAL"
        assert decision.reason == "authentication failure"

    def test_deny_requires_explicit_principal_placeholder(self):
        with pytest.raises(ApprovalDecisionError):
            ApprovalDecision.deny(operation_id="op-51", principal="", operation="capability.Payment")


class TestScopePrincipalRequestBinding:
    """Sections 17/18/19/21: covers() enforces all four bindings at once;
    a decision for one request/principal/operation/scope never silently
    authorizes a materially different one."""

    def test_covers_exact_match(self):
        decision = make_decision(
            operation_id="op-1",
            principal="user:alice",
            operation="capability.Payment",
            scope="order:1",
        )
        assert decision.covers(
            operation_id="op-1",
            principal="user:alice",
            operation="capability.Payment",
            scope="order:1",
        )

    def test_does_not_cover_different_request(self):
        """Section 21 (replay): request A's decision must reject request B."""
        decision = make_decision(operation_id="op-1")
        assert not decision.covers(
            operation_id="op-2",
            principal="user:alice",
            operation="capability.ProductSelection",
        )

    def test_does_not_cover_different_principal(self):
        """Section 18: Agent B cannot reuse Agent A's decision."""
        decision = make_decision(operation_id="op-1", principal="agent:a")
        assert not decision.covers(
            operation_id="op-1",
            principal="agent:b",
            operation="capability.ProductSelection",
        )

    def test_does_not_cover_different_operation(self):
        decision = make_decision(operation_id="op-1", operation="capability.ProductSelection")
        assert not decision.covers(operation_id="op-1", principal="user:alice", operation="capability.Payment")

    def test_does_not_cover_expanded_scope(self):
        """Section 17: approved scope A must not authorize scope A + new
        capability, or an unrelated scope B."""
        decision = make_decision(operation_id="op-1", scope="read_customer_record")
        assert not decision.covers(
            operation_id="op-1",
            principal="user:alice",
            operation="capability.ProductSelection",
            scope="write_customer_record",
        )

    def test_material_change_requires_new_decision_not_reuse(self):
        """Section 17 end-to-end: simulate "the operation changed" by
        asserting the old decision cannot cover the new request; a new
        ApprovalDecision (a distinct object) is the only valid path
        forward -- there is no widen()/extend() method on this class."""
        original = make_decision(operation_id="op-1", scope="read_customer_record")
        assert not hasattr(original, "widen")
        assert not hasattr(original, "extend_scope")
        new_decision = make_decision(operation_id="op-2", scope="write_customer_record")
        assert new_decision is not original


class TestSerializationRoundTrip:
    """Section 22: canonical serialized form; round-trips exactly."""

    def test_to_dict_from_dict_round_trip(self):
        original = make_decision(mode=ApprovalMode.HUMAN_APPROVAL_REQUIRED, risk_level="HIGH")
        restored = ApprovalDecision.from_dict(original.to_dict())
        assert restored == original

    def test_serialized_form_has_no_signature_field_fabricated(self):
        """Section 22: this repo has no signed/authenticated envelope for
        approval-related objects to plug into (kernel/approval.py's
        ApprovalArtifact.signature field is present but never actually
        computed anywhere in this codebase -- confirmed by grep). Do not
        invent cryptographic signing here solely for appearance; the
        serialized form is honest about carrying none."""
        decision = make_decision()
        assert "signature" not in decision.to_dict()


class TestSecurityPropertyInvariants:
    """Section 26: regression tests proving what an agent can never do."""

    def test_agent_cannot_construct_auto_approve_by_only_asserting_allowed(self):
        """An agent-controlled result claiming allowed=True with no
        approval_mode and no operation_id/principal binding from a
        trusted caller still requires the CALLER to supply operation_id/
        principal explicitly -- there is no zero-argument or
        result-only constructor that could let a bare {"allowed": True}
        (or similar) become a decision without a trusted caller's
        explicit principal/operation_id."""
        import inspect

        sig = inspect.signature(ApprovalDecision.from_policy_result)
        required_kwonly = {
            name
            for name, p in sig.parameters.items()
            if p.kind is inspect.Parameter.KEYWORD_ONLY and p.default is inspect.Parameter.empty
        }
        assert {"operation_id", "principal", "operation", "scope"} <= required_kwonly

    def test_agent_cannot_convert_deny_to_auto_approve(self):
        denied = ApprovalDecision.deny(operation_id="op-60", principal="agent:x", operation="capability.Payment")
        with pytest.raises(dataclasses.FrozenInstanceError):
            denied.mode = ApprovalMode.AUTO_APPROVE  # type: ignore[misc]
        # The only way to get an AUTO_APPROVE "for op-60" is a fresh
        # decision from a fresh policy evaluation -- covers() proves the
        # original DENY can never be reinterpreted as authorizing it.
        assert denied.mode == ApprovalMode.DENY

    def test_agent_cannot_expand_scope_via_covers(self):
        decision = make_decision(scope="read_customer_record")
        assert not decision.covers(
            operation_id=decision.operation_id,
            principal=decision.principal,
            operation=decision.operation,
            scope="delete_customer_record",
        )

    def test_agent_cannot_substitute_another_principal(self):
        decision = make_decision(principal="agent:legit")
        assert not decision.covers(
            operation_id=decision.operation_id,
            principal="agent:impersonator",
            operation=decision.operation,
            scope=decision.scope,
        )

    def test_agent_cannot_replay_approval_against_another_request(self):
        decision = make_decision(operation_id="op-original")
        assert not decision.covers(
            operation_id="op-replayed",
            principal=decision.principal,
            operation=decision.operation,
            scope=decision.scope,
        )

    def test_deny_is_not_weakened_by_downstream_information(self):
        """Section 13/16: nothing downstream can transform DENY -- proven
        by the type having no method that takes additional context and
        returns a more permissive mode."""
        denied = ApprovalDecision.deny(operation_id="op-61", principal="agent:x", operation="capability.Payment")
        public_methods = [name for name in dir(denied) if not name.startswith("_") and callable(getattr(denied, name))]
        # covers() and to_dict() are read-only; nothing named like a
        # transition/upgrade exists.
        assert not any(name in ("approve", "upgrade", "escalate_to_auto", "override") for name in public_methods)


class TestFailureSemanticsMatrix:
    """Section 25's decision matrix, expressed as direct constructions of
    the decision each row implies. This does not exercise
    GovernanceEngine/security_boundary.py end-to-end (already covered by
    tests/security/test_governance_gate.py and
    tests/security/test_runtime_approval_gate_wiring.py); it pins that
    ApprovalDecision itself can represent every row correctly."""

    @pytest.mark.parametrize(
        "auth_valid,policy_result,expected_mode",
        [
            # valid auth, automatic policy, no human approval needed
            (
                True,
                {"allowed": True, "approval_mode": "AUTO_APPROVE"},
                ApprovalMode.AUTO_APPROVE,
            ),
            # valid auth, HITL policy, no human approval yet
            (
                True,
                {"allowed": True, "approval_mode": "HUMAN_APPROVAL_REQUIRED"},
                ApprovalMode.HUMAN_APPROVAL_REQUIRED,
            ),
            # valid auth, policy denies
            (True, {"allowed": False, "approval_mode": "DENY"}, ApprovalMode.DENY),
        ],
    )
    def test_authenticated_rows(self, auth_valid, policy_result, expected_mode):
        decision = ApprovalDecision.from_policy_result(
            operation_id="op-70",
            principal="user:alice",
            operation="capability.Payment",
            scope="order:1",
            result=policy_result,
        )
        assert decision.mode is expected_mode

    def test_invalid_authentication_row_resolves_to_deny(self):
        """invalid auth + automatic policy -> DENY, never AUTO_APPROVE."""
        decision = ApprovalDecision.deny(
            operation_id="op-71",
            principal="unknown",
            operation="capability.Payment",
            reason="authentication invalid",
        )
        assert decision.mode == ApprovalMode.DENY

    def test_opa_unavailable_row_resolves_to_deny(self):
        decision = ApprovalDecision.deny(
            operation_id="op-72",
            principal="user:alice",
            operation="capability.Payment",
            reason="opa_unavailable",
            policy_rule="opa_unavailable",
        )
        assert decision.mode == ApprovalMode.DENY
        assert decision.risk_level == "CRITICAL"

    def test_policy_error_row_resolves_to_deny(self):
        decision = ApprovalDecision.deny(
            operation_id="op-73",
            principal="user:alice",
            operation="capability.Payment",
            reason="policy_evaluation_error",
        )
        assert decision.mode == ApprovalMode.DENY

    def test_fake_agent_approval_does_not_change_automatic_decision(self):
        """valid auth + automatic policy + a fake/claimed agent approval
        embedded in context must still resolve purely from the policy
        result -- from_policy_result has no parameter for "agent-claimed
        approval" at all, so there is nothing for such a claim to attach
        to."""
        import inspect

        assert "agent_approval" not in inspect.signature(ApprovalDecision.from_policy_result).parameters
        assert "claimed_approval" not in inspect.signature(ApprovalDecision.from_policy_result).parameters
        decision = ApprovalDecision.from_policy_result(
            operation_id="op-74",
            principal="agent:x",
            operation="capability.Payment",
            scope="order:1",
            result={"allowed": True, "approval_mode": "AUTO_APPROVE"},
        )
        assert decision.mode == ApprovalMode.AUTO_APPROVE  # from POLICY, not from any agent claim

    def test_agent_claims_approval_under_hitl_does_not_change_mode(self):
        """valid auth + HITL policy + agent claims approval -> still
        HUMAN_APPROVAL_REQUIRED. Same reasoning as above: there is no
        input path for an agent's claim to reach the decision at all."""
        decision = ApprovalDecision.from_policy_result(
            operation_id="op-75",
            principal="agent:x",
            operation="capability.Payment",
            scope="order:1",
            result={"allowed": True, "approval_mode": "HUMAN_APPROVAL_REQUIRED"},
        )
        assert decision.mode == ApprovalMode.HUMAN_APPROVAL_REQUIRED

    def test_audit_unavailable_does_not_appear_on_the_decision_at_all(self):
        """Final matrix row: 'valid auth + automatic policy + audit
        unavailable -> AUTO_APPROVE diagnostically, not executable.' This
        class has no audit-status field and no execution_permitted field
        -- by design (Section 14/15), audit failure is purely an EXECUTION
        GATE concern (kernel/security_boundary.py::run_governed_mutation's
        own AUDIT_INTENT stage, unchanged by this task), never encoded on
        the decision. Proven by absence."""
        decision = ApprovalDecision.from_policy_result(
            operation_id="op-76",
            principal="user:alice",
            operation="capability.Payment",
            scope="order:1",
            result={"allowed": True, "approval_mode": "AUTO_APPROVE"},
        )
        assert not hasattr(decision, "audit_status")
        assert not hasattr(decision, "execution_permitted")
        assert decision.mode == ApprovalMode.AUTO_APPROVE  # diagnostically valid regardless


class TestNonceOperationIdDiscoveryFinding:
    """Not a defect in ApprovalDecision itself -- a discovery finding this
    contract's own `operation_id` documentation surfaces: kernel/
    security_boundary.py::ensure_governed()/run_governed_mutation() do not
    currently thread a caller-supplied, request-stable operation_id in
    from CapabilityBus.execute()/ActionExecutor.execute() -- each such
    call falls back to `new_operation_id()`, a fresh uuid4() nonce, on
    EVERY invocation (kernel/security_operation.py:210). Two calls for
    what is semantically "the same request" (e.g. a network-level retry
    of one delegated NATS message) therefore do NOT share an
    operation_id/nonce today, so the existing idempotency machinery
    (kernel/api/idempotency.py's Idempotency-Key + SecurityOperation
    ledger, both keyed by operation_id) cannot recognize them as
    duplicates at that layer. Fixing this means threading a stable id
    into ensure_governed()'s callers -- exactly the "wiring into agent
    execution / plan executor" this task explicitly defers pending
    separate authorization (Section 28) -- so this is documented here,
    not patched."""

    def test_new_operation_id_is_not_stable_across_calls(self):
        from src.monkey_brain.kernel.security_operation import new_operation_id

        first = new_operation_id()
        second = new_operation_id()
        assert first != second, (
            "new_operation_id() intentionally mints a fresh nonce per call -- "
            "callers that need idempotent retries MUST supply their own stable "
            "operation_id explicitly; nothing generates one for them today."
        )

    def test_decision_operation_id_must_be_caller_supplied_not_synthesized(self):
        """ApprovalDecision itself never calls new_operation_id() or
        generates any identifier on the caller's behalf -- operation_id
        has no default_factory, unlike e.g. ApprovalArtifact.approval_id.
        This is deliberate: silently defaulting it here would let exactly
        the nonce-instability above hide behind this contract instead of
        being visible at the real call site that needs to fix it."""
        import dataclasses as _dc

        field = next(f for f in _dc.fields(ApprovalDecision) if f.name == "operation_id")
        assert field.default is _dc.MISSING
        assert field.default_factory is _dc.MISSING  # type: ignore[misc]
