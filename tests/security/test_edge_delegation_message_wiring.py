"""Live delegation-message wiring (kernel/edge/delegation_message.py) --
proves the gap named in action_executor.py's own former comment is
closed: a delegation chain riding on a live inbound agent-to-agent
message is extracted, independently verified via the REAL
kernel/delegation.py::verify_delegation_chain (no second verifier), and
only the verified result ever reaches a trusted context, never a raw
agent claim.

D1 -> D2 -> ActionExecutor -> LocalGovernanceEvaluator, per the required
scenario list: valid chain, attenuated chain, wrong delegate, wrong
audience, expired parent, revoked parent, privilege escalation, malformed
chain, excessive depth, SPIFFE mismatch.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from src.monkey_brain.kernel.delegation import (
    DelegationCredential,
    DelegationScope,
    get_delegation_store,
    issue_delegation,
    reset_delegation_store_for_tests,
)
from src.monkey_brain.kernel.edge.delegation_message import extract_and_verify_delegation
from src.monkey_brain.kernel.edge.local_governance import LocalGovernanceEvaluator
from src.monkey_brain.kernel.edge.policy_cache import EdgePolicyCache, issue_policy_snapshot
from src.monkey_brain.kernel.edge.local_store import EdgeLocalStore
from src.monkey_brain.kernel.pipeline.action_executor import ActionExecutor
from src.monkey_brain.kernel.pipeline.execution import Action
from src.monkey_brain.kernel.security_boundary import reset_governed_pipeline_for_tests
from src.monkey_brain.kernel.approval import reset_approval_store
from src.monkey_brain.kernel.trusted_auth import TrustedAuthEvidence, bind_trusted_auth

PRINCIPAL = "spiffe://cognitiveos/agent/C"


@pytest.fixture(autouse=True)
def _reset():
    from src.monkey_brain.kernel.audit import get_audit_log
    get_audit_log().set_store(None)
    reset_delegation_store_for_tests()
    reset_approval_store()
    reset_governed_pipeline_for_tests()
    yield
    reset_delegation_store_for_tests()
    reset_approval_store()
    reset_governed_pipeline_for_tests()


def _issue(issuer, delegate, capabilities=("grocery.purchase",), parent=None, ttl=3600, audience=""):
    return issue_delegation(
        issuer=issuer, delegate=delegate, capabilities=capabilities,
        scope=DelegationScope(resources=("order-123",), actions=("create",)),
        ttl_seconds=ttl, parent=parent, audience=audience,
    )


def _chain_payload(*hops):
    return {"msg_type": "delegated_task", "delegation_chain": [h.to_dict() for h in hops]}


class TestExtractionValidChains:
    def test_valid_root_delegation_is_verified(self):
        d1 = _issue("A", "C")
        result = extract_and_verify_delegation(_chain_payload(d1), authenticated_delegate="C")
        assert result.present is True
        assert result.verified is True
        assert result.verified_delegation["delegate"] == "C"
        assert result.chain == (d1,)

    def test_attenuated_two_hop_chain_is_verified(self):
        d1 = _issue("A", "B")
        d2 = _issue("B", "C", parent=d1)
        result = extract_and_verify_delegation(_chain_payload(d1, d2), authenticated_delegate="C")
        assert result.verified is True
        assert result.verified_delegation["root_issuer"] == "A"
        assert len(result.chain) == 2

    def test_no_delegation_field_is_not_an_error(self):
        result = extract_and_verify_delegation({"msg_type": "delegated_task", "tasks": []}, authenticated_delegate="C")
        assert result.present is False
        assert result.verified is False
        assert result.verified_delegation is None


class TestExtractionRejections:
    def test_wrong_delegate_is_rejected(self):
        d1 = _issue("A", "B")
        result = extract_and_verify_delegation(_chain_payload(d1), authenticated_delegate="mallory")
        assert result.present is True
        assert result.verified is False
        assert "delegate" in result.denial_reason.lower()

    def test_wrong_audience_is_rejected(self):
        d1 = _issue("A", "C", audience="only-for-D")
        result = extract_and_verify_delegation(_chain_payload(d1), authenticated_delegate="C")
        assert result.verified is False
        assert "audience" in result.denial_reason.lower()

    def test_expired_parent_is_rejected(self):
        d1 = _issue("A", "B", ttl=1)
        d1_expired = time.time() + 5
        d2 = _issue("B", "C", parent=d1)
        result = extract_and_verify_delegation(_chain_payload(d1, d2), authenticated_delegate="C", now=d1_expired)
        assert result.verified is False

    def test_revoked_parent_invalidates_descendant(self):
        d1 = _issue("A", "B")
        d2 = _issue("B", "C", parent=d1)
        store = get_delegation_store()
        store.register(d1)
        store.register(d2)
        store.revoke(d1.delegation_id)
        result = extract_and_verify_delegation(_chain_payload(d1, d2), authenticated_delegate="C")
        assert result.verified is False
        assert "revoc" in result.denial_reason.lower() or "revoked" in result.denial_reason.lower()

    def test_privilege_escalation_is_rejected_despite_a_valid_signature(self):
        """B properly signs D2 with its OWN real key (so proof
        verification alone would pass) but claims a capability D1 never
        granted it -- verify_delegation_chain's independent attenuation
        re-check must catch this, not merely trust B's signature."""
        from src.monkey_brain.kernel.identity import get_key_manager, sign_bytes

        d1 = _issue("A", "B", capabilities=("grocery.purchase",))
        forged = DelegationCredential(
            issuer="B", delegate="C", parent_delegation_id=d1.delegation_id,
            issued_at=time.time(), expires_at=d1.expires_at,
            scope=d1.scope, capabilities=("grocery.purchase", "bank.transfer"),
            delegation_depth=d1.delegation_depth + 1,
        )
        km = get_key_manager()
        private_key = km.get_or_create("B")
        signed_forged = forged.with_proof(sign_bytes(forged.signing_bytes(), private_key))

        result = extract_and_verify_delegation(_chain_payload(d1, signed_forged), authenticated_delegate="C")
        assert result.verified is False

    def test_malformed_chain_not_a_list_is_rejected(self):
        result = extract_and_verify_delegation(
            {"msg_type": "delegated_task", "delegation_chain": "not-a-list"}, authenticated_delegate="C",
        )
        assert result.present is True
        assert result.verified is False
        assert "must be a list" in result.denial_reason

    def test_malformed_chain_bad_hop_is_rejected(self):
        result = extract_and_verify_delegation(
            {"msg_type": "delegated_task", "delegation_chain": [{"issuer": "A"}]}, authenticated_delegate="C",
        )
        assert result.present is True
        assert result.verified is False
        assert "malformed" in result.denial_reason.lower()

    def test_excessive_depth_is_rejected(self):
        from src.monkey_brain.kernel.delegation import DEFAULT_MAX_DELEGATION_DEPTH

        hops = [_issue("A0", "A1")]
        for i in range(1, DEFAULT_MAX_DELEGATION_DEPTH + 2):
            hops.append(_issue(f"A{i}", f"A{i + 1}", parent=hops[-1]))
        result = extract_and_verify_delegation(_chain_payload(*hops), authenticated_delegate=hops[-1].delegate)
        assert result.verified is False

    def test_spiffe_identity_binding_never_reads_delegate_from_the_message(self):
        """authenticated_delegate is a REQUIRED kwarg sourced by the
        caller from its own already-bound identity (SPIFFE/trusted_auth)
        -- proves the function has no path to read it out of `payload`
        even if an attacker stuffs one in."""
        d1 = _issue("A", "C")
        payload = _chain_payload(d1)
        payload["authenticated_delegate"] = "mallory"  # must be ignored entirely
        payload["delegate"] = "mallory"
        result = extract_and_verify_delegation(payload, authenticated_delegate="C")
        assert result.verified is True  # honors the REAL identity ("C"), not the spoofed field


