"""Runtime Approval Gate — wiring regression tests.

The AUTO_APPROVE / HUMAN_APPROVAL_REQUIRED / DENY machinery
(kernel/approval.py, kernel/security_boundary.py::run_governed_mutation)
already existed and was already unit-tested in isolation
(tests/unit/test_approval_gate_e2e.py, which calls run_governed_mutation
directly). What those tests could not catch: kernel/execute/capability_bus.py
and kernel/pipeline/action_executor.py — the real, narrow choke point every
capability dispatch (an actor's own plan step, or a delegated/asked
capability from another actor) actually goes through — call
security_boundary.py::ensure_governed(), not run_governed_mutation()
directly. ensure_governed() used to short-circuit straight to the raw
effect whenever insecure_dev_mode() was set, for every operation class, so
the approval pipeline these tests exist to prove was in the real dispatch
path was previously unreachable from it in exactly the environment this
repo runs in locally (COGNITIVEOS_ALLOW_INSECURE_DEV_MODE=true).

These tests exercise ensure_governed() itself (not run_governed_mutation
directly) under insecure-dev, the same OPA-response pass-through
GovernanceEngine now surfaces, and the NATS actor-inbox receiving side's
principal binding.
"""

from __future__ import annotations

import json
import time

import pytest

from src.monkey_brain.kernel.approval import (
    ApprovalMode,
    ApprovalSource,
    get_approval_store,
    prevent_self_approval,
    reset_approval_store,
)
from src.monkey_brain.kernel.security_boundary import (
    HumanApprovalRequired,
    SecurityBoundaryDenied,
    ensure_governed,
    reset_governed_pipeline_for_tests,
)
from src.monkey_brain.kernel.trusted_auth import (
    TrustedAuthEvidence,
    bind_trusted_auth,
    get_trusted_auth,
    unauthenticated_evidence,
)


def make_trusted_auth(principal_id: str, principal_type: str = "human"):
    return TrustedAuthEvidence(
        authenticated=True,
        token_valid=True,
        principal_id=principal_id,
        principal_type=principal_type,
        mfa_status="satisfied",
    )


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    reset_approval_store()
    reset_governed_pipeline_for_tests()
    monkeypatch.setenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", "true")
    bind_trusted_auth(make_trusted_auth("user:test"))
    yield
    reset_approval_store()
    reset_governed_pipeline_for_tests()


