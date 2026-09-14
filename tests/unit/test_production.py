"""Production tests — identity, trust, storage, security, governance, audit."""

from __future__ import annotations

import pytest
import time

# ── Identity Tests ──────────────────────────────────────────────────────────


class TestRuntimeIdentity:
    def test_create_identity(self):
        from src.monkey_brain.kernel.identity import create_identity

        id1 = create_identity("test-rt-1", runtime_type="enterprise", owner="acme")
        assert id1.is_valid
        assert id1.runtime_type == "enterprise"
        assert id1.owner == "acme"
        assert "PUBLIC KEY" in id1.public_key_pem

    def test_sign_and_verify(self):
        from src.monkey_brain.kernel.identity import (
            create_identity,
            sign_payload,
            verify_signed_payload,
            get_key_manager,
        )

        id1 = create_identity("signer-1")
        km = get_key_manager()
        key = km.get_or_create("signer-1")

        envelope = sign_payload({"data": "test"}, key, "signer-1")
        valid, reason = verify_signed_payload(envelope, id1.public_key_pem)
        assert valid
        assert reason == "ok"

    def test_wrong_key_rejects(self):
        from src.monkey_brain.kernel.identity import (
            create_identity,
            sign_payload,
            verify_signed_payload,
            get_key_manager,
        )

        id1 = create_identity("signer-a")
        id2 = create_identity("signer-b")
        km = get_key_manager()
        key = km.get_or_create("signer-a")

        envelope = sign_payload({"data": "test"}, key, "signer-a")
        valid, reason = verify_signed_payload(envelope, id2.public_key_pem)
        assert not valid
        assert reason == "signature_invalid"

    def test_tamper_detection(self):
        from src.monkey_brain.kernel.identity import (
            create_identity,
            sign_payload,
            verify_signed_payload,
            get_key_manager,
        )

        id1 = create_identity("tamper-test")
        km = get_key_manager()
        key = km.get_or_create("tamper-test")

        envelope = sign_payload({"answer": "42"}, key, "tamper-test")
        tampered = {**envelope, "answer": "99"}
        valid, reason = verify_signed_payload(tampered, id1.public_key_pem)
        assert not valid

    def test_replay_protection(self):
        from src.monkey_brain.kernel.identity import (
            NonceStore,
            create_identity,
            sign_payload,
            verify_signed_payload,
            get_key_manager,
        )

        id1 = create_identity("replay-test")
        km = get_key_manager()
        key = km.get_or_create("replay-test")
        ns = NonceStore()

        envelope = sign_payload({"data": "x"}, key, "replay-test")
        valid1, _ = verify_signed_payload(envelope, id1.public_key_pem, nonce_store=ns)
        assert valid1

        valid2, reason = verify_signed_payload(envelope, id1.public_key_pem, nonce_store=ns)
        assert not valid2
        assert reason == "replay_detected"

    def test_timestamp_expired(self):
        from src.monkey_brain.kernel.identity import (
            create_identity,
            sign_payload,
            verify_signed_payload,
            get_key_manager,
        )

        id1 = create_identity("expire-test")
        km = get_key_manager()
        key = km.get_or_create("expire-test")

        envelope = sign_payload({"data": "old"}, key, "expire-test")
        envelope["_timestamp"] = time.time() - 7200  # 2 hours ago
        valid, reason = verify_signed_payload(envelope, id1.public_key_pem, max_age=3600)
        assert not valid
        assert reason == "timestamp_expired"


# ── Trust Tests ─────────────────────────────────────────────────────────────


