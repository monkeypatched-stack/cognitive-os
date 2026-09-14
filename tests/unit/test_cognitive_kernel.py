"""Unit tests for CognitiveKernel — the central cognitive loop."""

import asyncio
import pytest

from monkey_brain.kernel.cognitive_kernel import CognitiveKernel

# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_kernel_initialises_without_error():
    k = CognitiveKernel()
    assert k._step == 0
    assert k._goal is not None
    assert k.state is not None


def test_kernel_state_has_epa_fields():
    k = CognitiveKernel()
    s = k.state
    assert hasattr(s, "S")
    assert hasattr(s, "B")
    assert hasattr(s, "A")
    assert hasattr(s, "M")
    assert isinstance(s.S, dict)
    assert isinstance(s.A, set)
    assert isinstance(s.M, dict)


# ---------------------------------------------------------------------------
# set_goal
# ---------------------------------------------------------------------------


def test_set_goal_updates_objective():
    k = CognitiveKernel()
    k.set_goal("minimise equipment downtime")
    assert k._goal.objective == "minimise equipment downtime"


def test_set_goal_with_predicates():
    k = CognitiveKernel()
    k.set_goal("inspect bay 3", target_predicates=["bay_3_inspected"])
    assert "bay_3_inspected" in k._goal.target_predicates


def test_set_goal_replaces_previous_goal():
    k = CognitiveKernel()
    k.set_goal("goal A")
    k.set_goal("goal B")
    assert k._goal.objective == "goal B"


# ---------------------------------------------------------------------------
# get_state
# ---------------------------------------------------------------------------


def test_get_state_returns_expected_keys():
    k = CognitiveKernel()
    state = k.get_state()
    for key in ("step", "epa_state", "goal", "mesh", "planner_policy"):
        assert key in state, f"missing key: {key}"


def test_get_state_step_zero_on_fresh_kernel():
    k = CognitiveKernel()
    assert k.get_state()["step"] == 0


# ---------------------------------------------------------------------------
# step() — planner drives action selection
# ---------------------------------------------------------------------------


def test_step_returns_required_keys():
    k = CognitiveKernel()
    k.set_goal("test goal")
    result = asyncio.run(k.step())
    for key in ("step", "action", "confidence", "L_E", "elapsed_ms"):
        assert key in result, f"missing key: {key}"


def test_step_increments_counter():
    k = CognitiveKernel()
    asyncio.run(k.step())
    asyncio.run(k.step())
    assert k._step == 2


def test_step_with_explicit_action():
    k = CognitiveKernel()
    result = asyncio.run(k.step(action="inspect_machine_42"))
    assert result["action"] == "inspect_machine_42"


def test_step_loss_is_non_negative():
    k = CognitiveKernel()
    result = asyncio.run(k.step())
    assert result["L_E"] >= 0.0


def test_step_updates_epa_state():
    k = CognitiveKernel()
    state_before = k.get_state()["step"]
    asyncio.run(k.step(action="noop"))
    assert k.get_state()["step"] == state_before + 1


# ---------------------------------------------------------------------------
# Learning loop (Law 4 — all three components update)
# ---------------------------------------------------------------------------


def test_planner_policy_updated_after_step():
    k = CognitiveKernel()
    k.set_goal("learn goal")
    asyncio.run(k.step(action="action_a"))
    summary = k._planner_policy.summary()
    assert isinstance(summary, dict)


def test_retrieval_policy_has_update_method():
    k = CognitiveKernel()
    # Must not raise — confirms Law 4 bug (Bug 2) is fixed
    k.retrieval_policy.update(cost=0.1, gained=1)


def test_capability_utility_populated_after_step():
    k = CognitiveKernel()
    asyncio.run(k.step(action="cap_x"))
    assert hasattr(k, "_capability_utility")
    assert "cap_x" in k._capability_utility


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


def test_history_grows_with_steps():
    k = CognitiveKernel()
    for _ in range(3):
        asyncio.run(k.step())
    assert len(k._history) == 3


def test_history_capped_at_max():
    k = CognitiveKernel()
    k._max_history = 5
    for _ in range(8):
        asyncio.run(k.step())
    assert len(k._history) <= 5
