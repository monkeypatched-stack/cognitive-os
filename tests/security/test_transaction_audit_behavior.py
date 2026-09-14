"""Transaction semantics and audit-outage behavior for security-critical ops.

Insecure-dev is unset. FAILED and UNKNOWN are distinct.
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
    classify_transaction,
    get_operation_ledger,
    reconcile_operation,
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


def _ready_store():
    from src.monkey_brain.api.idempotency import (
        IdempotencyStore,
        _InMemoryIdempotencyBackend,
    )

    IdempotencyStore._instance = None
    store = IdempotencyStore.__new__(IdempotencyStore)
    store._backend = _InMemoryIdempotencyBackend()
    IdempotencyStore._instance = store
    get_audit_log().set_store(MemoryDurableAuditStore())
    return store


def _alice():
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


class TestAuditIntentFailClosed:
    @pytest.mark.asyncio
    async def test_mongo_unavailable_no_effect(self, opa_allow):
        _ready_store()
        _alice()

        class Boom:
            def append(self, *a, **k):
                raise RuntimeError("mongo unavailable")

        get_audit_log().set_store(Boom())
        mutations = []

        async def mutate():
            mutations.append(1)

        with pytest.raises(AuditPersistenceError):
            await run_governed_mutation(action="orders.create", resource="o", mutate=mutate)
        assert mutations == []
        assert "MUTATION" not in pipeline_stages()

    @pytest.mark.asyncio
    async def test_intent_timeout_no_effect(self, opa_allow):
        _ready_store()
        _alice()

        class TimeoutStore:
            def append(self, *a, **k):
                raise TimeoutError("audit timeout")

        get_audit_log().set_store(TimeoutStore())
        mutations = []

        async def mutate():
            mutations.append(1)

        with pytest.raises(AuditPersistenceError):
            await run_governed_mutation(action="orders.create", resource="o", mutate=mutate)
        assert mutations == []


class TestAuditResultAfterEffect:
    @pytest.mark.asyncio
    async def test_effect_succeeds_result_audit_fails_unknown(self, opa_allow):
        _ready_store()
        _alice()

        class IntentThenFail:
            def append(self, *a, **k):
                payload = k.get("payload") or (a[2] if len(a) > 2 else {})
                action = payload.get("action", "")
                if ".result" in str(action):
                    raise RuntimeError("mongo down after effect")
                MemoryDurableAuditStore().append(a[0] if a else "t", a[1] if len(a) > 1 else "e", payload)

        # Use a store that succeeds intent (pending) and fails result.
        class Split:
            def __init__(self):
                self._ok = MemoryDurableAuditStore()

            def append(self, tenant_id, event_type, payload):
                if str(payload.get("action", "")).endswith(".result"):
                    raise RuntimeError("result persist failed")
                self._ok.append(tenant_id, event_type, payload)

        get_audit_log().set_store(Split())
        mutations = []

        async def mutate():
            mutations.append(1)
            return "done"

        with pytest.raises(AuditResultUnavailable) as exc:
            await run_governed_mutation(action="orders.create", resource="o", mutate=mutate)
        assert mutations == [1]
        assert exc.value.effect_occurred is True
        op = get_operation_ledger().get(exc.value.operation_id)
        assert op is not None
        assert op.state is SecurityOperationState.RECONCILIATION_REQUIRED

    @pytest.mark.asyncio
    async def test_effect_fails_durable_failure_result(self, opa_allow):
        _ready_store()
        _alice()

        async def mutate():
            raise RuntimeError("mutation failed")

        with pytest.raises(RuntimeError, match="mutation failed"):
            await run_governed_mutation(action="orders.create", resource="o", mutate=mutate)
        ops = list(get_operation_ledger()._ops.values())
        assert ops
        assert ops[0].state is SecurityOperationState.FAILED

    @pytest.mark.asyncio
    async def test_external_timeout_is_unknown_not_failed(self, opa_allow):
        _ready_store()
        _alice()

        async def mutate():
            raise TimeoutError("payment gateway timed out")

        with pytest.raises(UnknownOutcomeError):
            await run_governed_mutation(action="orders.payment", resource="pay", mutate=mutate)
        ops = list(get_operation_ledger()._ops.values())
        assert ops[0].state is SecurityOperationState.RECONCILIATION_REQUIRED
        assert ops[0].state is not SecurityOperationState.FAILED


class TestOrdering:
    @pytest.mark.asyncio
    async def test_pipeline_order(self, opa_allow):
        _ready_store()
        _alice()
        recorder = []

        async def mutate():
            recorder.append("EFFECT")
            return True

        await run_governed_mutation(action="orders.create", resource="o", mutate=mutate)
        stages = pipeline_stages()
        # Runtime Approval Gate added APPROVAL_ARTIFACT_CREATED (right
        # after AUTHZ, before IDEMPOTENCY -- the policy decision becomes an
        # ApprovalArtifact before anything idempotency/audit/mutation-side
        # happens) and APPROVAL_VALIDATED (right after AUDIT_INTENT, before
        # MUTATION -- the artifact is re-checked immediately before the
        # effect runs). The AUTH -> AUTHZ -> IDEMPOTENCY -> AUDIT_INTENT ->
        # MUTATION -> AUDIT_RESULT skeleton this test guards is unchanged;
        # only these two approval-lifecycle stages were inserted into it.
        assert stages == [
            "AUTH",
            "AUTHZ",
            "APPROVAL_ARTIFACT_CREATED",
            "IDEMPOTENCY",
            "AUDIT_INTENT",
            "APPROVAL_VALIDATED",
            "MUTATION",
            "AUDIT_RESULT",
        ]
        assert recorder == ["EFFECT"]
        assert stages.index("AUDIT_INTENT") < stages.index("MUTATION")
        assert stages.index("IDEMPOTENCY") < stages.index("MUTATION")


class TestFailureMatrix:
    @pytest.mark.asyncio
    async def test_invalid_jwt_denies(self, opa_allow):
        _ready_store()
        bind_trusted_auth(unauthenticated_evidence())
        mutations = []

        async def mutate():
            mutations.append(1)

        with pytest.raises(SecurityBoundaryDenied) as exc:
            await run_governed_mutation(action="orders.create", resource="o", mutate=mutate)
        assert exc.value.stage == "AUTH"
        assert mutations == []

    @pytest.mark.asyncio
    async def test_missing_mfa_denies(self, opa_allow):
        _ready_store()
        bind_trusted_auth(
            TrustedAuthEvidence(
                authenticated=True,
                token_valid=True,
                principal_id="alice",
                principal_type="human",
                mfa_status="not_satisfied",
            )
        )
        mutations = []

        async def mutate():
            mutations.append(1)

        with pytest.raises(SecurityBoundaryDenied) as exc:
            await run_governed_mutation(action="orders.create", resource="o", mutate=mutate)
        assert exc.value.stage == "AUTH"
        assert mutations == []

    @pytest.mark.asyncio
    async def test_opa_deny(self, monkeypatch):
        _ready_store()
        _alice()
        monkeypatch.setenv("OPA_URL", "http://opa.internal:8181")

        async def deny(*a, **k):
            return {"allowed": False, "reason": "nope", "source": "opa"}

        monkeypatch.setattr("services.common.opa.evaluate_full", deny)
        mutations = []

        async def mutate():
            mutations.append(1)

        with pytest.raises(SecurityBoundaryDenied):
            await run_governed_mutation(action="orders.create", resource="o", mutate=mutate)
        assert mutations == []


class TestIdempotencyReservation:
    def test_duplicate_and_abandoned_reservation(self):
        from src.monkey_brain.api.idempotency import _InMemoryIdempotencyBackend

        backend = _InMemoryIdempotencyBackend()
        ok, _ = backend.reserve("k", "h")
        assert ok is True
        ok2, existing = backend.reserve("k", "h")
        assert ok2 is False
        assert existing is not None
        assert existing.state == "in_progress"
        # simulate crash expiry
        import time
        from src.monkey_brain.api.idempotency import IdempotencyRecord, _IN_PROGRESS

        backend._records["k"] = IdempotencyRecord(
            state=_IN_PROGRESS,
            request_hash="h",
            reserved_at=time.time() - 10_000,
        )
        ok3, abandoned = backend.reserve("k", "h")
        assert ok3 is False
        assert abandoned is not None
        assert abandoned.state == "abandoned"

    def test_mutating_routes_have_idempotent_decorator(self):
        import ast
        from pathlib import Path

        routes = Path("src/monkey_brain/api/routes")
        missing = []
        for path in routes.glob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                mutating = False
                idem = False
                for d in node.decorator_list:
                    if (
                        isinstance(d, ast.Call)
                        and isinstance(d.func, ast.Attribute)
                        and d.func.attr in ("post", "put", "patch", "delete")
                    ):
                        mutating = True
                    if isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == "idempotent":
                        idem = True
                if mutating and not idem:
                    missing.append(f"{path.name}:{node.name}")
        assert missing == []


class TestReconciliation:
    def test_agent_cannot_reconcile(self):
        from src.monkey_brain.kernel.security_operation import (
            SecurityOperation,
            TransactionClass,
            get_operation_ledger,
        )

        op = get_operation_ledger().create(
            SecurityOperation(
                operation_id="op-1",
                action="orders.payment",
                resource="pay",
                state=SecurityOperationState.RECONCILIATION_REQUIRED,
                transaction_class=TransactionClass.CLASS_B_EXTERNAL,
            )
        )
        with pytest.raises(PermissionError):
            reconcile_operation("op-1", confirmed="succeeded")
        assert get_operation_ledger().get("op-1").state is SecurityOperationState.RECONCILIATION_REQUIRED

    def test_kernel_reconcile_inside_privileged_infra(self):
        from src.monkey_brain.kernel.security_boundary import privileged_infrastructure
        from src.monkey_brain.kernel.security_operation import (
            SecurityOperation,
            TransactionClass,
        )

        get_operation_ledger().create(
            SecurityOperation(
                operation_id="op-2",
                action="orders.payment",
                resource="pay",
                state=SecurityOperationState.UNKNOWN,
                transaction_class=TransactionClass.CLASS_B_EXTERNAL,
            )
        )
        with privileged_infrastructure("test reconcile"):
            out = reconcile_operation("op-2", confirmed="succeeded")
        assert out.state is SecurityOperationState.SUCCEEDED

    def test_razorpay_timeout_is_unknown_not_failed(self):
        from src.monkey_brain.kernel.domains.payment_provider import ReservationStatus

        assert ReservationStatus.UNKNOWN != ReservationStatus.FAILED
        assert ReservationStatus.UNKNOWN.value == "unknown"

    def test_reconstruct_intent_only_is_executing(self):
        from src.monkey_brain.kernel.security_operation import (
            reconstruct_operations_from_audit,
        )

        recovered = reconstruct_operations_from_audit(
            [
                {
                    "action": "orders.create.intent",
                    "outcome": "pending",
                    "details": {"operation_id": "x", "stage": "AUDIT_INTENT"},
                },
            ]
        )
        assert recovered["x"].value == "executing"
