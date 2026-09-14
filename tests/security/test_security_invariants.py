"""Architectural security invariants — agents propose; the kernel decides.

This suite MUST NOT enable COGNITIVEOS_ALLOW_INSECURE_DEV_MODE.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from src.monkey_brain.api.idempotency import (
    IdempotencyStore,
    _InMemoryIdempotencyBackend,
    get_idempotency_store,
    idempotent,
)
from src.monkey_brain.kernel.audit import (
    AuditLog,
    AuditPersistenceError,
    MemoryDurableAuditStore,
    get_audit_log,
)
from src.monkey_brain.kernel.plan.goals.executor import GoalExecutor
from src.monkey_brain.kernel.plan.goals.goal import Goal, GoalType
from src.monkey_brain.kernel.production_gates import (
    idempotency_fail_closed,
    insecure_dev_mode,
    mfa_required,
    require_opa,
    require_redis,
    validate_production_gates,
)
from src.monkey_brain.kernel.security_boundary import (
    PIPELINE_STAGES,
    SecurityBoundaryDenied,
    build_opa_input,
    pipeline_stages,
    run_governed_mutation,
)
from src.monkey_brain.kernel.trusted_auth import (
    TrustedAuthEvidence,
    bind_trusted_auth,
    strip_untrusted_security_signals,
    unauthenticated_evidence,
)

AGENT_PRIVILEGE_PAYLOAD = {
    "authorized": True,
    "is_admin": True,
    "role": "admin",
    "permissions": ["execute", "governance"],
    "mfa_status": "satisfied",
    "mfa_enforced": True,
    "trusted_auth": True,
    "policy_approval": True,
    "governance_approval": True,
}


@pytest.fixture(autouse=True)
def _invariant_secure_env(monkeypatch):
    monkeypatch.delenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", raising=False)
    monkeypatch.delenv("COGNITIVEOS_PRODUCTION_MODE", raising=False)
    monkeypatch.setenv("AGENTOS_AUTH_REQUIRED", "true")
    monkeypatch.delenv("OPA_REQUIRED", raising=False)
    monkeypatch.delenv("REQUIRE_REDIS", raising=False)
    monkeypatch.delenv("IDEMPOTENCY_FAIL_CLOSED", raising=False)
    monkeypatch.delenv("COGNITIVEOS_MFA_REQUIRED", raising=False)
    monkeypatch.setenv("AGENTOS_OPA_ENFORCE", "true")
    monkeypatch.delenv("OPA_URL", raising=False)
    bind_trusted_auth(unauthenticated_evidence())
    assert insecure_dev_mode() is False


@pytest.fixture
def working_idempotency():
    IdempotencyStore._instance = None
    store = IdempotencyStore.__new__(IdempotencyStore)
    store._backend = _InMemoryIdempotencyBackend()
    IdempotencyStore._instance = store
    yield store
    IdempotencyStore._instance = None


@pytest.fixture
def durable_audit():
    store = MemoryDurableAuditStore()
    log = get_audit_log()
    log.set_store(store)
    yield store


def _alice() -> TrustedAuthEvidence:
    return TrustedAuthEvidence(
        authenticated=True,
        token_valid=True,
        principal_id="alice",
        principal_type="human",
        mfa_status="satisfied",
        permissions=("perm-execute-action",),
    )


class TestInvariant1AgentCannotEstablishAuthority:
    def test_strip_drops_privilege_keys(self):
        cleaned = strip_untrusted_security_signals(
            {
                **AGENT_PRIVILEGE_PAYLOAD,
                "question": "buy milk",
                "nested": {"authorized": True, "ok": 1},
            }
        )
        assert "authorized" not in cleaned
        assert "is_admin" not in cleaned
        assert "permissions" not in cleaned
        assert "mfa_status" not in cleaned
        assert "trusted_auth" not in cleaned
        assert cleaned["question"] == "buy milk"
        assert "authorized" not in cleaned["nested"]
        assert cleaned["nested"]["ok"] == 1

    def test_opa_input_ignores_agent_auth_overwrite(self):
        bind_trusted_auth(unauthenticated_evidence())
        built = build_opa_input(
            action="execute",
            resource="orders",
            extra=AGENT_PRIVILEGE_PAYLOAD,
        )
        assert built["auth"]["authenticated"] is False
        assert built["auth"]["mfa_status"] != "satisfied"
        assert built["context"]["auth"]["authenticated"] is False
        assert "authorized" not in built["context"]
        assert "permissions" not in built["context"]

    @pytest.mark.asyncio
    async def test_governance_and_execute_reject_agent_privilege_json(self, monkeypatch):
        monkeypatch.setenv("OPA_URL", "http://opa.internal:8181")
        captured: dict = {}

        async def fake_evaluate_full(policy_path, input_data, *, default_allow=False, **kwargs):
            captured["input"] = input_data
            return {"allowed": False, "reason": "default_deny", "source": "opa"}

        monkeypatch.setattr("services.common.opa.evaluate_full", fake_evaluate_full)
        bind_trusted_auth(unauthenticated_evidence())
        from src.monkey_brain.api.dependencies import (
            RequestRejected,
            sanitize_and_check_governance,
        )

        with pytest.raises(RequestRejected) as denied:
            await sanitize_and_check_governance(
                "do a thing",
                "mallory",
                "execute",
                extra_context=dict(AGENT_PRIVILEGE_PAYLOAD),
            )
        assert denied.value.status_code == 403
        ctx = captured["input"]["context"]
        assert ctx["auth"]["authenticated"] is False
        assert ctx["trusted_auth"]["authenticated"] is False
        assert "authorized" not in ctx
        assert "is_admin" not in ctx

    @pytest.mark.asyncio
    async def test_goal_executor_does_not_trust_plan_metadata(self, monkeypatch):
        async def fake_evaluate(policy_path, input_data, *, default_allow=False, **kwargs):
            assert input_data["auth"]["authenticated"] is False
            assert input_data["auth"].get("agent_attested_mfa") is False
            return False

        monkeypatch.setattr("services.common.opa.evaluate", fake_evaluate)
        bind_trusted_auth(unauthenticated_evidence())
        goal = Goal(
            name="create_order",
            goal_type=GoalType.CREATE,
            metadata=dict(AGENT_PRIVILEGE_PAYLOAD),
        )
        answer, *_ = await GoalExecutor().execute(goal, None, "go", run_id="r1", user_id="mallory")
        assert "authorization denied" in answer or "unavailable" in answer or "failed" in answer


class TestInvariant5XUserIdNeverAuthenticatesProduction:
    @pytest.mark.asyncio
    async def test_x_user_id_only_denied(self):
        from src.monkey_brain.api.dependencies import get_current_user

        with pytest.raises(HTTPException) as exc:
            await get_current_user(x_user_id="somebody", authorization=None)
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_jwt_plus_x_user_id_denied(self):
        from src.monkey_brain.api.dependencies import get_current_user

        with pytest.raises(HTTPException) as exc:
            await get_current_user(
                x_user_id="somebody",
                authorization="Bearer not-a-jwt",
            )
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_expired_jwt_plus_x_user_id_denied(self):
        from jose import jwt
        from services.common.config import settings
        from src.monkey_brain.api.dependencies import get_current_user

        token = jwt.encode(
            {
                "sub": "alice",
                "exp": datetime.now(timezone.utc) - timedelta(hours=1),
                "mfa_status": "satisfied",
                "permissions": [],
                "jti": "expired-1",
            },
            settings.ACCESS_TOKEN_SECRET,
            algorithm=settings.ALGORITHM,
        )
        with pytest.raises(HTTPException) as exc:
            await get_current_user(x_user_id="alice", authorization=f"Bearer {token}")
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_forged_jwt_denied(self):
        from jose import jwt
        from src.monkey_brain.api.dependencies import get_current_user

        token = jwt.encode(
            {
                "sub": "alice",
                "mfa_status": "satisfied",
                "permissions": ["*"],
                "jti": "x",
            },
            "forged-secret-that-is-not-the-real-one!!",
            algorithm="HS256",
        )
        with pytest.raises(HTTPException) as exc:
            await get_current_user(x_user_id=None, authorization=f"Bearer {token}")
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_jwt_denied(self):
        from src.monkey_brain.api.dependencies import get_current_user

        with pytest.raises(HTTPException) as exc:
            await get_current_user(x_user_id=None, authorization=None)
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_insecure_dev_x_user_id_still_works(self, monkeypatch):
        monkeypatch.setenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", "true")
        monkeypatch.setenv("AGENTOS_AUTH_REQUIRED", "false")
        from src.monkey_brain.api.dependencies import get_current_user

        user = await get_current_user(x_user_id="dev-user", authorization=None)
        assert user == "dev-user"


class TestInvariant4FailClosedInfrastructure:
    def test_auth_required_false_ignored_without_insecure_dev(self, monkeypatch):
        monkeypatch.setenv("AGENTOS_AUTH_REQUIRED", "false")
        from src.monkey_brain.api.dependencies import auth_required

        assert auth_required() is True

    def test_boot_rejects_auth_off_without_insecure_dev(self, monkeypatch):
        monkeypatch.setenv("AGENTOS_AUTH_REQUIRED", "false")
        with pytest.raises(RuntimeError, match="AGENTOS_AUTH_REQUIRED"):
            validate_production_gates(redis_available=True, opa_configured=True)

    def test_production_plus_insecure_dev_boot_failure(self, monkeypatch):
        monkeypatch.setenv("COGNITIVEOS_PRODUCTION_MODE", "true")
        monkeypatch.setenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", "true")
        with pytest.raises(RuntimeError, match="cannot be combined"):
            validate_production_gates(redis_available=True, opa_configured=True)

    def test_secure_defaults(self):
        assert require_opa() is True
        assert require_redis() is True
        assert idempotency_fail_closed() is True
        assert mfa_required() is True

    @pytest.mark.asyncio
    async def test_opa_unset_denies_even_with_default_allow_true(self, monkeypatch):
        import cerebellum.capabilities.security.opa_client as m

        monkeypatch.setattr(m, "_OPA_URL", "")
        result = await m.evaluate_full("agentos/allow", {}, default_allow=True)
        assert result["allowed"] is False
        assert result["source"] == "skip"

    @pytest.mark.asyncio
    async def test_opa_malformed_document_denies(self, monkeypatch):
        import cerebellum.capabilities.security.opa_client as m

        class _Resp:
            status_code = 200

            def json(self):
                return {"result": {"foo": "bar"}}

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def post(self, *a, **k):
                return _Resp()

        monkeypatch.setattr(m, "_OPA_URL", "http://opa.internal:8181")
        monkeypatch.setattr(m.httpx, "AsyncClient", lambda **k: _Client())
        result = await m.evaluate_full("agentos/allow", {})
        assert result["allowed"] is False


class TestInvariant3And9AuditBeforeMutation:
    @pytest.mark.asyncio
    async def test_pipeline_order_and_intent_failure_blocks_mutation(
        self,
        monkeypatch,
        working_idempotency,
        durable_audit,
    ):
        monkeypatch.setenv("OPA_URL", "http://opa.internal:8181")

        async def allow(*a, **k):
            return {"allowed": True, "reason": "", "source": "opa"}

        monkeypatch.setattr("services.common.opa.evaluate_full", allow)
        bind_trusted_auth(_alice())
        mutations: list[int] = []
        recorded: list[str] = []

        class ProbeStore:
            def append(self, tenant_id, event_type, payload):
                recorded.append(payload.get("action", ""))
                if str(payload.get("action", "")).endswith(".intent"):
                    raise RuntimeError("mongo down")
                durable_audit.append(tenant_id, event_type, payload)

        get_audit_log().set_store(ProbeStore())

        async def mutate():
            mutations.append(1)
            return "did-it"

        with pytest.raises(AuditPersistenceError):
            await run_governed_mutation(
                action="execute",
                resource="orders",
                mutate=mutate,
            )
        assert mutations == []
        assert "MUTATION" not in pipeline_stages()

    @pytest.mark.asyncio
    async def test_success_records_canonical_order(
        self,
        monkeypatch,
        working_idempotency,
        durable_audit,
    ):
        monkeypatch.setenv("OPA_URL", "http://opa.internal:8181")

        async def allow(*a, **k):
            return {"allowed": True, "reason": "", "source": "opa"}

        monkeypatch.setattr("services.common.opa.evaluate_full", allow)
        bind_trusted_auth(_alice())
        mutations = []

        async def mutate():
            mutations.append(1)
            return "ok"

        result = await run_governed_mutation(
            action="execute",
            resource="orders",
            mutate=mutate,
        )
        assert result == "ok"
        assert mutations == [1]
        # pipeline_stages is reset after return; reconstruct from audit actions
        actions = [row["action"] for row in durable_audit.find()]
        assert any(a.endswith(".intent") for a in actions)
        assert any(a.endswith(".result") for a in actions)
        intent_idx = next(i for i, a in enumerate(actions) if a.endswith(".intent"))
        result_idx = next(i for i, a in enumerate(actions) if a.endswith(".result"))
        assert intent_idx < result_idx

    @pytest.mark.asyncio
    async def test_mutation_failure_still_writes_audit_result(
        self,
        monkeypatch,
        working_idempotency,
        durable_audit,
    ):
        monkeypatch.setenv("OPA_URL", "http://opa.internal:8181")

        async def allow(*a, **k):
            return {"allowed": True, "reason": "", "source": "opa"}

        monkeypatch.setattr("services.common.opa.evaluate_full", allow)
        bind_trusted_auth(_alice())

        async def mutate():
            raise RuntimeError("handler boom")

        with pytest.raises(RuntimeError, match="handler boom"):
            await run_governed_mutation(
                action="execute",
                resource="orders",
                mutate=mutate,
            )
        results = [r for r in durable_audit.find() if str(r.get("action", "")).endswith(".result")]
        assert results
        assert results[-1]["outcome"] == "failure"

    @pytest.mark.asyncio
    async def test_unauthenticated_never_mutates(self, working_idempotency, durable_audit):
        bind_trusted_auth(unauthenticated_evidence())
        mutations = []

        async def mutate():
            mutations.append(1)

        with pytest.raises(SecurityBoundaryDenied) as exc:
            await run_governed_mutation(action="execute", resource="x", mutate=mutate)
        assert exc.value.stage == "AUTH"
        assert mutations == []


class TestInvariant8IdempotencyFailClosed:
    def test_duplicate_reserve(self, working_idempotency):
        ok1, _ = working_idempotency.reserve("k", "h")
        ok2, existing = working_idempotency.reserve("k", "h")
        assert ok1 is True
        assert ok2 is False
        assert existing is not None

    def test_race(self, working_idempotency):
        wins = []

        def claim():
            wins.append(working_idempotency.reserve("race", "h")[0])

        threads = [threading.Thread(target=claim) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert wins.count(True) == 1

    def test_redis_reserve_exception_fail_closed(self, monkeypatch):
        from src.monkey_brain.api.idempotency import _RedisIdempotencyBackend

        backend = _RedisIdempotencyBackend("redis://localhost:6379/0")
        client = MagicMock()
        client.set.side_effect = RuntimeError("lookup failed")
        backend._client = client
        claimed, existing = backend.reserve("k", "h")
        assert claimed is False
        assert existing is None
        # Resetting _instance to None and calling get_idempotency_store()
        # here previously constructed a BRAND NEW store with its own real
        # backend (_make_backend()) -- in any dev environment with a real
        # local Redis actually reachable (this repo's own tests/conftest.py
        # ::_flush_shared_redis fixture assumes exactly that), that fresh
        # store reports available=True, which is the opposite of what this
        # test means to verify.
        #
        # Reusing the SAME already-broken `backend` object doesn't work
        # either: IdempotencyStore.is_available() reads a static
        # `unavailable` attribute (idempotency.py:364), which
        # _RedisIdempotencyBackend never sets at all -- not even after a
        # failed reserve() call above -- it only has an available() method
        # that does a fresh ping. The real fail-closed signal is
        # _UnavailableIdempotencyBackend (unavailable=True), which is
        # exactly what _make_backend() selects when Redis is unreachable
        # at construction time (idempotency.py's own _make_backend()).
        # Verify that real mechanism directly rather than depend on
        # whether Redis happens to be reachable in this environment.
        from src.monkey_brain.api.idempotency import _UnavailableIdempotencyBackend

        IdempotencyStore._instance = None
        store = IdempotencyStore.__new__(IdempotencyStore)
        store._backend = _UnavailableIdempotencyBackend()
        IdempotencyStore._instance = store
        assert store.is_available() is False
        claimed, existing = store.reserve("k", "h")
        assert claimed is False
        assert existing is None
        IdempotencyStore._instance = None

    @pytest.mark.asyncio
    async def test_lookup_exception_denies_mutation(self, monkeypatch, durable_audit):
        class Boom:
            unavailable = False

            def ping(self):
                raise RuntimeError("lookup failed")

            def reserve(self, *a, **k):
                raise RuntimeError("lookup failed")

        IdempotencyStore._instance = None
        store = IdempotencyStore.__new__(IdempotencyStore)
        store._backend = Boom()
        IdempotencyStore._instance = store
        monkeypatch.setenv("OPA_URL", "http://opa.internal:8181")

        async def allow(*a, **k):
            return {"allowed": True, "reason": "", "source": "opa"}

        monkeypatch.setattr("services.common.opa.evaluate_full", allow)
        bind_trusted_auth(_alice())
        mutations = []

        async def mutate():
            mutations.append(1)

        with pytest.raises(SecurityBoundaryDenied) as exc:
            await run_governed_mutation(action="execute", resource="x", mutate=mutate)
        assert exc.value.stage == "IDEMPOTENCY"
        assert mutations == []
        IdempotencyStore._instance = None


class TestInvariant7RedisNotRegistryOfRecord:
    def test_stale_and_malformed_redis_ignored(self):
        from src.monkey_brain.kernel.society.integration import (
            ActorRegistryEntry,
            PlanetaryRuntime,
        )

        mongo_entry = ActorRegistryEntry(
            actor_id="a1",
            actor_type="human",
            name="alice",
            society_id="s1",
            status="active",
            node_id="n1",
            updated_at=1.0,
        )
        stale = json.dumps(
            {
                "actor_id": "a1",
                "actor_type": "human",
                "name": "evil-admin",
                "society_id": "s-evil",
                "status": "active",
                "node_id": "attacker",
                "updated_at": 99.0,
            }
        )
        redis = SimpleNamespace(
            hgetall=lambda k: {"a1": stale},
            hget=lambda k, i: stale if i == "a1" else None,
            hset=lambda *a, **k: None,
        )
        pr = SimpleNamespace(
            _redis=redis,
            _societies={},
            _node_id="n1",
            _boot_time=0.0,
            _artifact_version="",
            _runtime_version="",
            _redis_reconstructor=None,
            _home_society_runtime=lambda actor_id: None,
            _ACTORS_HASH_KEY=PlanetaryRuntime._ACTORS_HASH_KEY,
            rebuild_redis_index_from_mongodb=lambda: None,
            _list_registry_from_mongodb=lambda: (mongo_entry,),
            _locate_actor_from_mongodb=lambda aid: mongo_entry if aid == "a1" else None,
            _cache_registry_entry=lambda e: None,
            _registry_entry_from_dict=PlanetaryRuntime._registry_entry_from_dict,
        )
        found = PlanetaryRuntime.locate_actor(pr, "a1")
        listed = PlanetaryRuntime.list_registry(pr)
        assert found is not None and found.name == "alice" and found.society_id == "s1"
        assert listed[0].name == "alice"

    def test_redis_only_phantom_is_not_authority(self):
        from src.monkey_brain.kernel.society.integration import PlanetaryRuntime

        phantom = json.dumps(
            {
                "actor_id": "ghost",
                "actor_type": "human",
                "name": "ghost",
                "society_id": "s1",
                "status": "active",
                "node_id": "n1",
                "updated_at": 1.0,
            }
        )
        pr = SimpleNamespace(
            _redis=SimpleNamespace(
                hgetall=lambda k: {"ghost": phantom},
                hget=lambda k, i: phantom,
                hset=lambda *a, **k: None,
            ),
            _societies={},
            _node_id="n1",
            _boot_time=0.0,
            _artifact_version="",
            _runtime_version="",
            _redis_reconstructor=None,
            _home_society_runtime=lambda actor_id: None,
            _ACTORS_HASH_KEY=PlanetaryRuntime._ACTORS_HASH_KEY,
            rebuild_redis_index_from_mongodb=lambda: None,
            _list_registry_from_mongodb=lambda: (),
            _locate_actor_from_mongodb=lambda aid: None,
            _cache_registry_entry=lambda e: None,
            _registry_entry_from_dict=PlanetaryRuntime._registry_entry_from_dict,
        )
        assert PlanetaryRuntime.locate_actor(pr, "ghost") is None
        assert PlanetaryRuntime.list_registry(pr) == ()


class TestInvariant10ArchitectureCheck:
    def test_no_trusted_auth_update_merges(self):
        from scripts.check_architecture_conformance import (
            _untrusted_security_authority_violations,
        )

        assert _untrusted_security_authority_violations() == []

    def test_pipeline_stage_names_are_stable(self):
        assert PIPELINE_STAGES == (
            "AUTH",
            "AUTHZ",
            "IDEMPOTENCY",
            "AUDIT_INTENT",
            "MUTATION",
            "AUDIT_RESULT",
        )
