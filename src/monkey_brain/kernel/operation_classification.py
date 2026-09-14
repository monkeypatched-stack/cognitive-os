"""Canonical operation classification — side effects, not names.

Unknown operations are security-critical. Agents cannot declare an
operation non-critical if its name/effect looks mutating.
"""

from __future__ import annotations

from enum import Enum

from src.monkey_brain.kernel.trusted_auth import strip_untrusted_security_signals


class OperationClass(str, Enum):
    READ_ONLY = "read_only"
    PROPOSAL_ONLY = "proposal_only"
    SECURITY_CRITICAL = "security_critical"
    PRIVILEGED_INFRA = "privileged_infra"


_MUTATING_MARKERS = (
    "insert",
    "update",
    "delete",
    "replace",
    "drop",
    "create",
    "commit",
    "mutate",
    "write",
    "execute",
    "invoke",
    "payment",
    "pay_",
    "refund",
    "capture",
    "webhook",
    "grant",
    "revoke",
    "approve",
    "deny",
    "policy",
    "opa",
    "mfa",
    "credential",
    "session",
    "role",
    "permission",
    "transition",
    "actor.tick",
    "tool.",
    "send_",
    "world.",
    "shipment",
    "fulfill",
    "orders.",
)

_READ_MARKERS = (
    "get_",
    "list_",
    "query",
    "observe",
    "lookup",
    "search",
    "retrieve",
    "find_",
    "read_",
    "status",
    "health",
    "metrics",
    "describe",
)

_PROPOSAL_MARKERS = (
    "plan",
    "predict",
    "simulate",
    "rank",
    "score",
    "embed",
    "reason",
    "propose",
    "jepa",
    "forecast",
    "candidate",
    "classify",
)


def _looks_mutating(name: str) -> bool:
    return any(marker in name for marker in _MUTATING_MARKERS)


def classify_operation(name: str, *, declared: OperationClass | None = None) -> OperationClass:
    """Classify an operation. Declared READ_ONLY/PROPOSAL_ONLY cannot
    override a mutating name (agents do not get to mark payment as a read)."""
    key = (name or "").strip().lower()
    if declared is OperationClass.PRIVILEGED_INFRA:
        return OperationClass.PRIVILEGED_INFRA
    if declared in (
        OperationClass.READ_ONLY,
        OperationClass.PROPOSAL_ONLY,
    ) and _looks_mutating(key):
        return OperationClass.SECURITY_CRITICAL
    if declared is OperationClass.SECURITY_CRITICAL:
        return OperationClass.SECURITY_CRITICAL
    if _looks_mutating(key):
        return OperationClass.SECURITY_CRITICAL
    if declared in (OperationClass.READ_ONLY, OperationClass.PROPOSAL_ONLY):
        return declared
    if any(marker in key for marker in _READ_MARKERS) and not _looks_mutating(key):
        return OperationClass.READ_ONLY
    if any(marker in key for marker in _PROPOSAL_MARKERS) and not _looks_mutating(key):
        return OperationClass.PROPOSAL_ONLY
    return OperationClass.SECURITY_CRITICAL


def sanitize_operation_metadata(metadata: dict | None) -> dict:
    return strip_untrusted_security_signals(metadata)


def is_security_critical(name: str, *, declared: OperationClass | None = None) -> bool:
    return classify_operation(name, declared=declared) == OperationClass.SECURITY_CRITICAL