class TestTrustInfrastructure:
    def test_typed_relationship(self):
        from src.monkey_brain.kernel.compile.trust import (
            TrustNetwork,
            Relationship,
            Perm,
        )

        tn = TrustNetwork()
        tn.connect("rt-a", "rt-b", Relationship.ENTERPRISE_TO_GOVERNMENT, trust=0.6)
        assert tn.permits("rt-a", "rt-b", Perm.SHARE_EXECUTION_GRAPHS)
        assert tn.trust("rt-a", "rt-b") == 0.6

    def test_revocation(self):
        from src.monkey_brain.kernel.compile.trust import TrustNetwork, Relationship

        tn = TrustNetwork()
        tn.connect("rt-a", "rt-b", Relationship.COLLEAGUE)
        assert tn.edge("rt-a", "rt-b") is not None

        tn.revoke("rt-a", "rt-b", revoked_by="admin")
        assert tn.edge("rt-a", "rt-b") is None
        assert len(tn._revoked) == 1

    def test_permission_revocation(self):
        from src.monkey_brain.kernel.compile.trust import (
            TrustNetwork,
            Relationship,
            Perm,
        )

        tn = TrustNetwork()
        tn.connect("rt-a", "rt-b", Relationship.COLLEAGUE)
        assert tn.permits("rt-a", "rt-b", Perm.EXECUTE_JOINTLY)

        tn.revoke("rt-a", "rt-b", permission=Perm.EXECUTE_JOINTLY)
        assert not tn.permits("rt-a", "rt-b", Perm.EXECUTE_JOINTLY)

    def test_reputation(self):
        from src.monkey_brain.kernel.compile.trust import TrustNetwork, Relationship

        tn = TrustNetwork()
        tn.connect("rt-a", "rt-b", Relationship.COLLEAGUE)
        rep = tn.update_reputation("rt-a", "rt-b", 0.1)
        assert rep == 0.6

    def test_audit_trail(self):
        from src.monkey_brain.kernel.compile.trust import TrustNetwork, Relationship

        tn = TrustNetwork()
        tn.connect("rt-a", "rt-b", Relationship.FRIEND)
        tn.grant("rt-a", "rt-b", "custom.perm")
        audit = tn.audit("rt-a", "rt-b")
        assert len(audit) == 2

    def test_delegation_path(self):
        from src.monkey_brain.kernel.compile.trust import TrustNetwork, Relationship

        tn = TrustNetwork()
        tn.connect("gov", "region", Relationship.GOVERNMENT_TO_GOVERNMENT)
        tn.connect("region", "enterprise", Relationship.GOVERNMENT_TO_GOVERNMENT)
        path = tn.delegation_path("gov", "enterprise", Relationship.GOVERNMENT_TO_GOVERNMENT)
        assert path == ["gov", "region", "enterprise"]


# ── Agreement Tests ─────────────────────────────────────────────────────────


class TestAgreements:
    def test_create_and_query(self):
        from src.monkey_brain.kernel.agreements import Agreement, AgreementStore

        store = AgreementStore()
        a = Agreement(participants=["a", "b"], knowledge_permissions={"graph"})
        store.register(a)
        assert a.is_active
        assert a.covers_knowledge("graph")
        assert not a.covers_knowledge("secret")

    def test_revoke(self):
        from src.monkey_brain.kernel.agreements import Agreement, AgreementStore

        store = AgreementStore()
        a = Agreement(participants=["a", "b"])
        store.register(a)
        store.revoke(a.agreement_id, revoked_by="admin")
        assert not a.is_active

    def test_expiry(self):
        from src.monkey_brain.kernel.agreements import Agreement, AgreementStore

        store = AgreementStore()
        a = Agreement(participants=["a", "b"], duration=1)  # 1 second
        import time

        time.sleep(1.1)  # wait for expiry
        assert not a.is_active
        assert a.is_expired


# ── Storage Tests ───────────────────────────────────────────────────────────