class TestEnsureGovernedNoLongerBypassesInsecureDev:
    """ensure_governed() is the real dispatch-path entrypoint
    (CapabilityBus.execute/ActionExecutor.execute) -- these prove the
    approval pipeline actually fires from IT, under insecure-dev, which is
    the exact environment this repo runs in locally."""

    @pytest.mark.asyncio
    async def test_auto_approve_creates_artifact_under_insecure_dev(self, monkeypatch):
        mutation_called = False

        async def mutate():
            nonlocal mutation_called
            mutation_called = True
            return {"result": "ok"}

        async def mock_authorize(action, resource, extra, *, verified_delegation=None):
            return {
                "allowed": True,
                "reason": "policy_permit",
                "approval_mode": "AUTO_APPROVE",
                "approval_source": "POLICY_AUTOMATIC",
                "risk_level": "LOW",
                "policy_rule": "default_allow",
                "requires_hitl": False,
            }

        monkeypatch.setattr("src.monkey_brain.kernel.security_boundary._authorize", mock_authorize)

        result = await ensure_governed("capability.ProductSelection", "milk", mutate)

        assert mutation_called, "AUTO_APPROVE must still execute the effect"
        assert result == {"result": "ok"}

        # Before the fix, insecure-dev skipped run_governed_mutation
        # entirely -- no artifact, no audit trail, nothing to inspect.
        # Now every SECURITY_CRITICAL capability call leaves a record.
        store = get_approval_store()
        artifacts = [a for a in store._artifacts.values() if a.target_operation == "capability.ProductSelection"]
        assert len(artifacts) == 1
        assert artifacts[0].approval_mode == ApprovalMode.AUTO_APPROVE
        assert artifacts[0].approval_source == ApprovalSource.POLICY_AUTOMATIC

    @pytest.mark.asyncio
    async def test_human_approval_required_blocks_even_under_insecure_dev(self, monkeypatch):
        """Previously unreachable: insecure-dev bypassed ensure_governed
        before HUMAN_APPROVAL_REQUIRED could ever be decided from the real
        capability-dispatch entrypoint. OPA_REQUIRED=true forces AUTHZ to
        actually run here (require_opa() honors it even under insecure-dev)
        -- a supported production_gates.py combination, not a workaround."""
        monkeypatch.setenv("OPA_REQUIRED", "true")
        mutation_called = False

        async def mutate():
            nonlocal mutation_called
            mutation_called = True
            return "should not execute"

        async def mock_authorize(action, resource, extra, *, verified_delegation=None):
            return {
                "allowed": True,
                "reason": "policy_requires_human_approval",
                "approval_mode": "HUMAN_APPROVAL_REQUIRED",
                "approval_source": "POLICY_AUTOMATIC",
                "risk_level": "MEDIUM",
                "policy_rule": "high_risk_action",
                "requires_hitl": True,
            }

        monkeypatch.setattr("src.monkey_brain.kernel.security_boundary._authorize", mock_authorize)
        bind_trusted_auth(make_trusted_auth("agent:processor", "service"))

        with pytest.raises(HumanApprovalRequired):
            await ensure_governed("capability.Payment", "order:1", mutate)

        assert not mutation_called, "HUMAN_APPROVAL_REQUIRED must never execute the effect"

    @pytest.mark.asyncio
    async def test_deny_blocks_even_under_insecure_dev(self, monkeypatch):
        monkeypatch.setenv("OPA_REQUIRED", "true")
        mutation_called = False

        async def mutate():
            nonlocal mutation_called
            mutation_called = True
            return "should not execute"

        async def mock_authorize(action, resource, extra, *, verified_delegation=None):
            return {
                "allowed": False,
                "reason": "policy_deny",
                "approval_mode": "DENY",
                "approval_source": "POLICY_AUTOMATIC",
                "risk_level": "CRITICAL",
                "policy_rule": "forbidden_action",
                "requires_hitl": False,
            }

        monkeypatch.setattr("src.monkey_brain.kernel.security_boundary._authorize", mock_authorize)

        with pytest.raises(SecurityBoundaryDenied):
            await ensure_governed("capability.DeleteAccount", "account:1", mutate)

        assert not mutation_called, "DENY must never execute the effect"

    @pytest.mark.asyncio
    async def test_read_only_and_proposal_operations_still_bypass_immediately(self):
        """Regression guard: the fix must not have widened the gate onto
        READ_ONLY/PROPOSAL_ONLY operations, which should still run without
        any authz/approval overhead."""
        calls = []

        async def mutate():
            calls.append(1)
            return "ok"

        result = await ensure_governed("get_status", "system", mutate)
        assert result == "ok"
        assert calls == [1]
        # No approval artifact should exist for a read-only operation.
        store = get_approval_store()
        assert not [a for a in store._artifacts.values() if a.target_resource == "system"]


class TestSelfApprovalPrevention:
    def test_prevent_self_approval_rejects_matching_principals(self):
        is_valid, reason = prevent_self_approval("agent:worker-7", "agent:worker-7")
        assert is_valid is False
        assert "self-approval" in reason

    def test_prevent_self_approval_allows_distinct_principals(self):
        is_valid, _ = prevent_self_approval("agent:worker-7", "user:admin")
        assert is_valid is True

    @pytest.mark.asyncio
    async def test_agent_cannot_approve_its_own_human_required_operation(self, monkeypatch):
        """Agent A requests a HUMAN_APPROVAL_REQUIRED operation; nothing in
        the runtime lets Agent A itself become the approving_principal.
        AUTO_APPROVE's approving_principal is always 'runtime:governance'
        (create_approval_artifact_from_policy) -- never the requester -- so
        the only way to reach APPROVED is POST /runtime-approvals/{id}/approve,
        which itself 403s when the caller's authenticated principal equals
        requesting_principal (api/routes/approval.py: self-approval check)."""

        async def mutate():
            return "should not execute"

        async def mock_authorize(action, resource, extra, *, verified_delegation=None):
            return {
                "allowed": True,
                "approval_mode": "HUMAN_APPROVAL_REQUIRED",
                "approval_source": "POLICY_AUTOMATIC",
                "risk_level": "HIGH",
                "policy_rule": "high_risk_action",
                "requires_hitl": True,
            }

        monkeypatch.setenv("OPA_REQUIRED", "true")
        monkeypatch.setattr("src.monkey_brain.kernel.security_boundary._authorize", mock_authorize)
        bind_trusted_auth(make_trusted_auth("agent:requester", "service"))

        with pytest.raises(HumanApprovalRequired) as exc_info:
            await ensure_governed("capability.TransferFunds", "account:9", mutate)

        artifact = get_approval_store().get(exc_info.value.approval_id)
        assert artifact.requesting_principal == "agent:requester"
        # No approving_principal exists yet -- it is set ONLY by the human
        # approval endpoint, and only to the AUTHENTICATED caller of that
        # endpoint, never to a value the requester itself can supply.
        assert artifact.approving_principal == ""


