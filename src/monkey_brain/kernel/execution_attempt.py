"""Execution-attempt state machine — one concrete try at causing a
committed operation's effect.

This is deliberately a SEPARATE state machine from commitment
(`security_operation.SecurityOperationState`). Commitment is the logical
acceptance of an operation (AUTHORIZED, AUDIT_INTENT_RECORDED, ...).
An execution attempt records what the execution machinery knows about
ONE try at the effect; it does not itself establish authorization and
does not guarantee an external effect occurred.

    operation_id (commitment)
        |
        +-- execution_attempt_id (attempt #1)
        +-- execution_attempt_id (attempt #2, only if attempt #1 is safe to retry)

States:

    NOT_STARTED -> READY -> STARTED -> SUBMITTED -> SUCCEEDED
                                              |  --> FAILED
                                              ------> UNKNOWN
    (NOT_STARTED | READY | STARTED) -> CANCELLED  (only if no effect was
    submitted; otherwise STARTED -> UNKNOWN, never a hidden CANCELLED)

    UNKNOWN -> RECONCILIATION_REQUIRED -> RECONCILING -> SUCCEEDED | FAILED | UNKNOWN
                                                                          |
                                                                          v
                                                            (loops back) RECONCILIATION_REQUIRED

Reconciliation is its own explicit lifecycle, not a synonym for UNKNOWN:

    UNKNOWN                  We do not know the effect outcome.
    RECONCILIATION_REQUIRED  The unknown outcome requires an explicit
                             recovery process before the operation may
                             safely proceed further (no owner yet).
    RECONCILING              A trusted reconciliation worker currently
                             holds the lease and is actively checking
                             authoritative evidence.

SUCCEEDED, FAILED, CANCELLED are terminal for the attempt. UNKNOWN,
RECONCILIATION_REQUIRED, and RECONCILING are not terminal — they are
unresolved safety states. RECONCILIATION_REQUIRED and RECONCILING can
ONLY be left via claim_reconciliation() / record_reconciliation_result()
(never the generic transition_attempt()) — see those functions below.

Agents/LLMs cannot call transition_attempt() (or claim_reconciliation() /
record_reconciliation_result()) to assert SUCCEEDED (or any other state)
— every one of them requires the caller to already be inside a governed
commitment or explicit privileged-infrastructure context, exactly like
security_operation.reconcile_operation().
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("agentos.execution_attempt")


class ExecutionAttemptState(str, Enum):
    NOT_STARTED = "not_started"
    READY = "ready"
    STARTED = "started"
    SUBMITTED = "submitted"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    RECONCILING = "reconciling"
    CANCELLED = "cancelled"


TERMINAL_STATES = frozenset(
    {
        ExecutionAttemptState.SUCCEEDED,
        ExecutionAttemptState.FAILED,
        ExecutionAttemptState.CANCELLED,
    }
)

# Outcomes a reconciliation pass may record (RECONCILING's implicit exit
# set — deliberately NOT part of _TRANSITIONS; see record_reconciliation_result()).
_RECONCILING_OUTCOMES = frozenset(
    {
        ExecutionAttemptState.SUCCEEDED,
        ExecutionAttemptState.FAILED,
        ExecutionAttemptState.UNKNOWN,
    }
)


def is_terminal(state: ExecutionAttemptState) -> bool:
    """UNKNOWN, RECONCILIATION_REQUIRED, and RECONCILING are deliberately
    excluded — they are operationally unresolved, not successful or safe
    terminal states (Part D)."""
    return state in TERMINAL_STATES


def _require_own_enum(value: Any) -> None:
    """Reject anything that is not literally an ExecutionAttemptState member.

    `ExecutionAttemptState` and other kernel state enums (e.g.
    `security_operation.SecurityOperationState`) are both `(str, Enum)` —
    members sharing a spelling (`SUCCEEDED`, `UNKNOWN`, ...) compare EQUAL
    across the two classes (str equality) and hash identically, so a plain
    `in`/`==` check against this module's transition tables would silently
    accept a same-named member of a DIFFERENT state machine. `isinstance`
    checks the actual class, not the string value, so it does not have
    this hole — this is the one canonical execution-attempt vocabulary,
    and nothing that merely *looks* like one of its members is accepted.
    """
    if not isinstance(value, ExecutionAttemptState):
        raise TypeError(
            f"expected execution_attempt.ExecutionAttemptState, got "
            f"{type(value).__module__}.{type(value).__qualname__} ({value!r})",
        )


# Valid first-pass transition graph (Part B/K/16). RECONCILIATION_REQUIRED
# and RECONCILING map to an EMPTY edge set here on purpose: the generic
# transition()/transition_attempt() path can enter them (UNKNOWN ->
# RECONCILIATION_REQUIRED) but can never leave them. The only sanctioned
# way out of RECONCILIATION_REQUIRED is claim_reconciliation(); the only
# sanctioned way out of RECONCILING is record_reconciliation_result() —
# both below, both lease/generation-checked (Part 12/13). This structurally
# prevents "RECONCILING -> SUBMITTED on the same attempt" and "skip the
# lease and jump straight to SUCCEEDED" (Part 16), regardless of any flag
# a caller might pass to the generic transition function.
_TRANSITIONS: dict[ExecutionAttemptState, frozenset[ExecutionAttemptState]] = {
    ExecutionAttemptState.NOT_STARTED: frozenset(
        {
            ExecutionAttemptState.READY,
            ExecutionAttemptState.CANCELLED,
        }
    ),
    ExecutionAttemptState.READY: frozenset(
        {
            ExecutionAttemptState.STARTED,
            ExecutionAttemptState.CANCELLED,
        }
    ),
    ExecutionAttemptState.STARTED: frozenset(
        {
            ExecutionAttemptState.SUBMITTED,
            ExecutionAttemptState.CANCELLED,
            ExecutionAttemptState.UNKNOWN,
        }
    ),
    ExecutionAttemptState.SUBMITTED: frozenset(
        {
            ExecutionAttemptState.SUCCEEDED,
            ExecutionAttemptState.FAILED,
            ExecutionAttemptState.UNKNOWN,
        }
    ),
    ExecutionAttemptState.UNKNOWN: frozenset(
        {
            # The ONLY edge out of UNKNOWN. Never SUCCEEDED/FAILED/UNKNOWN
            # directly — UNKNOWN != RECONCILIATION_REQUIRED (Part 3), but the
            # transition between them is automatic bookkeeping, not itself an
            # outcome claim, so it needs no special evidence gate.
            ExecutionAttemptState.RECONCILIATION_REQUIRED,
        }
    ),
    ExecutionAttemptState.RECONCILIATION_REQUIRED: frozenset(),
    ExecutionAttemptState.RECONCILING: frozenset(),
    ExecutionAttemptState.SUCCEEDED: frozenset(),
    ExecutionAttemptState.FAILED: frozenset(),
    ExecutionAttemptState.CANCELLED: frozenset(),
}


class InvalidAttemptTransition(Exception):
    """A transition outside the canonical state graph."""

    def __init__(
        self,
        attempt_id: str,
        source: ExecutionAttemptState,
        target: ExecutionAttemptState,
    ) -> None:
        self.attempt_id = attempt_id
        self.source = source
        self.target = target
        super().__init__(f"invalid execution-attempt transition {attempt_id}: {source.value} -> {target.value}")


class AttemptNotFound(Exception):
    pass


class UnsafeBlindRetry(Exception):
    """A new attempt was requested while the prior attempt is UNKNOWN,
    RECONCILIATION_REQUIRED, or RECONCILING and the effect is not declared
    idempotent-safe. Part L rule 10 / Invariant 8: non-idempotent unresolved
    effects cannot be blindly retried."""


class ReconciliationAlreadyInProgress(Exception):
    """Another worker already holds a live reconciliation lease for this
    attempt (Part 12: one authoritative reconciliation owner at a time)."""

    def __init__(self, attempt_id: str, reconciliation_id: str) -> None:
        self.attempt_id = attempt_id
        self.reconciliation_id = reconciliation_id
        super().__init__(f"{attempt_id}: reconciliation {reconciliation_id!r} already in progress")


class StaleReconciliation(Exception):
    """A reconciliation write was rejected because a newer reconciliation
    (a different reconciliation_id) has since claimed or resolved this
    attempt (Part 13: a stale worker must never overwrite a newer result)."""


@dataclass
class ExecutionAttempt:
    execution_attempt_id: str
    operation_id: str
    attempt_number: int
    state: ExecutionAttemptState = ExecutionAttemptState.NOT_STARTED
    idempotent_effect: bool = False
    submitted: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    evidence: dict[str, Any] = field(default_factory=dict)
    # Reconciliation lease/version state (Part 12/13/15). reconciliation_id
    # identifies the CURRENT (or most recently resolved) reconciliation
    # attempt for this execution attempt — a distinct identity from
    # execution_attempt_id, since one execution attempt's UNKNOWN outcome
    # may be reconciled (claimed/reclaimed) more than once.
    reconciliation_id: str = ""
    reconciliation_generation: int = 0
    reconciliation_lease_expires_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_attempt_id": self.execution_attempt_id,
            "operation_id": self.operation_id,
            "attempt_number": self.attempt_number,
            "state": self.state.value,
            "idempotent_effect": self.idempotent_effect,
            "submitted": self.submitted,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "reconciliation_id": self.reconciliation_id,
            "reconciliation_generation": self.reconciliation_generation,
        }


class AttemptStore:
    """Authoritative in-process attempt registry.

    Like OperationLedger, this is a fast-path cache/coordinator — durable
    evidence lives in the audit log (attempt_created/started/submitted/...
    records, see security_boundary.py). A short-lived store is not the
    security authority (P10); after a process restart, attempt state is
    recovered via reconstruct_attempts_from_audit(), not trusted from here.
    """

    def __init__(self) -> None:
        self._attempts: dict[str, ExecutionAttempt] = {}
        self._by_operation: dict[str, list[str]] = {}
        self._lock = threading.Lock()

    def create(self, operation_id: str, *, idempotent_effect: bool = False) -> ExecutionAttempt:
        """Allocate the next, uniquely-numbered attempt for operation_id.

        Locked so concurrent workers retrying the same operation never mint
        two attempts with the same attempt_number/attempt_id (Part F).
        """
        with self._lock:
            existing_ids = self._by_operation.setdefault(operation_id, [])
            attempt_number = len(existing_ids) + 1
            attempt_id = f"{operation_id}-ATT-{attempt_number}"
            attempt = ExecutionAttempt(
                execution_attempt_id=attempt_id,
                operation_id=operation_id,
                attempt_number=attempt_number,
                idempotent_effect=idempotent_effect,
            )
            self._attempts[attempt_id] = attempt
            existing_ids.append(attempt_id)
            return attempt

    def get(self, attempt_id: str) -> ExecutionAttempt | None:
        with self._lock:
            return self._attempts.get(attempt_id)

    def attempts_for(self, operation_id: str) -> list[ExecutionAttempt]:
        with self._lock:
            return [self._attempts[i] for i in self._by_operation.get(operation_id, [])]

    def latest_for(self, operation_id: str) -> ExecutionAttempt | None:
        attempts = self.attempts_for(operation_id)
        return attempts[-1] if attempts else None

    def claim_start(self, attempt_id: str) -> bool:
        """Atomically move READY -> STARTED.

        Returns False (does not raise) if another worker already claimed
        this attempt — callers use this for exactly-once ownership of a
        single attempt (Part M step 6): concurrent workers must not both
        believe they own execution of the same attempt.
        """
        with self._lock:
            attempt = self._attempts.get(attempt_id)
            if attempt is None:
                raise AttemptNotFound(attempt_id)
            if attempt.state is not ExecutionAttemptState.READY:
                return False
            attempt.state = ExecutionAttemptState.STARTED
            attempt.updated_at = time.time()
            return True

    def transition(
        self,
        attempt_id: str,
        target: ExecutionAttemptState,
        *,
        evidence: dict[str, Any] | None = None,
    ) -> ExecutionAttempt:
        """Generic transition. Cannot leave RECONCILIATION_REQUIRED or
        RECONCILING (both map to an empty edge set in _TRANSITIONS) —
        those are only left via claim_reconciliation() /
        record_reconciliation_result() below.
        """
        _require_own_enum(target)
        evidence = dict(evidence or {})
        with self._lock:
            attempt = self._attempts.get(attempt_id)
            if attempt is None:
                raise AttemptNotFound(attempt_id)
            source = attempt.state
            if is_terminal(source):
                raise InvalidAttemptTransition(attempt_id, source, target)
            allowed = _TRANSITIONS.get(source, frozenset())
            if target not in allowed:
                raise InvalidAttemptTransition(attempt_id, source, target)
            if target is ExecutionAttemptState.CANCELLED and source is ExecutionAttemptState.STARTED:
                # Part B: only allowed when the implementation can PROVE no
                # effect was submitted. Cancellation must never hide an
                # already-submitted or unknown external effect.
                if attempt.submitted or not evidence.get("no_effect_submitted"):
                    raise InvalidAttemptTransition(attempt_id, source, target)
            if target is ExecutionAttemptState.SUBMITTED:
                attempt.submitted = True
            attempt.state = target
            attempt.updated_at = time.time()
            attempt.evidence.update(evidence)
            return attempt

    def claim_reconciliation(self, attempt_id: str, *, lease_seconds: float = 60.0) -> str:
        """Atomically move RECONCILIATION_REQUIRED -> RECONCILING, claiming
        a time-bound lease, and return the new reconciliation_id the caller
        must present to record_reconciliation_result() (Part 11/12/15).

        Also reclaims an EXPIRED RECONCILING lease (a crashed worker) —
        this mints a NEW reconciliation_id/generation, so the crashed
        worker's eventual late write is rejected as stale (Part 13) rather
        than silently accepted.

        Raises ReconciliationAlreadyInProgress if another worker's lease
        is still live: exactly one worker may hold RECONCILING at a time
        (Part 12) — a second worker must not independently retry/recover.
        """
        with self._lock:
            attempt = self._attempts.get(attempt_id)
            if attempt is None:
                raise AttemptNotFound(attempt_id)
            now = time.time()
            if attempt.state is ExecutionAttemptState.RECONCILING:
                if attempt.reconciliation_lease_expires_at > now:
                    raise ReconciliationAlreadyInProgress(attempt_id, attempt.reconciliation_id)
                logger.warning(
                    "reclaiming expired reconciliation lease for %s (generation %d -> %d)",
                    attempt_id,
                    attempt.reconciliation_generation,
                    attempt.reconciliation_generation + 1,
                )
            elif attempt.state is not ExecutionAttemptState.RECONCILIATION_REQUIRED:
                raise InvalidAttemptTransition(attempt_id, attempt.state, ExecutionAttemptState.RECONCILING)
            attempt.reconciliation_generation += 1
            attempt.reconciliation_id = f"{attempt_id}-REC-{attempt.reconciliation_generation}"
            attempt.reconciliation_lease_expires_at = now + lease_seconds
            attempt.state = ExecutionAttemptState.RECONCILING
            attempt.updated_at = now
            return attempt.reconciliation_id

    def record_reconciliation_result(
        self,
        attempt_id: str,
        reconciliation_id: str,
        outcome: ExecutionAttemptState,
        *,
        evidence: dict[str, Any] | None = None,
    ) -> ExecutionAttempt:
        """Resolve the reconciliation lease identified by reconciliation_id
        with `outcome` (SUCCEEDED, FAILED, or UNKNOWN — Part 4/16).

        Rejects a stale reconciliation_id (Part 13). An UNKNOWN outcome
        loops back to RECONCILIATION_REQUIRED rather than resting on bare
        UNKNOWN again (Part 16: "If reconciliation returns UNKNOWN:
        UNKNOWN -> RECONCILIATION_REQUIRED").
        """
        _require_own_enum(outcome)
        if outcome not in _RECONCILING_OUTCOMES:
            raise ValueError(f"reconciliation outcome must be one of {sorted(s.value for s in _RECONCILING_OUTCOMES)}")
        evidence = dict(evidence or {})
        with self._lock:
            attempt = self._attempts.get(attempt_id)
            if attempt is None:
                raise AttemptNotFound(attempt_id)
            if attempt.state is not ExecutionAttemptState.RECONCILING:
                raise InvalidAttemptTransition(attempt_id, attempt.state, outcome)
            if attempt.reconciliation_id != reconciliation_id:
                raise StaleReconciliation(
                    f"{attempt_id}: reconciliation {reconciliation_id!r} is no longer current "
                    f"(current is {attempt.reconciliation_id!r}); a newer reconciliation has "
                    "already claimed or resolved this attempt",
                )
            final = (
                ExecutionAttemptState.RECONCILIATION_REQUIRED if outcome is ExecutionAttemptState.UNKNOWN else outcome
            )
            attempt.state = final
            attempt.updated_at = time.time()
            attempt.evidence.update(evidence)
            attempt.reconciliation_lease_expires_at = 0.0
            return attempt


_store: AttemptStore | None = None
_store_lock = threading.Lock()


def get_attempt_store() -> AttemptStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = AttemptStore()
    return _store


def reset_attempt_store_for_tests() -> None:
    global _store
    _store = AttemptStore()


def _require_governed_context(action: str) -> None:
    from src.monkey_brain.kernel.production_gates import insecure_dev_mode
    from src.monkey_brain.kernel.security_boundary import (
        commitment_active,
        privileged_infra_active,
    )

    if not (commitment_active() or privileged_infra_active() or insecure_dev_mode()):
        raise PermissionError(f"{action} requires governed execution")


def transition_attempt(
    attempt_id: str,
    target: ExecutionAttemptState,
    *,
    evidence: dict[str, Any] | None = None,
) -> ExecutionAttempt:
    """The one trusted transition entrypoint for the non-reconciliation
    edges (Part M step 4, Part H, Part L rule 16).

    Requires the caller to already be inside a governed commitment or an
    explicit privileged-infrastructure context — the same trust boundary
    security_operation.reconcile_operation() uses. An agent/LLM calling
    this directly, outside that context, is refused regardless of what
    `evidence` claims (evidence is recorded, never trusted as proof of
    authorization or of the transition itself being legitimate).

    Cannot be used to leave RECONCILIATION_REQUIRED or RECONCILING — use
    claim_reconciliation() / record_reconciliation_result() for those.
    """
    _require_governed_context("execution-attempt transition")
    return get_attempt_store().transition(attempt_id, target, evidence=evidence)


def claim_reconciliation(attempt_id: str, *, lease_seconds: float = 60.0) -> str:
    """The one trusted entrypoint to BEGIN reconciliation (Part 11/12).

    Same governed/privileged-context guard as transition_attempt(). Moves
    RECONCILIATION_REQUIRED -> RECONCILING (or reclaims an expired
    RECONCILING lease from a crashed worker — Part 13) and returns the
    reconciliation_id the caller must present to
    record_reconciliation_result(). Concurrency-safe: raises
    ReconciliationAlreadyInProgress if another worker's lease is still
    live (Part 12) — that caller must NOT independently retry/recover.
    """
    _require_governed_context("reconciliation")
    return get_attempt_store().claim_reconciliation(attempt_id, lease_seconds=lease_seconds)


def record_reconciliation_result(
    attempt_id: str,
    reconciliation_id: str,
    outcome: ExecutionAttemptState,
    *,
    evidence: dict[str, Any] | None = None,
) -> ExecutionAttempt:
    """The one trusted entrypoint to RESOLVE reconciliation (Part 7/8/11).

    `evidence` must be built from a trusted source (external provider
    status query by idempotency key, an authoritative internal transaction
    record, a provider receipt, authoritative device state) — never an
    LLM/agent assertion or a client-supplied success flag (Part 8). This
    function does not itself inspect evidence content; the security
    boundary is the same governed/privileged-context guard every trusted
    transition uses, so only trusted kernel code can even reach this call.
    `reconciliation_id` must match the live claim from
    claim_reconciliation(), or this raises StaleReconciliation (Part 13).
    """
    _require_governed_context("reconciliation")
    return get_attempt_store().record_reconciliation_result(
        attempt_id,
        reconciliation_id,
        outcome,
        evidence=evidence,
    )


def cancel_attempt(attempt_id: str, *, proof_no_effect_submitted: bool) -> ExecutionAttempt:
    """Cancel an attempt that has not (provably) produced an effect.

    Part B: `STARTED -> CANCELLED` only when the implementation can prove
    no effect was submitted. When that cannot be proven, this transitions
    to UNKNOWN instead of raising — cancellation must never be used to
    hide an already-submitted or ambiguous external effect.
    """
    attempt = get_attempt_store().get(attempt_id)
    if attempt is None:
        raise AttemptNotFound(attempt_id)
    if attempt.state is ExecutionAttemptState.STARTED and (attempt.submitted or not proof_no_effect_submitted):
        return transition_attempt(
            attempt_id,
            ExecutionAttemptState.UNKNOWN,
            evidence={"cancel_requested": True, "no_effect_submitted": False},
        )
    return transition_attempt(
        attempt_id,
        ExecutionAttemptState.CANCELLED,
        evidence={"no_effect_submitted": proof_no_effect_submitted},
    )


# An attempt in any of these states has NOT been resolved to positive
# evidence either way — a blind retry (no idempotent_effect, no completed
# reconciliation) is refused for all three, not just bare UNKNOWN (Part
# 3/16): RECONCILIATION_REQUIRED and RECONCILING are equally "we do not
# yet have a safe basis for another attempt".
_UNRESOLVED_RETRY_BLOCKING_STATES = frozenset(
    {
        ExecutionAttemptState.UNKNOWN,
        ExecutionAttemptState.RECONCILIATION_REQUIRED,
        ExecutionAttemptState.RECONCILING,
    }
)


def assert_retry_safe(
    operation_id: str,
    *,
    idempotent_effect: bool = False,
    reconciled: bool = False,
) -> None:
    """Read-only: would a new attempt for operation_id be safe right now?

    Raises UnsafeBlindRetry without allocating or mutating anything (Part
    L rule 10). Call this BEFORE re-running AUTH/AUTHZ/re-commitment for a
    retry (security_boundary.retry_execution_attempt does), so a doomed
    blind retry never touches ledger or attempt state at all — a refused
    retry leaves the prior attempt and commitment exactly as they were.

    FAILED is positive evidence the effect did not occur, so a retry after
    FAILED (including after reconciliation resolves TO failed) is never
    blind and is always allowed here.
    """
    prior = get_attempt_store().latest_for(operation_id)
    if prior is not None and prior.state in _UNRESOLVED_RETRY_BLOCKING_STATES:
        if not idempotent_effect and not reconciled:
            raise UnsafeBlindRetry(
                f"operation {operation_id} attempt {prior.execution_attempt_id} is "
                f"{prior.state.value}; reconciliation required before another "
                "effect-producing attempt",
            )


def new_attempt_after(
    operation_id: str,
    *,
    idempotent_effect: bool = False,
    reconciled: bool = False,
) -> ExecutionAttempt:
    """Validate-and-allocate attempt N+1 for operation_id (Part F/G).

    Only ever called from inside _execute_attempt_pipeline, strictly AFTER
    the commitment is durable (ledger AUDIT_INTENT_RECORDED) — see that
    function's docstring. Callers gating a retry BEFORE re-commitment
    should call assert_retry_safe() instead, which performs the identical
    check without allocating.
    """
    assert_retry_safe(operation_id, idempotent_effect=idempotent_effect, reconciled=reconciled)
    return get_attempt_store().create(operation_id, idempotent_effect=idempotent_effect)


def reconcile_execution_attempt(
    operation_id: str,
    *,
    confirmed: str,
    evidence: dict[str, Any] | None = None,
) -> ExecutionAttempt:
    """Kernel-only convenience: run one full, single-shot reconciliation
    pass for operation_id's latest attempt through the EXPLICIT states
    (Part C: UNKNOWN -> RECONCILIATION_REQUIRED -> RECONCILING ->
    SUCCEEDED|FAILED|UNKNOWN), bootstrapping UNKNOWN -> RECONCILIATION_
    REQUIRED first if the attempt hasn't been marked yet.

    `confirmed` mirrors security_operation.reconcile_operation()'s own
    contract (succeeded|failed|unknown) exactly — call both together when
    reconciling a real operation that has a recorded attempt, since they
    are two separate state machines (commitment vs. attempt) that must
    both be told the same authoritative outcome. Trust is enforced by
    claim_reconciliation()/record_reconciliation_result() themselves
    (commitment/privileged-infra only); an agent cannot call this to
    assert its own outcome.

    For a caller that needs to hold the reconciliation lease across an
    async external query (rather than resolving it synchronously in one
    call), use claim_reconciliation() / record_reconciliation_result()
    directly instead of this convenience wrapper.
    """
    if confirmed not in ("succeeded", "failed", "unknown"):
        raise ValueError("confirmed must be succeeded|failed|unknown")
    attempt = get_attempt_store().latest_for(operation_id)
    if attempt is None:
        raise AttemptNotFound(operation_id)
    if attempt.state is ExecutionAttemptState.UNKNOWN:
        transition_attempt(attempt.execution_attempt_id, ExecutionAttemptState.RECONCILIATION_REQUIRED)
    reconciliation_id = claim_reconciliation(attempt.execution_attempt_id)
    outcome = {
        "succeeded": ExecutionAttemptState.SUCCEEDED,
        "failed": ExecutionAttemptState.FAILED,
        "unknown": ExecutionAttemptState.UNKNOWN,
    }[confirmed]
    return record_reconciliation_result(
        attempt.execution_attempt_id,
        reconciliation_id,
        outcome,
        evidence={**(evidence or {}), "reconciled": True},
    )


_STAGE_TO_STATE = {
    "ATTEMPT_CREATED": ExecutionAttemptState.NOT_STARTED,
    "ATTEMPT_READY": ExecutionAttemptState.READY,
    "ATTEMPT_STARTED": ExecutionAttemptState.STARTED,
    "ATTEMPT_SUBMITTED": ExecutionAttemptState.SUBMITTED,
}


def reconstruct_attempts_from_audit(
    entries: list[dict[str, Any]],
) -> dict[str, ExecutionAttemptState]:
    """Recover execution-attempt state from durable audit evidence.

    Mirrors security_operation.reconstruct_operations_from_audit but at
    attempt granularity (Part M step 5: attempt state must survive a
    process/worker restart or Redis loss — the in-process AttemptStore is
    not the durable registry). Entries carrying a recognized `state` (from
    a terminal .result record) win outright; otherwise the furthest-along
    lifecycle stage seen (created < ready < started < submitted) wins.
    """
    order = [
        ExecutionAttemptState.NOT_STARTED,
        ExecutionAttemptState.READY,
        ExecutionAttemptState.STARTED,
        ExecutionAttemptState.SUBMITTED,
    ]
    rank = {state: i for i, state in enumerate(order)}
    best: dict[str, ExecutionAttemptState] = {}
    for raw in entries:
        details = raw.get("details") or {}
        attempt_id = str(details.get("execution_attempt_id") or "")
        if not attempt_id:
            continue
        state_str = str(details.get("state") or details.get("execution_state") or "")
        state: ExecutionAttemptState | None = None
        if state_str:
            try:
                state = ExecutionAttemptState(state_str)
            except ValueError:
                state = None
        if state is not None and state not in rank:
            # A terminal/authoritative state (SUCCEEDED/FAILED/UNKNOWN/
            # CANCELLED) always wins over a lifecycle-stage guess.
            best[attempt_id] = state
            continue
        if state is None:
            stage = str(details.get("stage") or "")
            state = _STAGE_TO_STATE.get(stage)
        if state is None:
            continue
        current = best.get(attempt_id)
        if current is None or (current in rank and rank[state] >= rank[current]):
            best[attempt_id] = state
    for attempt_id, state in best.items():
        if state is ExecutionAttemptState.RECONCILING:
            # Part 13: the reconciliation LEASE (owner, generation, expiry)
            # lives only in the in-process AttemptStore -- it is gone after
            # a process restart. Recovering "as if still RECONCILING" would
            # imply an active owner that no longer exists, so recovery is
            # conservative: the attempt is unresolved and requires a fresh
            # claim_reconciliation() call, not a resumed one.
            best[attempt_id] = ExecutionAttemptState.RECONCILIATION_REQUIRED
    return best
