"""Portable Delegation — security invariant tests (attack model, Section
27) for kernel/delegation.py, plus one end-to-end test proving a verified
delegation reaches the REAL capability execution path through
ensure_governed/OPA (Section 26's "Delegation + capability execution").
"""

from __future__ import annotations

import dataclasses
import time

import pytest

from src.monkey_brain.kernel.delegation import (
    DEFAULT_MAX_DELEGATION_DEPTH,
    DelegationCredential,
    DelegationDeniedError,
    DelegationError,
    DelegationScope,
    DelegationStore,
    get_delegation_store,
    issue_delegation,
    reset_delegation_store_for_tests,
    resolve_issuer_public_key_pem,
    to_opa_delegation_context,
    validate_delegation,
    verify_delegation_chain,
    verify_delegation_proof,
)


@pytest.fixture(autouse=True)
def _reset():
    # Defensive: tests/security/test_operation_boundaries.py's
    # test_audit_intent_before_effect installs a permanently-broken
    # get_audit_log() store (Boom(), raises on every .append()) with no
    # teardown of its own -- a pre-existing test-isolation gap in that
    # file, unrelated to delegation, that otherwise makes ANY test in a
    # later-running file that hits a real (non-mocked) critical audit
    # write fail with "mongo down" purely due to run order. Resetting the
    # store here is a no-op when nothing has polluted it.
    from src.monkey_brain.kernel.audit import get_audit_log

    get_audit_log().set_store(None)
    reset_delegation_store_for_tests()
    yield
    reset_delegation_store_for_tests()


def _issue(
    issuer,
    delegate,
    capabilities=("grocery.purchase",),
    amount=10000,
    region="IN",
    resources=("order-123",),
    actions=("create",),
    ttl=3600,
    parent=None,
):
    return issue_delegation(
        issuer=issuer,
        delegate=delegate,
        capabilities=capabilities,
        scope=DelegationScope(resources=resources, actions=actions),
        constraints={"max_amount": amount, "region": region},
        ttl_seconds=ttl,
        parent=parent,
    )


class TestValidDelegation:
    def test_valid_root_delegation_allows(self):
        d = _issue("A", "B")
        result = validate_delegation(child=d, parent=None, authenticated_issuer="A", authenticated_delegate="B")
        assert result.authorized is True

    def test_valid_chain_allows(self):
        d1 = _issue("A", "B", amount=10000)
        d2 = _issue("B", "C", amount=5000, parent=d1)
        result = verify_delegation_chain(chain=(d1, d2), authenticated_delegate="C")
        assert result.authorized is True


class TestForgedDelegation:
    def test_tampered_field_fails_proof(self):
        d = _issue("A", "B")
        tampered = dataclasses.replace(d, constraints={"max_amount": 999999, "region": "IN"})
        assert verify_delegation_proof(tampered) is False

    def test_tampered_delegation_denied_end_to_end(self):
        d = _issue("A", "B")
        tampered = dataclasses.replace(d, capabilities=("bank.transfer",))
        result = validate_delegation(
            child=tampered,
            parent=None,
            authenticated_issuer="A",
            authenticated_delegate="B",
        )
        assert result.authorized is False
        assert result.proof_valid is False


class TestWrongDelegate:
    def test_wrong_recipient_replay_denied(self):
        d = _issue("A", "B")
        result = validate_delegation(child=d, parent=None, authenticated_issuer="A", authenticated_delegate="C")
        assert result.authorized is False
        assert "delegate" in result.failure_reason

    def test_wrong_recipient_chain_replay_denied(self):
        d1 = _issue("A", "B")
        d2 = _issue("B", "C", parent=d1)
        result = verify_delegation_chain(chain=(d1, d2), authenticated_delegate="D")
        assert result.authorized is False


class TestPrivilegeEscalation:
    def test_amount_widening_denied_at_issuance(self):
        d1 = _issue("A", "B", amount=10000)
        with pytest.raises(DelegationDeniedError):
            _issue("B", "C", amount=20000, parent=d1)

    def test_amount_widening_denied_if_forcibly_constructed(self):
        d1 = _issue("A", "B", amount=10000)
        # Bypass issue_delegation's own pre-check to prove the VERIFIER
        # (not just the issuance helper) independently catches this.
        forged = DelegationCredential(
            issuer="B",
            delegate="C",
            parent_delegation_id=d1.delegation_id,
            issued_at=time.time(),
            expires_at=d1.expires_at,
            scope=d1.scope,
            capabilities=d1.capabilities,
            constraints={"max_amount": 20000, "region": "IN"},
            delegation_depth=1,
        )
        from src.monkey_brain.kernel.identity import get_key_manager, sign_bytes

        km = get_key_manager()
        forged = forged.with_proof(sign_bytes(forged.signing_bytes(), km.get_or_create("B")))
        result = validate_delegation(
            child=forged,
            parent=d1,
            authenticated_issuer="B",
            authenticated_delegate="C",
        )
        assert result.authorized is False
        assert "broader" in result.failure_reason


