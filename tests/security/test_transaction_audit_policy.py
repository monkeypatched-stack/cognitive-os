"""POLICY tests — security guarantees, not storage/engine details.

Insecure-dev is unset. These tests remain valid if Mongo, Redis, or
class names change, as long as the governed commitment API still
enforces the invariants.
"""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.audit import (
    AuditPersistenceError,
    MemoryDurableAuditStore,
    get_audit_log,
)
from src.monkey_brain.kernel.security_boundary import (
    SecurityBoundaryDenied,
    pipeline_stages,
    run_governed_mutation,
)
from src.monkey_brain.kernel.security_operation import (
    AuditResultUnavailable,
    SecurityOperationState,
    UnknownOutcomeError,
    reconstruct_operations_from_audit,
    reset_operation_ledger_for_tests,
)
from src.monkey_brain.kernel.trusted_auth import (
    TrustedAuthEvidence,
    bind_trusted_auth,
    unauthenticated_evidence,
)


@pytest.fixture(autouse=True)
def _secure(monkeypatch):
    monkeypatch.delenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", raising=False)
    monkeypatch.delenv("COGNITIVEOS_PRODUCTION_MODE", raising=False)
    bind_trusted_auth(unauthenticated_evidence())
    reset_operation_ledger_for_tests()


def _durable_audit():
    from src.monkey_brain.api.idempotency import (
        IdempotencyStore,
        _InMemoryIdempotencyBackend,
    )

    IdempotencyStore._instance = None
    store = IdempotencyStore.__new__(IdempotencyStore)
    store._backend = _InMemoryIdempotencyBackend()
    IdempotencyStore._instance = store
    backing = MemoryDurableAuditStore()
    get_audit_log().set_store(backing)
    return backing


def _principal():
    bind_trusted_auth(
        TrustedAuthEvidence(
            authenticated=True,
            token_valid=True,
            principal_id="alice",
            principal_type="human",
            mfa_status="satisfied",
        )
    )


async def _allow(*a, **k):
    return {"allowed": True, "reason": "ok", "source": "opa"}


@pytest.fixture
def opa_allow(monkeypatch):
    monkeypatch.setenv("OPA_URL", "http://opa.internal:8181")
    monkeypatch.setattr("services.common.opa.evaluate_full", _allow)


class TestPolicyNoUnauthorizedEffect:
    @pytest.mark.asyncio
    async def test_invalid_authentication_denies_effect(self, opa_allow):
        _durable_audit()
        bind_trusted_auth(unauthenticated_evidence())
        effects = []

        async def effect():
            effects.append("ran")

        with pytest.raises(SecurityBoundaryDenied):
            await run_governed_mutation(action="orders.create", resource="o", mutate=effect)
        assert effects == []

    @pytest.mark.asyncio
    async def test_missing_mfa_denies_effect(self, opa_allow):
        _durable_audit()
        bind_trusted_auth(
            TrustedAuthEvidence(
                authenticated=True,
                token_valid=True,
                principal_id="alice",
                principal_type="human",
                mfa_status="not_satisfied",
            )
        )
        effects = []

        async def effect():
            effects.append("ran")

        with pytest.raises(SecurityBoundaryDenied):
            await run_governed_mutation(action="orders.create", resource="o", mutate=effect)
        assert effects == []

    @pytest.mark.asyncio
    async def test_opa_denied_denies_effect(self, monkeypatch):
        _durable_audit()
        _principal()
        monkeypatch.setenv("OPA_URL", "http://opa.internal:8181")

        async def deny(*a, **k):
            return {"allowed": False, "reason": "denied", "source": "opa"}

        monkeypatch.setattr("services.common.opa.evaluate_full", deny)
        effects = []

        async def effect():
            effects.append("ran")

        with pytest.raises(SecurityBoundaryDenied):
            await run_governed_mutation(action="orders.create", resource="o", mutate=effect)
        assert effects == []

    @pytest.mark.asyncio
    async def test_opa_unavailable_denies_effect(self, monkeypatch):
        _durable_audit()
        _principal()
        monkeypatch.setenv("OPA_URL", "http://opa.internal:8181")

        async def down(*a, **k):
            raise ConnectionError("opa down")

        monkeypatch.setattr("services.common.opa.evaluate_full", down)
        effects = []

        async def effect():
            effects.append("ran")

        with pytest.raises((SecurityBoundaryDenied, Exception)):
            await run_governed_mutation(action="orders.create", resource="o", mutate=effect)
        assert effects == []

    @pytest.mark.asyncio
    async def test_agent_claim_does_not_authorize(self, monkeypatch):
        _durable_audit()
        _principal()
        monkeypatch.setenv("OPA_URL", "http://opa.internal:8181")

        async def deny(*a, **k):
            return {"allowed": False, "reason": "denied", "source": "opa"}

        monkeypatch.setattr("services.common.opa.evaluate_full", deny)
        effects = []

        async def effect():
            effects.append("ran")

        with pytest.raises(SecurityBoundaryDenied):
            await run_governed_mutation(
                action="orders.create",
                resource="o",
                mutate=effect,
                extra={
                    "authorized": True,
                    "mfa_status": "satisfied",
                    "opa_allow": True,
                },
            )
        assert effects == []


