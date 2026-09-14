"""Unit tests for cortex.epa — EpistemicPredictiveState, epa_transition, epa_loss."""

import pytest
from cortex.epa import EpistemicPredictiveState, epa_transition, epa_loss
from cortex.epistemic import BeliefState, GoalState


def _make_state(world=None, affordances=None):
    return EpistemicPredictiveState(
        S=world or {"x": 1},
        B=BeliefState(knowledge=[], confidence=0.8),
        A=set(affordances or ["cap_a", "cap_b"]),
        M={},
    )


def _make_goal(predicates=None):
    return GoalState(objective="test", target_predicates=predicates or [])


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_from_world_state_constructs_valid_state():
    state = EpistemicPredictiveState.from_world_state({"x": 1})
    assert isinstance(state.S, dict)
    assert isinstance(state.B, BeliefState)
    assert isinstance(state.A, set)
    assert isinstance(state.M, dict)


def test_epa_state_properties():
    state = _make_state()
    assert state.world_state is state.S
    assert state.belief is state.B
    assert state.affordances is state.A
    assert state.mesh is state.M


def test_epa_state_to_dict_roundtrip():
    state = _make_state(world={"y": 2}, affordances=["cap_x"])
    d = state.to_dict()
    assert d["S"] == {"y": 2}
    assert "cap_x" in d["A"]
    assert "confidence" in d["B"]


# ---------------------------------------------------------------------------
# Transition
# ---------------------------------------------------------------------------


def test_epa_transition_updates_all_four_components():
    E_t = _make_state(affordances=["cap_a"])
    G_t = _make_goal()
    E_next = epa_transition(E_t, G_t, "cap_a")

    assert isinstance(E_next, EpistemicPredictiveState)
    assert isinstance(E_next.S, dict)
    assert isinstance(E_next.B, BeliefState)
    assert isinstance(E_next.A, set)
    assert isinstance(E_next.M, dict)


def test_epa_transition_with_evidence_positive():
    E_t = _make_state()
    G_t = _make_goal()
    evidence = {"converged": True, "knowledge_loss": 0.0, "confidence_delta": 0.1}
    E_next = epa_transition(E_t, G_t, "cap_a", evidence=evidence)
    assert E_next.B.confidence >= 0.0
    assert E_next.B.confidence <= 1.0


def test_epa_transition_with_evidence_negative():
    E_t = _make_state()
    G_t = _make_goal()
    evidence = {"converged": False, "knowledge_loss": 0.5, "constraint_violations": 2}
    E_next = epa_transition(E_t, G_t, "unknown_action", evidence=evidence)
    assert isinstance(E_next, EpistemicPredictiveState)


def test_epa_transition_goal_progress_in_mesh():
    E_t = _make_state(world={"x": 1})
    G_t = GoalState(objective="set x", target_predicates=["x == 1"])
    E_next = epa_transition(E_t, G_t, "cap_a")
    assert E_next.M.get("goal_progress") == 1.0


def test_epa_transition_last_action_in_mesh():
    E_t = _make_state()
    G_t = _make_goal()
    E_next = epa_transition(E_t, G_t, "my_action")
    assert E_next.M.get("last_action") == "my_action"


# ---------------------------------------------------------------------------
# Loss
# ---------------------------------------------------------------------------


def test_epa_loss_returns_all_four_terms():
    state = _make_state()
    loss = epa_loss(state, state)
    assert "L_S" in loss
    assert "L_B" in loss
    assert "L_A" in loss
    assert "L_M" in loss
    assert "L_E" in loss
    assert all(isinstance(v, float) for v in loss.values())


def test_epa_loss_identical_states_zero():
    state = _make_state()
    loss = epa_loss(state, state)
    assert loss["L_S"] == 0.0
    assert loss["L_A"] == 0.0
    assert loss["L_M"] == 0.0


def test_epa_loss_l_e_equals_sum():
    s1 = _make_state(world={"a": 1}, affordances=["x"])
    s2 = _make_state(world={"a": 2}, affordances=["y"])
    loss = epa_loss(s1, s2)
    expected = round(
        loss["L_S"] + loss["L_B"] + loss["L_A"] + loss["L_M"] + loss["L_K"] + loss["L_C"] + loss["L_G"],
        4,
    )
    assert loss["L_E"] == expected


def test_epa_loss_values_in_range():
    s1 = _make_state(world={"a": 1})
    s2 = _make_state(world={"b": 2})
    loss = epa_loss(s1, s2)
    for k, v in loss.items():
        assert 0.0 <= v, f"{k}={v} is negative"


# ---------------------------------------------------------------------------
# BeliefState.loss()
# ---------------------------------------------------------------------------


def test_belief_state_loss_in_range():
    b = BeliefState(knowledge=[], confidence=0.7)
    l = b.loss()
    assert 0.0 <= l <= 1.0


def test_belief_state_perfect_confidence_low_loss():
    b = BeliefState(knowledge=[], confidence=1.0)
    l = b.loss()
    assert l < 0.5


def test_belief_state_zero_confidence_high_loss():
    b = BeliefState(knowledge=[], confidence=0.0)
    l = b.loss()
    assert l > 0.0


# ---------------------------------------------------------------------------
# GoalState.progress()
# ---------------------------------------------------------------------------


def test_goal_progress_all_satisfied():
    g = GoalState(objective="test", target_predicates=["x == 1"])
    assert g.progress({"x": 1}) == 1.0


def test_goal_progress_none_satisfied():
    g = GoalState(objective="test", target_predicates=["x == 1"])
    assert g.progress({"x": 0}) == 0.0


def test_goal_progress_partial():
    g = GoalState(objective="test", target_predicates=["x == 1", "y == 2"])
    assert g.progress({"x": 1, "y": 99}) == 0.5


def test_goal_progress_no_predicates():
    g = GoalState(objective="test", target_predicates=[])
    assert g.progress({"x": 1}) == 0.0
