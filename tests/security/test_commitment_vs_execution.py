"""POLICY tests: commitment vs. execution attempt are separate questions.

    Commitment answers  "Has CognitiveOS authoritatively accepted this
                          logical operation?"
    Execution attempt answers
                         "Did CognitiveOS attempt to cause the effect?"
    Effect outcome answers
                         "Do we have evidence the effect succeeded,
                          failed, or is unresolved?"

These are policy-level tests: they exercise the public governed API
(run_governed_mutation / retry_execution_attempt) and assert on
commitment identity + attempt identity, not on internal mechanism
(Mongo/Redis/outbox) — see test_execution_attempt_state_machine.py for
the attempt-level state-graph mechanics, and
test_transaction_audit_policy.py for the P1-P11 audit/transaction
invariants this file's assertions build on.

Insecure-dev is unset, matching the other policy test files.
"""

from __future__ import annotations

import asyncio

import pytest

from src.monkey_brain.kernel.audit import (
    AuditPersistenceError,
    MemoryDurableAuditStore,
    get_audit_log,
)
from src.monkey_brain.kernel.execution_attempt import (
    ExecutionAttemptState,
    get_attempt_store,
    reconcile_execution_attempt,
    reset_attempt_store_for_tests,
)
from src.monkey_brain.kernel.security_boundary import (
    SecurityBoundaryDenied,
    privileged_infrastructure,
    retry_execution_attempt,
    run_governed_mutation,
)
from src.monkey_brain.kernel.security_operation import (
    SecurityOperationState,
    UnknownOutcomeError,
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
    reset_attempt_store_for_tests()


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


# ── INVARIANT 2: one operation_id -> at most one authoritative commitment ─


class TestOneOperationOneCommitment:
    @pytest.mark.asyncio
    async def test_concurrent_requests_same_operation_id_resolve_to_one_commitment(self, opa_allow):
        _durable_audit()
        _principal()
        ran: list[int] = []

        async def request_a():
            async def effect():
                await asyncio.sleep(0)  # yield, so request_b can race in
                ran.append("A")
                return "A-result"

            return await run_governed_mutation(
                action="orders.create",
                resource="o",
                mutate=effect,
                operation_id="op-race",
            )

        async def request_b():
            async def effect():
                ran.append("B")
                return "B-result"

            return await run_governed_mutation(
                action="orders.create",
                resource="o",
                mutate=effect,
                operation_id="op-race",
            )

        results = await asyncio.gather(request_a(), request_b(), return_exceptions=True)
        successes = [r for r in results if not isinstance(r, Exception)]
        denials = [r for r in results if isinstance(r, SecurityBoundaryDenied)]

        # Exactly one request produced the effect; the other was refused as
        # a duplicate of the SAME logical operation — never a second
        # commitment.
        assert len(successes) == 1
        assert len(denials) == 1
        assert denials[0].stage == "IDEMPOTENCY"
        assert len(ran) == 1

        assert len(get_operation_ledger()._ops) == 1
        op = get_operation_ledger().get("op-race")
        assert op is not None
        assert op.state is SecurityOperationState.SUCCEEDED

        # ONE commitment -> at most one execution attempt actually ran.
        attempts = get_attempt_store().attempts_for("op-race")
        assert len(attempts) == 1
        assert attempts[0].state is ExecutionAttemptState.SUCCEEDED


# ── INVARIANT 3: commitment does not imply execution occurred ────────────


class TestCommitmentWithoutExecution:
    def test_commitment_can_exist_with_zero_execution_attempts(self):
        """COMMITTED, then 'crash' before any attempt is ever allocated —
        the commitment is real and recoverable; zero attempts is a valid,
        distinct state, not an error."""
        from src.monkey_brain.kernel.security_operation import (
            SecurityOperation,
            TransactionClass,
        )

        ledger = get_operation_ledger()
        with privileged_infrastructure("simulate crash after commitment, before attempt"):
            ledger.create(
                SecurityOperation(
                    operation_id="op-zero-attempts",
                    action="orders.create",
                    resource="o",
                    state=SecurityOperationState.AUDIT_INTENT_RECORDED,
                    transaction_class=TransactionClass.CLASS_A_INTERNAL,
                )
            )

        op = ledger.get("op-zero-attempts")
        assert op is not None
        assert op.state is SecurityOperationState.AUDIT_INTENT_RECORDED
        assert get_attempt_store().attempts_for("op-zero-attempts") == []


# ── STEP 10: audit-intent failure -> NO commitment, NO execution attempt ─


class TestAuditIntentFailurePreventsCommitmentAndAttempt:
    @pytest.mark.asyncio
    async def test_audit_intent_failure_leaves_no_commitment_and_no_attempt(self, opa_allow):
        _durable_audit()
        _principal()

        class Boom:
            def append(self, *a, **k):
                raise RuntimeError("mongo unavailable")

        get_audit_log().set_store(Boom())
        ran = []

        async def effect():
            ran.append(1)

        with pytest.raises(AuditPersistenceError):
            await run_governed_mutation(
                action="orders.create",
                resource="o",
                mutate=effect,
                operation_id="op-no-commit",
            )
        assert ran == []

        # A ledger row exists (AUTHORIZED, from before audit intent was
        # even attempted) but it never reached AUDIT_INTENT_RECORDED —
        # per Policy 1, that row is NOT yet a commitment.
        op = get_operation_ledger().get("op-no-commit")
        assert op is not None
        assert op.state is SecurityOperationState.AUTHORIZED
        assert op.state is not SecurityOperationState.AUDIT_INTENT_RECORDED

        # No execution attempt was ever allocated for the uncommitted
        # operation (Invariant 5).
        assert get_attempt_store().attempts_for("op-no-commit") == []

    @pytest.mark.asyncio
    async def test_authz_failure_leaves_no_commitment_and_no_attempt(self, monkeypatch):
        _durable_audit()
        _principal()
        monkeypatch.setenv("OPA_URL", "http://opa.internal:8181")

        async def deny(*a, **k):
            return {"allowed": False, "reason": "denied", "source": "opa"}

        monkeypatch.setattr("services.common.opa.evaluate_full", deny)
        ran = []

        async def effect():
            ran.append(1)

        with pytest.raises(SecurityBoundaryDenied) as exc:
            await run_governed_mutation(
                action="orders.create",
                resource="o",
                mutate=effect,
                operation_id="op-no-authz",
            )
        assert exc.value.stage == "AUTHZ"
        assert ran == []
        # AUTHZ failure happens before ledger.create() is even reached.
        assert get_operation_ledger().get("op-no-authz") is None
        assert get_attempt_store().attempts_for("op-no-authz") == []


# ── INVARIANT 6/7: retry / UNKNOWN never creates a new commitment ────────


class TestRetryAndUnknownPreserveCommitment:
    @pytest.mark.asyncio
    async def test_retry_preserves_commitment_two_attempts(self, opa_allow):
        _durable_audit()
        _principal()

        async def times_out():
            raise TimeoutError("gateway timed out after send")

        with pytest.raises(UnknownOutcomeError):
            await run_governed_mutation(
                action="orders.payment",
                resource="pay",
                mutate=times_out,
                operation_id="op-retry-commit",
            )

        # UNKNOWN outcome — same commitment, no second operation_id.
        assert len(get_operation_ledger()._ops) == 1
        op = get_operation_ledger().get("op-retry-commit")
        assert op.state is SecurityOperationState.RECONCILIATION_REQUIRED

        async def succeeds():
            return "captured"

        result = await retry_execution_attempt(
            operation_id="op-retry-commit",
            mutate=succeeds,
            idempotent_effect=True,
        )
        assert result == "captured"

        # Still exactly ONE commitment ...
        assert len(get_operation_ledger()._ops) == 1
        assert get_operation_ledger().get("op-retry-commit").state is SecurityOperationState.SUCCEEDED
        # ... covering exactly TWO execution attempts.
        attempts = get_attempt_store().attempts_for("op-retry-commit")
        assert len(attempts) == 2
        assert all(a.operation_id == "op-retry-commit" for a in attempts)
        # UNKNOWN is never a resting state (Part 3/16) — attempt #1 advanced
        # immediately to RECONCILIATION_REQUIRED and was never rewritten.
        assert attempts[0].state is ExecutionAttemptState.RECONCILIATION_REQUIRED
        assert attempts[1].state is ExecutionAttemptState.SUCCEEDED

    @pytest.mark.asyncio
    async def test_unknown_outcome_never_mints_a_second_operation_id(self, opa_allow):
        _durable_audit()
        _principal()

        async def times_out():
            raise TimeoutError("gateway timed out after send")

        with pytest.raises(UnknownOutcomeError):
            await run_governed_mutation(
                action="orders.payment",
                resource="pay",
                mutate=times_out,
                operation_id="op-unknown-only",
            )

        assert list(get_operation_ledger()._ops.keys()) == ["op-unknown-only"]
        attempt = get_attempt_store().latest_for("op-unknown-only")
        assert attempt.operation_id == "op-unknown-only"
        assert attempt.state is ExecutionAttemptState.RECONCILIATION_REQUIRED


# ── INVARIANT 8: non-idempotent UNKNOWN is never blindly retried ────────


class TestNoUnsafeRetry:
    @pytest.mark.asyncio
    async def test_non_idempotent_unknown_blocks_automatic_second_attempt(self, opa_allow):
        _durable_audit()
        _principal()

        async def times_out():
            raise TimeoutError("gateway timed out after send")

        with pytest.raises(UnknownOutcomeError):
            await run_governed_mutation(
                action="orders.payment",
                resource="pay",
                mutate=times_out,
                operation_id="op-unsafe",
            )

        async def would_run_twice():
            raise AssertionError("must never run — blind retry of a non-idempotent UNKNOWN effect")

        with pytest.raises(SecurityBoundaryDenied) as exc:
            await retry_execution_attempt(operation_id="op-unsafe", mutate=would_run_twice)
        assert exc.value.stage == "IDEMPOTENCY"

        # The refused retry touched nothing: still one attempt, still
        # unresolved, still the same commitment state as before the attempt.
        attempts = get_attempt_store().attempts_for("op-unsafe")
        assert len(attempts) == 1
        assert attempts[0].state is ExecutionAttemptState.RECONCILIATION_REQUIRED
        assert get_operation_ledger().get("op-unsafe").state is SecurityOperationState.RECONCILIATION_REQUIRED

    @pytest.mark.asyncio
    async def test_reconciled_failure_permits_a_safe_retry(self, opa_allow):
        _durable_audit()
        _principal()

        async def times_out():
            raise TimeoutError("gateway timed out after send")

        with pytest.raises(UnknownOutcomeError):
            await run_governed_mutation(
                action="orders.payment",
                resource="pay",
                mutate=times_out,
                operation_id="op-reconciled-safe",
            )

        with privileged_infrastructure("reconciliation job: PSP confirms no charge occurred"):
            reconcile_operation("op-reconciled-safe", confirmed="failed")
            reconcile_execution_attempt("op-reconciled-safe", confirmed="failed")

        async def now_succeeds():
            return "captured"

        # Reconciliation established the effect did NOT happen — a retry
        # is no longer blind, even without idempotent_effect=True.
        result = await retry_execution_attempt(operation_id="op-reconciled-safe", mutate=now_succeeds)
        assert result == "captured"
        assert len(get_operation_ledger()._ops) == 1


# ── INVARIANT 4/6: retry never creates ATTEMPT -> new COMMITMENT mapping ─


class TestRetryNeverCreatesNewCommitment:
    @pytest.mark.asyncio
    async def test_cannot_retry_a_succeeded_operation_into_a_new_effect(self, opa_allow):
        _durable_audit()
        _principal()

        async def succeeds():
            return "ok"

        await run_governed_mutation(
            action="orders.create",
            resource="o",
            mutate=succeeds,
            operation_id="op-succeeded",
        )

        async def would_double_charge():
            raise AssertionError("must never run")

        with pytest.raises(SecurityBoundaryDenied):
            await retry_execution_attempt(operation_id="op-succeeded", mutate=would_double_charge)

        # No new commitment, no new attempt.
        assert len(get_operation_ledger()._ops) == 1
        assert len(get_attempt_store().attempts_for("op-succeeded")) == 1

    @pytest.mark.asyncio
    async def test_retry_of_nonexistent_operation_is_refused_not_silently_committed(self, opa_allow):
        _durable_audit()
        _principal()

        async def would_run():
            raise AssertionError("must never run — no commitment exists for this operation_id")

        with pytest.raises(SecurityBoundaryDenied):
            await retry_execution_attempt(operation_id="op-never-committed", mutate=would_run)
        assert get_operation_ledger().get("op-never-committed") is None
        assert get_attempt_store().attempts_for("op-never-committed") == []


# ── INVARIANT 10: an attempt never claims an effect it cannot evidence ───


class TestNoOverclaimedEffect:
    @pytest.mark.asyncio
    async def test_confirmed_failure_is_not_reported_as_unknown_or_success(self, opa_allow):
        _durable_audit()
        _principal()

        async def declined():
            raise RuntimeError("psp declined the charge")

        with pytest.raises(RuntimeError):
            await run_governed_mutation(
                action="orders.payment",
                resource="pay",
                mutate=declined,
                operation_id="op-declined",
            )
        attempt = get_attempt_store().latest_for("op-declined")
        assert attempt.state is ExecutionAttemptState.FAILED
        assert attempt.state is not ExecutionAttemptState.UNKNOWN
        assert attempt.state is not ExecutionAttemptState.SUCCEEDED

    @pytest.mark.asyncio
    async def test_ambiguous_timeout_is_not_reported_as_failure_or_success(self, opa_allow):
        _durable_audit()
        _principal()

        async def times_out():
            raise TimeoutError("gateway timed out after send")

        with pytest.raises(UnknownOutcomeError):
            await run_governed_mutation(
                action="orders.payment",
                resource="pay",
                mutate=times_out,
                operation_id="op-ambiguous",
            )
        attempt = get_attempt_store().latest_for("op-ambiguous")
        # Never a resting bare UNKNOWN (Part 3/16) — it advances immediately
        # to RECONCILIATION_REQUIRED, and is still never FAILED/SUCCEEDED.
        assert attempt.state is ExecutionAttemptState.RECONCILIATION_REQUIRED
        assert attempt.state is not ExecutionAttemptState.FAILED
        assert attempt.state is not ExecutionAttemptState.SUCCEEDED
