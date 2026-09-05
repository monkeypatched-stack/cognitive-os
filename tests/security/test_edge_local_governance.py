"""Edge-local state and governance layer — end-to-end tests against the
REAL ActionExecutor (not mocked), proving:

1. A locally-authorized operation executes WITHOUT a central governance
   round trip (the live _authorize()/OPA call is never reached).
2. An operation requiring fresh authority the edge cannot establish
   locally correctly ESCALATES (refused, capability never invoked) --
   never guessed into an ALLOW.
3. Local execution preserves the same security invariants
   kernel/security_boundary.py::ensure_governed already enforces
   centrally: DENY blocks, HUMAN_APPROVAL_REQUIRED blocks, only a fresh
   verified AUTO_APPROVE snapshot permits capability.handle() to run.
4. Offline/partition behavior never silently reduces security: unknown
   authorization, expired authority, and missing human approval all fail
   closed regardless of network state.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.monkey_brain.kernel.approval import reset_approval_store
from src.monkey_brain.kernel.edge.local_governance import GovernanceOrigin, LocalGovernanceEvaluator
from src.monkey_brain.kernel.edge.local_store import EdgeLocalStore
from src.monkey_brain.kernel.edge.policy_cache import EdgePolicyCache, issue_policy_snapshot
from src.monkey_brain.kernel.pipeline.action_executor import ActionExecutor
from src.monkey_brain.kernel.pipeline.execution import Action
from src.monkey_brain.kernel.security_boundary import reset_governed_pipeline_for_tests
from src.monkey_brain.kernel.trusted_auth import TrustedAuthEvidence, bind_trusted_auth

PRINCIPAL = "spiffe://cognitiveos/agent/edge-robot-1"


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    reset_approval_store()
    reset_governed_pipeline_for_tests()
    monkeypatch.setenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", "true")
    # Deliberately NOT setting OPA_REQUIRED=true here (unlike other
    # governance test files in this repo): these tests model a
    # DISCONNECTED edge node, where the outer, generic per-batch
    # ensure_governed("action_executor.execute", "actions", ...) call
    # (kernel/pipeline/action_executor.py's own pre-existing wrapper,
    # unrelated to any specific capability) must gracefully skip the live
    # OPA round trip via the SAME insecure_dev_mode relaxation it already
    # honors, exactly as it would on a real edge node with no reachable
    # OPA. The per-capability decision under test is made entirely by
    # local_policy_decision, which takes priority over that relaxation
    # regardless of this setting (see _authorize_and_gate).
    bind_trusted_auth(TrustedAuthEvidence(
        authenticated=True, token_valid=True, principal_id=PRINCIPAL,
        principal_type="service", mfa_status="satisfied",
    ))
    yield
    reset_approval_store()
    reset_governed_pipeline_for_tests()


def _refusing_connectivity_check(capability_name: str):
    """Simulates offline_safety.py's own connectivity gate having already
    decided this capability cannot proceed centrally (DISCONNECTED) --
    the exact condition under which edge governance is meant to be
    consulted at all."""
    return False, "WAITING_FOR_AUTHORITY", f"{capability_name} requires authority but this node is disconnected"


def _fake_bus_and_capability():
    capability = MagicMock()
    capability.handle = MagicMock(return_value={"success": True, "result": "done"})
    bus = MagicMock()
    bus.discover.return_value = capability
    return bus, capability


def _edge_governance(tmp_path):
    store = EdgeLocalStore(str(tmp_path / "edge.db"))
    cache = EdgePolicyCache(store)
    gov = LocalGovernanceEvaluator(cache)
    return store, cache, gov


class TestLocalAllowRequiresNoCentralRoundTrip:
    @pytest.mark.asyncio
    async def test_valid_cached_authority_executes_without_calling_opa(self, monkeypatch, tmp_path):
        store, cache, gov = _edge_governance(tmp_path)
        snapshot = issue_policy_snapshot(
            principal=PRINCIPAL, action="capability.ReserveDock", resource="ReserveDock",
            policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE", "policy_rule": "edge_cached"},
        )
        cache.store_snapshot(snapshot)

        opa_calls = {"n": 0}

        async def fail_if_opa_called(action, resource, extra, *, verified_delegation=None):
            opa_calls["n"] += 1
            raise AssertionError("central OPA must not be contacted for a locally-authorized operation")

        monkeypatch.setattr("src.monkey_brain.kernel.security_boundary._authorize", fail_if_opa_called)

        bus, capability = _fake_bus_and_capability()
        executor = ActionExecutor(
            capability_bus=bus, connectivity_check=_refusing_connectivity_check, edge_governance=gov,
        )
        action = Action(action_id="a1", capability="ReserveDock", step_index=0)

        result = await executor.execute((action,))

        assert opa_calls["n"] == 0, "central governance round trip must never happen for a local ALLOW"
        assert capability.handle.called is True
        assert result.actions[0].success is True
        store.close()


class TestEscalationWhenFreshAuthorityRequired:
    @pytest.mark.asyncio
    async def test_no_cached_authority_escalates_and_refuses_without_guessing(self, tmp_path):
        store, cache, gov = _edge_governance(tmp_path)
        bus, capability = _fake_bus_and_capability()
        executor = ActionExecutor(
            capability_bus=bus, connectivity_check=_refusing_connectivity_check, edge_governance=gov,
        )
        action = Action(action_id="a1", capability="Payment", step_index=0)

        result = await executor.execute((action,))

        assert capability.handle.called is False, "no confident local decision exists -- must escalate, never guess"
        assert result.actions[0].success is False
        assert result.actions[0].result.get("governance_origin") != GovernanceOrigin.LOCAL.value
        store.close()

    @pytest.mark.asyncio
    async def test_no_edge_governance_wired_preserves_unconditional_refusal(self, tmp_path):
        """A cloud/non-edge ActionExecutor (edge_governance=None, the
        default) must behave EXACTLY as before this feature existed."""
        bus, capability = _fake_bus_and_capability()
        executor = ActionExecutor(capability_bus=bus, connectivity_check=_refusing_connectivity_check)
        action = Action(action_id="a1", capability="Payment", step_index=0)

        result = await executor.execute((action,))

        assert capability.handle.called is False
        assert result.actions[0].success is False
        assert result.actions[0].result.get("waiting_state") == "WAITING_FOR_AUTHORITY"


class TestLocalExecutionPreservesEnsureGovernedInvariants:
    @pytest.mark.asyncio
    async def test_cached_deny_blocks_execution_locally(self, tmp_path):
        store, cache, gov = _edge_governance(tmp_path)
        snapshot = issue_policy_snapshot(
            principal=PRINCIPAL, action="capability.Delete", resource="Delete",
            policy_decision={"allowed": False, "approval_mode": "DENY", "policy_rule": "blocked"},
        )
        cache.store_snapshot(snapshot)

        bus, capability = _fake_bus_and_capability()
        executor = ActionExecutor(
            capability_bus=bus, connectivity_check=_refusing_connectivity_check, edge_governance=gov,
        )
        action = Action(action_id="a1", capability="Delete", step_index=0)

        result = await executor.execute((action,))

        assert capability.handle.called is False
        assert result.actions[0].success is False
        store.close()

    @pytest.mark.asyncio
    async def test_cached_human_approval_required_cannot_be_bypassed_offline(self, tmp_path):
        """The single most important offline invariant: a cached
        HUMAN_APPROVAL_REQUIRED snapshot, however fresh and valid, can
        NEVER be locally satisfied -- it must escalate every time."""
        store, cache, gov = _edge_governance(tmp_path)
        snapshot = issue_policy_snapshot(
            principal=PRINCIPAL, action="capability.Refund", resource="Refund",
            policy_decision={"allowed": True, "approval_mode": "HUMAN_APPROVAL_REQUIRED", "policy_rule": "needs_human"},
        )
        cache.store_snapshot(snapshot)

        bus, capability = _fake_bus_and_capability()
        executor = ActionExecutor(
            capability_bus=bus, connectivity_check=_refusing_connectivity_check, edge_governance=gov,
        )
        action = Action(action_id="a1", capability="Refund", step_index=0)

        result = await executor.execute((action,))

        assert capability.handle.called is False, "HITL can never be satisfied by a cached snapshot alone"
        assert result.actions[0].success is False
        store.close()

    @pytest.mark.asyncio
    async def test_wrong_principal_presenting_cached_snapshot_is_refused(self, tmp_path):
        store, cache, gov = _edge_governance(tmp_path)
        snapshot = issue_policy_snapshot(
            principal="someone-else", action="capability.ReserveDock", resource="ReserveDock",
            policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE"},
        )
        cache.store_snapshot(snapshot)

        bus, capability = _fake_bus_and_capability()
        executor = ActionExecutor(
            capability_bus=bus, connectivity_check=_refusing_connectivity_check, edge_governance=gov,
        )
        action = Action(action_id="a1", capability="ReserveDock", step_index=0)

        result = await executor.execute((action,))

        assert capability.handle.called is False
        store.close()

    @pytest.mark.asyncio
    async def test_expired_cached_authority_blocks_operation(self, tmp_path):
        store, cache, gov = _edge_governance(tmp_path)
        snapshot = issue_policy_snapshot(
            principal=PRINCIPAL, action="capability.ReserveDock", resource="ReserveDock",
            policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE"}, ttl_seconds=0.01,
        )
        cache.store_snapshot(snapshot)
        import time
        time.sleep(0.02)

        bus, capability = _fake_bus_and_capability()
        executor = ActionExecutor(
            capability_bus=bus, connectivity_check=_refusing_connectivity_check, edge_governance=gov,
        )
        action = Action(action_id="a1", capability="ReserveDock", step_index=0)

        result = await executor.execute((action,))

        assert capability.handle.called is False, "expired authority must not be silently used"
        store.close()

    @pytest.mark.asyncio
    async def test_no_second_execution_path_bypasses_governance(self, monkeypatch, tmp_path):
        """Structural check: even with edge_governance wired in and
        connectivity refusing, the ONLY way capability.handle() gets
        called is through ensure_governed -- never a parallel edge-only
        dispatch path."""
        store, cache, gov = _edge_governance(tmp_path)
        snapshot = issue_policy_snapshot(
            principal=PRINCIPAL, action="capability.ReserveDock", resource="ReserveDock",
            policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE"},
        )
        cache.store_snapshot(snapshot)

        import src.monkey_brain.kernel.security_boundary as sb
        real_ensure_governed = sb.ensure_governed
        calls = []

        async def _spy(action, resource, effect, **kwargs):
            calls.append((action, resource, kwargs.get("local_policy_decision") is not None))
            return await real_ensure_governed(action, resource, effect, **kwargs)

        monkeypatch.setattr(sb, "ensure_governed", _spy)

        bus, capability = _fake_bus_and_capability()
        executor = ActionExecutor(
            capability_bus=bus, connectivity_check=_refusing_connectivity_check, edge_governance=gov,
        )
        action = Action(action_id="a1", capability="ReserveDock", step_index=0)
        result = await executor.execute((action,))

        assert ("capability.ReserveDock", "ReserveDock", True) in calls
        assert capability.handle.called is True
        assert result.actions[0].success is True
        store.close()


class TestNetworkFailureDoesNotCauseUnsafeFallback:
    @pytest.mark.asyncio
    async def test_edge_governance_raising_never_falls_back_to_allow(self, tmp_path):
        """If the local governance evaluator itself errors (e.g. a
        corrupt local DB row), ActionExecutor must not interpret that as
        permission to proceed -- Action must still fail closed."""
        store, cache, gov = _edge_governance(tmp_path)

        class _BrokenGovernance:
            def evaluate(self, **kwargs):
                raise RuntimeError("local store corrupted")

        bus, capability = _fake_bus_and_capability()
        executor = ActionExecutor(
            capability_bus=bus, connectivity_check=_refusing_connectivity_check,
            edge_governance=_BrokenGovernance(),
        )
        action = Action(action_id="a1", capability="Payment", step_index=0)

        with pytest.raises(RuntimeError):
            await executor.execute((action,))
        assert capability.handle.called is False
        store.close()
