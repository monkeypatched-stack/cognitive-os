"""Tests for Counterfactual Reasoning (Step 11.4).

Covers all four example "what if?" scenarios from Step 11.4's own spec
(store closed, inventory unavailable, budget changes, another actor
completes first) as real branching simulations, not just described --
each is a genuine TransitionModel override re-simulated against the same
baseline plan.
"""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.pipeline.prediction.transitions import (
    TransitionModel,
    WorldTransition,
)
from src.monkey_brain.kernel.pipeline.prediction.counterfactuals import (
    CounterfactualAssumption,
    CounterfactualBranch,
    CounterfactualEngine,
)


class _Step:
    def __init__(self, description: str) -> None:
        self.description = description


class _Plan:
    def __init__(self, steps) -> None:
        self.steps = steps


def _baseline_model() -> TransitionModel:
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


def _milk_plan() -> _Plan:
    return _Plan(steps=(_Step("drive_to_store_a"), _Step("purchase_milk")))


def _engine() -> CounterfactualEngine:
    return CounterfactualEngine(_baseline_model())


# ═══════════════════════════════════════════════════════════════════════════
# The four example "what if?" scenarios from Step 11.4's own spec
# ═══════════════════════════════════════════════════════════════════════════


class TestExampleCounterfactuals:
    def test_what_if_store_a_is_closed(self):
        assumption = CounterfactualAssumption(
            description="What if Store A is closed?",
            category="availability",
            transition_overrides={
                "purchase_milk": (
                    WorldTransition(
                        description="Purchase failed, store closed",
                        probability=0.98,
                        confidence=0.9,
                        resulting_world_delta={"actor.has_milk": False},
                    ),
                ),
            },
        )
        branch = _engine().branch(None, None, _milk_plan(), assumption)

        assert branch.divergence["actor.has_milk"] == {
            "baseline": True,
            "counterfactual": False,
        }

    def test_what_if_inventory_is_unavailable(self):
        assumption = CounterfactualAssumption(
            description="What if inventory is unavailable?",
            category="inventory",
            transition_overrides={
                "purchase_milk": (
                    WorldTransition(
                        description="Purchase failed, no stock",
                        probability=0.9,
                        confidence=0.85,
                        resulting_world_delta={
                            "actor.has_milk": False,
                            "store_a.stock": "empty",
                        },
                    ),
                ),
            },
        )
        branch = _engine().branch(None, None, _milk_plan(), assumption)

        assert branch.divergence["store_a.stock"] == {
            "baseline": "<unset>",
            "counterfactual": "empty",
        }

    def test_what_if_budget_changes(self):
        assumption = CounterfactualAssumption(
            description="What if budget changes?",
            category="budget",
            transition_overrides={
                "purchase_milk": (
                    WorldTransition(
                        description="Purchase failed, over budget",
                        probability=0.7,
                        confidence=0.6,
                        resulting_world_delta={
                            "actor.has_milk": False,
                            "actor.over_budget": True,
                        },
                    ),
                ),
            },
        )
        branch = _engine().branch(None, None, _milk_plan(), assumption)

        assert branch.trajectory.final_state.world_state["actor.over_budget"] is True

    def test_what_if_another_actor_completes_the_task_first(self):
        assumption = CounterfactualAssumption(
            description="What if another actor completes the task first?",
            category="competition",
            transition_overrides={
                "purchase_milk": (
                    WorldTransition(
                        description="Milk already purchased by another actor",
                        probability=0.85,
                        confidence=0.7,
                        resulting_world_delta={
                            "actor.has_milk": False,
                            "store_a.stock": "depleted_by_other_actor",
                        },
                    ),
                ),
            },
        )
        branch = _engine().branch(None, None, _milk_plan(), assumption)

        assert branch.divergence["store_a.stock"]["counterfactual"] == "depleted_by_other_actor"


# ═══════════════════════════════════════════════════════════════════════════
# Branching — generate_branches produces one branch per assumption
# ═══════════════════════════════════════════════════════════════════════════


class TestBranchGeneration:
    def test_one_branch_per_assumption(self):
        assumptions = (
            CounterfactualAssumption(description="A", transition_overrides={}),
            CounterfactualAssumption(description="B", transition_overrides={}),
            CounterfactualAssumption(description="C", transition_overrides={}),
        )
        branches = _engine().generate_branches(None, None, _milk_plan(), assumptions)
        assert len(branches) == 3
        assert all(isinstance(b, CounterfactualBranch) for b in branches)

    def test_branches_have_distinct_ids(self):
        assumptions = (
            CounterfactualAssumption(description="A"),
            CounterfactualAssumption(description="B"),
        )
        branches = _engine().generate_branches(None, None, _milk_plan(), assumptions)
        assert branches[0].branch_id != branches[1].branch_id

    def test_branches_diverge_independently_from_the_same_baseline(self):
        store_closed = CounterfactualAssumption(
            description="Store closed",
            transition_overrides={
                "purchase_milk": (
                    WorldTransition(
                        probability=1.0,
                        confidence=0.9,
                        resulting_world_delta={"actor.has_milk": False},
                    ),
                )
            },
        )
        over_budget = CounterfactualAssumption(
            description="Over budget",
            transition_overrides={
                "purchase_milk": (
                    WorldTransition(
                        probability=1.0,
                        confidence=0.9,
                        resulting_world_delta={
                            "actor.has_milk": False,
                            "actor.over_budget": True,
                        },
                    ),
                )
            },
        )
        branches = _engine().generate_branches(None, None, _milk_plan(), (store_closed, over_budget))

        assert "actor.over_budget" not in branches[0].divergence
        assert "actor.over_budget" in branches[1].divergence

    def test_baseline_model_never_mutated_by_branching(self):
        model = _baseline_model()
        original_keys = set(model.known_transitions.keys())
        original_transition = model.known_transitions[("", "drive_to_store_a")][0].description

        engine = CounterfactualEngine(model)
        assumption = CounterfactualAssumption(
            transition_overrides={"drive_to_store_a": (WorldTransition(probability=1.0, confidence=1.0),)}
        )
        engine.branch(None, None, _milk_plan(), assumption)

        assert set(model.known_transitions.keys()) == original_keys
        assert model.known_transitions[("", "drive_to_store_a")][0].description == original_transition

    def test_empty_assumptions_yields_empty_branches(self):
        assert _engine().generate_branches(None, None, _milk_plan(), ()) == ()


