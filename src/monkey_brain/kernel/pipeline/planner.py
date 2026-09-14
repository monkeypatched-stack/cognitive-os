"""Planner — the abstraction for goal-directed planning.

The planner consumes BeliefState and Goal, and produces a Plan.
The runtime depends only on this interface.

Different planners can be swapped in:
- LLMPlanner (default: LLM-assisted planning, kernel/pipeline/llm_planner.py)
- GraphPlanner (world graph traversal)
- GOAP (Goal-Oriented Action Planning)
- HTN (Hierarchical Task Network)
- SATPlanner (constraint satisfaction)
- RLPlanner (reinforcement learning)

The runtime never knows which planner is active.
"""

from __future__ import annotations

from typing import Protocol, Any

from src.monkey_brain.kernel.pipeline.belief_state import BeliefState, Goal, Plan


class PlanningEngine(Protocol):
    """Protocol for anything that produces plans.

    The runtime depends only on this interface.
    Implementations can be deterministic, probabilistic, or LLM-based.
    """

    def plan(
        self,
        belief: BeliefState,
        goal: Goal,
        context: Any = None,
    ) -> Plan:
        """Generate a plan from the current belief state and goal.

        Args:
            belief: The actor's current belief state (facts, hypotheses, confidence)
            goal: What the actor wants to achieve
            context: Optional runtime context (services, constraints)

        Returns:
            A structured Plan describing what to do
        """
        ...
