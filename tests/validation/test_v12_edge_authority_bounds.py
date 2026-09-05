"""Systems Validation Suite — Section 24: edge authority bounds.

Real, non-aspirational implementation confirmed to exist (not just
architecturally described): kernel/edge/policy_cache.py::
SignedPolicySnapshot (Ed25519-signed, scope=principal+action+resource+
audience, time-bounded expires_at, authority_epoch for revocation),
kernel/edge/local_governance.py::LocalGovernanceEvaluator (consulted
only when the control plane is unreachable). This file proves the
bound experimentally: issue a centrally-valid, scope+expiry-bounded
snapshot, "disconnect" (no live control-plane call available), and
confirm the edge can act ONLY within that exact scope/expiry, and that
a later central epoch bump (simulating revocation) invalidates it on
reconnect.
"""
from __future__ import annotations

import time

from src.monkey_brain.kernel.edge.local_governance import GovernanceOrigin, LocalGovernanceEvaluator
from src.monkey_brain.kernel.edge.local_store import EdgeLocalStore
from src.monkey_brain.kernel.edge.policy_cache import EdgePolicyCache, issue_policy_snapshot


def _cache(tmp_path):
    store = EdgeLocalStore(str(tmp_path / "edge.sqlite3"))
    return EdgePolicyCache(store)


class TestEdgeOperatesOnlyWithinIssuedScopeAndExpiry:
    def test_in_scope_in_expiry_operation_is_allowed_while_disconnected(self, tmp_path):
        cache = _cache(tmp_path)
        snapshot = issue_policy_snapshot(
            principal="agent:edge-1", action="capability.grocery.purchase", resource="order-1",
            policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE"},
            ttl_seconds=300, authority_epoch=1,
        )
        cache.store_snapshot(snapshot)
        evaluator = LocalGovernanceEvaluator(cache, current_authority_epoch_fn=lambda: 1)

        outcome = evaluator.evaluate(
            principal="agent:edge-1", action="capability.grocery.purchase", resource="order-1",
            authenticated_principal="agent:edge-1",
        )
        assert outcome.allowed is True
        assert outcome.origin == GovernanceOrigin.LOCAL

    def test_a_different_resource_outside_scope_is_denied_not_locally_widened(self, tmp_path):
        cache = _cache(tmp_path)
        snapshot = issue_policy_snapshot(
            principal="agent:edge-1", action="capability.grocery.purchase", resource="order-1",
            policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE"},
            ttl_seconds=300, authority_epoch=1,
        )
        cache.store_snapshot(snapshot)
        evaluator = LocalGovernanceEvaluator(cache, current_authority_epoch_fn=lambda: 1)

        outcome = evaluator.evaluate(
            principal="agent:edge-1", action="capability.grocery.purchase", resource="order-2",  # scope+1
            authenticated_principal="agent:edge-1",
        )
        assert outcome.allowed is False
        assert outcome.escalate is True, "an out-of-scope request must escalate to the control plane, never be locally denied-as-if-decided nor locally allowed"

    def test_a_different_action_outside_scope_is_denied(self, tmp_path):
        cache = _cache(tmp_path)
        snapshot = issue_policy_snapshot(
            principal="agent:edge-1", action="capability.grocery.purchase", resource="order-1",
            policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE"},
            ttl_seconds=300, authority_epoch=1,
        )
        cache.store_snapshot(snapshot)
        evaluator = LocalGovernanceEvaluator(cache, current_authority_epoch_fn=lambda: 1)

        outcome = evaluator.evaluate(
            principal="agent:edge-1", action="capability.bank.transfer", resource="order-1",  # action+1
            authenticated_principal="agent:edge-1",
        )
        assert outcome.allowed is False
        assert outcome.escalate is True

    def test_past_expiry_is_denied_not_extended(self, tmp_path):
        cache = _cache(tmp_path)
        snapshot = issue_policy_snapshot(
            principal="agent:edge-1", action="capability.grocery.purchase", resource="order-1",
            policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE"},
            ttl_seconds=1, authority_epoch=1,  # expiry+1 attempted below
        )
        cache.store_snapshot(snapshot)
        evaluator = LocalGovernanceEvaluator(cache, current_authority_epoch_fn=lambda: 1)

        # "attempt expiry T+1": ask for a decision well past the snapshot's
        # own expires_at while still fully disconnected.
        time.sleep(1.2)
        outcome = evaluator.evaluate(
            principal="agent:edge-1", action="capability.grocery.purchase", resource="order-1",
            authenticated_principal="agent:edge-1",
        )
        assert outcome.allowed is False
        assert outcome.escalate is True

    def test_a_different_authenticated_principal_cannot_reuse_someone_elses_snapshot(self, tmp_path):
        """Scope includes the principal the snapshot was computed for --
        a different (even honestly-authenticated) principal must not
        borrow it."""
        cache = _cache(tmp_path)
        snapshot = issue_policy_snapshot(
            principal="agent:edge-1", action="capability.grocery.purchase", resource="order-1",
            policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE"},
            ttl_seconds=300, authority_epoch=1,
        )
        cache.store_snapshot(snapshot)
        evaluator = LocalGovernanceEvaluator(cache, current_authority_epoch_fn=lambda: 1)

        outcome = evaluator.evaluate(
            principal="agent:edge-1", action="capability.grocery.purchase", resource="order-1",
            authenticated_principal="agent:edge-2",  # different real caller
        )
        assert outcome.allowed is False