class TestGovernanceEngineSurfacesApprovalMode:
    """GovernanceEngine.evaluate() already read approval_mode/risk_level/
    policy_rule off the OPA response -- but the OPA client wrapper
    (evaluate_full) silently dropped every key except allowed/obligations/
    reason before it got there, so a Rego rule emitting approval_mode could
    never actually reach GovernanceEngine. These pin the pass-through and
    the pre-existing default-when-absent behavior side by side."""

    @pytest.mark.asyncio
    async def test_opa_supplied_approval_mode_is_surfaced(self, monkeypatch):
        from src.monkey_brain.kernel.governance import GovernanceEngine

        monkeypatch.setenv("OPA_URL", "http://opa.internal:8181")

        async def fake_evaluate_full(policy_path, input_data, *, default_allow=False, **kwargs):
            return {
                "allowed": True,
                "obligations": [],
                "reason": "",
                "source": "opa",
                "approval_mode": "HUMAN_APPROVAL_REQUIRED",
                "risk_level": "MEDIUM",
                "policy_rule": "high_risk_action",
                "requires_hitl": True,
            }

        monkeypatch.setattr("services.common.opa.evaluate_full", fake_evaluate_full)

        eng = GovernanceEngine()
        decision = await eng.evaluate("alice", "transfer_money", {})
        assert decision["allowed"] is True
        assert decision["approval_mode"] == "HUMAN_APPROVAL_REQUIRED"
        assert decision["risk_level"] == "MEDIUM"
        assert decision["requires_hitl"] is True

    @pytest.mark.asyncio
    async def test_missing_approval_mode_falls_back_exactly_as_before(self, monkeypatch):
        """Regression guard: a policy_path that has never defined
        approval_mode (every existing deployment today) must resolve
        exactly as it did before this change."""
        from src.monkey_brain.kernel.governance import GovernanceEngine

        monkeypatch.setenv("OPA_URL", "http://opa.internal:8181")

        async def fake_evaluate_full(policy_path, input_data, *, default_allow=False, **kwargs):
            return {"allowed": True, "obligations": [], "reason": "", "source": "opa"}

        monkeypatch.setattr("services.common.opa.evaluate_full", fake_evaluate_full)

        eng = GovernanceEngine()
        decision = await eng.evaluate("alice", "plan", {})
        assert decision["allowed"] is True
        assert decision["approval_mode"] == "AUTO_APPROVE"
        assert decision["risk_level"] == "LOW"
        assert decision["requires_hitl"] is False


class _FakeNatsClient:
    def __init__(self) -> None:
        self.subject = None
        self.callback = None

    async def subscribe(self, subject, cb):
        self.subject = subject
        self.callback = cb


class _FakeMsg:
    def __init__(self, payload: dict) -> None:
        self.data = json.dumps(payload).encode()
        self.reply = None


class TestActorInboxBindsRespondingActorIdentity:
    """kernel/domains/grocery.py::subscribe_actor_inbox's _on_message is
    the real receiving side of AskActor/DelegateTask -- the actual
    agent-to-agent transport. Before this fix it never bound a principal,
    so a governed capability it went on to execute ran under whatever
    TrustedAuthEvidence happened to be ambient in that asyncio context,
    not the responding actor's own identity."""

    @pytest.mark.asyncio
    async def test_on_message_binds_responding_actor_before_dispatch(self):
        from src.monkey_brain.kernel.domains.grocery import subscribe_actor_inbox

        class _FakePR:
            _nats_client = _FakeNatsClient()
            memory_manager = None

        pr = _FakePR()
        ok = await subscribe_actor_inbox(pr, "actor-b-123", "Actor B")
        assert ok is True
        assert pr._nats_client.callback is not None

        # Simulate the ambient context NOT already carrying an identity --
        # e.g. a NATS client callback running in a detached task.
        bind_trusted_auth(unauthenticated_evidence())
        assert get_trusted_auth().authenticated is False

        msg = _FakeMsg({"msg_type": "broadcast", "message": "hello"})
        await pr._nats_client.callback(msg)

        evidence = get_trusted_auth()
        assert evidence.authenticated is True
        assert evidence.principal_type == "service"
        assert evidence.principal_id == "actor-runtime:actor-b-123"
