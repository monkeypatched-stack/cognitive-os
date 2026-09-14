"""Execution learning helpers — reward computation and policy update."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

from src.monkey_brain.kernel.execute.models import ExecutionResult

if TYPE_CHECKING:
    from src.monkey_brain.kernel.fix.policy.policy import BellmanPolicy

logger = logging.getLogger("agentos.executor")

# Reward weights — must sum to ≤ 1.0 so the clamped result stays in [0, 1].
_BASE_REWARD = 0.5
_LLM_BONUS = 0.3
_MAX_HIT_BONUS = 0.2
_HIT_BONUS_PER_RESULT = 0.05


def compute_reward(llm_answered: bool, semantic_hits: int, graph_paths: int) -> float:
    reward = _BASE_REWARD
    if llm_answered:
        reward += _LLM_BONUS
    if semantic_hits > 0:
        reward += min(_MAX_HIT_BONUS, semantic_hits * _HIT_BONUS_PER_RESULT)
    if graph_paths > 0:
        reward += min(_MAX_HIT_BONUS, graph_paths * _HIT_BONUS_PER_RESULT)
    return max(0.0, min(1.0, reward))


def learn_from_execution(
    goal: object,
    workload: object,
    result: ExecutionResult,
    get_policy: "Callable[[], BellmanPolicy]",
) -> None:
    try:
        from src.monkey_brain.kernel.fix.policy.transition import Transition

        _, semantic_hits, graph_paths, llm_answered = result
        reward = compute_reward(llm_answered, len(semantic_hits), len(graph_paths))
        transition = Transition(
            state={"goal": goal.name},
            action=workload.workload_id,
            next_state={"success": llm_answered},
            reward=reward,
            done=True,
        )
        get_policy().update(transition)
        logger.info(
            "Policy updated: workload=%s goal=%s reward=%.2f",
            workload.workload_id,
            goal.name,
            reward,
        )
    except Exception as e:
        logger.warning("Policy learning failed — Q-values not updated: %s", e)