class TestCentralRevocationInvalidatesCachedAuthorityOnReconnect:
    def test_epoch_bump_after_issuance_invalidates_the_snapshot_even_though_it_has_not_expired(self, tmp_path):
        """Simulates: centrally revoke (bumps authority_epoch) while the
        edge is disconnected -> edge reconnects (its
        current_authority_epoch_fn now reflects the new epoch) -> the
        previously-cached, still-unexpired snapshot must no longer be
        trusted, because a later epoch means the control plane's picture
        of authority has moved on since this snapshot was issued."""
        cache = _cache(tmp_path)
        snapshot = issue_policy_snapshot(
            principal="agent:edge-1", action="capability.grocery.purchase", resource="order-1",
            policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE"},
            ttl_seconds=300, authority_epoch=1,
        )
        cache.store_snapshot(snapshot)

        # While disconnected, at the SAME epoch it was issued under: allowed.
        still_disconnected = LocalGovernanceEvaluator(cache, current_authority_epoch_fn=lambda: 1)
        assert still_disconnected.evaluate(
            principal="agent:edge-1", action="capability.grocery.purchase", resource="order-1",
            authenticated_principal="agent:edge-1",
        ).allowed is True

        # Reconnect: the runtime's own last-synced epoch has advanced
        # past what this snapshot was issued under (a real revocation
        # happened centrally in the meantime).
        after_reconnect = LocalGovernanceEvaluator(cache, current_authority_epoch_fn=lambda: 2)
        outcome = after_reconnect.evaluate(
            principal="agent:edge-1", action="capability.grocery.purchase", resource="order-1",
            authenticated_principal="agent:edge-1",
        )
        assert outcome.allowed is False
        assert outcome.escalate is True


class TestHumanApprovalRequiredNeverBecomesLocallySatisfiable:
    def test_a_cached_human_approval_required_snapshot_always_escalates_while_disconnected(self, tmp_path):
        """Edge authority bounds intersect with Section 13's own
        invariant: even a fresh, in-scope, unexpired snapshot can never
        be locally sufficient if its own approval_mode says a human must
        decide -- the edge has no authority to manufacture that."""
        cache = _cache(tmp_path)
        snapshot = issue_policy_snapshot(
            principal="agent:edge-1", action="capability.bank.transfer", resource="acct-1",
            policy_decision={"allowed": False, "approval_mode": "HUMAN_APPROVAL_REQUIRED"},
            ttl_seconds=300, authority_epoch=1,
        )
        cache.store_snapshot(snapshot)
        evaluator = LocalGovernanceEvaluator(cache, current_authority_epoch_fn=lambda: 1)

        outcome = evaluator.evaluate(
            principal="agent:edge-1", action="capability.bank.transfer", resource="acct-1",
            authenticated_principal="agent:edge-1",
        )
        assert outcome.allowed is False
        assert outcome.escalate is True