class TestPolicyAuditIntentPrecedesEffect:
    @pytest.mark.asyncio
    async def test_audit_intent_unavailable_no_effect(self, opa_allow):
        _durable_audit()
        _principal()

        class DeadAudit:
            def append(self, *a, **k):
                raise RuntimeError("durable audit unavailable")

        get_audit_log().set_store(DeadAudit())
        effects = []

        async def effect():
            effects.append("ran")

        with pytest.raises(AuditPersistenceError):
            await run_governed_mutation(action="orders.create", resource="o", mutate=effect)
        assert effects == []

    @pytest.mark.asyncio
    async def test_audit_intent_timeout_no_effect(self, opa_allow):
        _durable_audit()
        _principal()

        class SlowAudit:
            def append(self, *a, **k):
                raise TimeoutError("durable audit timeout")

        get_audit_log().set_store(SlowAudit())
        effects = []

        async def effect():
            effects.append("ran")

        with pytest.raises(AuditPersistenceError):
            await run_governed_mutation(action="orders.create", resource="o", mutate=effect)
        assert effects == []

    @pytest.mark.asyncio
    async def test_pipeline_never_runs_effect_before_audit_intent(self, opa_allow):
        _durable_audit()
        _principal()

        async def effect():
            return True

        await run_governed_mutation(action="orders.create", resource="o", mutate=effect)
        stages = pipeline_stages()
        assert stages.index("AUDIT_INTENT") < stages.index("MUTATION")
        assert stages.index("IDEMPOTENCY") < stages.index("MUTATION")
        assert stages.index("AUTH") < stages.index("AUDIT_INTENT")


class TestPolicyUnknownVsFailed:
    @pytest.mark.asyncio
    async def test_explicit_failure_is_failed_not_unknown(self, opa_allow):
        _durable_audit()
        _principal()

        async def effect():
            raise RuntimeError("psp declined")

        with pytest.raises(RuntimeError):
            await run_governed_mutation(action="orders.payment", resource="pay", mutate=effect)
        from src.monkey_brain.kernel.security_operation import get_operation_ledger

        op = list(get_operation_ledger()._ops.values())[0]
        assert op.state is SecurityOperationState.FAILED
        assert op.state is not SecurityOperationState.UNKNOWN

    @pytest.mark.asyncio
    async def test_timeout_after_submission_is_unknown(self, opa_allow):
        _durable_audit()
        _principal()

        async def effect():
            raise TimeoutError("gateway timed out after send")

        with pytest.raises(UnknownOutcomeError):
            await run_governed_mutation(action="orders.payment", resource="pay", mutate=effect)
        from src.monkey_brain.kernel.security_operation import get_operation_ledger

        op = list(get_operation_ledger()._ops.values())[0]
        assert op.state is SecurityOperationState.RECONCILIATION_REQUIRED
        assert op.state is not SecurityOperationState.FAILED
        assert op.state is not SecurityOperationState.SUCCEEDED

    @pytest.mark.asyncio
    async def test_post_effect_audit_failure_is_unknown(self, opa_allow):
        _durable_audit()
        _principal()

        class Split:
            def __init__(self):
                self._ok = MemoryDurableAuditStore()

            def append(self, tenant_id, event_type, payload):
                if str(payload.get("action", "")).endswith(".result"):
                    raise RuntimeError("audit result unavailable")
                self._ok.append(tenant_id, event_type, payload)

        get_audit_log().set_store(Split())
        effects = []

        async def effect():
            effects.append("ran")
            return "ok"

        with pytest.raises(AuditResultUnavailable) as exc:
            await run_governed_mutation(action="orders.create", resource="o", mutate=effect)
        assert effects == ["ran"]
        assert exc.value.effect_occurred is True


