"""Bias Audit (kernel/bias_audit.py) — disparate-impact ("80% rule") check
over an action's own recorded decision history.

Not executed via pytest in this session (per standing instruction) — written
so the suite covers this module the next time the full suite is run.
"""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.audit import AuditLog
from src.monkey_brain.kernel.bias_audit import BiasAuditor
import src.monkey_brain.kernel.bias_audit as bias_audit_module


@pytest.fixture
def auditor(monkeypatch):
    """A BiasAuditor wired to a fresh, isolated AuditLog — not the process
    singleton — so tests never see another test's history."""
    fresh_log = AuditLog()
    monkeypatch.setattr(bias_audit_module, "get_audit_log", lambda: fresh_log)
    return BiasAuditor(), fresh_log


def _seed_decisions(log: AuditLog, action: str, attribute: str, group_value: str, allowed: bool, count: int) -> None:
    for _ in range(count):
        log.record(
            runtime_id="rt1",
            event_type="bias_audit",
            action=action,
            outcome="allow" if allowed else "deny",
            details={"protected_attribute": attribute, "group_value": group_value},
        )


def test_insufficient_history_never_flags(auditor):
    bias, log = auditor
    _seed_decisions(log, "execute", "gender", "female", allowed=False, count=3)
    _seed_decisions(log, "execute", "gender", "male", allowed=True, count=3)

    result = bias.evaluate("execute", "gender", "female")

    assert result.bias_detected is False
    assert result.reason == "insufficient_history"


def test_single_group_history_has_no_comparison(auditor):
    bias, log = auditor
    _seed_decisions(log, "execute", "gender", "female", allowed=True, count=25)

    result = bias.evaluate("execute", "gender", "female")

    assert result.bias_detected is False
    assert result.reason == "no_comparison_group"


def test_disparate_impact_below_threshold_is_flagged(auditor):
    bias, log = auditor
    # male: 20/20 allowed (100%); female: 4/20 allowed (20%) -> ratio 0.2, well under the 0.8 threshold.
    _seed_decisions(log, "execute", "gender", "male", allowed=True, count=20)
    _seed_decisions(log, "execute", "gender", "female", allowed=True, count=4)
    _seed_decisions(log, "execute", "gender", "female", allowed=False, count=16)

    result = bias.evaluate("execute", "gender", "female")

    assert result.bias_detected is True
    assert result.disparity_ratio == pytest.approx(0.2)
    assert result.sample_size == 40


def test_comparable_rates_are_not_flagged(auditor):
    bias, log = auditor
    _seed_decisions(log, "execute", "gender", "male", allowed=True, count=18)
    _seed_decisions(log, "execute", "gender", "male", allowed=False, count=2)
    _seed_decisions(log, "execute", "gender", "female", allowed=True, count=17)
    _seed_decisions(log, "execute", "gender", "female", allowed=False, count=3)

    result = bias.evaluate("execute", "gender", "female")

    assert result.bias_detected is False


def test_different_action_history_is_not_mixed_in(auditor):
    bias, log = auditor
    _seed_decisions(log, "plan", "gender", "male", allowed=True, count=20)
    _seed_decisions(log, "plan", "gender", "female", allowed=False, count=20)

    result = bias.evaluate("execute", "gender", "female")

    assert result.sample_size == 0
    assert result.bias_detected is False


def test_record_then_evaluate_round_trips_through_the_real_audit_log(auditor):
    bias, _log = auditor
    for _ in range(20):
        bias.record("rt1", "execute", "gender", "male", allowed=True)
    for _ in range(4):
        bias.record("rt1", "execute", "gender", "female", allowed=True)
    for _ in range(16):
        bias.record("rt1", "execute", "gender", "female", allowed=False)

    result = bias.evaluate("execute", "gender", "female")

    assert result.bias_detected is True
    assert result.group_counts == {"male": 20, "female": 20}


def test_evaluate_never_raises_when_audit_log_query_fails(auditor, monkeypatch):
    bias, log = auditor

    def boom(*args, **kwargs):
        raise RuntimeError("audit store unavailable")

    monkeypatch.setattr(log, "query", boom)

    result = bias.evaluate("execute", "gender", "female")

    assert result.bias_detected is False
    assert result.reason == "audit_query_failed"
