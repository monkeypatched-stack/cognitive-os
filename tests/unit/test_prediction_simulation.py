"""Tests for the Simulation Engine (Step 11.3).

Exercises "Current State -> Apply Step 1 -> Apply Step 2 -> ... ->
Predicted State" against fake, deliberately mutable world/belief stand-ins
-- proving simulation never mutates them (identity checks, not just
equality) and that step trajectories accumulate correctly.
"""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.pipeline.prediction.transitions import (
    TransitionKind,
    TransitionModel,
    TransitionPredictionEngine,
    WorldTransition,
)
from src.monkey_brain.kernel.pipeline.prediction.simulation import (
    SimulationEngine,
    SimulationState,
    SimulationTrajectory,
    clone_state,
)


class _Step:
    def __init__(self, description: str) -> None:
        self.description = description


class _MutableWorld:
    def __init__(self) -> None:
        self.state = {"store_a_open": True}
        self.mutated = False

    def mutate(self) -> None:
        self.mutated = True


class _MutableBelief:
    def __init__(self) -> None:
        self.facts: list = []
        self.mutated = False


class _FakePlan:
    def __init__(self, steps) -> None:
        self.steps = steps


def _milk_model() -> TransitionModel:
    return TransitionModel(
        known_transitions={
            ("", "drive_to_store_a"): (
                WorldTransition(
                    description="Arrived, store open",
                    probability=0.9,
                    confidence=0.85,
                    resulting_world_delta={
                        "actor.location": "store_a",
                        "store_a.open": True,
                    },
                ),
                WorldTransition(
                    description="Traffic delay",
                    probability=0.1,
                    confidence=0.85,
                    resulting_world_delta={"actor.location": "en_route"},
                ),
            ),
            ("", "purchase_milk"): (
                WorldTransition(
                    description="Milk purchased",
                    probability=0.96,
                    confidence=0.9,
                    resulting_world_delta={"actor.has_milk": True},
                ),
            ),
        }
    )


def _engine() -> SimulationEngine:
    return SimulationEngine(TransitionPredictionEngine(_milk_model()))


# ═══════════════════════════════════════════════════════════════════════════
# Step-by-step trajectory: Current State -> Step 1 -> Step 2 -> Predicted State
# ═══════════════════════════════════════════════════════════════════════════


class TestTrajectory:
    def test_produces_a_state_per_step_plus_the_initial_state(self):
        trajectory = _engine().simulate(
            _MutableWorld(),
            _MutableBelief(),
            (_Step("drive_to_store_a"), _Step("purchase_milk")),
        )
        assert len(trajectory.states) == 3

    def test_initial_state_has_index_negative_one_and_empty_world(self):
        trajectory = _engine().simulate(_MutableWorld(), _MutableBelief(), (_Step("drive_to_store_a"),))
        assert trajectory.states[0].step_index == -1
        assert trajectory.states[0].world_state == {}

    def test_states_indexed_in_step_order(self):
        trajectory = _engine().simulate(
            _MutableWorld(),
            _MutableBelief(),
            (_Step("drive_to_store_a"), _Step("purchase_milk")),
        )
        assert trajectory.states[1].step_index == 0
        assert trajectory.states[2].step_index == 1

    def test_world_state_accumulates_across_steps(self):
        trajectory = _engine().simulate(
            _MutableWorld(),
            _MutableBelief(),
            (_Step("drive_to_store_a"), _Step("purchase_milk")),
        )
        assert trajectory.final_state.world_state == {
            "actor.location": "store_a",
            "store_a.open": True,
            "actor.has_milk": True,
        }

    def test_final_state_is_the_last_state(self):
        trajectory = _engine().simulate(_MutableWorld(), _MutableBelief(), (_Step("drive_to_store_a"),))
        assert trajectory.final_state == trajectory.states[-1]

    def test_each_state_records_its_applied_transition(self):
        trajectory = _engine().simulate(_MutableWorld(), _MutableBelief(), (_Step("purchase_milk"),))
        assert trajectory.states[1].applied_transition.description == "Milk purchased"

    def test_uses_the_most_likely_transition_at_each_step(self):
        """drive_to_store_a has a 90%/10% split -- the trajectory must
        follow the 90% branch, not the 10% one."""
        trajectory = _engine().simulate(_MutableWorld(), _MutableBelief(), (_Step("drive_to_store_a"),))
        assert trajectory.final_state.world_state["actor.location"] == "store_a"


# ═══════════════════════════════════════════════════════════════════════════
# succeeded — reflects whether every applied transition was confident and known
# ═══════════════════════════════════════════════════════════════════════════


class TestSucceeded:
    def test_all_known_confident_steps_succeed(self):
        trajectory = _engine().simulate(
            _MutableWorld(),
            _MutableBelief(),
            (_Step("drive_to_store_a"), _Step("purchase_milk")),
        )
        assert trajectory.succeeded is True

    def test_unknown_step_fails_the_trajectory(self):
        trajectory = _engine().simulate(
            _MutableWorld(),
            _MutableBelief(),
            (_Step("drive_to_store_a"), _Step("teleport_home")),
        )
        assert trajectory.succeeded is False
        assert trajectory.states[-1].applied_transition.kind == TransitionKind.UNKNOWN

    def test_empty_plan_is_vacuously_succeeded(self):
        trajectory = _engine().simulate(_MutableWorld(), _MutableBelief(), ())
        assert trajectory.succeeded is True
        assert len(trajectory.states) == 1


