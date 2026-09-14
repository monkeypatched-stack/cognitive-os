"""Regression tests for SOC 2 control remediations F1–F7."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from src.monkey_brain.kernel.audit import (
    AuditLog,
    AuditPersistenceError,
    MemoryDurableAuditStore,
)
from src.monkey_brain.kernel.production_gates import (
    block_direct_world_api_mutations,
    idempotency_fail_closed,
    insecure_dev_mode,
    mfa_required,
    require_opa,
    require_redis,
    validate_production_gates,
)
from src.monkey_brain.kernel.trusted_auth import (
    MFA_UNKNOWN,
    TrustedAuthEvidence,
    bind_trusted_auth,
    evidence_from_jwt,
    mfa_allows_operation,
    strip_untrusted_security_signals,
    unauthenticated_evidence,
)


@pytest.fixture
def secure_env(monkeypatch):
    monkeypatch.delenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", raising=False)
    monkeypatch.delenv("COGNITIVEOS_PRODUCTION_MODE", raising=False)
    monkeypatch.delenv("REQUIRE_REDIS", raising=False)
    monkeypatch.delenv("OPA_REQUIRED", raising=False)
    monkeypatch.delenv("IDEMPOTENCY_FAIL_CLOSED", raising=False)
    monkeypatch.delenv("ALLOW_DIRECT_WORLD_API", raising=False)
    monkeypatch.delenv("COGNITIVEOS_MFA_REQUIRED", raising=False)
    monkeypatch.delenv("AGENTOS_OPA_ENFORCE", raising=False)
    return monkeypatch


class TestF1JwtSecrets:
    def test_rejects_placeholder(self):
        from services.common.secrets import reject_insecure_hmac_secret

        with pytest.raises(ValueError, match="placeholder"):
            reject_insecure_hmac_secret("REPLACE_ME", name="ACCESS_TOKEN_SECRET")

    def test_rejects_compose_default(self):
        from services.common.secrets import reject_insecure_hmac_secret

        with pytest.raises(ValueError):
            reject_insecure_hmac_secret("dev-access-token-secret")

    def test_rejects_short_secret(self):
        from services.common.secrets import reject_insecure_hmac_secret

        with pytest.raises(ValueError, match="32"):
            reject_insecure_hmac_secret("short-but-not-placeholder")

    def test_accepts_unique_secret(self):
        from services.common.secrets import reject_insecure_hmac_secret

        value = reject_insecure_hmac_secret("unit-test-hmac-key-not-a-placeholder!!")
        assert len(value) >= 32

    def test_env_validation_missing(self, monkeypatch):
        from services.common.secrets import validate_hmac_secrets_from_env

        monkeypatch.delenv("ACCESS_TOKEN_SECRET", raising=False)
        monkeypatch.delenv("REFRESH_TOKEN_SECRET", raising=False)
        with pytest.raises(RuntimeError, match="missing"):
            validate_hmac_secrets_from_env()

    def test_jwt_roundtrip_with_valid_secret(self):
        from services.auth.helpers.tokens import (
            create_access_token,
            decode_access_token,
        )

        token = create_access_token(
            "u1",
            "u@example.com",
            "user",
            permissions=["perm-view-x"],
            mfa_status="satisfied",
        )
        payload = decode_access_token(token)
        assert payload["sub"] == "u1"
        assert payload["mfa_status"] == "satisfied"


class TestF2DurableAudit:
    def test_persist_and_query_across_log_instances(self):
        backing: dict = {}
        store = MemoryDurableAuditStore(backing)
        log = AuditLog()
        log.set_store(store)
        entry = log.record("rt", "execute", "plan.execute", actor="alice", outcome="success")
        log2 = AuditLog()
        log2.set_store(MemoryDurableAuditStore(backing))
        found = log2.query(runtime_id="rt", event_type="execute")
        assert any(e.entry_id == entry.entry_id for e in found)

    def test_persist_failure_fail_closed(self):
        class Boom:
            def append(self, *a, **k):
                raise RuntimeError("mongo down")

        log = AuditLog()
        log.set_store(Boom())
        with pytest.raises(AuditPersistenceError):
            log.record("rt", "execute", "commit", actor="alice")

    def test_non_critical_does_not_raise(self):
        class Boom:
            def append(self, *a, **k):
                raise RuntimeError("mongo down")

        log = AuditLog()
        log.set_store(Boom())
        entry = log.record("rt", "proposal", "debug", actor="alice", critical=False)
        assert entry.action == "debug"

    def test_append_only(self):
        store = MemoryDurableAuditStore()
        payload = {"entry_id": "same", "runtime_id": "rt", "event_type": "execute"}
        store.append("rt", "audit.execute", payload)
        with pytest.raises(AuditPersistenceError):
            store.append("rt", "audit.execute", payload)

    def test_correlation_and_policy(self):
        store = MemoryDurableAuditStore()
        log = AuditLog()
        log.set_store(store)
        entry = log.record(
            "rt",
            "authorization",
            "execute",
            actor="alice",
            correlation_id="req-1",
            policy_decision="allow",
        )
        assert entry.details["correlation_id"] == "req-1"
        assert entry.details["policy_decision"] == "allow"


class TestF3MfaTrusted:
    def test_agent_cannot_self_attest(self):
        stripped = strip_untrusted_security_signals({"mfa_enforced": True, "has_user_access": True})
        assert "mfa_enforced" not in stripped
        assert stripped["has_user_access"] is True

    def test_unknown_mfa_fail_closed(self, secure_env):
        bind_trusted_auth(unauthenticated_evidence())
        assert mfa_required() is True
        assert mfa_allows_operation() is False

    def test_jwt_unknown_mfa_denied(self, secure_env):
        ev = evidence_from_jwt({"sub": "alice", "mfa_status": MFA_UNKNOWN})
        bind_trusted_auth(ev)
        assert mfa_allows_operation(ev) is False

    def test_satisfied_mfa_allows(self, secure_env):
        ev = evidence_from_jwt({"sub": "alice", "mfa_status": "satisfied"})
        assert mfa_allows_operation(ev) is True

    def test_compliance_agent_ignores_payload_mfa(self, secure_env):
        from broca.agents.ddd.compliance.soc2 import SOC2Agent

        bind_trusted_auth(unauthenticated_evidence())
        agent = SOC2Agent()
        perception = agent.perceive(
            {
                "data_signals": {"has_user_access": True, "mfa_enforced": True},
                "system_attributes": {"mfa_enforced": True},
            }
        )
        assert perception["signals"]["mfa_enforced"] is False


class TestF4RedisDiscovery:
    def test_empty_redis_falls_back_to_mongo(self):
        from src.monkey_brain.kernel.society.integration import (
            ActorRegistryEntry,
            PlanetaryRuntime,
        )

        pr = SimpleNamespace(
            _redis=SimpleNamespace(hgetall=lambda k: {}, hget=lambda k, i: None, hset=lambda *a, **k: None),
            _societies={},
            _node_id="n1",
            _boot_time=0.0,
            _artifact_version="",
            _runtime_version="",
            _redis_reconstructor=None,
            _home_society_runtime=lambda actor_id: None,
        )
        pr._ACTORS_HASH_KEY = PlanetaryRuntime._ACTORS_HASH_KEY
        pr.rebuild_redis_index_from_mongodb = lambda: SimpleNamespace(summary=lambda: "ok")
        entry = ActorRegistryEntry(
            actor_id="a1",
            actor_type="human",
            name="alice",
            society_id="s1",
            status="active",
            node_id="n1",
            updated_at=1.0,
        )
        pr._list_registry_from_mongodb = lambda: (entry,)
        pr._locate_actor_from_mongodb = lambda aid: entry if aid == "a1" else None
        pr._cache_registry_entry = lambda e: None
        pr._registry_entry_from_dict = PlanetaryRuntime._registry_entry_from_dict
        found = PlanetaryRuntime.locate_actor(pr, "a1")
        listed = PlanetaryRuntime.list_registry(pr)
        assert found is not None and found.actor_id == "a1"
        assert listed[0].actor_id == "a1"


class TestF5SecureByDefault:
    def test_no_special_env_enables_controls(self, secure_env):
        assert insecure_dev_mode() is False
        assert require_redis() is True
        assert require_opa() is True
        assert idempotency_fail_closed() is True
        assert block_direct_world_api_mutations() is True
        assert mfa_required() is True

    def test_insecure_dev_rejected_with_production(self, monkeypatch):
        monkeypatch.setenv("COGNITIVEOS_PRODUCTION_MODE", "true")
        monkeypatch.setenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", "true")
        with pytest.raises(RuntimeError, match="cannot be combined"):
            validate_production_gates(redis_available=True, opa_configured=True)

    def test_auth_required_false_rejected_without_insecure_dev(self, monkeypatch):
        monkeypatch.delenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", raising=False)
        monkeypatch.setenv("AGENTOS_AUTH_REQUIRED", "false")
        with pytest.raises(RuntimeError, match="AGENTOS_AUTH_REQUIRED"):
            validate_production_gates(redis_available=True, opa_configured=True)

    def test_world_mutations_blocked_without_production_flag(self, secure_env):
        assert block_direct_world_api_mutations() is True


class TestF7OpaDenyByDefault:
    @pytest.mark.asyncio
    async def test_missing_identity_deny(self, secure_env):
        from src.monkey_brain.kernel.governance import GovernanceEngine

        bind_trusted_auth(unauthenticated_evidence())
        engine = GovernanceEngine()
        result = await engine.evaluate("rt", "execute", {})
        assert result["allowed"] is False

    @pytest.mark.asyncio
    async def test_opa_unavailable_deny(self, secure_env, monkeypatch):
        from src.monkey_brain.kernel.governance import GovernanceEngine

        monkeypatch.setenv("OPA_URL", "http://opa.internal:8181")

        async def boom(*a, **k):
            raise ConnectionError("down")

        monkeypatch.setattr("services.common.opa.evaluate_full", boom)
        engine = GovernanceEngine()
        result = await engine.evaluate("rt", "execute", {})
        assert result["allowed"] is False

    @pytest.mark.asyncio
    async def test_explicit_allow(self, secure_env, monkeypatch):
        from src.monkey_brain.kernel.governance import GovernanceEngine

        monkeypatch.setenv("OPA_URL", "http://opa.internal:8181")

        async def allow(*a, **k):
            return {"allowed": True, "reason": "", "source": "opa"}

        monkeypatch.setattr("services.common.opa.evaluate_full", allow)
        bind_trusted_auth(
            TrustedAuthEvidence(
                authenticated=True,
                token_valid=True,
                principal_id="alice",
                principal_type="human",
                mfa_status="satisfied",
            )
        )
        engine = GovernanceEngine()
        result = await engine.evaluate("rt", "execute", {})
        assert result["allowed"] is True

    def test_soc2_rego_defaults_deny(self):
        text = open("opa/policies/compliance/soc2.rego").read()
        assert "default allow := false" in text or "default allow = false" in text
        assert "mfa_enforced" not in text
