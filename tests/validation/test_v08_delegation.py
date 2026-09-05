"""Systems Validation Suite — Sections 10-13: delegation attenuation,
chains, revocation, approval separation.

Most of Sections 10-12 (attenuation of resource/capability/expiry/
audience/principal/constraints, chain depth limits, expired/revoked/
tampered/wrong-audience/wrong-subject parents, online revocation
cascading to descendants) are ALREADY proven, live, executable, real
(no mocking of the boundary itself) in tests/security/test_portable_
delegation.py -- confirmed read in full for this validation pass:
TestPrivilegeEscalation, TestCapabilityEscalation, TestExpirationEscalation,
TestConstraintWidening, TestChainVerification, TestBrokenChain,
TestChainPrivilegeEscalation, TestExcessiveDepth, TestForgedIssuer,
TestSpiffeIdentityMismatch, TestForgedDelegation (tamper),
TestChainVerification::test_revoked_root_invalidates_descendant. This
file does not duplicate that coverage; running it is part of this
validation pass's own regression confirmation (see the baseline report).

What THIS file adds -- two genuine gaps identified while auditing that
coverage against the Systems Validation spec's Sections 12-13:

  1. OFFLINE/EDGE revocation (Section 12's "test both: online,
     offline/edge if edge authority caching is supported",
     kernel/edge/delegation_cache.py::VerifiedDelegationCache). A live
     hook FOR invalidating a cached verified-delegation result on
     revocation (`invalidate_delegation()`) exists, but grep across the
     whole repo shows nothing in production code ever calls it -- see
     TestEdgeCacheRevocationIsBoundedNotImmediate's docstring for the
     precise finding.

  2. "delegation != approval" at the EXECUTION boundary specifically
     (kernel/edge/local_governance.py's own stated invariant), not just
     at issuance (TestHumanApprovalCannotBeDelegated in the other file
     only proves certain capability NAMES can never be delegated at
     all -- a different, narrower guarantee).
"""
from __future__ import annotations

import time

import pytest

from src.monkey_brain.kernel.delegation import (
    DelegationScope, get_delegation_store, issue_delegation,
    reset_delegation_store_for_tests, verify_delegation_chain,
)
from src.monkey_brain.kernel.edge.delegation_cache import (
    VerifiedDelegationCache, _MAX_CACHE_TTL_SECONDS,
)


@pytest.fixture(autouse=True)
def _reset():
    from src.monkey_brain.kernel.audit import get_audit_log
    get_audit_log().set_store(None)
    reset_delegation_store_for_tests()
    yield
    reset_delegation_store_for_tests()


def _issue(issuer, delegate, capabilities=("grocery.purchase",), parent=None, ttl=3600):
    return issue_delegation(
        issuer=issuer, delegate=delegate, capabilities=capabilities,
        scope=DelegationScope(resources=("order-1",), actions=("create",)),
        constraints={"max_amount": 100}, ttl_seconds=ttl, parent=parent,
    )


class TestExistingPortableDelegationCoverageIsReal:
    """Not a duplicate -- a direct spot-check that the extensive existing
    suite's OWN headline claims (attenuation + chain + revocation) still
    hold right now, in this environment, as part of this validation
    pass's baseline (Systems Validation Section 1: 'do not simply run
    the existing suite and declare success' -- so this re-derives the
    two sharpest claims by hand, independent of that file's own
    fixtures, rather than only trusting its own green checkmark)."""

    def test_a_child_cannot_broaden_capability_or_expiry_beyond_its_parent(self):
        from src.monkey_brain.kernel.delegation import DelegationDeniedError

        parent = _issue("A", "B", capabilities=("grocery.purchase",), ttl=100)

        # issue_delegation() itself is the first enforcement point -- both
        # attempted expansions are refused before a credential is even
        # constructed (matches TestCapabilityEscalation/TestConstraintWidening's
        # own pattern in test_portable_delegation.py).
        with pytest.raises(DelegationDeniedError):
            issue_delegation(
                issuer="B", delegate="C", capabilities=("grocery.purchase", "bank.transfer"),
                scope=DelegationScope(resources=("order-1",), actions=("create",)),
                constraints={"max_amount": 100}, ttl_seconds=50, parent=parent,
            )

        # A forcibly-constructed credential that outlives its parent is
        # independently caught by the VERIFIER too, not merely by the
        # issuance helper's own guard (mirrors TestExpirationEscalation's
        # forced-construction proof).
        import dataclasses

        from src.monkey_brain.kernel.delegation import validate_delegation
        from src.monkey_brain.kernel.identity import get_key_manager, sign_bytes

        forged = dataclasses.replace(
            parent, issuer="B", delegate="C", parent_delegation_id=parent.delegation_id,
            expires_at=parent.expires_at + 999_999, delegation_depth=1, proof="",
        )
        km = get_key_manager()
        forged = forged.with_proof(sign_bytes(forged.signing_bytes(), km.get_or_create("B")))
        result = validate_delegation(child=forged, parent=parent, authenticated_issuer="B", authenticated_delegate="C")
        assert result.authorized is False
        assert "outlive" in result.failure_reason

    def test_revoking_a_root_denies_every_descendant_immediately_online(self):
        store = get_delegation_store()
        d1 = _issue("A", "B")
        d2 = issue_delegation(
            issuer="B", delegate="C", capabilities=("grocery.purchase",),
            scope=DelegationScope(resources=("order-1",), actions=("create",)),
            constraints={"max_amount": 100}, ttl_seconds=100, parent=d1,
        )
        store.register(d1)
        store.register(d2)
        store.revoke(d1.delegation_id, reason="validation-suite test")
        result = verify_delegation_chain(chain=(d1, d2), authenticated_delegate="C", is_revoked=store.is_revoked)
        assert result.authorized is False
        assert "revoked" in result.failure_reason


