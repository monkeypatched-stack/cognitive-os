"""Reconciliation as an explicit execution-attempt state.

Reconciliation is not a function call or retry helper — it is a distinct
operational lifecycle: UNKNOWN -> RECONCILIATION_REQUIRED -> RECONCILING
-> SUCCEEDED | FAILED | (looped back to) RECONCILIATION_REQUIRED.

UNKNOWN, RECONCILIATION_REQUIRED, and RECONCILING are three different
concepts (never conflated):

    UNKNOWN                  we do not know the effect outcome
    RECONCILIATION_REQUIRED  the unknown outcome requires an explicit
                              recovery process before the operation may
                              safely proceed further
    RECONCILING               a trusted worker currently holds the lease
                              and is actively checking authoritative
                              evidence

See test_execution_attempt_state_machine.py for the raw state-graph
mechanics and test_commitment_vs_execution.py for the commitment/attempt
policy invariants this file builds on.

Insecure-dev is unset, matching the other policy test files.
"""

from __future__ import annotations

import threading
import time

import pytest

from src.monkey_brain.kernel.audit import MemoryDurableAuditStore, get_audit_log
from src.monkey_brain.kernel.execution_attempt import (
    ExecutionAttemptState,
    InvalidAttemptTransition,
    ReconciliationAlreadyInProgress,
    StaleReconciliation,
    UnsafeBlindRetry,
    claim_reconciliation,
    get_attempt_store,
    reconcile_execution_attempt,
    record_reconciliation_result,
    reset_attempt_store_for_tests,
    transition_attempt,
)
from src.monkey_brain.kernel.security_boundary import (
    SecurityBoundaryDenied,
    begin_reconciliation,
    complete_reconciliation,
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


async def _run_to_unknown(operation_id: str):
    async def times_out():
        raise TimeoutError("gateway timed out after send")

    with pytest.raises(UnknownOutcomeError):
        await run_governed_mutation(
            action="orders.payment",
            resource="pay",
            mutate=times_out,
            operation_id=operation_id,
        )


# ── UNKNOWN -> RECONCILIATION_REQUIRED (automatic, no privilege needed) ──


class TestUnknownToReconciliationRequired:
    @pytest.mark.asyncio
    async def test_submitted_timeout_advances_to_reconciliation_required(self, opa_allow):
        _durable_audit()
        _principal()
        await _run_to_unknown("op-submit-unknown")

        attempt = get_attempt_store().latest_for("op-submit-unknown")
        assert attempt.state is ExecutionAttemptState.RECONCILIATION_REQUIRED
        # UNKNOWN and RECONCILIATION_REQUIRED are different concepts — the
        # attempt passed THROUGH unknown but does not rest there.
        assert attempt.state is not ExecutionAttemptState.UNKNOWN

        op = get_operation_ledger().get("op-submit-unknown")
        assert op.state is SecurityOperationState.RECONCILIATION_REQUIRED


# ── Reconciliation success / failure / unresolved ────────────────────────


class TestReconciliationOutcomes:
    @pytest.mark.asyncio
    async def test_reconciliation_success(self, opa_allow):
        _durable_audit()
        _principal()
        await _run_to_unknown("op-recon-success")
        attempt = get_attempt_store().latest_for("op-recon-success")

        with privileged_infrastructure("reconciliation worker"):
            reconciliation_id = begin_reconciliation("op-recon-success")
            assert get_attempt_store().get(attempt.execution_attempt_id).state is ExecutionAttemptState.RECONCILING
            result = complete_reconciliation(
                "op-recon-success",
                reconciliation_id,
                confirmed="succeeded",
                evidence_source="razorpay_status_api",
            )
        assert result.state is ExecutionAttemptState.SUCCEEDED
        assert get_operation_ledger().get("op-recon-success").state is SecurityOperationState.SUCCEEDED

    @pytest.mark.asyncio
    async def test_reconciliation_failure_only_with_authoritative_evidence(self, opa_allow):
        _durable_audit()
        _principal()
        await _run_to_unknown("op-recon-failure")

        with privileged_infrastructure("reconciliation worker"):
            reconciliation_id = begin_reconciliation("op-recon-failure")
            result = complete_reconciliation(
                "op-recon-failure",
                reconciliation_id,
                confirmed="failed",
                evidence_source="internal_transaction_record",
            )
        assert result.state is ExecutionAttemptState.FAILED
        assert result.evidence.get("evidence_source") == "internal_transaction_record"
        assert get_operation_ledger().get("op-recon-failure").state is SecurityOperationState.FAILED

    @pytest.mark.asyncio
    async def test_reconciliation_unresolved_loops_back_not_bare_unknown(self, opa_allow):
        _durable_audit()
        _principal()
        await _run_to_unknown("op-recon-unresolved")

        with privileged_infrastructure("reconciliation worker"):
            reconciliation_id = begin_reconciliation("op-recon-unresolved")
            result = complete_reconciliation(
                "op-recon-unresolved",
                reconciliation_id,
                confirmed="unknown",
                evidence_source="provider_status_api_ambiguous",
            )
        assert result.state is ExecutionAttemptState.RECONCILIATION_REQUIRED
        assert result.state is not ExecutionAttemptState.UNKNOWN
        # Ledger is not force-resolved either — reconcile_operation is only
        # invoked by complete_reconciliation() for succeeded|failed.
        assert get_operation_ledger().get("op-recon-unresolved").state is SecurityOperationState.RECONCILIATION_REQUIRED


# ── Safe retry after reconciliation proves the effect absent ─────────────


class TestSafeRetryAfterReconciliation:
    @pytest.mark.asyncio
    async def test_attempt1_unknown_reconciliation_effect_absent_attempt2(self, opa_allow):
        _durable_audit()
        _principal()
        await _run_to_unknown("op-safe-retry")

        with privileged_infrastructure("reconciliation worker"):
            reconciliation_id = begin_reconciliation("op-safe-retry")
            complete_reconciliation(
                "op-safe-retry",
                reconciliation_id,
                confirmed="failed",
                evidence_source="razorpay_status_api",
            )
            reconcile_operation("op-safe-retry", confirmed="failed")

        async def succeeds():
            return "captured"

        result = await retry_execution_attempt(operation_id="op-safe-retry", mutate=succeeds)
        assert result == "captured"

        attempts = get_attempt_store().attempts_for("op-safe-retry")
        assert len(attempts) == 2
        assert attempts[0].state is ExecutionAttemptState.FAILED
        assert attempts[1].state is ExecutionAttemptState.SUCCEEDED
        # ONE commitment throughout.
        assert len(get_operation_ledger()._ops) == 1
        assert get_operation_ledger().get("op-safe-retry").state is SecurityOperationState.SUCCEEDED


# ── No blind retry for a non-idempotent effect ───────────────────────────


class TestNoBlindRetry:
    @pytest.mark.asyncio
    async def test_unknown_does_not_automatically_create_another_attempt(self, opa_allow):
        _durable_audit()
        _principal()
        await _run_to_unknown("op-no-blind-retry")

        async def would_run_twice():
            raise AssertionError("must never run — blind retry of a non-idempotent, unresolved effect")

        with pytest.raises(SecurityBoundaryDenied) as exc:
            await retry_execution_attempt(operation_id="op-no-blind-retry", mutate=would_run_twice)
        assert exc.value.stage == "IDEMPOTENCY"
        assert len(get_attempt_store().attempts_for("op-no-blind-retry")) == 1

    @pytest.mark.asyncio
    async def test_still_blocked_while_reconciliation_required_or_reconciling(self, opa_allow):
        """The blind-retry block covers all three unresolved states, not
        just bare UNKNOWN — RECONCILIATION_REQUIRED and RECONCILING are
        equally 'no safe basis for another attempt yet'."""
        _durable_audit()
        _principal()
        await _run_to_unknown("op-still-blocked")

        async def would_run():
            raise AssertionError("must never run")

        # Still RECONCILIATION_REQUIRED (nobody has claimed reconciliation yet).
        with pytest.raises(SecurityBoundaryDenied):
            await retry_execution_attempt(operation_id="op-still-blocked", mutate=would_run)

        with privileged_infrastructure("reconciliation worker"):
            begin_reconciliation("op-still-blocked")  # now RECONCILING, still unresolved

        with pytest.raises(SecurityBoundaryDenied):
            await retry_execution_attempt(operation_id="op-still-blocked", mutate=would_run)

        assert len(get_attempt_store().attempts_for("op-still-blocked")) == 1


# ── No history rewriting ──────────────────────────────────────────────────


class TestNoHistoryRewriting:
    @pytest.mark.asyncio
    async def test_attempt1_state_unchanged_after_attempt2_succeeds_via_reconciled_failure(self, opa_allow):
        _durable_audit()
        _principal()
        await _run_to_unknown("op-history-1")
        attempt1 = get_attempt_store().latest_for("op-history-1")

        with privileged_infrastructure("reconciliation worker"):
            reconciliation_id = begin_reconciliation("op-history-1")
            complete_reconciliation(
                "op-history-1",
                reconciliation_id,
                confirmed="failed",
                evidence_source="provider_status_api",
            )
            reconcile_operation("op-history-1", confirmed="failed")

        async def succeeds():
            return "ok"

        await retry_execution_attempt(operation_id="op-history-1", mutate=succeeds)

        # Attempt #1's forensic record is exactly what actually happened —
        # never mutated to STARTED/SUBMITTED/SUCCEEDED to match attempt #2.
        replayed = get_attempt_store().get(attempt1.execution_attempt_id)
        assert replayed.state is ExecutionAttemptState.FAILED
        assert replayed.attempt_number == 1

    @pytest.mark.asyncio
    async def test_attempt1_remains_unresolved_when_attempt2_succeeds_via_idempotent_retry(self, opa_allow):
        """A retry declared idempotent-safe may proceed without attempt #1
        ever being fully reconciled — attempt #1's own history must still
        never be rewritten to a false SUCCEEDED/FAILED it never earned."""
        _durable_audit()
        _principal()
        await _run_to_unknown("op-history-2")
        attempt1 = get_attempt_store().latest_for("op-history-2")

        async def succeeds():
            return "ok"

        await retry_execution_attempt(operation_id="op-history-2", mutate=succeeds, idempotent_effect=True)

        replayed = get_attempt_store().get(attempt1.execution_attempt_id)
        assert replayed.state is ExecutionAttemptState.RECONCILIATION_REQUIRED
        assert replayed.state is not ExecutionAttemptState.SUCCEEDED
        assert replayed.state is not ExecutionAttemptState.FAILED

        attempts = get_attempt_store().attempts_for("op-history-2")
        assert attempts[1].state is ExecutionAttemptState.SUCCEEDED


# ── Concurrent reconciliation ─────────────────────────────────────────────


class TestConcurrentReconciliation:
    @pytest.mark.asyncio
    async def test_two_workers_cannot_both_reconcile_independently(self, opa_allow):
        _durable_audit()
        _principal()
        await _run_to_unknown("op-concurrent-recon")

        won: list[bool] = []
        lock = threading.Lock()

        def worker():
            try:
                with privileged_infrastructure("worker"):
                    begin_reconciliation("op-concurrent-recon")
                with lock:
                    won.append(True)
            except ReconciliationAlreadyInProgress:
                with lock:
                    won.append(False)

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert won.count(True) == 1
        assert won.count(False) == 5
        attempt = get_attempt_store().latest_for("op-concurrent-recon")
        assert attempt.state is ExecutionAttemptState.RECONCILING


# ── Reconciliation crash: lease expiry, stale write rejected ─────────────


class TestReconciliationCrashRecovery:
    @pytest.mark.asyncio
    async def test_expired_lease_can_be_reclaimed_and_does_not_block_forever(self, opa_allow):
        _durable_audit()
        _principal()
        await _run_to_unknown("op-crash-recon")

        with privileged_infrastructure("worker A"):
            rid_a = begin_reconciliation("op-crash-recon", lease_seconds=0.05)
        time.sleep(0.1)  # worker A crashes; lease expires

        with privileged_infrastructure("worker B"):
            rid_b = begin_reconciliation("op-crash-recon", lease_seconds=30)
        assert rid_b != rid_a

        with privileged_infrastructure("worker B resolves"):
            result = complete_reconciliation(
                "op-crash-recon",
                rid_b,
                confirmed="failed",
                evidence_source="provider_status_api",
            )
        assert result.state is ExecutionAttemptState.FAILED

    @pytest.mark.asyncio
    async def test_stale_worker_cannot_overwrite_a_still_reconciling_newer_claim(self, opa_allow):
        """Worker A's lease expires; worker B reclaims (still RECONCILING,
        has not resolved yet). Worker A's late write — same attempt, stale
        reconciliation_id — must be rejected even though the attempt is
        still (from a different owner's) RECONCILING."""
        _durable_audit()
        _principal()
        await _run_to_unknown("op-stale-write")

        with privileged_infrastructure("worker A"):
            rid_a = begin_reconciliation("op-stale-write", lease_seconds=0.05)
        time.sleep(0.1)
        with privileged_infrastructure("worker B"):
            rid_b = begin_reconciliation("op-stale-write", lease_seconds=30)

        with (
            pytest.raises(StaleReconciliation),
            privileged_infrastructure("worker A late write"),
        ):
            complete_reconciliation(
                "op-stale-write",
                rid_a,
                confirmed="succeeded",
                evidence_source="fabricated",
            )
        # Still RECONCILING under worker B's (unaffected) claim.
        attempt = get_attempt_store().latest_for("op-stale-write")
        assert attempt.state is ExecutionAttemptState.RECONCILING
        assert attempt.reconciliation_id == rid_b

        with privileged_infrastructure("worker B resolves"):
            result = complete_reconciliation(
                "op-stale-write",
                rid_b,
                confirmed="failed",
                evidence_source="provider_status_api",
            )
        assert result.state is ExecutionAttemptState.FAILED

    @pytest.mark.asyncio
    async def test_stale_worker_cannot_overwrite_an_already_resolved_result(self, opa_allow):
        """Worker A's lease expires; worker B reclaims AND resolves before
        worker A's late write arrives. The late write must not resurrect
        or overwrite the terminal state worker B already recorded."""
        _durable_audit()
        _principal()
        await _run_to_unknown("op-stale-write-2")

        with privileged_infrastructure("worker A"):
            rid_a = begin_reconciliation("op-stale-write-2", lease_seconds=0.05)
        time.sleep(0.1)
        with privileged_infrastructure("worker B"):
            rid_b = begin_reconciliation("op-stale-write-2", lease_seconds=30)
            complete_reconciliation(
                "op-stale-write-2",
                rid_b,
                confirmed="failed",
                evidence_source="provider_status_api",
            )

        with pytest.raises(Exception), privileged_infrastructure("worker A late write"):
            complete_reconciliation(
                "op-stale-write-2",
                rid_a,
                confirmed="succeeded",
                evidence_source="fabricated",
            )
        # Worker B's FAILED result stands, untouched by the late write.
        attempt = get_attempt_store().latest_for("op-stale-write-2")
        assert attempt.state is ExecutionAttemptState.FAILED


# ── Agents cannot force reconciliation to succeed ────────────────────────


class TestAgentCannotForceReconciliation:
    def test_record_reconciliation_result_requires_governed_context(self):
        attempt = get_attempt_store().create("op-agent-recon")
        with privileged_infrastructure("setup"):
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.READY)
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.STARTED)
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.SUBMITTED)
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.UNKNOWN)
            transition_attempt(
                attempt.execution_attempt_id,
                ExecutionAttemptState.RECONCILIATION_REQUIRED,
            )
            reconciliation_id = claim_reconciliation(attempt.execution_attempt_id)

        # Outside any governed/privileged context — an agent-controlled
        # call path cannot reach this even with a "valid-looking" id and
        # fabricated evidence.
        with pytest.raises(PermissionError):
            record_reconciliation_result(
                attempt.execution_attempt_id,
                reconciliation_id,
                ExecutionAttemptState.SUCCEEDED,
                evidence={
                    "success": True,
                    "mfa": "satisfied",
                    "authorized": True,
                    "provider_status": "captured",
                },
            )
        assert get_attempt_store().get(attempt.execution_attempt_id).state is ExecutionAttemptState.RECONCILING

    def test_claim_reconciliation_requires_governed_context(self):
        attempt = get_attempt_store().create("op-agent-claim")
        with privileged_infrastructure("setup"):
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.READY)
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.STARTED)
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.SUBMITTED)
            transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.UNKNOWN)
            transition_attempt(
                attempt.execution_attempt_id,
                ExecutionAttemptState.RECONCILIATION_REQUIRED,
            )

        with pytest.raises(PermissionError):
            claim_reconciliation(attempt.execution_attempt_id)
        assert get_attempt_store().get(attempt.execution_attempt_id).state is (
            ExecutionAttemptState.RECONCILIATION_REQUIRED
        )

    @pytest.mark.asyncio
    async def test_reconciliation_never_grants_authorization(self, opa_allow, monkeypatch):
        """Invariant 3/7: reconciliation resolves the execution-attempt
        state machine only — it must never let a retry skip AUTH/AUTHZ.
        Even after a real, evidence-backed reconciliation to FAILED (which
        makes a retry SAFE), the retry is still refused if authorization
        itself is denied — reconciliation and authorization are orthogonal."""
        _durable_audit()
        _principal()
        await _run_to_unknown("op-recon-not-authz")

        with privileged_infrastructure("reconciliation worker"):
            reconciliation_id = begin_reconciliation("op-recon-not-authz")
            complete_reconciliation(
                "op-recon-not-authz",
                reconciliation_id,
                confirmed="failed",
                evidence_source="provider_status_api",
            )
            reconcile_operation("op-recon-not-authz", confirmed="failed")

        async def deny(*a, **k):
            return {
                "allowed": False,
                "reason": "policy revoked since attempt #1",
                "source": "opa",
            }

        monkeypatch.setattr("services.common.opa.evaluate_full", deny)

        async def would_run():
            raise AssertionError("must never run — retry authorization denied")

        with pytest.raises(SecurityBoundaryDenied) as exc:
            await retry_execution_attempt(operation_id="op-recon-not-authz", mutate=would_run)
        assert exc.value.stage == "AUTHZ"
        # Reconciliation resolved the ATTEMPT (retry-safety); it did not
        # and cannot resolve AUTHORIZATION for the new attempt.
        assert len(get_attempt_store().attempts_for("op-recon-not-authz")) == 1


# ── Reconciliation does not create a new commitment ──────────────────────


class TestReconciliationDoesNotCreateNewCommitment:
    @pytest.mark.asyncio
    async def test_one_commitment_throughout_full_reconciliation_and_retry_cycle(self, opa_allow):
        _durable_audit()
        _principal()
        await _run_to_unknown("op-one-commitment")

        with privileged_infrastructure("reconciliation worker"):
            reconciliation_id = begin_reconciliation("op-one-commitment")
            complete_reconciliation(
                "op-one-commitment",
                reconciliation_id,
                confirmed="failed",
                evidence_source="provider_status_api",
            )
            reconcile_operation("op-one-commitment", confirmed="failed")

        async def succeeds():
            return "ok"

        await retry_execution_attempt(operation_id="op-one-commitment", mutate=succeeds)

        assert len(get_operation_ledger()._ops) == 1
        assert list(get_operation_ledger()._ops.keys()) == ["op-one-commitment"]
        attempts = get_attempt_store().attempts_for("op-one-commitment")
        assert all(a.operation_id == "op-one-commitment" for a in attempts)
        assert len(attempts) == 2
