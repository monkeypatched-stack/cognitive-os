"""RL Planner Policy — reinforcement learning-based planning.

This policy learns to select better plans based on past outcomes.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from src.monkey_brain.kernel.config import (
    PLANNER_LEARNING_RATE,
    PLANNER_EXPLORATION_RATE,
    PLANNER_EXPLORATION_DECAY,
    PLANNER_MIN_EXPLORATION_RATE,
    DISCOUNT_FACTOR,
)


@dataclass
class PlanDecision:
    """Outcome of a planning decision."""

    plan_id: str = ""
    expected_reward: float = 0.0
    confidence: float = 0.5
    reason: str = ""


class PlannerPolicy:
    """RL-based planner policy that learns from execution outcomes.

    Uses PolicyStore for Q-storage with a synthetic '__plan__' state key,
    converting plan-level Q to the standard (state, action) format.
    """

    _PLAN_STATE = "__plan__"  # Synthetic state for plan-level Q-values

    def __init__(self):
        # Delegate Q-storage to PolicyStore
        from src.monkey_brain.kernel.policy.store import PolicyStore

        self._policy_store = PolicyStore()
        self._plan_counts: dict[str, int] = {}
        self._learning_rate = PLANNER_LEARNING_RATE
        self._discount_factor = DISCOUNT_FACTOR
        self._exploration_rate = PLANNER_EXPLORATION_RATE
        self._random = random.Random()
        self._update_count: int = 0

    @property
    def _q_values(self) -> dict[str, float]:
        """Backward-compat view of plan Q-values from PolicyStore."""
        snapshot = self._policy_store.snapshot()
        return {k.split("|", 1)[1]: v for k, v in snapshot.items() if k.startswith(f"{self._PLAN_STATE}|")}

    # ── selection ────────────────────────────────────────────────────────────────

    def _optimism_bonus(self, plan_id: str) -> float:
        """Optimism in the face of uncertainty — decays as evidence accumulates.

        1/(1+n), so an untried plan carries +1.0 and a well-tried one ~0. The old form
        cut the bonus to exactly 0.0 the moment count hit 10, a discontinuity that made
        a plan's score jump down by 0.1 on its tenth run; 1/(1+n) already decays to
        ~0.09 there, so the cliff bought nothing.
        """
        return 1.0 / (1.0 + self._plan_counts.get(plan_id, 0))

    def _score(self, plan_id: str) -> float:
        """Ranking score: learned value plus the optimism bonus."""
        return self._policy_store.value(self._PLAN_STATE, plan_id) + self._optimism_bonus(plan_id)

    def _confidence(self, plan_id: str) -> float:
        """How much evidence backs this plan's Q."""
        n = self._plan_counts.get(plan_id, 0)
        return round(n / (n + 5.0), 4)

    def select_plan(self, plans: list[str], state: dict[str, Any] | None = None) -> PlanDecision:
        """Select the best plan using epsilon-greedy strategy."""
        if not plans:
            return PlanDecision(reason="no plans available")

        if len(plans) > 1 and self._should_explore():
            selected_plan = self._random.choice(plans)
            return PlanDecision(
                plan_id=selected_plan,
                expected_reward=self._policy_store.value(self._PLAN_STATE, selected_plan),
                confidence=self._confidence(selected_plan),
                reason="exploration",
            )

        selected_plan = max(plans, key=self._score)
        q = self._policy_store.value(self._PLAN_STATE, selected_plan)
        return PlanDecision(
            plan_id=selected_plan,
            expected_reward=round(q, 4),
            confidence=self._confidence(selected_plan),
            reason=f"q_value={q:.3f}",
        )

    def update(self, plan_id: str, reward: float, next_state: dict[str, Any] | None = None) -> None:
        """Update Q(plan) toward the observed reward via PolicyStore."""
        count = self._plan_counts.get(plan_id, 0)
        lr = self._learning_rate if count < 5 else self._learning_rate * 0.5

        # Use PolicyStore for Q-update (terminal transition, no next_state)
        # Apply computed learning rate by temporarily overriding store's lr
        old_lr = self._policy_store._lr
        self._policy_store._lr = lr
        try:
            self._policy_store.update(self._PLAN_STATE, plan_id, reward)
        finally:
            self._policy_store._lr = old_lr

        self._plan_counts[plan_id] = count + 1
        self._update_count += 1

        self._exploration_rate = max(
            PLANNER_MIN_EXPLORATION_RATE,
            self._exploration_rate * PLANNER_EXPLORATION_DECAY,
        )

    def _should_explore(self) -> bool:
        """Determine if we should explore (random selection) vs exploit."""
        return self._random.random() < self._exploration_rate

    def get_stats(self) -> dict[str, Any]:
        """Get statistics about planning decisions from PolicyStore."""
        return {
            "total_plans": self._policy_store.size,
            "exploration_rate": self._exploration_rate,
            "learning_rate": self._learning_rate,
            "discount_factor": self._discount_factor,
            "algorithm": "contextual_bandit",
            "avg_q_value": (sum(self._q_values.values()) / max(1, len(self._q_values)) if self._q_values else 0.0),
        }

    def get_best_action(self, objective: str, candidates: list[str]) -> str | None:
        """Best action from `candidates` — ranks by score (learned value + optimism)."""
        if not candidates:
            return None
        return max(candidates, key=self._score)

    def summary(self) -> dict[str, Any]:
        """Get summary of policy state from PolicyStore."""
        return {
            "type": "rl_planner",
            **self.get_stats(),
            "plan_counts": dict(self._plan_counts),
            "top_plans": sorted(
                [(p, self._policy_store.value(self._PLAN_STATE, p)) for p in self._plan_counts],
                key=lambda x: x[1],
                reverse=True,
            )[:5],
        }