class TestCapabilityEscalation:
    def test_new_capability_not_in_parent_denied(self):
        d1 = _issue("A", "B", capabilities=("grocery.purchase",))
        with pytest.raises(DelegationDeniedError):
            issue_delegation(
                issuer="B",
                delegate="C",
                capabilities=("bank.transfer",),
                scope=DelegationScope(),
                constraints={},
                ttl_seconds=1800,
                parent=d1,
            )


class TestExpirationEscalation:
    def test_child_cannot_outlive_parent(self):
        # issue_delegation() itself always clamps expires_at to the
        # parent's (a safe-by-construction property) -- so to prove the
        # VALIDATOR independently catches this too (not merely that the
        # issuance helper is well-behaved), forcibly construct a
        # credential that outlives its parent and confirm validation
        # rejects it.
        d1 = _issue("A", "B", ttl=1800)
        forged = DelegationCredential(
            issuer="B",
            delegate="C",
            parent_delegation_id=d1.delegation_id,
            issued_at=time.time(),
            expires_at=d1.expires_at + 999999,
            scope=d1.scope,
            capabilities=d1.capabilities,
            constraints=d1.constraints,
            delegation_depth=1,
        )
        from src.monkey_brain.kernel.identity import get_key_manager, sign_bytes

        km = get_key_manager()
        forged = forged.with_proof(sign_bytes(forged.signing_bytes(), km.get_or_create("B")))
        result = validate_delegation(
            child=forged,
            parent=d1,
            authenticated_issuer="B",
            authenticated_delegate="C",
        )
        assert result.authorized is False
        assert "outlive" in result.failure_reason

    def test_issue_delegation_clamps_to_parent_expiry_not_silently_widen(self):
        d1 = _issue("A", "B", ttl=100)
        d2 = issue_delegation(
            issuer="B",
            delegate="C",
            capabilities=("grocery.purchase",),
            scope=d1.scope,
            constraints=d1.constraints,
            ttl_seconds=50,
            parent=d1,
        )
        assert d2.expires_at <= d1.expires_at


class TestConstraintWidening:
    def test_region_any_denied(self):
        d1 = _issue("A", "B", region="IN")
        with pytest.raises(DelegationDeniedError):
            issue_delegation(
                issuer="B",
                delegate="C",
                capabilities=("grocery.purchase",),
                scope=d1.scope,
                constraints={"max_amount": 5000, "region": "ANY"},
                ttl_seconds=1800,
                parent=d1,
            )

    def test_dropping_a_parent_constraint_denied(self):
        d1 = _issue("A", "B", region="IN")
        with pytest.raises(DelegationDeniedError):
            issue_delegation(
                issuer="B",
                delegate="C",
                capabilities=("grocery.purchase",),
                scope=d1.scope,
                constraints={"max_amount": 5000},  # region dropped
                ttl_seconds=1800,
                parent=d1,
            )


class TestChainVerification:
    def test_three_hop_chain_allows(self):
        d1 = _issue("A", "B", amount=10000)
        d2 = _issue("B", "C", amount=5000, parent=d1)
        d3 = _issue("C", "D", amount=1000, parent=d2)
        result = verify_delegation_chain(chain=(d1, d2, d3), authenticated_delegate="D")
        assert result.authorized is True


class TestBrokenChain:
    def test_revoked_root_invalidates_descendant(self):
        store = get_delegation_store()
        d1 = _issue("A", "B", amount=10000)
        d2 = _issue("B", "C", amount=5000, parent=d1)
        store.register(d1)
        store.register(d2)
        store.revoke(d1.delegation_id, reason="compromised")
        assert store.is_revoked(d2.delegation_id) is True
        result = verify_delegation_chain(chain=(d1, d2), authenticated_delegate="C", is_revoked=store.is_revoked)
        assert result.authorized is False
        assert "revoked" in result.failure_reason


