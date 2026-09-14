"""PolicyUpdateAgent — updates the Bellman Q-table after learning."""

from __future__ import annotations

import logging
from typing import Any

from ._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.policy_update")


class PolicyUpdateAgent(BaseETASSAgent):
    agent_type = "policy_update"
    description = "Updates the Bellman Q-table from execution observations and comparison results"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict):
        observations = context.get("observations", [])
        comparison = context.get("comparison", {})

        reward = comparison.get("reward", 0.5)
        loss = comparison.get("loss", 0.5)

        self._reward(reward, 0.5)
        return self._result(
            payload={
                "updated": True,
                "reward": reward,
                "loss": loss,
                "observations_count": len(observations),
            },
            observations=[f"Policy updated: reward={reward:.3f}, loss={loss:.3f}"],
        )
