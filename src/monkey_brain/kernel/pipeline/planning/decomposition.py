"""Goal decomposition engine (Step 8.3) — hierarchical goal breakdown.

Decomposes a Goal into SubGoals, optionally recursively into a tree. Two
strategies, tried in order for any node being decomposed:

  1. An explicit DecompositionRule registered for that goal's name (e.g.
     "prepare_breakfast" -> buy_milk / buy_eggs / buy_bread). Rules can express
     dependencies between sibling subgoals.
  2. Falling back to the goal's own `completion_criteria`: one subgoal per
     criterion, no declared dependencies (a goal that already lists what needs
     to be true has, implicitly, already named its own subgoals).

A goal with neither a rule nor completion criteria is a leaf — decompose()
returns an empty tuple for it.

No cost optimization, no candidate generation, no scoring — this step only
produces the hierarchy. Purely additive: no coupling to CognitiveRuntime or
ExecutionEngine.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Union

from src.monkey_brain.kernel.pipeline.planning.domain import Goal, SubGoal

GoalNode = Union[Goal, SubGoal]  # both share .name, .goal_id, .completion_criteria


@dataclass(frozen=True)
class SubGoalSpec:
    """A declarative template for one subgoal within a DecompositionRule.

    Not yet resolved to a concrete goal_id — that happens when the decomposer
    instantiates it against a specific parent. depends_on_names references
    other SubGoalSpec.name values within the *same* rule; the decomposer
    resolves those names to real goal_ids after creating all siblings.
    """

    name: str = ""
    description: str = ""
    completion_criteria: tuple[str, ...] = ()
    depends_on_names: tuple[str, ...] = ()
    priority: float = 0.5


@dataclass(frozen=True)
class DecompositionRule:
    """Declares how to decompose a goal matched by name into subgoals."""

    goal_name: str = ""
    subgoals: tuple[SubGoalSpec, ...] = ()


@dataclass(frozen=True)
class GoalTree:
    """A node in a decomposed goal hierarchy.

    `goal` is the Goal (root) or SubGoal (any other node) at this position;
    `children` are its immediate decomposition, recursively decomposed in turn.
    A node with no children is a leaf.
    """

    goal: GoalNode
    children: tuple["GoalTree", ...] = ()

    def leaves(self) -> tuple[GoalNode, ...]:
        """All leaf goals under this node, in left-to-right order."""
        if not self.children:
            return (self.goal,)
        result: list[GoalNode] = []
        for child in self.children:
            result.extend(child.leaves())
        return tuple(result)

    def depth(self) -> int:
        """Number of levels in this subtree (a leaf has depth 1)."""
        if not self.children:
            return 1
        return 1 + max(child.depth() for child in self.children)

    def flatten(self) -> tuple[GoalNode, ...]:
        """Every node in this subtree (including self), pre-order."""
        result: list[GoalNode] = [self.goal]
        for child in self.children:
            result.extend(child.flatten())
        return tuple(result)


# Illustrative default rules. Callers register their own via
# GoalDecomposer(rules=...) or decomposer.register_rule(...); these exist so
# the engine is usable and testable out of the box.
DEFAULT_RULES: dict[str, DecompositionRule] = {
    "prepare_breakfast": DecompositionRule(
        goal_name="prepare_breakfast",
        subgoals=(
            SubGoalSpec(
                name="buy_milk",
                description="Buy milk",
                completion_criteria=("milk_acquired",),
            ),
            SubGoalSpec(
                name="buy_eggs",
                description="Buy eggs",
                completion_criteria=("eggs_acquired",),
            ),
            SubGoalSpec(
                name="buy_bread",
                description="Buy bread",
                completion_criteria=("bread_acquired",),
            ),
        ),
    ),
    # Demonstrates genuine dependency chains (not just independent subgoals).
    "bake_bread": DecompositionRule(
        goal_name="bake_bread",
        subgoals=(
            SubGoalSpec(
                name="buy_flour",
                description="Buy flour",
                completion_criteria=("flour_acquired",),
            ),
            SubGoalSpec(
                name="mix_dough",
                description="Mix the dough",
                completion_criteria=("dough_ready",),
                depends_on_names=("buy_flour",),
            ),
            SubGoalSpec(
                name="bake",
                description="Bake the bread",
                completion_criteria=("bread_baked",),
                depends_on_names=("mix_dough",),
            ),
        ),
    ),
}


class GoalDecomposer:
    """Breaks a Goal down into SubGoals, one level or recursively into a tree."""

    def __init__(self, rules: dict[str, DecompositionRule] | None = None) -> None:
        self._rules: dict[str, DecompositionRule] = dict(rules) if rules is not None else dict(DEFAULT_RULES)

    def register_rule(self, rule: DecompositionRule) -> None:
        self._rules[rule.goal_name] = rule

    def decompose(self, goal: GoalNode) -> tuple[SubGoal, ...]:
        """One level of decomposition. Empty tuple means `goal` is a leaf."""
        rule = self._rules.get(goal.name)
        if rule is not None:
            return self._from_rule(goal.goal_id, rule)
        # A single criterion just restates the goal itself (e.g. a "buy_milk"
        # subgoal with completion_criteria=("milk_acquired",)) — decomposing it
        # would regenerate an identical single-criterion subgoal forever. Only
        # *multiple* criteria genuinely suggest separable pieces of work.
        if len(goal.completion_criteria) > 1:
            return self._from_completion_criteria(goal.goal_id, goal.completion_criteria)
        return ()

    def decompose_tree(self, goal: Goal, *, max_depth: int = 5) -> GoalTree:
        """Recursively decompose `goal` into a full hierarchy.

        max_depth bounds the resulting tree's depth (GoalTree.depth() <=
        max_depth) — a safety guard against a rule chain that referenced
        itself looping forever, not a cost/quality optimization.
        """
        return self._build_tree(goal, depth=0, max_depth=max_depth)

    def _build_tree(self, node: GoalNode, *, depth: int, max_depth: int) -> GoalTree:
        children: tuple[GoalTree, ...] = ()
        if depth < max_depth - 1:
            subgoals = self.decompose(node)
            children = tuple(self._build_tree(sg, depth=depth + 1, max_depth=max_depth) for sg in subgoals)
        return GoalTree(goal=node, children=children)

    def _from_rule(self, parent_goal_id: str, rule: DecompositionRule) -> tuple[SubGoal, ...]:
        name_to_id: dict[str, str] = {}
        drafts: list[tuple[SubGoal, tuple[str, ...]]] = []
        for spec in rule.subgoals:
            sg = SubGoal(
                parent_goal_id=parent_goal_id,
                name=spec.name,
                description=spec.description,
                completion_criteria=spec.completion_criteria,
                priority=spec.priority,
            )
            name_to_id[spec.name] = sg.goal_id
            drafts.append((sg, spec.depends_on_names))

        resolved: list[SubGoal] = []
        for sg, dep_names in drafts:
            deps = tuple(name_to_id[n] for n in dep_names if n in name_to_id)
            resolved.append(replace(sg, depends_on=deps) if deps else sg)
        return tuple(resolved)

    def _from_completion_criteria(
        self,
        parent_goal_id: str,
        criteria: tuple[str, ...],
    ) -> tuple[SubGoal, ...]:
        return tuple(
            SubGoal(
                parent_goal_id=parent_goal_id,
                name=criterion,
                completion_criteria=(criterion,),
            )
            for criterion in criteria
        )
