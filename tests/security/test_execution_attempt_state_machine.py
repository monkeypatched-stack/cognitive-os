"""Execution-attempt state machine — Part N of the execution-attempt audit.

Execution attempt is a SEPARATE state machine from commitment
(SecurityOperationState). These tests verify: valid/invalid transitions,
timeout -> UNKNOWN (never FAILED), one commitment can hold two attempts
under a safe retry, a non-idempotent UNKNOWN effect cannot be blindly
retried, cancellation cannot hide a submitted effect, attempt state
survives audit-log reconstruction after a crash, and an agent cannot
assert its own execution outcome.

Insecure-dev is unset, matching the other transaction/audit policy tests.
"""

from __future__ import annotations

import threading

import pytest

from src.monkey_brain.kernel.audit import MemoryDurableAuditStore, get_audit_log
from src.monkey_brain.kernel.execution_attempt import (
    AttemptNotFound,
    ExecutionAttemptState,
    InvalidAttemptTransition,
    UnsafeBlindRetry,
    cancel_attempt,
    claim_reconciliation,
    get_attempt_store,
    new_attempt_after,
    record_reconciliation_result,
    reconcile_execution_attempt,
    reconstruct_attempts_from_audit,
    reset_attempt_store_for_tests,
    transition_attempt,
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


# ── Valid transitions ────────────────────────────────────────────────────


class TestValidTransitions:
    def test_not_started_to_ready(self):
        attempt = get_attempt_store().create("op-1")
        with privileged_infrastructure("test"):
            out = transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.READY)
        assert out.state is ExecutionAttemptState.READY

    def test_ready_to_started(self):
        attempt = get_attempt_store().create("op-2")
        with privileged_infrastructure("test"):
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.READY)
            out = transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.STARTED)
        assert out.state is ExecutionAttemptState.STARTED

    def test_started_to_submitted(self):
        attempt = get_attempt_store().create("op-3")
        with privileged_infrastructure("test"):
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.READY)
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.STARTED)
            out = transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.SUBMITTED)
        assert out.state is ExecutionAttemptState.SUBMITTED
        assert out.submitted is True

    @pytest.mark.parametrize(
        "target",
        [
            ExecutionAttemptState.SUCCEEDED,
            ExecutionAttemptState.FAILED,
            ExecutionAttemptState.UNKNOWN,
        ],
    )
    def test_submitted_to_outcome(self, target):
        attempt = get_attempt_store().create(f"op-submitted-{target.value}")
        with privileged_infrastructure("test"):
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.READY)
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.STARTED)
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.SUBMITTED)
            out = transition_attempt(attempt.execution_attempt_id, target)
        assert out.state is target

    def test_unknown_to_reconciliation_required(self):
        attempt = get_attempt_store().create("op-unknown-rr")
        with privileged_infrastructure("test"):
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.READY)
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.STARTED)
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.SUBMITTED)
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.UNKNOWN)
            out = transition_attempt(
                attempt.execution_attempt_id,
                ExecutionAttemptState.RECONCILIATION_REQUIRED,
            )
        assert out.state is ExecutionAttemptState.RECONCILIATION_REQUIRED

    @pytest.mark.parametrize(
        "target",
        [
            ExecutionAttemptState.SUCCEEDED,
            ExecutionAttemptState.FAILED,
        ],
    )
    def test_reconciling_to_outcome(self, target):
        """UNKNOWN -> RECONCILIATION_REQUIRED -> RECONCILING -> outcome, via
        the lease-based claim_reconciliation()/record_reconciliation_result()
        pair — the only sanctioned way to leave RECONCILING."""
        attempt = get_attempt_store().create(f"op-reconciling-{target.value}")
        with privileged_infrastructure("test"):
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.READY)
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.STARTED)
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.SUBMITTED)
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.UNKNOWN)
            transition_attempt(
                attempt.execution_attempt_id,
                ExecutionAttemptState.RECONCILIATION_REQUIRED,
            )
            reconciliation_id = claim_reconciliation(attempt.execution_attempt_id)
            out = record_reconciliation_result(attempt.execution_attempt_id, reconciliation_id, target)
        assert out.state is target

    def test_reconciling_to_unknown_loops_back_to_reconciliation_required(self):
        attempt = get_attempt_store().create("op-reconciling-unresolved")
        with privileged_infrastructure("test"):
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.READY)
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.STARTED)
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.SUBMITTED)
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.UNKNOWN)
            transition_attempt(
                attempt.execution_attempt_id,
                ExecutionAttemptState.RECONCILIATION_REQUIRED,
            )
            reconciliation_id = claim_reconciliation(attempt.execution_attempt_id)
            out = record_reconciliation_result(
                attempt.execution_attempt_id,
                reconciliation_id,
                ExecutionAttemptState.UNKNOWN,
            )
        # Part 16: reconciliation returning UNKNOWN loops back to
        # RECONCILIATION_REQUIRED — it never rests on bare UNKNOWN again.
        assert out.state is ExecutionAttemptState.RECONCILIATION_REQUIRED
        assert out.state is not ExecutionAttemptState.UNKNOWN


