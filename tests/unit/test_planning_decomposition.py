"""Tests for the goal decomposition engine (Step 8.3).

Validates one-level decomposition (rules and completion-criteria fallback),
recursive tree decomposition, dependency resolution, and termination
(leaf detection) — including a regression test for an infinite-recursion bug
found while building this: a subgoal whose single completion criterion
restates its own name must NOT be decomposed further.
"""

from __future__ import annotations

from src.monkey_brain.kernel.pipeline.planning.domain import Goal, SubGoal
from src.monkey_brain.kernel.pipeline.planning.decomposition import (
    SubGoalSpec,
    DecompositionRule,
    GoalTree,
    GoalDecomposer,
    DEFAULT_RULES,
)

# ═══════════════════════════════════════════════════════════════════════════
# One-level decomposition via a registered rule
# ═══════════════════════════════════════════════════════════════════════════


class TestDecomposeViaRule:
    def test_prepare_breakfast_example(self):
        """The exact example from the spec: Prepare breakfast -> buy milk / eggs / bread."""
        decomposer = GoalDecomposer()
        breakfast = Goal(name="prepare_breakfast", description="Prepare breakfast")

        subgoals = decomposer.decompose(breakfast)

        names = {sg.name for sg in subgoals}
        assert names == {"buy_milk", "buy_eggs", "buy_bread"}
        for sg in subgoals:
            assert sg.parent_goal_id == breakfast.goal_id

    def test_independent_subgoals_have_no_dependencies(self):
        decomposer = GoalDecomposer()
        breakfast = Goal(name="prepare_breakfast")

        subgoals = decomposer.decompose(breakfast)

        assert all(sg.depends_on == () for sg in subgoals)

    def test_dependency_chain_resolves_to_real_goal_ids(self):
        decomposer = GoalDecomposer()
        goal = Goal(name="bake_bread")

        subgoals = decomposer.decompose(goal)
        by_name = {sg.name: sg for sg in subgoals}

        assert by_name["buy_flour"].depends_on == ()
        assert by_name["mix_dough"].depends_on == (by_name["buy_flour"].goal_id,)
        assert by_name["bake"].depends_on == (by_name["mix_dough"].goal_id,)

    def test_custom_rule_registration(self):
        decomposer = GoalDecomposer(rules={})
        decomposer.register_rule(
            DecompositionRule(
                goal_name="pack_lunch",
                subgoals=(
                    SubGoalSpec(name="make_sandwich", completion_criteria=("sandwich_made",)),
                    SubGoalSpec(name="add_fruit", completion_criteria=("fruit_added",)),
                ),
            )
        )

        subgoals = decomposer.decompose(Goal(name="pack_lunch"))

        assert {sg.name for sg in subgoals} == {"make_sandwich", "add_fruit"}

    def test_default_rules_are_not_mutated_by_registration(self):
        decomposer = GoalDecomposer()
        decomposer.register_rule(DecompositionRule(goal_name="custom", subgoals=()))
        assert "custom" not in DEFAULT_RULES


# ═══════════════════════════════════════════════════════════════════════════
# One-level decomposition via completion_criteria fallback
# ═══════════════════════════════════════════════════════════════════════════


class TestDecomposeViaCompletionCriteria:
    def test_multiple_criteria_produce_one_subgoal_each(self):
        decomposer = GoalDecomposer(rules={})
        goal = Goal(name="get_supplies", completion_criteria=("milk_in_cart", "eggs_in_cart"))

        subgoals = decomposer.decompose(goal)

        assert {sg.name for sg in subgoals} == {"milk_in_cart", "eggs_in_cart"}
        for sg in subgoals:
            assert sg.parent_goal_id == goal.goal_id
            assert len(sg.completion_criteria) == 1

    def test_single_criterion_is_a_leaf(self):
        """Regression: a goal with exactly one criterion must NOT decompose —
        doing so previously regenerated an identical single-criterion subgoal
        forever, only stopped by max_depth."""
        decomposer = GoalDecomposer(rules={})
        goal = Goal(name="buy_milk", completion_criteria=("milk_acquired",))

        assert decomposer.decompose(goal) == ()

    def test_no_rule_and_no_criteria_is_a_leaf(self):
        decomposer = GoalDecomposer(rules={})
        goal = Goal(name="unknown_thing")

        assert decomposer.decompose(goal) == ()