class TestPolicyIdempotencyAndCrash:
    @pytest.mark.asyncio
    async def test_duplicate_operation_id_no_second_effect(self, opa_allow):
        _durable_audit()
        _principal()
        effects = []

        async def effect():
            effects.append("ran")
            return "ok"

        await run_governed_mutation(
            action="orders.create",
            resource="o",
            mutate=effect,
            operation_id="op-same",
        )
        with pytest.raises(SecurityBoundaryDenied) as exc:
            await run_governed_mutation(
                action="orders.create",
                resource="o",
                mutate=effect,
                operation_id="op-same",
            )
        assert exc.value.stage == "IDEMPOTENCY"
        assert effects == ["ran"]

    @pytest.mark.asyncio
    async def test_idempotency_unavailable_denies_effect(self, opa_allow):
        from src.monkey_brain.api.idempotency import (
            IdempotencyStore,
            _UnavailableIdempotencyBackend,
        )

        IdempotencyStore._instance = None
        store = IdempotencyStore.__new__(IdempotencyStore)
        store._backend = _UnavailableIdempotencyBackend()
        IdempotencyStore._instance = store
        get_audit_log().set_store(MemoryDurableAuditStore())
        _principal()
        effects = []

        async def effect():
            effects.append("ran")

        with pytest.raises(SecurityBoundaryDenied) as exc:
            await run_governed_mutation(action="orders.create", resource="o", mutate=effect)
        assert exc.value.stage == "IDEMPOTENCY"
        assert effects == []
        IdempotencyStore._instance = None

    @pytest.mark.asyncio
    async def test_durable_intent_without_result_is_not_success(self, opa_allow):
        store = _durable_audit()
        store.append(
            "alice",
            "audit.execute",
            {
                "action": "orders.create.intent",
                "outcome": "pending",
                "correlation_id": "op-pre-effect",
                "details": {"stage": "AUDIT_INTENT", "operation_id": "op-pre-effect"},
            },
        )
        reset_operation_ledger_for_tests()
        recovered = reconstruct_operations_from_audit(store.find())
        assert recovered["op-pre-effect"] is SecurityOperationState.EXECUTING
        assert recovered["op-pre-effect"] is not SecurityOperationState.SUCCEEDED

    @pytest.mark.asyncio
    async def test_crash_after_successful_result_reconstructs_succeeded(self, opa_allow):
        store = _durable_audit()
        _principal()

        async def effect():
            return "ok"

        await run_governed_mutation(
            action="orders.create",
            resource="o",
            mutate=effect,
            operation_id="op-ok",
        )
        reset_operation_ledger_for_tests()
        recovered = reconstruct_operations_from_audit(store.find())
        assert recovered["op-ok"] is SecurityOperationState.SUCCEEDED


class TestPolicyAgentsCannotResolve:
    def test_untrusted_caller_cannot_reconcile_unknown(self):
        from src.monkey_brain.kernel.security_operation import (
            SecurityOperation,
            TransactionClass,
            get_operation_ledger,
            reconcile_operation,
        )

        get_operation_ledger().create(
            SecurityOperation(
                operation_id="op-u",
                action="orders.payment",
                resource="pay",
                state=SecurityOperationState.UNKNOWN,
                transaction_class=TransactionClass.CLASS_B_EXTERNAL,
            )
        )
        with pytest.raises(PermissionError):
            reconcile_operation("op-u", confirmed="succeeded")
        assert get_operation_ledger().get("op-u").state is SecurityOperationState.UNKNOWN