# ── Invalid transitions (Part K) ─────────────────────────────────────────


class TestInvalidTransitions:
    def _attempt(self, op_id, *, to=None):
        attempt = get_attempt_store().create(op_id)
        with privileged_infrastructure("test"):
            for state in to or []:
                transition_attempt(attempt.execution_attempt_id, state)
        return attempt

    def test_not_started_to_succeeded(self):
        attempt = self._attempt("inv-1")
        with privileged_infrastructure("test"), pytest.raises(InvalidAttemptTransition):
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.SUCCEEDED)

    def test_not_started_to_failed(self):
        attempt = self._attempt("inv-2")
        with privileged_infrastructure("test"), pytest.raises(InvalidAttemptTransition):
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.FAILED)

    def test_ready_to_succeeded(self):
        attempt = self._attempt("inv-3", to=[ExecutionAttemptState.READY])
        with privileged_infrastructure("test"), pytest.raises(InvalidAttemptTransition):
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.SUCCEEDED)

    def test_ready_to_failed(self):
        attempt = self._attempt("inv-4", to=[ExecutionAttemptState.READY])
        with privileged_infrastructure("test"), pytest.raises(InvalidAttemptTransition):
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.FAILED)

    def test_succeeded_to_started(self):
        attempt = self._attempt(
            "inv-5",
            to=[
                ExecutionAttemptState.READY,
                ExecutionAttemptState.STARTED,
                ExecutionAttemptState.SUBMITTED,
                ExecutionAttemptState.SUCCEEDED,
            ],
        )
        with privileged_infrastructure("test"), pytest.raises(InvalidAttemptTransition):
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.STARTED)

    def test_succeeded_to_submitted(self):
        attempt = self._attempt(
            "inv-6",
            to=[
                ExecutionAttemptState.READY,
                ExecutionAttemptState.STARTED,
                ExecutionAttemptState.SUBMITTED,
                ExecutionAttemptState.SUCCEEDED,
            ],
        )
        with privileged_infrastructure("test"), pytest.raises(InvalidAttemptTransition):
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.SUBMITTED)

    def test_failed_to_started(self):
        attempt = self._attempt(
            "inv-7",
            to=[
                ExecutionAttemptState.READY,
                ExecutionAttemptState.STARTED,
                ExecutionAttemptState.SUBMITTED,
                ExecutionAttemptState.FAILED,
            ],
        )
        with privileged_infrastructure("test"), pytest.raises(InvalidAttemptTransition):
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.STARTED)

    def test_failed_to_submitted(self):
        attempt = self._attempt(
            "inv-8",
            to=[
                ExecutionAttemptState.READY,
                ExecutionAttemptState.STARTED,
                ExecutionAttemptState.SUBMITTED,
                ExecutionAttemptState.FAILED,
            ],
        )
        with privileged_infrastructure("test"), pytest.raises(InvalidAttemptTransition):
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.SUBMITTED)

    def test_cancelled_to_started(self):
        attempt = self._attempt("inv-9", to=[ExecutionAttemptState.READY, ExecutionAttemptState.CANCELLED])
        with privileged_infrastructure("test"), pytest.raises(InvalidAttemptTransition):
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.STARTED)

    def test_cancelled_to_submitted(self):
        attempt = self._attempt("inv-10", to=[ExecutionAttemptState.READY, ExecutionAttemptState.CANCELLED])
        with privileged_infrastructure("test"), pytest.raises(InvalidAttemptTransition):
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.SUBMITTED)

    def test_unknown_to_succeeded_without_reconciliation(self):
        attempt = self._attempt(
            "inv-11",
            to=[
                ExecutionAttemptState.READY,
                ExecutionAttemptState.STARTED,
                ExecutionAttemptState.SUBMITTED,
                ExecutionAttemptState.UNKNOWN,
            ],
        )
        with privileged_infrastructure("test"), pytest.raises(InvalidAttemptTransition):
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.SUCCEEDED)

    def test_unknown_to_failed_without_reconciliation(self):
        attempt = self._attempt(
            "inv-12",
            to=[
                ExecutionAttemptState.READY,
                ExecutionAttemptState.STARTED,
                ExecutionAttemptState.SUBMITTED,
                ExecutionAttemptState.UNKNOWN,
            ],
        )
        with privileged_infrastructure("test"), pytest.raises(InvalidAttemptTransition):
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.FAILED)

    def test_reconciliation_required_to_succeeded_without_reconciling(self):
        """Part 16: RECONCILIATION_REQUIRED -> SUCCEEDED is refused even
        via the generic transition path — actually performing reconciliation
        (claim_reconciliation + record_reconciliation_result) is mandatory,
        not skippable."""
        attempt = self._attempt(
            "inv-13",
            to=[
                ExecutionAttemptState.READY,
                ExecutionAttemptState.STARTED,
                ExecutionAttemptState.SUBMITTED,
                ExecutionAttemptState.UNKNOWN,
                ExecutionAttemptState.RECONCILIATION_REQUIRED,
            ],
        )
        with privileged_infrastructure("test"), pytest.raises(InvalidAttemptTransition):
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.SUCCEEDED)

    def test_reconciling_to_submitted_refused(self):
        """Part 16: 'Do not permit RECONCILING -> SUBMITTED on the same
        execution attempt. A new submission requires a new execution
        attempt.'"""
        attempt = self._attempt(
            "inv-14",
            to=[
                ExecutionAttemptState.READY,
                ExecutionAttemptState.STARTED,
                ExecutionAttemptState.SUBMITTED,
                ExecutionAttemptState.UNKNOWN,
                ExecutionAttemptState.RECONCILIATION_REQUIRED,
            ],
        )
        with privileged_infrastructure("test"):
            claim_reconciliation(attempt.execution_attempt_id)
        with privileged_infrastructure("test"), pytest.raises(InvalidAttemptTransition):
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.SUBMITTED)


