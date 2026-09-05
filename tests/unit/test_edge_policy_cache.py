"""Signed policy snapshot issuance/verification/caching
(kernel/edge/policy_cache.py)."""
from __future__ import annotations

import dataclasses
import time

import pytest

from src.monkey_brain.kernel.edge.freshness import Freshness
from src.monkey_brain.kernel.edge.local_store import EdgeLocalStore
from src.monkey_brain.kernel.edge.policy_cache import (
    EdgePolicyCache,
    PolicySnapshotError,
    SignedPolicySnapshot,
    issue_policy_snapshot,
    verify_policy_snapshot,
)


@pytest.fixture()
def store(tmp_path):
    s = EdgeLocalStore(str(tmp_path / "edge.db"))
    yield s
    s.close()


@pytest.fixture()
def cache(store):
    return EdgePolicyCache(store)


def _issue(**overrides):
    defaults = dict(
        principal="spiffe://cognitiveos/agent/priya", action="capability.grocery.purchase",
        resource="order-123", policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE", "policy_rule": "default_allow"},
    )
    defaults.update(overrides)
    return issue_policy_snapshot(**defaults)


class TestSnapshotConstruction:
    def test_missing_principal_rejected(self):
        with pytest.raises(PolicySnapshotError):
            SignedPolicySnapshot(principal="", action="x", expires_at=time.time() + 10)

    def test_invalid_approval_mode_rejected(self):
        with pytest.raises(PolicySnapshotError):
            SignedPolicySnapshot(principal="p", action="a", approval_mode="MAYBE", expires_at=time.time() + 10)

    def test_not_time_bounded_rejected(self):
        with pytest.raises(PolicySnapshotError):
            SignedPolicySnapshot(principal="p", action="a", expires_at=0.0)


class TestValidSignedPolicyAccepted:
    def test_valid_snapshot_verifies(self):
        snap = _issue()
        ok, reason = verify_policy_snapshot(snap, authenticated_principal=snap.principal)
        assert ok is True
        assert reason == "ok"


class TestWrongAudienceRejected:
    def test_audience_mismatch_rejected(self):
        snap = _issue(audience="edge-node-1")
        ok, reason = verify_policy_snapshot(snap, authenticated_principal=snap.principal, audience="edge-node-2")
        assert ok is False
        assert "audience" in reason

    def test_no_audience_set_is_unscoped_and_accepted_anywhere(self):
        snap = _issue()  # audience="" (default)
        ok, _ = verify_policy_snapshot(snap, authenticated_principal=snap.principal, audience="any-node")
        assert ok is True

    def test_wrong_principal_rejected(self):
        snap = _issue()
        ok, reason = verify_policy_snapshot(snap, authenticated_principal="someone-else")
        assert ok is False
        assert "principal" in reason


class TestExpiredPolicyRejected:
    def test_get_valid_rejects_expired_snapshot(self, cache):
        snap = _issue(ttl_seconds=0.01)
        cache.store_snapshot(snap)
        time.sleep(0.02)
        got, freshness, reason = cache.get_valid(
            principal=snap.principal, action=snap.action, resource=snap.resource,
            authenticated_principal=snap.principal,
        )
        assert got is None
        assert freshness == Freshness.STALE_MUST_REFRESH


class TestInvalidSignatureRejected:
    def test_tampered_field_fails_verification(self):
        snap = _issue()
        tampered = dataclasses.replace(snap, approval_mode="AUTO_APPROVE", resource="different-order")
        ok, reason = verify_policy_snapshot(tampered, authenticated_principal=snap.principal)
        assert ok is False
        assert "signature" in reason

    def test_forged_proof_from_wrong_key_fails(self):
        from src.monkey_brain.kernel.identity import get_key_manager, sign_bytes

        snap = _issue()
        attacker_key = get_key_manager().get_or_create("attacker")
        forged_proof = sign_bytes(snap.signing_bytes(), attacker_key)
        forged = dataclasses.replace(snap, proof=forged_proof)
        ok, _ = verify_policy_snapshot(forged, authenticated_principal=snap.principal)
        assert ok is False


class TestStalePolicyFreshnessSemantics:
    def test_fresh_snapshot_is_returned(self, cache):
        snap = _issue(ttl_seconds=300)
        cache.store_snapshot(snap)
        got, freshness, _ = cache.get_valid(
            principal=snap.principal, action=snap.action, resource=snap.resource,
            authenticated_principal=snap.principal,
        )
        assert got is not None
        assert freshness == Freshness.FRESH

    def test_requires_authority_snapshot_has_no_grace_past_expiry(self, cache):
        """Section 3/16: policy snapshots are requires_authority-grade --
        must never be treated as STALE_BUT_USABLE past their own expiry,
        unlike a requires_world_state cache entry."""
        snap = _issue(ttl_seconds=0.01)
        cache.store_snapshot(snap)
        time.sleep(0.02)
        got, freshness, _ = cache.get_valid(
            principal=snap.principal, action=snap.action, resource=snap.resource,
            authenticated_principal=snap.principal,
        )
        assert got is None
        assert freshness != Freshness.STALE_BUT_USABLE


class TestRevokedAuthorityDoesNotSilentlyContinue:
    def test_snapshot_with_superseded_epoch_is_rejected(self, cache):
        snap = _issue(authority_epoch=1)
        cache.store_snapshot(snap)
        got, freshness, reason = cache.get_valid(
            principal=snap.principal, action=snap.action, resource=snap.resource,
            authenticated_principal=snap.principal, current_authority_epoch=2,
        )
        assert got is None
        assert freshness == Freshness.STALE_MUST_REFRESH

    def test_no_cached_snapshot_at_all_is_unknown_not_denied(self, cache):
        got, freshness, reason = cache.get_valid(
            principal="p1", action="capability.X", resource="r1", authenticated_principal="p1",
        )
        assert got is None
        assert freshness == Freshness.UNKNOWN
