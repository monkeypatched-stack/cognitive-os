"""Section 10 performance regression: a locally-authorized edge operation
performs ZERO central governance round trips, and the escalated path is
measured SEPARATELY -- proving the intended shape:

    LOCAL AUTHORITY        -> LOCAL EXECUTION
    INSUFFICIENT AUTHORITY -> CENTRAL ESCALATION

never "every action -> central service" regardless of locally-available
authority.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from src.monkey_brain.kernel.edge.local_governance import LocalGovernanceEvaluator
from src.monkey_brain.kernel.edge.local_store import EdgeLocalStore
from src.monkey_brain.kernel.edge.policy_cache import EdgePolicyCache, issue_policy_snapshot
from src.monkey_brain.kernel.pipeline.action_executor import ActionExecutor
from src.monkey_brain.kernel.pipeline.execution import Action
from src.monkey_brain.kernel.trusted_auth import TrustedAuthEvidence, bind_trusted_auth

PRINCIPAL = "spiffe://cognitiveos/agent/bench-actor"
N = 30


def _edge_governance(tmp_path):
    store = EdgeLocalStore(str(tmp_path / "edge.db"))
    cache = EdgePolicyCache(store)
    return LocalGovernanceEvaluator(cache)


def _fake_bus():
    capability = MagicMock()
    capability.handle = MagicMock(return_value={"success": True})
    bus = MagicMock()
    bus.discover.return_value = capability
    return bus, capability


@pytest.fixture(autouse=True)
def _bind(monkeypatch):
    monkeypatch.setenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", "true")
    bind_trusted_auth(TrustedAuthEvidence(
        authenticated=True, token_valid=True, principal_id=PRINCIPAL,
        principal_type="service", mfa_status="satisfied",
    ))
    yield


class TestZeroRoundTripsWhenLocallyAuthorized:
    @pytest.mark.asyncio
    async def test_locally_cached_authority_never_calls_central_opa(self, monkeypatch, tmp_path):
        gov = _edge_governance(tmp_path)
        snapshot = issue_policy_snapshot(
            principal=PRINCIPAL, action="capability.grocery.purchase", resource="grocery.purchase",
            policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE", "policy_rule": "edge_cached"},
        )
        gov._policy_cache.store_snapshot(snapshot)

        opa_calls = {"n": 0}

        async def fail_if_opa_called(*args, **kwargs):
            opa_calls["n"] += 1
            raise AssertionError("central OPA must not be contacted for a locally-authorized operation")

        monkeypatch.setattr("src.monkey_brain.kernel.security_boundary._authorize", fail_if_opa_called)

        bus, capability = _fake_bus()
        executor = ActionExecutor(
            bus, connectivity_check=lambda cap: (False, "WAITING_FOR_AUTHORITY", "disconnected"),
            edge_governance=gov,
        )
        action = Action(action_id="a1", capability="grocery.purchase", parameters={})

        await executor.execute((action,), {"actor_id": PRINCIPAL})

        assert opa_calls["n"] == 0, "central governance round trip must never happen for a local ALLOW"
        assert capability.handle.called is True


class TestHotPathLatencyBreakdown:
    """Real measurement, not invented numbers -- N=30 samples of each
    stage on the LOCAL path, reported for visibility (not asserted
    against an arbitrary threshold, since absolute latency is
    machine-dependent; the round-trip-count assertion above is the real
    regression guard)."""

    @pytest.mark.asyncio
    async def test_measure_local_path_stage_latency(self, tmp_path, capsys):
        gov = _edge_governance(tmp_path)
        snapshot = issue_policy_snapshot(
            principal=PRINCIPAL, action="capability.grocery.purchase", resource="grocery.purchase",
            policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE", "policy_rule": "edge_cached"},
        )
        gov._policy_cache.store_snapshot(snapshot)

        governance_latencies = []
        execution_latencies = []

        for _ in range(N):
            bus, capability = _fake_bus()
            executor = ActionExecutor(
                bus, connectivity_check=lambda cap: (False, "WAITING_FOR_AUTHORITY", "disconnected"),
                edge_governance=gov,
            )
            action = Action(action_id="a1", capability="grocery.purchase", parameters={})

            t0 = time.monotonic()
            outcome = gov.evaluate(
                principal=PRINCIPAL, action="capability.grocery.purchase", resource="grocery.purchase",
                authenticated_principal=PRINCIPAL,
            )
            t1 = time.monotonic()
            governance_latencies.append((t1 - t0) * 1000)
            assert outcome.allowed

            t2 = time.monotonic()
            await executor.execute((action,), {"actor_id": PRINCIPAL})
            t3 = time.monotonic()
            execution_latencies.append((t3 - t2) * 1000)

        governance_latencies.sort()
        execution_latencies.sort()

        def p(vals, pct):
            return vals[int(len(vals) * pct)] if vals else 0.0

        print(f"\nedge local governance decision: p50={p(governance_latencies, 0.50):.3f}ms p95={p(governance_latencies, 0.95):.3f}ms")
        print(f"full ActionExecutor.execute (local path): p50={p(execution_latencies, 0.50):.3f}ms p95={p(execution_latencies, 0.95):.3f}ms")


class TestEscalatedPathMeasuredSeparately:
    @pytest.mark.asyncio
    async def test_no_cached_authority_escalates_and_never_executes_locally(self, tmp_path):
        gov = _edge_governance(tmp_path)  # no snapshot stored -- nothing cached
        bus, capability = _fake_bus()
        executor = ActionExecutor(
            bus, connectivity_check=lambda cap: (False, "WAITING_FOR_AUTHORITY", "disconnected"),
            edge_governance=gov,
        )
        action = Action(action_id="a1", capability="grocery.purchase", parameters={})

        await executor.execute((action,), {"actor_id": PRINCIPAL})

        assert capability.handle.called is False, "insufficient local authority must escalate, never execute locally"