class TestChainPrivilegeEscalation:
    def test_leaf_cannot_exceed_root_via_forced_construction(self):
        d1 = _issue("A", "B", amount=1000)
        forged_d2 = DelegationCredential(
            issuer="B",
            delegate="C",
            parent_delegation_id=d1.delegation_id,
            issued_at=time.time(),
            expires_at=d1.expires_at,
            scope=d1.scope,
            capabilities=d1.capabilities,
            constraints={"max_amount": 999999, "region": "IN"},
            delegation_depth=1,
        )
        from src.monkey_brain.kernel.identity import get_key_manager, sign_bytes

        km = get_key_manager()
        forged_d2 = forged_d2.with_proof(sign_bytes(forged_d2.signing_bytes(), km.get_or_create("B")))
        result = verify_delegation_chain(chain=(d1, forged_d2), authenticated_delegate="C")
        assert result.authorized is False


class TestExcessiveDepth:
    def test_depth_beyond_max_denied(self):
        chain = [_issue("A0", "A1")]
        prev = chain[0]
        for i in range(1, DEFAULT_MAX_DELEGATION_DEPTH + 3):
            nxt = issue_delegation(
                issuer=f"A{i}",
                delegate=f"A{i + 1}",
                capabilities=("grocery.purchase",),
                scope=prev.scope,
                constraints=prev.constraints,
                ttl_seconds=3600,
                parent=prev,
            )
            chain.append(nxt)
            prev = nxt
        result = verify_delegation_chain(chain=tuple(chain), authenticated_delegate=f"A{len(chain)}")
        assert result.authorized is False
        assert "exceeds max_delegation_depth" in result.failure_reason


class TestSelfDelegation:
    def test_self_delegation_rejected(self):
        with pytest.raises(DelegationError):
            issue_delegation(
                issuer="A",
                delegate="A",
                capabilities=("grocery.purchase",),
                ttl_seconds=3600,
            )


class TestHumanApprovalCannotBeDelegated:
    @pytest.mark.parametrize(
        "capability",
        ["human_approval", "mfa_override", "operator_identity", "approval.override"],
    )
    def test_forbidden_capabilities_rejected_at_issuance(self, capability):
        with pytest.raises(DelegationError):
            issue_delegation(issuer="A", delegate="B", capabilities=(capability,), ttl_seconds=3600)


class TestForgedIssuer:
    def test_credential_signed_by_wrong_key_fails(self):
        d = _issue("A", "B")
        # Someone else's key claims to have issued A's delegation.
        from src.monkey_brain.kernel.identity import get_key_manager, sign_bytes

        km = get_key_manager()
        attacker_key = km.get_or_create("attacker")
        forged_proof = sign_bytes(d.signing_bytes(), attacker_key)
        forged = d.with_proof(forged_proof)
        assert verify_delegation_proof(forged) is False


class TestSpiffeIdentityMismatch:
    def test_authenticated_delegate_must_match_credential_delegate(self):
        d = _issue("spiffe://cognitiveos/agent/planner", "spiffe://cognitiveos/agent/executor")
        result = validate_delegation(
            child=d,
            parent=None,
            authenticated_issuer="spiffe://cognitiveos/agent/planner",
            authenticated_delegate="spiffe://cognitiveos/agent/impostor",
        )
        assert result.authorized is False

    def test_authenticated_issuer_must_match_credential_issuer(self):
        d = _issue("spiffe://cognitiveos/agent/planner", "spiffe://cognitiveos/agent/executor")
        result = validate_delegation(
            child=d,
            parent=None,
            authenticated_issuer="spiffe://cognitiveos/agent/impostor",
            authenticated_delegate="spiffe://cognitiveos/agent/executor",
        )
        assert result.authorized is False


class TestOpaUnavailable:
    @pytest.mark.asyncio
    async def test_opa_unavailable_denies_delegated_request(self, monkeypatch):
        from src.monkey_brain.kernel.approval import reset_approval_store
        from src.monkey_brain.kernel.security_boundary import (
            SecurityBoundaryDenied,
            ensure_governed,
            reset_governed_pipeline_for_tests,
        )
        from src.monkey_brain.kernel.trusted_auth import (
            TrustedAuthEvidence,
            bind_trusted_auth,
        )

        reset_approval_store()
        reset_governed_pipeline_for_tests()
        monkeypatch.setenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", "true")
        monkeypatch.setenv("OPA_REQUIRED", "true")
        bind_trusted_auth(
            TrustedAuthEvidence(
                authenticated=True,
                token_valid=True,
                principal_id="agent:C",
                principal_type="service",
                mfa_status="satisfied",
            )
        )

        async def _opa_unreachable(action, resource, extra, *, verified_delegation=None):
            return {
                "allowed": False,
                "reason": "opa_unreachable",
                "approval_mode": "DENY",
            }

        monkeypatch.setattr("src.monkey_brain.kernel.security_boundary._authorize", _opa_unreachable)

        d1 = _issue("A", "B")
        d2 = _issue("B", "C", parent=d1)
        called = {"ran": False}

        async def effect():
            called["ran"] = True
            return "ran"

        with pytest.raises(SecurityBoundaryDenied):
            await ensure_governed(
                "capability.grocery.purchase",
                "order-123",
                effect,
                verified_delegation=to_opa_delegation_context((d1, d2)),
            )
        assert called["ran"] is False