class TestStorage:
    def test_append_and_query(self):
        from src.monkey_brain.kernel.storage import AppendOnlyLog

        log = AppendOnlyLog()
        log.append("tenant-1", "test.event", {"key": "value"})
        log.append("tenant-1", "other.event", {"key": "value2"})
        events = log.query("tenant-1", event_type="test.event")
        assert len(events) == 1
        assert events[0].payload["key"] == "value"

    def test_revision_tracking(self):
        from src.monkey_brain.kernel.storage import AppendOnlyLog

        log = AppendOnlyLog()
        log.append("t1", "e1", {})
        log.append("t1", "e2", {})
        assert log.latest_revision("t1") == 2

    def test_tenant_isolation(self):
        from src.monkey_brain.kernel.storage import AppendOnlyLog

        log = AppendOnlyLog()
        log.append("t1", "e", {"v": 1})
        log.append("t2", "e", {"v": 2})
        assert len(log.query("t1")) == 1
        assert len(log.query("t2")) == 1
        assert log.query("t1")[0].payload["v"] == 1


# ── Security Tests ──────────────────────────────────────────────────────────


class TestSecurity:
    def test_identifier_validation(self):
        from src.monkey_brain.kernel.security import validate_identifier

        assert validate_identifier("valid_name")[0]
        assert not validate_identifier("")[0]
        assert not validate_identifier("has spaces")[0]
        assert not validate_identifier("a" * 300)[0]

    def test_domain_validation(self):
        from src.monkey_brain.kernel.security import validate_domain

        assert validate_domain("manufacturing")[0]
        assert not validate_domain("")[0]
        assert not validate_domain("Has Capital")[0]

    def test_input_sanitization(self):
        from src.monkey_brain.kernel.security import sanitize_input

        assert sanitize_input("  hello  ") == "hello"
        with pytest.raises(ValueError):
            sanitize_input("<script>alert('xss')</script>")

    def test_rate_limiter(self):
        from src.monkey_brain.kernel.security import RateLimiter

        rl = RateLimiter(rate=2, burst=2)
        assert rl.allow("user-1")
        assert rl.allow("user-1")
        assert not rl.allow("user-1")  # exhausted burst

    def test_bounded_queue(self):
        from src.monkey_brain.kernel.security import BoundedQueue

        q = BoundedQueue(max_size=3)
        assert q.put("a")
        assert q.put("b")
        assert q.put("c")
        assert not q.put("d")  # full
        assert q.get() == "a"
        assert q.metrics["dropped"] == 1


# ── Governance Tests ────────────────────────────────────────────────────────


class TestGovernance:
    """GovernanceEngine.evaluate() now delegates to real OPA
    (opa/policies/agentos_governance.rego) instead of the old in-memory
    RuntimeCharter/constitution-string-matching stub — register_charter()
    had zero callers anywhere in production, no charter was ever created
    at boot, and there was no API to make one, so real enforcement was
    never actually reachable. RuntimeCharter/register_charter/get_charter
    are kept only for an unrelated module's backward-compatible import;
    evaluate() no longer reads self._charters at all.
    """

    @pytest.mark.asyncio
    async def test_opa_evaluation_allow_and_deny(self, monkeypatch):
        from src.monkey_brain.kernel.governance import GovernanceEngine

        async def fake_evaluate_full(policy_path, input_data, *, default_allow=True):
            assert policy_path == "agentos/governance"
            deny = input_data["action"] == "export_classified"
            return {
                "allowed": not deny,
                "obligations": [],
                "source": "opa",
                "reason": 'charter denies action "export_classified"' if deny else "",
            }

        monkeypatch.setattr("services.common.opa.evaluate_full", fake_evaluate_full)

        engine = GovernanceEngine()
        result = await engine.evaluate("rt-1", "execute_query")
        assert result["allowed"]

        result2 = await engine.evaluate("rt-1", "export_classified")
        assert not result2["allowed"]

    @pytest.mark.asyncio
    async def test_unconfigured_governance_allows_but_warns(self, monkeypatch):
        # Was: a fresh engine DENIED everything ("no_charter"). Since register_charter() has no
        # callers in production and there is no API to create one, that denied /plan and
        # /execute to EVERY authenticated user (verified live: 403 governance_denied). An
        # unprovisioned fail-closed control is an outage, not security — so an engine with
        # OPA_URL unset (the real "not configured" signal now) skips the check (and logs it).
        monkeypatch.delenv("OPA_URL", raising=False)
        monkeypatch.setenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", "true")
        from src.monkey_brain.kernel.governance import GovernanceEngine

        engine = GovernanceEngine()
        result = await engine.evaluate("unknown-runtime", "anything")
        assert result["allowed"]
        assert result["reason"] == "governance_not_configured"

    @pytest.mark.asyncio
    async def test_opa_denial_is_enforced_once_configured(self, monkeypatch):
        # The security property still holds: once OPA is actually configured (reachable, real
        # policy loaded), a denied action IS denied — governance is genuinely enforceable now,
        # not just a data structure nothing ever populates.
        from src.monkey_brain.kernel.governance import GovernanceEngine

        async def fake_evaluate_full(policy_path, input_data, *, default_allow=True):
            return {
                "allowed": False,
                "obligations": [],
                "reason": "runtime_blocked",
                "source": "opa",
            }

        monkeypatch.setattr("services.common.opa.evaluate_full", fake_evaluate_full)

        engine = GovernanceEngine()
        result = await engine.evaluate("unknown-runtime", "anything")
        assert not result["allowed"]
        assert result["reason"] == "runtime_blocked"