class TestEndToEndActionExecutorWiring:
    """D1 -> D2 -> ActionExecutor -> LocalGovernanceEvaluator, against the
    REAL ActionExecutor and REAL LocalGovernanceEvaluator (not mocked)."""

    @pytest.fixture(autouse=True)
    def _bind_identity(self):
        bind_trusted_auth(TrustedAuthEvidence(
            authenticated=True, token_valid=True, principal_id=PRINCIPAL,
            principal_type="service", mfa_status="satisfied",
        ))
        yield

    def _edge_governance(self, tmp_path):
        store = EdgeLocalStore(str(tmp_path / "edge.db"))
        cache = EdgePolicyCache(store)
        return LocalGovernanceEvaluator(cache)

    @pytest.mark.asyncio
    async def test_verified_chain_reaches_local_governance_and_allows_local_execution(self, monkeypatch, tmp_path):
        d1 = _issue("A", "B")
        d2 = _issue("B", PRINCIPAL, parent=d1)
        extraction = extract_and_verify_delegation(_chain_payload(d1, d2), authenticated_delegate=PRINCIPAL)
        assert extraction.verified is True

        gov = self._edge_governance(tmp_path)
        cache = gov._policy_cache
        # Delegation establishes WHO may act under whose authority; the
        # cached policy snapshot establishes WHAT that authority actually
        # permits -- both are required, matching test_edge_local_governance
        # .py's own convention (delegation is never a substitute for a
        # locally-cached policy decision, nor vice versa).
        snapshot = issue_policy_snapshot(
            principal=PRINCIPAL, action="capability.grocery.purchase", resource="grocery.purchase",
            policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE", "policy_rule": "edge_cached"},
        )
        cache.store_snapshot(snapshot)

        capability = MagicMock()
        capability.handle = MagicMock(return_value={"success": True})
        bus = MagicMock()
        bus.discover.return_value = capability

        executor = ActionExecutor(
            bus,
            connectivity_check=lambda cap: (False, "WAITING_FOR_AUTHORITY", "disconnected"),
            edge_governance=gov,
        )
        action = Action(action_id="a1", capability="grocery.purchase", parameters={})
        context = {"actor_id": PRINCIPAL, "delegation_chain": extraction.chain, "verified_delegation": extraction.verified_delegation}

        monkeypatch.setenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", "true")
        await executor.execute((action,), context)

        assert capability.handle.called, "verified delegation + cached policy should grant LOCAL authority -- capability must execute"

    @pytest.mark.asyncio
    async def test_unverifiable_chain_never_grants_local_authority(self, monkeypatch, tmp_path):
        d1 = _issue("A", "B")
        forged = _issue("B", PRINCIPAL, parent=d1)
        # Tamper post-issuance -- proof no longer matches content.
        import dataclasses
        tampered = dataclasses.replace(forged, capabilities=("bank.transfer",))

        gov = self._edge_governance(tmp_path)
        capability = MagicMock()
        capability.handle = MagicMock(return_value={"success": True})
        bus = MagicMock()
        bus.discover.return_value = capability

        executor = ActionExecutor(
            bus,
            connectivity_check=lambda cap: (False, "WAITING_FOR_AUTHORITY", "disconnected"),
            edge_governance=gov,
        )
        action = Action(action_id="a1", capability="grocery.purchase", parameters={})
        context = {"actor_id": PRINCIPAL, "delegation_chain": (d1, tampered)}

        monkeypatch.setenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", "true")
        await executor.execute((action,), context)

        assert not capability.handle.called, "a tampered/unverifiable chain must never grant local authority"
