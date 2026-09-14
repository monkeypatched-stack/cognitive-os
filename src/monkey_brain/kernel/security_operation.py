"""Durable lifecycle for security-critical operations.

This is NOT a distributed transaction across Mongo + Redis + HTTP.
Mongo multi-document transactions are not used in this deployment
(no session/with_transaction usage). Class A is a *logical* unit:
operation ledger + audit intent must both exist before the effect.

Class B (local state + external effect) uses pending → confirm → result.
Class C (irreversible) never claims rollback.

FAILED ≠ UNKNOWN. Agents cannot set these states.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

logger = logging.getLogger("agentos.security_operation")


class SecurityOperationState(str, Enum):
    AUTHORIZED = "authorized"
    AWAITING_APPROVAL = "awaiting_approval"  # Queued for human approval
    AUDIT_INTENT_RECORDED = "audit_intent_recorded"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"
    RECONCILIATION_REQUIRED = "reconciliation_required"


class TransactionClass(str, Enum):
    CLASS_A_INTERNAL = "class_a_internal_mutation"
    CLASS_B_EXTERNAL = "class_b_local_plus_external"
    CLASS_C_IRREVERSIBLE = "class_c_irreversible"


class DuplicateSecurityOperation(Exception):
    """Same operation_id already admitted; a second effect is forbidden."""

    def __init__(self, operation_id: str, state: SecurityOperationState) -> None:
        self.operation_id = operation_id
        self.state = state
        super().__init__(f"duplicate operation {operation_id} in state {state.value}")


class EffectKind(str, Enum):
    """Policy classification of an effect — not a database mechanism."""

    INTERNAL_TRANSACTIONAL = "internal_transactional"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"
    IRREVERSIBLE_EFFECT = "irreversible_effect"
    AUTHORITY_CHANGE = "authority_change"
    AUDIT_OPERATION = "audit_operation"


def classify_effect_kind(action: str) -> EffectKind:
    key = (action or "").lower()
    if "audit" in key:
        return EffectKind.AUDIT_OPERATION
    if any(
        m in key
        for m in (
            "grant",
            "revoke",
            "role",
            "permission",
            "mfa",
            "credential",
            "session",
        )
    ):
        return EffectKind.AUTHORITY_CHANGE
    if any(m in key for m in _EXTERNAL_MARKERS):
        return EffectKind.EXTERNAL_SIDE_EFFECT
    if "execute" in key or "actor.tick" in key:
        return EffectKind.IRREVERSIBLE_EFFECT
    return EffectKind.INTERNAL_TRANSACTIONAL


class UnknownOutcomeError(Exception):
    """Effect may have occurred; CognitiveOS cannot confirm success or failure."""

    def __init__(self, message: str, *, operation_id: str = "") -> None:
        self.operation_id = operation_id
        super().__init__(message)


class AuditResultUnavailable(Exception):
    """Effect ran; durable audit result could not be stored."""

    def __init__(self, message: str, *, operation_id: str = "", effect_occurred: bool = True) -> None:
        self.operation_id = operation_id
        self.effect_occurred = effect_occurred
        super().__init__(message)


_EXTERNAL_MARKERS = (
    "payment",
    "webhook",
    "razorpay",
    "http",
    "send_",
    "sms",
    "email",
    "device",
    "robot",
    "actor.tick",
    "capture",
    "refund",
)


def classify_transaction(action: str) -> TransactionClass:
    key = (action or "").lower()
    if any(m in key for m in _EXTERNAL_MARKERS):
        return TransactionClass.CLASS_B_EXTERNAL
    if "execute" in key or "world." in key or "orders." in key:
        return TransactionClass.CLASS_A_INTERNAL
    return TransactionClass.CLASS_A_INTERNAL


@dataclass
class SecurityOperation:
    operation_id: str
    action: str
    resource: str
    state: SecurityOperationState
    transaction_class: TransactionClass
    principal_id: str = ""
    idempotency_key: str = ""
    policy_decision: str = ""
    mfa_status: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "action": self.action,
            "resource": self.resource,
            "state": self.state.value,
            "transaction_class": self.transaction_class.value,
            "principal_id": self.principal_id,
            "idempotency_key": self.idempotency_key,
            "policy_decision": self.policy_decision,
            "mfa_status": self.mfa_status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "details": self.details,
        }


class OperationLedger:
    """Authoritative operation lifecycle. Memory in tests; Mongo when available."""

    def __init__(self) -> None:
        self._ops: dict[str, SecurityOperation] = {}
        self._lock = threading.Lock()

    def create(self, op: SecurityOperation) -> SecurityOperation:
        with self._lock:
            existing = self._ops.get(op.operation_id)
            if existing is not None and existing.state in (
                SecurityOperationState.SUCCEEDED,
                SecurityOperationState.EXECUTING,
                SecurityOperationState.UNKNOWN,
                SecurityOperationState.RECONCILIATION_REQUIRED,
                SecurityOperationState.AUDIT_INTENT_RECORDED,
            ):
                raise DuplicateSecurityOperation(existing.operation_id, existing.state)
            self._ops[op.operation_id] = op
        return op

    def get(self, operation_id: str) -> SecurityOperation | None:
        with self._lock:
            return self._ops.get(operation_id)

    def transition(self, operation_id: str, state: SecurityOperationState, **details: Any) -> SecurityOperation:
        # SecurityOperationState and execution_attempt.ExecutionAttemptState
        # are both (str, Enum) — a same-named member (e.g. SUCCEEDED) of the
        # WRONG enum compares equal by string value. isinstance checks the
        # actual class, not the string, so a foreign enum can't silently
        # pass as a commitment state here.
        if not isinstance(state, SecurityOperationState):
            raise TypeError(
                f"expected security_operation.SecurityOperationState, got "
                f"{type(state).__module__}.{type(state).__qualname__} ({state!r})",
            )
        with self._lock:
            op = self._ops[operation_id]
            if op.state in (SecurityOperationState.SUCCEEDED,) and state != SecurityOperationState.SUCCEEDED:
                raise ValueError("cannot unwind a succeeded operation")
            op.state = state
            op.updated_at = time.time()
            op.details.update(details)
            return op

    def find_by_idempotency(self, idempotency_key: str) -> SecurityOperation | None:
        if not idempotency_key:
            return None
        with self._lock:
            for op in self._ops.values():
                if op.idempotency_key == idempotency_key:
                    return op
        return None


_ledger: OperationLedger | None = None


def get_operation_ledger() -> OperationLedger:
    global _ledger
    if _ledger is None:
        _ledger = OperationLedger()
    return _ledger


def reset_operation_ledger_for_tests() -> None:
    global _ledger
    _ledger = OperationLedger()


def new_operation_id() -> str:
    return str(uuid4())


def reconcilable(op: SecurityOperation) -> bool:
    return op.state in (
        SecurityOperationState.UNKNOWN,
        SecurityOperationState.RECONCILIATION_REQUIRED,
        SecurityOperationState.EXECUTING,
    )


def reconcile_operation(
    operation_id: str,
    *,
    confirmed: str,
) -> SecurityOperation:
    """Kernel-only reconciliation. `confirmed` is succeeded|failed|unknown.

    Agents cannot call this with a fabricated confirmation; callers must
    already be inside a governed commitment.
    """
    from src.monkey_brain.kernel.security_boundary import (
        commitment_active,
        privileged_infra_active,
    )
    from src.monkey_brain.kernel.production_gates import insecure_dev_mode

    if not (commitment_active() or privileged_infra_active() or insecure_dev_mode()):
        raise PermissionError("reconciliation requires governed execution")
    if confirmed not in ("succeeded", "failed", "unknown"):
        raise ValueError("confirmed must be succeeded|failed|unknown")
    ledger = get_operation_ledger()
    op = ledger.get(operation_id)
    if op is None:
        raise KeyError(operation_id)
    if confirmed == "succeeded":
        return ledger.transition(operation_id, SecurityOperationState.SUCCEEDED, reconciled=True)
    if confirmed == "failed":
        return ledger.transition(operation_id, SecurityOperationState.FAILED, reconciled=True)
    return ledger.transition(
        operation_id,
        SecurityOperationState.RECONCILIATION_REQUIRED,
        reconciled=False,
    )


def reconstruct_operations_from_audit(
    entries: list[dict[str, Any]],
) -> dict[str, SecurityOperationState]:
    """Recover operation outcomes from durable audit evidence (any store).

    Intent without a result → EXECUTING (crash before or during effect).
    Result outcome success → SUCCEEDED.
    Result outcome failure → FAILED.
    Result outcome unknown/pending after intent → UNKNOWN / RECONCILIATION.
    """
    by_id: dict[str, dict[str, Any]] = {}
    for raw in entries:
        details = raw.get("details") or {}
        op_id = str(details.get("operation_id") or raw.get("correlation_id") or "")
        if not op_id:
            continue
        slot = by_id.setdefault(op_id, {"intent": False, "result": None})
        action = str(raw.get("action") or "")
        outcome = str(raw.get("outcome") or "")
        if action.endswith(".intent") or details.get("stage") == "AUDIT_INTENT":
            slot["intent"] = True
        if action.endswith(".result") or details.get("stage") == "AUDIT_RESULT":
            slot["result"] = outcome
    recovered: dict[str, SecurityOperationState] = {}
    for op_id, slot in by_id.items():
        if slot["result"] in ("success", "succeeded"):
            recovered[op_id] = SecurityOperationState.SUCCEEDED
        elif slot["result"] in ("unknown",):
            recovered[op_id] = SecurityOperationState.UNKNOWN
        elif slot["result"] in ("failure", "failed"):
            recovered[op_id] = SecurityOperationState.FAILED
        elif slot["intent"]:
            recovered[op_id] = SecurityOperationState.EXECUTING
    return recovered


def classify_external_exception(exc: BaseException) -> str:
    """Map an exception to failed vs unknown. Timeouts/connection resets are unknown."""
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    unknown_markers = (
        "timeout",
        "timed out",
        "connection reset",
        "connectionreset",
        "temporarily unavailable",
    )
    if isinstance(exc, UnknownOutcomeError) or any(m in name or m in text for m in unknown_markers):
        return "unknown"
    return "failed"