# ── Cancellation cannot hide a submitted/ambiguous effect (Part B) ───────


class TestCancellation:
    def test_not_started_can_cancel(self):
        attempt = get_attempt_store().create("cancel-1")
        with privileged_infrastructure("test"):
            out = cancel_attempt(attempt.execution_attempt_id, proof_no_effect_submitted=True)
        assert out.state is ExecutionAttemptState.CANCELLED

    def test_started_with_proof_cancels(self):
        attempt = get_attempt_store().create("cancel-2")
        with privileged_infrastructure("test"):
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.READY)
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.STARTED)
            out = cancel_attempt(attempt.execution_attempt_id, proof_no_effect_submitted=True)
        assert out.state is ExecutionAttemptState.CANCELLED

    def test_started_without_proof_becomes_unknown_not_cancelled(self):
        attempt = get_attempt_store().create("cancel-3")
        with privileged_infrastructure("test"):
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.READY)
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.STARTED)
            out = cancel_attempt(attempt.execution_attempt_id, proof_no_effect_submitted=False)
        assert out.state is ExecutionAttemptState.UNKNOWN
        assert out.state is not ExecutionAttemptState.CANCELLED

    def test_already_submitted_cannot_cancel_even_with_claimed_proof(self):
        """Cancellation must not be used to hide an already-submitted effect —
        `submitted=True` overrides a caller's proof claim."""
        attempt = get_attempt_store().create("cancel-4")
        with privileged_infrastructure("test"):
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.READY)
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.STARTED)
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.SUBMITTED)
            with pytest.raises(InvalidAttemptTransition):
                transition_attempt(
                    attempt.execution_attempt_id,
                    ExecutionAttemptState.CANCELLED,
                    evidence={"no_effect_submitted": True},
                )