class TestDelegationReachesRealExecution:
    """Section 26's 'Delegation + capability execution': prove that
    valid delegation -> governance -> OPA -> execution actually reaches
    the real capability, and that a delegation valid for a DIFFERENT
    capability does not."""

    @pytest.mark.asyncio
    async def test_valid_delegation_for_this_capability_executes(self, monkeypatch):
        from src.monkey_brain.kernel.approval import reset_approval_store
        from src.monkey_brain.kernel.security_boundary import (
            ensure_governed,
            reset_governed_pipeline_for_tests,
        )
        from src.monkey_brain.kernel.trusted_auth import (
            TrustedAuthEvidence,
            bind_trusted_auth,
        )

        reset_approval_store()
        reset_governed_pipeline_for_tests()
        monkeypatch.setenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", "true")
        monkeypatch.setenv("OPA_REQUIRED", "true")
        bind_trusted_auth(
            TrustedAuthEvidence(
                authenticated=True,
                token_valid=True,
                principal_id="agent:C",
                principal_type="service",
                mfa_status="satisfied",
            )
        )

        d1 = _issue("A", "B", capabilities=("grocery.purchase",))
        d2 = _issue("B", "C", capabilities=("grocery.purchase",), parent=d1)
        chain_result = verify_delegation_chain(chain=(d1, d2), authenticated_delegate="C")
        assert chain_result.authorized is True

        # Mirrors the real rego rule added to agentos_governance.rego
        # (delegation_capability_mismatch): no live OPA server exists in
        # this dev/test environment, so _authorize -- the one seam every
        # governance test in this repo already mocks in place of a real
        # OPA round-trip -- is stubbed with the SAME decision that policy
        # makes. build_opa_input's actual wiring (verified_delegation ->
        # ctx["delegation"] -> the real rego rule) is separately proven
        # by `opa eval` against the real .rego file, and by
        # TestAgentSuppliedDelegationClaimIsStripped that the trusted path
        # is the only way this key gets populated.
        async def _authorize_like_real_opa(action, resource, extra, *, verified_delegation=None):
            requested = (extra or {}).get("capability", "")
            delegation_caps = (verified_delegation or {}).get("capabilities", [])
            if verified_delegation and requested not in delegation_caps:
                return {
                    "allowed": False,
                    "reason": "delegation_capability_mismatch",
                    "approval_mode": "DENY",
                }
            return {
                "allowed": True,
                "approval_mode": "AUTO_APPROVE",
                "reason": "default_allow",
            }

        monkeypatch.setattr(
            "src.monkey_brain.kernel.security_boundary._authorize",
            _authorize_like_real_opa,
        )

        called = {"ran": False}

        async def effect():
            called["ran"] = True
            return "purchased"

        result = await ensure_governed(
            "capability.grocery.purchase",
            "order-123",
            effect,
            extra={"capability": "grocery.purchase"},
            verified_delegation=to_opa_delegation_context((d1, d2)),
        )
        assert called["ran"] is True
        assert result == "purchased"

    @pytest.mark.asyncio
    async def test_delegation_for_different_capability_denies_execution(self, monkeypatch):
        from src.monkey_brain.kernel.approval import reset_approval_store
        from src.monkey_brain.kernel.security_boundary import (
            SecurityBoundaryDenied,
            ensure_governed,
            reset_governed_pipeline_for_tests,
        )
        from src.monkey_brain.kernel.trusted_auth import (
            TrustedAuthEvidence,
            bind_trusted_auth,
        )

        reset_approval_store()
        reset_governed_pipeline_for_tests()
        monkeypatch.setenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", "true")
        monkeypatch.setenv("OPA_REQUIRED", "true")
        bind_trusted_auth(
            TrustedAuthEvidence(
                authenticated=True,
                token_valid=True,
                principal_id="agent:C",
                principal_type="service",
                mfa_status="satisfied",
            )
        )

        d1 = _issue("A", "B", capabilities=("grocery.purchase",))
        d2 = _issue("B", "C", capabilities=("grocery.purchase",), parent=d1)

        async def _authorize_like_real_opa(action, resource, extra, *, verified_delegation=None):
            requested = (extra or {}).get("capability", "")
            delegation_caps = (verified_delegation or {}).get("capabilities", [])
            if verified_delegation and requested not in delegation_caps:
                return {
                    "allowed": False,
                    "reason": "delegation_capability_mismatch",
                    "approval_mode": "DENY",
                }
            return {
                "allowed": True,
                "approval_mode": "AUTO_APPROVE",
                "reason": "default_allow",
            }

        monkeypatch.setattr(
            "src.monkey_brain.kernel.security_boundary._authorize",
            _authorize_like_real_opa,
        )

        called = {"ran": False}

        async def effect():
            called["ran"] = True
            return "should not run"

        with pytest.raises(SecurityBoundaryDenied):
            await ensure_governed(
                "capability.bank.transfer",
                "acct-1",
                effect,
                extra={"capability": "bank.transfer"},
                verified_delegation=to_opa_delegation_context((d1, d2)),
            )
        assert called["ran"] is False

    @pytest.mark.asyncio
    async def test_action_executor_threads_context_verified_delegation_to_opa(self, monkeypatch):
        """The actual integration point in kernel/pipeline/action_executor.py
        (_execute_action reads context["verified_delegation"] and passes it
        to ensure_governed) -- not just raw ensure_governed called by hand."""
        from unittest.mock import MagicMock

        from src.monkey_brain.kernel.approval import reset_approval_store
        from src.monkey_brain.kernel.pipeline.action_executor import ActionExecutor
        from src.monkey_brain.kernel.pipeline.execution import Action
        from src.monkey_brain.kernel.security_boundary import (
            reset_governed_pipeline_for_tests,
        )
        from src.monkey_brain.kernel.trusted_auth import (
            TrustedAuthEvidence,
            bind_trusted_auth,
        )

        reset_approval_store()
        reset_governed_pipeline_for_tests()
        monkeypatch.setenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", "true")
        monkeypatch.setenv("OPA_REQUIRED", "true")
        bind_trusted_auth(
            TrustedAuthEvidence(
                authenticated=True,
                token_valid=True,
                principal_id="agent:C",
                principal_type="service",
                mfa_status="satisfied",
            )
        )

        d1 = _issue("A", "B", capabilities=("grocery.purchase",))
        d2 = _issue("B", "C", capabilities=("grocery.purchase",), parent=d1)

        captured = {}

        async def _capture_authorize(action, resource, extra, *, verified_delegation=None):
            captured["verified_delegation"] = verified_delegation
            return {"allowed": True, "approval_mode": "AUTO_APPROVE", "reason": "ok"}

        monkeypatch.setattr("src.monkey_brain.kernel.security_boundary._authorize", _capture_authorize)

        capability = MagicMock()
        capability.handle = MagicMock(return_value={"success": True})
        bus = MagicMock()
        bus.discover.return_value = capability
        executor = ActionExecutor(capability_bus=bus)
        action = Action(action_id="a1", capability="grocery.purchase", step_index=0)
        context = {"verified_delegation": to_opa_delegation_context((d1, d2))}

        result = await executor.execute((action,), context)

        assert capability.handle.called is True
        assert result.actions[0].success is True
        assert captured["verified_delegation"]["delegation_id"] == d2.delegation_id
        assert captured["verified_delegation"]["capabilities"] == ["grocery.purchase"]


class TestAgentSuppliedDelegationClaimIsStripped:
    def test_extra_delegation_key_never_reaches_opa_context(self):
        from src.monkey_brain.kernel.security_boundary import build_opa_input
        from src.monkey_brain.kernel.trusted_auth import (
            TrustedAuthEvidence,
            bind_trusted_auth,
        )

        bind_trusted_auth(
            TrustedAuthEvidence(
                authenticated=True,
                token_valid=True,
                principal_id="agent:C",
                principal_type="service",
                mfa_status="satisfied",
            )
        )
        opa_input = build_opa_input(
            action="capability.bank.transfer",
            resource="acct-1",
            extra={
                "delegation": {
                    "delegation_id": "fake",
                    "capabilities": ["bank.transfer"],
                }
            },
        )
        assert opa_input["context"].get("delegation") in (None,)
        assert opa_input["delegation"] == {}