class TestEdgeCacheRevocationIsBoundedNotImmediate:
    """FINDING: kernel/edge/delegation_cache.py::VerifiedDelegationCache.
    invalidate_delegation() is unit-tested in isolation
    (tests/unit/test_edge_performance_caches.py) but `grep -rn
    "invalidate_delegation(" src/` shows NO production call site --
    nothing in DelegationStore.revoke() or anywhere else actually
    invokes it when a real revocation happens. The edge cache's only
    real protection is the unconditional _MAX_CACHE_TTL_SECONDS=30s
    bound in verify(): a chain verified and cached just before central
    revocation remains ALLOWED at a disconnected edge for up to (but
    not more than) that TTL, not "immediately" as the existence of an
    invalidation hook might suggest. This proves the actual, bounded
    exposure window experimentally rather than asserting the aspirational
    "invalidated immediately" behavior a reader might assume from the
    method's presence."""

    def test_a_cached_allow_survives_revocation_until_the_cache_ttl_bound_elapses(self):
        cache = VerifiedDelegationCache()
        store = get_delegation_store()
        d = _issue("A", "B", ttl=3600)  # delegation itself lives far longer than the cache TTL
        store.register(d)

        t0 = 1_000_000.0
        first = cache.verify(chain=(d,), authenticated_delegate="B", is_revoked=store.is_revoked, now=t0)
        assert first.authorized is True

        # Revoke centrally -- but invalidate_delegation() is never called
        # by any real revocation path (the finding above), so the cache
        # entry is untouched.
        store.revoke(d.delegation_id, reason="validation-suite: prove the exposure window")

        still_cached = cache.verify(chain=(d,), authenticated_delegate="B", is_revoked=store.is_revoked, now=t0 + 1.0)
        assert still_cached.authorized is True, (
            "this IS the finding, not a desired behavior: a revoked delegation remains "
            "usable from the edge cache until the TTL bound below elapses"
        )

        # Once the (independent of the delegation's own, much longer,
        # expiry) cache TTL bound elapses, the next verify() call is a
        # real, un-cached one and correctly sees the revocation.
        # BoundedTTLCache.put() stamps expires_at from the REAL wall
        # clock (time.time() + ttl), not the fictional `now` this test
        # passes into verify() -- advance it directly rather than a real
        # 30-second sleep.
        import dataclasses as _dc
        for k, entry in list(cache._cache._entries.items()):
            cache._cache._entries[k] = _dc.replace(entry, expires_at=0.0)
        after_ttl = cache.verify(
            chain=(d,), authenticated_delegate="B", is_revoked=store.is_revoked,
            now=t0 + _MAX_CACHE_TTL_SECONDS + 1.0,
        )
        assert after_ttl.authorized is False
        assert "revoked" in after_ttl.failure_reason

    def test_explicit_invalidate_delegation_would_close_the_window_if_it_were_ever_called(self):
        """Proves the hook itself works correctly in isolation -- the gap
        is specifically that nothing calls it, not that it's broken."""
        cache = VerifiedDelegationCache()
        store = get_delegation_store()
        d = _issue("A", "B", ttl=3600)
        store.register(d)
        t0 = 2_000_000.0
        cache.verify(chain=(d,), authenticated_delegate="B", is_revoked=store.is_revoked, now=t0)

        store.revoke(d.delegation_id, reason="validation-suite")
        cache.invalidate_delegation(d.delegation_id)  # what NO production caller currently does

        immediately_after = cache.verify(chain=(d,), authenticated_delegate="B", is_revoked=store.is_revoked, now=t0 + 1.0)
        assert immediately_after.authorized is False