# ── Timeout -> UNKNOWN, never FAILED ─────────────────────────────────────


class TestTimeoutIsUnknown:
    @pytest.mark.asyncio
    async def test_timeout_after_submission_is_unknown_attempt_and_ledger(self, opa_allow):
        _durable_audit()
        _principal()

        async def effect():
            raise TimeoutError("gateway timed out after send")

        with pytest.raises(UnknownOutcomeError):
            await run_governed_mutation(
                action="orders.payment",
                resource="pay",
                mutate=effect,
                operation_id="op-to",
            )

        op = get_operation_ledger().get("op-to")
        assert op.state is SecurityOperationState.RECONCILIATION_REQUIRED
        assert op.state is not SecurityOperationState.FAILED

        # UNKNOWN is never a resting state (Part 3/16 of the reconciliation
        # audit) — the attempt advances immediately to RECONCILIATION_REQUIRED.
        attempt = get_attempt_store().latest_for("op-to")
        assert attempt is not None
        assert attempt.state is ExecutionAttemptState.RECONCILIATION_REQUIRED
        assert attempt.state is not ExecutionAttemptState.FAILED
        assert attempt.state is not ExecutionAttemptState.SUCCEEDED
        assert attempt.attempt_number == 1


# ── Retry: one commitment, two attempts ──────────────────────────────────


class TestRetrySameCommitment:
    @pytest.mark.asyncio
    async def test_unknown_then_safe_retry_succeeds_same_operation(self, opa_allow):
        _durable_audit()
        _principal()

        async def times_out():
            raise TimeoutError("gateway timed out after send")

        with pytest.raises(UnknownOutcomeError):
            await run_governed_mutation(
                action="orders.payment",
                resource="pay",
                mutate=times_out,
                operation_id="op-retry",
            )

        attempts_before = get_attempt_store().attempts_for("op-retry")
        assert len(attempts_before) == 1
        assert attempts_before[0].state is ExecutionAttemptState.RECONCILIATION_REQUIRED

        async def succeeds():
            return "captured"

        # Effect is declared idempotent-safe (stable idempotency key against
        # the external PSP) — a second attempt under the SAME operation_id
        # is therefore not a blind retry.
        result = await retry_execution_attempt(
            operation_id="op-retry",
            mutate=succeeds,
            idempotent_effect=True,
        )
        assert result == "captured"

        attempts_after = get_attempt_store().attempts_for("op-retry")
        assert len(attempts_after) == 2
        assert attempts_after[0].execution_attempt_id == "op-retry-ATT-1"
        assert attempts_after[1].execution_attempt_id == "op-retry-ATT-2"
        assert attempts_after[1].state is ExecutionAttemptState.SUCCEEDED

        # ONE commitment throughout — no second operation_id was minted.
        assert len(get_operation_ledger()._ops) == 1
        op = get_operation_ledger().get("op-retry")
        assert op.state is SecurityOperationState.SUCCEEDED

    @pytest.mark.asyncio
    async def test_retry_after_reconciled_failure_succeeds(self, opa_allow):
        _durable_audit()
        _principal()

        async def times_out():
            raise TimeoutError("gateway timed out after send")

        with pytest.raises(UnknownOutcomeError):
            await run_governed_mutation(
                action="orders.payment",
                resource="pay",
                mutate=times_out,
                operation_id="op-reconciled",
            )

        # Kernel-only reconciliation establishes the effect did NOT happen.
        with privileged_infrastructure("test reconcile"):
            reconcile_operation("op-reconciled", confirmed="failed")
            reconcile_execution_attempt("op-reconciled", confirmed="failed")

        assert get_operation_ledger().get("op-reconciled").state is SecurityOperationState.FAILED
        assert get_attempt_store().latest_for("op-reconciled").state is ExecutionAttemptState.FAILED

        async def succeeds():
            return "captured"

        result = await retry_execution_attempt(operation_id="op-reconciled", mutate=succeeds)
        assert result == "captured"
        assert len(get_operation_ledger()._ops) == 1
        assert len(get_attempt_store().attempts_for("op-reconciled")) == 2

    @pytest.mark.asyncio
    async def test_cannot_retry_a_succeeded_operation(self, opa_allow):
        _durable_audit()
        _principal()

        async def succeeds():
            return "ok"

        await run_governed_mutation(
            action="orders.create",
            resource="o",
            mutate=succeeds,
            operation_id="op-done",
        )

        async def would_double_charge():
            raise AssertionError("must never run")

        with pytest.raises(SecurityBoundaryDenied) as exc:
            await retry_execution_attempt(operation_id="op-done", mutate=would_double_charge)
        assert exc.value.stage == "IDEMPOTENCY"


