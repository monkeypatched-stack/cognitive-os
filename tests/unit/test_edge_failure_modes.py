"""Failure-mode classification table (kernel/edge/failure_modes.py) --
proves every failure this task enumerates has exactly one classification
and that the security-critical ones are never LOCAL_CONTINUE."""
from __future__ import annotations

from src.monkey_brain.kernel.edge.failure_modes import (
    FailureMode,
    FailureResponse,
    all_classifications,
    classify,
)


def test_every_failure_mode_is_classified():
    for mode in FailureMode:
        c = classify(mode)
        assert c.mode is mode
        assert c.mechanism
        assert c.rationale


def test_all_classifications_covers_every_enum_member():
    modes = {c.mode for c in all_classifications()}
    assert modes == set(FailureMode)


def test_revoked_and_expired_delegation_always_deny():
    assert classify(FailureMode.REVOKED_DELEGATION).response is FailureResponse.DENY
    assert classify(FailureMode.EXPIRED_DELEGATION).response is FailureResponse.DENY


def test_stale_policy_escalates_rather_than_degrades():
    """Authority (policy) is never extended past its verified window --
    unlike world-state staleness, which is allowed to degrade."""
    assert classify(FailureMode.STALE_POLICY).response is FailureResponse.ESCALATE


def test_stale_world_state_is_allowed_to_degrade():
    assert classify(FailureMode.STALE_WORLD_STATE).response is FailureResponse.LOCAL_DEGRADE


def test_actor_restart_is_a_non_event_for_durable_local_state():
    assert classify(FailureMode.ACTOR_RESTART).response is FailureResponse.LOCAL_CONTINUE


def test_no_security_critical_failure_is_local_continue():
    security_critical = {FailureMode.REVOKED_DELEGATION, FailureMode.EXPIRED_DELEGATION, FailureMode.STALE_POLICY}
    for mode in security_critical:
        assert classify(mode).response is not FailureResponse.LOCAL_CONTINUE