# ═══════════════════════════════════════════════════════════════════════════
# Recursive tree decomposition
# ═══════════════════════════════════════════════════════════════════════════


class TestDecomposeTree:
    def test_prepare_breakfast_tree_shape(self):
        decomposer = GoalDecomposer()
        breakfast = Goal(name="prepare_breakfast")

        tree = decomposer.decompose_tree(breakfast)

        assert isinstance(tree, GoalTree)
        assert tree.goal is breakfast
        assert tree.depth() == 2
        assert {leaf.name for leaf in tree.leaves()} == {
            "buy_milk",
            "buy_eggs",
            "buy_bread",
        }

    def test_leaf_goal_tree_has_depth_one(self):
        decomposer = GoalDecomposer(rules={})
        goal = Goal(name="buy_milk", completion_criteria=("milk_acquired",))

        tree = decomposer.decompose_tree(goal)

        assert tree.depth() == 1
        assert tree.children == ()
        assert tree.leaves() == (goal,)

    def test_multi_criteria_tree_terminates_after_one_level(self):
        """Each generated subgoal has exactly one criterion, so recursion
        naturally terminates — this is the regression scenario exercised
        through the tree API instead of decompose() directly."""
        decomposer = GoalDecomposer(rules={})
        goal = Goal(name="get_supplies", completion_criteria=("milk_in_cart", "eggs_in_cart"))

        tree = decomposer.decompose_tree(goal)

        assert tree.depth() == 2
        assert all(child.children == () for child in tree.children)

    def test_flatten_includes_every_node(self):
        decomposer = GoalDecomposer()
        breakfast = Goal(name="prepare_breakfast")

        tree = decomposer.decompose_tree(breakfast)
        flat_names = [n.name for n in tree.flatten()]

        assert flat_names[0] == "prepare_breakfast"
        assert set(flat_names[1:]) == {"buy_milk", "buy_eggs", "buy_bread"}

    def test_max_depth_bounds_recursion(self):
        """Even a self-referential rule (a bug some caller could register) is
        bounded by max_depth rather than recursing forever. max_depth bounds
        the resulting tree's total depth, not the recursion-call count."""
        decomposer = GoalDecomposer(rules={})
        decomposer.register_rule(
            DecompositionRule(
                goal_name="loopy",
                subgoals=(SubGoalSpec(name="loopy"),),  # decomposes into itself
            )
        )

        assert decomposer.decompose_tree(Goal(name="loopy"), max_depth=3).depth() == 3
        assert decomposer.decompose_tree(Goal(name="loopy"), max_depth=1).depth() == 1


# ═══════════════════════════════════════════════════════════════════════════
# SubGoal shape produced by decomposition
# ═══════════════════════════════════════════════════════════════════════════


class TestSubGoalShape:
    def test_generated_subgoals_are_real_subgoal_instances(self):
        decomposer = GoalDecomposer()
        subgoals = decomposer.decompose(Goal(name="prepare_breakfast"))
        assert all(isinstance(sg, SubGoal) for sg in subgoals)

    def test_generated_subgoal_ids_are_unique(self):
        decomposer = GoalDecomposer()
        subgoals = decomposer.decompose(Goal(name="prepare_breakfast"))
        ids = [sg.goal_id for sg in subgoals]
        assert len(ids) == len(set(ids))


# ═══════════════════════════════════════════════════════════════════════════
# Ownership boundary — no coupling to execution/runtime
# ═══════════════════════════════════════════════════════════════════════════


class TestOwnershipBoundary:
    def test_no_runtime_or_execution_imports(self):
        import inspect
        import src.monkey_brain.kernel.pipeline.planning.decomposition as mod

        source = inspect.getsource(mod)
        forbidden = [
            "belief_runtime",
            "action_executor",
            "from src.monkey_brain.kernel.pipeline.execution import",
        ]
        for imp in forbidden:
            assert imp not in source, f"decomposition.py must not import: {imp}"