# ── Non-idempotent UNKNOWN cannot be blindly retried (Part L rule 10) ────


class TestNonIdempotentUnknownBlocksBlindRetry:
    @pytest.mark.asyncio
    async def test_blind_retry_refused_without_idempotent_effect_or_reconciliation(self, opa_allow):
        _durable_audit()
        _principal()

        async def times_out():
            raise TimeoutError("gateway timed out after send")

        with pytest.raises(UnknownOutcomeError):
            await run_governed_mutation(
                action="orders.payment",
                resource="pay",
                mutate=times_out,
                operation_id="op-blind",
            )

        async def would_run_twice():
            raise AssertionError("must never run — blind retry after non-idempotent UNKNOWN")

        with pytest.raises(SecurityBoundaryDenied) as exc:
            await retry_execution_attempt(operation_id="op-blind", mutate=would_run_twice)
        assert exc.value.stage == "IDEMPOTENCY"
        # Still exactly one attempt — the refused retry never created #2.
        assert len(get_attempt_store().attempts_for("op-blind")) == 1

    def test_new_attempt_after_raises_unsafe_blind_retry_directly(self):
        get_attempt_store().create("op-direct")
        attempt = get_attempt_store().latest_for("op-direct")
        with privileged_infrastructure("test"):
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.READY)
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.STARTED)
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.SUBMITTED)
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.UNKNOWN)
        with pytest.raises(UnsafeBlindRetry):
            new_attempt_after("op-direct", idempotent_effect=False, reconciled=False)
        # idempotent_effect=True is exactly the escape hatch — allowed.
        second = new_attempt_after("op-direct", idempotent_effect=True, reconciled=False)
        assert second.attempt_number == 2


# ── Crash recovery: attempt state survives audit reconstruction ─────────