# ═══════════════════════════════════════════════════════════════════════════
# State cloning — the explicit Step 11.3 deliverable
# ═══════════════════════════════════════════════════════════════════════════


class TestStateCloning:
    def test_trajectory_stores_clones_not_original_references(self):
        world = _MutableWorld()
        belief = _MutableBelief()
        trajectory = _engine().simulate(world, belief, (_Step("drive_to_store_a"),))

        assert trajectory.initial_world_snapshot is not world
        assert trajectory.belief_snapshot is not belief

    def test_clones_are_value_equal_to_the_originals(self):
        world = _MutableWorld()
        trajectory = _engine().simulate(world, _MutableBelief(), (_Step("drive_to_store_a"),))
        assert trajectory.initial_world_snapshot.state == world.state

    def test_original_world_is_never_mutated(self):
        world = _MutableWorld()
        original_state = dict(world.state)
        _engine().simulate(world, _MutableBelief(), (_Step("drive_to_store_a"), _Step("purchase_milk")))
        assert world.state == original_state
        assert world.mutated is False

    def test_original_belief_is_never_mutated(self):
        belief = _MutableBelief()
        _engine().simulate(_MutableWorld(), belief, (_Step("drive_to_store_a"),))
        assert belief.mutated is False
        assert belief.facts == []

    def test_clone_state_deep_copies_nested_structures(self):
        original = {"a": [1, 2, 3]}
        cloned = clone_state(original)
        cloned["a"].append(4)
        assert original["a"] == [1, 2, 3]

    def test_clone_state_survives_uncopyable_values(self):
        """Must not raise even for values deepcopy can't handle -- falls
        back to returning the value as-is, still never mutated by this
        module since nothing here calls methods on it."""
        import threading

        lock = threading.Lock()
        assert clone_state(lock) is lock  # deepcopy fails on locks; fallback returns as-is


# ═══════════════════════════════════════════════════════════════════════════
# simulate_plan / simulate_candidates — duck-typed plan extraction
# ═══════════════════════════════════════════════════════════════════════════


class TestSimulatePlan:
    def test_extracts_steps_from_a_plan_like_object(self):
        plan = _FakePlan(steps=(_Step("drive_to_store_a"), _Step("purchase_milk")))
        trajectory = _engine().simulate_plan(_MutableWorld(), _MutableBelief(), plan)
        assert len(trajectory.states) == 3
        assert trajectory.plan is plan

    def test_handles_a_bare_list_of_steps(self):
        trajectory = _engine().simulate_plan(_MutableWorld(), _MutableBelief(), [_Step("drive_to_store_a")])
        assert len(trajectory.states) == 2

    def test_unrecognized_plan_shape_yields_empty_trajectory(self):
        trajectory = _engine().simulate_plan(_MutableWorld(), _MutableBelief(), object())
        assert len(trajectory.states) == 1  # initial state only


class TestSimulateCandidates:
    def test_simulates_every_candidate_independently(self):
        candidate_a = _FakePlan(steps=(_Step("drive_to_store_a"),))
        candidate_b = _FakePlan(steps=(_Step("purchase_milk"),))

        trajectories = _engine().simulate_candidates(_MutableWorld(), _MutableBelief(), (candidate_a, candidate_b))

        assert len(trajectories) == 2
        assert trajectories[0].final_state.world_state != trajectories[1].final_state.world_state
        assert trajectories[0].trajectory_id != trajectories[1].trajectory_id

    def test_empty_candidates_yields_empty_tuple(self):
        assert _engine().simulate_candidates(_MutableWorld(), _MutableBelief(), ()) == ()


# ═══════════════════════════════════════════════════════════════════════════
# SimulationState / SimulationTrajectory — immutability
# ═══════════════════════════════════════════════════════════════════════════


class TestImmutability:
    def test_state_frozen(self):
        from dataclasses import FrozenInstanceError

        state = SimulationState()
        with pytest.raises(FrozenInstanceError):
            state.world_state = {}

    def test_trajectory_frozen(self):
        from dataclasses import FrozenInstanceError

        trajectory = SimulationTrajectory()
        with pytest.raises(FrozenInstanceError):
            trajectory.succeeded = True

    def test_trajectory_ids_are_unique(self):
        assert SimulationTrajectory().trajectory_id != SimulationTrajectory().trajectory_id


# ═══════════════════════════════════════════════════════════════════════════
# Ownership boundary — read-only, no execution coupling
# ═══════════════════════════════════════════════════════════════════════════


def _imported_modules(mod) -> list[str]:
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(mod))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
    return modules


class TestOwnershipBoundary:
    def test_no_execution_or_runtime_coupling(self):
        import src.monkey_brain.kernel.pipeline.prediction.simulation as mod

        imports = " ".join(_imported_modules(mod))
        for forbidden in (
            "belief_runtime",
            "kernel.pipeline.execution",
            "action_executor",
            "learning.",
        ):
            assert forbidden not in imports, f"simulation.py must not import: {forbidden}"

    def test_depends_only_on_prediction_transitions(self):
        import src.monkey_brain.kernel.pipeline.prediction.simulation as mod

        imports = _imported_modules(mod)
        project_imports = [m for m in imports if m.startswith("src.monkey_brain")]
        assert project_imports == ["src.monkey_brain.kernel.pipeline.prediction.transitions"]