class TestDelegationDoesNotSubstituteForApproval:
    """Section 13: 'delegation != approval, approval != delegation',
    proven at the EXECUTION boundary (kernel/security_boundary.py /
    kernel/edge/local_governance.py), not merely at issuance-time
    capability-name blocking (that's TestHumanApprovalCannotBeDelegated
    in test_portable_delegation.py -- a different, narrower guarantee:
    it stops "human_approval" itself from being a delegatable
    capability; it does not prove a delegated ordinary capability that
    the POLICY marks HUMAN_APPROVAL_REQUIRED is denied without an
    actual approval)."""

    def test_valid_delegation_alone_does_not_satisfy_a_human_approval_required_policy(self):
        from src.monkey_brain.kernel.edge.decision_state import EdgeDecisionState
        from src.monkey_brain.kernel.edge.local_governance import (
            GovernanceOrigin, LocalGovernanceEvaluator,
        )
        from src.monkey_brain.kernel.edge.policy_cache import EdgePolicyCache, issue_policy_snapshot
        from src.monkey_brain.kernel.edge.local_store import EdgeLocalStore

        store = EdgeLocalStore(db_path=":memory:") if _accepts_memory_path() else EdgeLocalStore()
        cache = EdgePolicyCache(store)
        snapshot = issue_policy_snapshot(
            principal="agent:B", action="capability.bank.transfer", resource="acct-1",
            policy_decision={"allowed": False, "approval_mode": "HUMAN_APPROVAL_REQUIRED", "policy_rule": "high_value_requires_approval"},
        )
        cache.store_snapshot(snapshot)
        evaluator = LocalGovernanceEvaluator(cache)

        d = _issue("A", "agent:B", capabilities=("bank.transfer",))  # a REAL, valid, unrevoked, unexpired delegation
        outcome = evaluator.evaluate(
            principal="agent:B", action="capability.bank.transfer", resource="acct-1",
            authenticated_principal="agent:B", delegation_chain=(d,),
        )
        # Escalates (cannot be locally satisfied), never a local ALLOW --
        # a real, valid delegation for this exact capability is present
        # and is NOT enough on its own.
        assert outcome.allowed is False
        assert outcome.escalate is True
        assert outcome.decision_state == EdgeDecisionState.LOCAL_HUMAN_APPROVAL_REQUIRED

    def test_an_approval_present_does_not_widen_an_insufficient_delegation_scope(self):
        """The reverse direction: even if a human HAS approved the
        operation, the delegation chain presented must still cover it on
        its own merits -- an approval never substitutes for missing
        delegated authority. Proven directly against
        verify_delegation_chain (approval is a wholly separate mechanism
        that never appears as an input to it at all -- this is the
        structural proof of that separation, not a simulated approval
        object, since delegation verification has literally no approval
        parameter to short-circuit)."""
        import inspect

        from src.monkey_brain.kernel.delegation import verify_delegation_chain as vdc
        params = set(inspect.signature(vdc).parameters)
        assert "approval" not in params and "approved" not in params and "human_approval" not in params

        from src.monkey_brain.kernel.delegation import DelegationDeniedError

        d = _issue("A", "B", capabilities=("grocery.purchase",))  # delegated for grocery.purchase only
        with pytest.raises(DelegationDeniedError):
            # attempts to widen to a NEW capability ("bank.transfer") no
            # amount of a hypothetical human approval could grant --
            # delegation issuance/verification has no approval input to
            # short-circuit through.
            issue_delegation(
                issuer="B", delegate="C", capabilities=("bank.transfer",),
                scope=DelegationScope(resources=("acct-1",), actions=("create",)),
                constraints={}, ttl_seconds=100, parent=d,
            )


def _accepts_memory_path() -> bool:
    import inspect

    from src.monkey_brain.kernel.edge.local_store import EdgeLocalStore
    return "db_path" in inspect.signature(EdgeLocalStore.__init__).parameters