class TestCrashRecovery:
    @pytest.mark.asyncio
    async def test_before_started_recovers_as_ready(self, opa_allow):
        store = _durable_audit()
        _principal()

        async def hang_forever():
            raise TimeoutError("simulated: crash before STARTED, never actually ran")

        # Force a crash-like scenario: attempt reaches READY (audited) but a
        # simulated crash means we only look at what's durable up to there.
        with pytest.raises(UnknownOutcomeError):
            await run_governed_mutation(
                action="orders.payment",
                resource="pay",
                mutate=hang_forever,
                operation_id="op-crash-1",
            )
        entries = store.find()
        ready_only = [e for e in entries if str(e.get("action", "")).endswith(".attempt.ready")]
        recovered = reconstruct_attempts_from_audit(ready_only)
        assert list(recovered.values()) == [ExecutionAttemptState.READY]

    @pytest.mark.asyncio
    async def test_after_submitted_before_result_recovers_as_submitted(self, opa_allow):
        store = _durable_audit()
        _principal()

        async def hang_forever():
            raise TimeoutError("simulated crash: submitted, no result record yet")

        with pytest.raises(UnknownOutcomeError):
            await run_governed_mutation(
                action="orders.payment",
                resource="pay",
                mutate=hang_forever,
                operation_id="op-crash-2",
            )
        entries = store.find()
        # Drop the terminal .result record AND any reconciliation-lifecycle
        # record to simulate "crashed before the outcome (or the fact that
        # reconciliation is now required) could be durably recorded".
        pre_result = [
            e
            for e in entries
            if not str(e.get("action", "")).endswith(".result") and ".reconciliation." not in str(e.get("action", ""))
        ]
        recovered = reconstruct_attempts_from_audit(pre_result)
        assert list(recovered.values()) == [ExecutionAttemptState.SUBMITTED]

    @pytest.mark.asyncio
    async def test_after_result_recovers_true_outcome(self, opa_allow):
        store = _durable_audit()
        _principal()

        async def effect():
            return "ok"

        await run_governed_mutation(
            action="orders.create",
            resource="o",
            mutate=effect,
            operation_id="op-crash-3",
        )
        recovered = reconstruct_attempts_from_audit(store.find())
        assert list(recovered.values()) == [ExecutionAttemptState.SUCCEEDED]


# ── Agents cannot assert their own execution outcome (Part H, L rule 16) ─


class TestAgentCannotAssertOutcome:
    def test_transition_outside_commitment_is_refused(self):
        attempt = get_attempt_store().create("op-agent-1")
        with pytest.raises(PermissionError):
            transition_attempt(
                attempt.execution_attempt_id,
                ExecutionAttemptState.SUCCEEDED,
                evidence={"success": True, "mfa": "satisfied", "authorized": True},
            )
        assert get_attempt_store().get("op-agent-1-ATT-1").state is ExecutionAttemptState.NOT_STARTED

    def test_agent_supplied_evidence_does_not_skip_the_state_graph(self):
        """Even inside a governed context, evidence content is never trusted
        to justify an otherwise-illegal jump (NOT_STARTED -> SUCCEEDED)."""
        attempt = get_attempt_store().create("op-agent-2")
        with privileged_infrastructure("test"), pytest.raises(InvalidAttemptTransition):
            transition_attempt(
                attempt.execution_attempt_id,
                ExecutionAttemptState.SUCCEEDED,
                evidence={"success": True, "authorized": True},
            )


# ── Concurrency: no duplicate attempt identity, single STARTED owner ────


class TestConcurrency:
    def test_concurrent_attempt_creation_gets_distinct_ids(self):
        store = get_attempt_store()
        created: list[str] = []
        lock = threading.Lock()

        def worker():
            attempt = store.create("op-concurrent")
            with lock:
                created.append(attempt.execution_attempt_id)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(created) == len(set(created)) == 8
        assert sorted(created) == [f"op-concurrent-ATT-{n}" for n in range(1, 9)]

    def test_only_one_worker_claims_started(self):
        store = get_attempt_store()
        attempt = store.create("op-claim")
        with privileged_infrastructure("test"):
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.READY)

        results: list[bool] = []
        lock = threading.Lock()

        def worker():
            won = store.claim_start(attempt.execution_attempt_id)
            with lock:
                results.append(won)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert results.count(True) == 1
        assert results.count(False) == 7
        assert store.get(attempt.execution_attempt_id).state is ExecutionAttemptState.STARTED


# ── Attempt identity/lookup basics ───────────────────────────────────────


class TestAttemptIdentity:
    def test_unknown_attempt_id_raises(self):
        with pytest.raises(AttemptNotFound):
            with privileged_infrastructure("test"):
                transition_attempt("does-not-exist", ExecutionAttemptState.READY)

    def test_attempt_belongs_to_exactly_one_operation(self):
        a1 = get_attempt_store().create("op-owner")
        a2 = get_attempt_store().create("op-owner")
        assert a1.operation_id == a2.operation_id == "op-owner"
        assert a1.execution_attempt_id != a2.execution_attempt_id
        assert a1.attempt_number == 1
        assert a2.attempt_number == 2
