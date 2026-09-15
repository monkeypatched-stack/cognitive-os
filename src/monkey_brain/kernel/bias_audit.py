"""Bias Audit — disparate-impact ("80% rule") check over an action's own
recorded decision history, grouped by a caller-supplied protected attribute.

Purely additive: `GovernanceEngine.evaluate()` (kernel/governance.py) only
calls this for actions listed in config.BIAS_AUDITED_ACTIONS (empty by
default), and only when the caller supplies a `bias_group` in the request
context — no bias_group, no config entry, no effect on anything else.

This module owns both ends of its own audit trail: `record()` writes a
`bias_audit` entry (runtime_id/action/protected_attribute/group_value/
outcome) after every governed decision that opted in, and `evaluate()`
reads that same trail back to compute each group's selection rate before
the NEXT such decision is made. Statistics happen here in Python, not in
Rego — OPA (opa/policies/agentos_governance.rego) only ever branches on the
pre-computed `bias_detected`/`bias_disparity_ratio` signals this produces.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from src.monkey_brain.kernel.audit import get_audit_log
from src.monkey_brain.kernel.config import (
    BIAS_DISPARITY_THRESHOLD,
    BIAS_HISTORY_WINDOW,
    BIAS_MIN_SAMPLE_SIZE,
)

logger = logging.getLogger("agentos.bias_audit")

BIAS_AUDIT_EVENT_TYPE = "bias_audit"


@dataclass(frozen=True)
class BiasAuditResult:
    bias_detected: bool
    disparity_ratio: float | None
    protected_attribute: str
    group_value: str
    group_counts: dict[str, int] = field(default_factory=dict)
    sample_size: int = 0
    reason: str = ""


class BiasAuditor:
    def evaluate(self, action: str, protected_attribute: str, group_value: str) -> BiasAuditResult:
        """Compute this group's selection-rate ratio against the
        best-performing other group, from prior recorded decisions for the
        same action + protected attribute. Never raises — an audit-log
        failure here must not block the governance decision it feeds."""
        try:
            entries = get_audit_log().query(event_type=BIAS_AUDIT_EVENT_TYPE, limit=BIAS_HISTORY_WINDOW)
        except Exception as exc:
            logger.error("Bias audit history query failed for action=%s: %s", action, exc)
            return BiasAuditResult(
                bias_detected=False,
                disparity_ratio=None,
                protected_attribute=protected_attribute,
                group_value=group_value,
                reason="audit_query_failed",
            )

        buckets: dict[str, dict[str, int]] = {}
        for entry in entries:
            if entry.action != action or entry.details.get("protected_attribute") != protected_attribute:
                continue
            group = entry.details.get("group_value", "")
            bucket = buckets.setdefault(group, {"total": 0, "allowed": 0})
            bucket["total"] += 1
            if entry.outcome == "allow":
                bucket["allowed"] += 1

        group_counts = {group: bucket["total"] for group, bucket in buckets.items()}
        sample_size = sum(group_counts.values())
        rates = {group: bucket["allowed"] / bucket["total"] for group, bucket in buckets.items() if bucket["total"] > 0}

        if sample_size < BIAS_MIN_SAMPLE_SIZE or len(rates) < 2:
            return BiasAuditResult(
                bias_detected=False,
                disparity_ratio=None,
                protected_attribute=protected_attribute,
                group_value=group_value,
                group_counts=group_counts,
                sample_size=sample_size,
                reason="insufficient_history",
            )

        this_rate = rates.get(group_value)
        other_rates = [rate for group, rate in rates.items() if group != group_value]
        if this_rate is None or not other_rates:
            return BiasAuditResult(
                bias_detected=False,
                disparity_ratio=None,
                protected_attribute=protected_attribute,
                group_value=group_value,
                group_counts=group_counts,
                sample_size=sample_size,
                reason="no_comparison_group",
            )

        best_other_rate = max(other_rates)
        disparity_ratio = (this_rate / best_other_rate) if best_other_rate > 0 else 0.0
        bias_detected = disparity_ratio < BIAS_DISPARITY_THRESHOLD

        return BiasAuditResult(
            bias_detected=bias_detected,
            disparity_ratio=disparity_ratio,
            protected_attribute=protected_attribute,
            group_value=group_value,
            group_counts=group_counts,
            sample_size=sample_size,
            reason="disparate_impact" if bias_detected else "",
        )

    def record(self, runtime_id: str, action: str, protected_attribute: str, group_value: str, allowed: bool) -> None:
        """Append this decision to the bias-audit trail so it counts toward
        future evaluate() calls for the same action + attribute. Best-effort
        — recording failure must not surface as a governance failure."""
        try:
            get_audit_log().record(
                runtime_id=runtime_id,
                event_type=BIAS_AUDIT_EVENT_TYPE,
                action=action,
                outcome="allow" if allowed else "deny",
                details={"protected_attribute": protected_attribute, "group_value": group_value},
            )
        except Exception as exc:
            logger.error("Failed to record bias_audit entry for action=%s: %s", action, exc)


_default_auditor: BiasAuditor | None = None


def get_bias_auditor() -> BiasAuditor:
    global _default_auditor
    if _default_auditor is None:
        _default_auditor = BiasAuditor()
    return _default_auditor
