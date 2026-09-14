"""ComparatorAgent — compares the simulator's predicted state against the
real environment's actual query answer, producing an epistemic loss score.

Thin wrapper around src.cortex.world_model_simulation.simulate() and
src.monkey_brain.api.helpers.simulate_helpers' fetch_query_answer()/
compare_predicted_vs_actual() — the same functions ComparatorRuntime.run()
already calls — so the SDLC graph gets a "comparator" capability node
without duplicating that logic.
"""

from __future__ import annotations

import logging
from typing import Any

from ._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.comparator")


class ComparatorAgent(BaseETASSAgent):
    agent_type = "comparator"
    description = "Compares simulated vs. actual outcomes and computes an epistemic loss score"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict) -> dict:
        question = str(context.get("question", "")).strip()
        if not question:
            self._reward(False, 0.0)
            return self._result(payload={"loss": None}, observations=["no question in context"])

        try:
            from src.cortex.world_model_simulation import simulate
            from src.monkey_brain.api.helpers.simulate_helpers import (
                compare_predicted_vs_actual,
                fetch_query_answer,
            )
        except ImportError as e:
            self._reward(False, 0.0)
            return self._result(
                payload={"loss": None, "error": repr(e)},
                observations=[f"import failed: {e}"],
            )

        mongo_client = context.get("mongo_client")

        try:
            sim_result = await simulate(question, context={"db": mongo_client} if mongo_client else {})
            predicted_state = sim_result.get("state_after") or sim_result.get("state_before") or {}
            query_answer = await fetch_query_answer(question, mongo_client)
            loss_result = compare_predicted_vs_actual(predicted_state, query_answer, question)
        except Exception as e:
            self._reward(False, 0.2)
            return self._result(
                payload={"loss": None, "error": str(e)},
                observations=[f"comparison failed: {e}"],
            )

        loss = loss_result.get("loss", 1.0)
        self._reward(loss < 0.5, 0.5)
        return self._result(
            payload={
                "loss": loss,
                "loss_detail": loss_result,
                "query_answer": query_answer,
            },
            observations=[f"epistemic loss: {loss:.3f}"],
        )