# ═══════════════════════════════════════════════════════════════════════════
# Divergence detection
# ═══════════════════════════════════════════════════════════════════════════


class TestDivergence:
    def test_no_op_override_yields_no_divergence(self):
        assumption = CounterfactualAssumption(description="Nothing changes", transition_overrides={})
        branch = _engine().branch(None, None, _milk_plan(), assumption)
        assert branch.divergence == {}
        assert "no change" in branch.impact_summary

    def test_divergent_keys_reported_in_impact_summary(self):
        assumption = CounterfactualAssumption(
            description="Store closed",
            transition_overrides={
                "purchase_milk": (
                    WorldTransition(
                        probability=1.0,
                        confidence=1.0,
                        resulting_world_delta={"actor.has_milk": False},
                    ),
                )
            },
        )
        branch = _engine().branch(None, None, _milk_plan(), assumption)
        assert "actor.has_milk" in branch.impact_summary

    def test_impact_summary_never_claims_success_or_failure(self):
        """SimulationTrajectory.succeeded measures confidence, not goal
        achievement -- a confidently predicted failure is still
        succeeded=True by that definition, so the impact summary must not
        use success/failure language pulled from it."""
        assumption = CounterfactualAssumption(
            description="Confidently bad outcome",
            transition_overrides={
                "purchase_milk": (
                    WorldTransition(
                        probability=0.99,
                        confidence=0.99,
                        resulting_world_delta={"actor.has_milk": False},
                    ),
                )
            },
        )
        branch = _engine().branch(None, None, _milk_plan(), assumption)
        assert branch.trajectory.succeeded is True  # confident prediction...
        assert "succeeds" not in branch.impact_summary  # ...but not described as "succeeding"
        assert "fails" not in branch.impact_summary

    def test_key_present_only_in_baseline_reports_unset_counterfactual_side(self):
        assumption = CounterfactualAssumption(
            transition_overrides={"drive_to_store_a": ()},  # no transitions at all for this action
        )
        # drive_to_store_a normally sets actor.location/store_a.open; an
        # empty override tuple falls back to UNKNOWN (Step 11.2 behavior),
        # producing no delta -- baseline's keys should show as diverged.
        branch = _engine().branch(None, None, _milk_plan(), assumption)
        assert branch.divergence["actor.location"]["counterfactual"] == "<unset>"


# ═══════════════════════════════════════════════════════════════════════════
# branch() without a precomputed baseline
# ═══════════════════════════════════════════════════════════════════════════


class TestBranchWithoutPrecomputedBaseline:
    def test_recomputes_baseline_when_not_supplied(self):
        assumption = CounterfactualAssumption(
            transition_overrides={
                "purchase_milk": (
                    WorldTransition(
                        probability=1.0,
                        confidence=1.0,
                        resulting_world_delta={"actor.has_milk": False},
                    ),
                )
            },
        )
        branch = _engine().branch(None, None, _milk_plan(), assumption)
        assert branch.divergence["actor.has_milk"] == {
            "baseline": True,
            "counterfactual": False,
        }


# ═══════════════════════════════════════════════════════════════════════════
# Immutability
# ═══════════════════════════════════════════════════════════════════════════


class TestImmutability:
    def test_assumption_frozen(self):
        from dataclasses import FrozenInstanceError

        assumption = CounterfactualAssumption()
        with pytest.raises(FrozenInstanceError):
            assumption.description = "x"

    def test_branch_frozen(self):
        from dataclasses import FrozenInstanceError

        branch = CounterfactualBranch()
        with pytest.raises(FrozenInstanceError):
            branch.impact_summary = "x"

    def test_assumption_ids_are_unique(self):
        assert CounterfactualAssumption().assumption_id != CounterfactualAssumption().assumption_id


# ═══════════════════════════════════════════════════════════════════════════
# Ownership boundary
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
        import src.monkey_brain.kernel.pipeline.prediction.counterfactuals as mod

        imports = " ".join(_imported_modules(mod))
        for forbidden in (
            "belief_runtime",
            "kernel.pipeline.execution",
            "action_executor",
            "learning.",
        ):
            assert forbidden not in imports, f"counterfactuals.py must not import: {forbidden}"

    def test_depends_only_on_prediction_transitions_and_simulation(self):
        import src.monkey_brain.kernel.pipeline.prediction.counterfactuals as mod

        imports = _imported_modules(mod)
        project_imports = sorted(m for m in imports if m.startswith("src.monkey_brain"))
        assert project_imports == [
            "src.monkey_brain.kernel.pipeline.prediction.simulation",
            "src.monkey_brain.kernel.pipeline.prediction.transitions",
        ]