# ── Audit Tests ─────────────────────────────────────────────────────────────


class TestAudit:
    def test_record_and_query(self):
        from src.monkey_brain.kernel.audit import AuditLog

        log = AuditLog()
        log.record("rt-1", "execute", "run_query", actor="user-1", outcome="success")
        entries = log.query("rt-1")
        assert len(entries) == 1
        assert entries[0].outcome == "success"

    def test_hash_chain_integrity(self):
        from src.monkey_brain.kernel.audit import AuditLog

        log = AuditLog()
        log.record("rt-1", "test", "a")
        log.record("rt-1", "test", "b")
        log.record("rt-1", "test", "c")
        valid, idx = log.verify_chain()
        assert valid

    def test_tamper_breaks_chain(self):
        from src.monkey_brain.kernel.audit import AuditLog

        log = AuditLog()
        log.record("rt-1", "test", "a")
        log.record("rt-1", "test", "b")
        # Tamper with entry
        log._entries[0].action = "TAMPERED"
        valid, idx = log.verify_chain()
        assert not valid
        assert idx == 0


# ── Integration Test ────────────────────────────────────────────────────────


class TestIntegration:
    def test_full_identity_trust_agreement_flow(self):
        """End-to-end: create identities, establish trust, make agreement, exchange."""
        from src.monkey_brain.kernel.identity import (
            create_identity,
            sign_payload,
            verify_signed_payload,
            get_key_manager,
        )
        from src.monkey_brain.kernel.compile.trust import TrustNetwork, Relationship
        from src.monkey_brain.kernel.agreements import Agreement, AgreementStore

        # Two runtimes
        rt_a = create_identity("enterprise-a", runtime_type="enterprise")
        rt_b = create_identity("enterprise-b", runtime_type="enterprise")

        # Establish trust
        tn = TrustNetwork()
        tn.connect("enterprise-a", "enterprise-b", Relationship.COLLEAGUE, trust=0.8)

        # Create agreement
        store = AgreementStore()
        agreement = Agreement(
            participants=["enterprise-a", "enterprise-b"],
            knowledge_permissions={"execution_graph", "workflow"},
            duration=3600,
        )
        store.register(agreement)

        # Sign and verify a proposal
        km = get_key_manager()
        key_a = km.get_or_create("enterprise-a")
        proposal = {"transitions": [("a", "b", 0.9)]}
        envelope = sign_payload(proposal, key_a, "enterprise-a")

        # Verify with the signer's public key (rt_a), not the receiver's
        valid, reason = verify_signed_payload(envelope, rt_a.public_key_pem)
        assert valid

        # Check agreement covers this knowledge
        found = store.covers_knowledge("enterprise-a", "enterprise-b", "execution_graph")
        assert found is not None

        # Check trust permits
        assert tn.permits("enterprise-a", "enterprise-b", "know.share_graphs")
