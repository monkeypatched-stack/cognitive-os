"""Idempotency classification for capabilities — richer than a binary flag.

A binary idempotent/non-idempotent split can't express the difference
between "safe to re-run because it has no side effects", "safe to re-run
because re-running converges to the same state", and "safe to re-run only
because the capability itself de-duplicates via a caller-supplied key". Those
three cases warrant different retry behavior, so they're distinct classes.
"""

from __future__ import annotations

from enum import Enum


class IdempotencyClass(str, Enum):
    PURE = "pure"
    """No side effects at all (read_file, classify_intent). Always safe to
    re-run unconditionally."""

    IDEMPOTENT = "idempotent"
    """Has a side effect, but re-running with the same inputs converges to
    the same end state (e.g. "set status = approved"). Safe to re-run."""

    AT_LEAST_ONCE_SAFE = "at_least_once_safe"
    """Side effect is not naturally idempotent, but the capability accepts
    an idempotency key and de-duplicates on it (e.g. an upsert keyed on a
    request id). Safe to retry ONLY when the Process Manager supplies the
    same key across attempts."""

    NON_IDEMPOTENT = "non_idempotent"
    """Re-running risks a distinct, uncontrolled side effect (send_email,
    append_audit_log). Must never be retried blindly — an ambiguous outcome
    (crash mid-call, unknown whether it landed) must escalate to a human
    decision rather than guess (see ProcessManager's partial-pipeline
    recovery path)."""


def idempotency_key(run_id: str, node_id: str) -> str:
    """Stable across retries of the SAME logical node — deliberately does
    NOT include an attempt number, since the whole point is that repeated
    attempts of the same node dedupe to one key. A node replaced by graph
    expansion (e.g. SelfHealingPolicy's `repair-N`) gets its own node_id and
    therefore its own key, which is correct — that's a distinct logical
    operation, not a retry of the same one.
    """
    return f"{run_id}:{node_id}"


# Capabilities that don't declare a class default to the safest assumption:
# don't retry blindly. This is a conservative default for retrofitting onto
# capabilities that were never written with idempotency in mind.
DEFAULT_IDEMPOTENCY_CLASS = IdempotencyClass.NON_IDEMPOTENT
