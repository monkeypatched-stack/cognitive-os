"""AdversarialFalsificationCapability — runs the G→A→R adversarial refinement loop.

Wraps src.cortex.adversarial_agents and the refinement loop from world_model_simulation.
Registered as capability_name="adversarial_falsification", capability_type="simulation".
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("cerebellum.etass.adversarial")

try:
    from src.monkey_brain.kernel.capability_interface import ICapability
    from src.monkey_brain.kernel.execution_state import ExecutionState
except ImportError:
    ICapability = object  # type: ignore
    ExecutionState = Any  # type: ignore


class AdversarialFalsificationCapability(ICapability):
    """Adversarial falsification via G→A→R loop.

    Runs all adversarial solver types (heuristic, algorithmic, graph,
    model_checker, sat_smt, constraint, optimizer) in repeated attack/repair
    cycles until attacks find nothing or max_rounds is exhausted.

    Context keys
    ------------
    prompt          str   Design description / question under test.
    world_state     dict  Entity snapshot from _capture_world_state().
    max_rounds      int   Maximum G→A→R cycles (default 3).
    timeout         float Seconds per attack round (default 90).
    """

    @property
    def capability_name(self) -> str:
        return "adversarial_falsification"

    @property
    def name(self) -> str:
        """Satisfies ICapabilityProtocol so runtime.register() can register
        this — see the identical fix on SittingFaceCodegenCapability. Without
        it, AdversarialAgent's capability lookup always got None."""
        return self.capability_name

    @property
    def capability_type(self) -> str:
        return "simulation"

    def can_execute(self, state: Any) -> bool:
        return True

    def estimate_reward(self, state: Any) -> float:
        return 0.85

    def estimate_cost(self, state: Any) -> float:
        return 0.4

    async def execute(self, state: Any, **kwargs: Any) -> Any:
        ctx: dict = state if isinstance(state, dict) else {}
        prompt = ctx.get("prompt", "")
        world_state = ctx.get("world_state", {})
        max_rounds = int(ctx.get("max_rounds", 3))
        timeout = float(ctx.get("timeout", 90.0))

        try:
            from src.cortex.world_model_simulation import (
                run_adversarial_refinement_loop,
            )

            result = await run_adversarial_refinement_loop(
                prompt=prompt,
                world_state=world_state,
                max_rounds=max_rounds,
                timeout_per_round=timeout,
            )
        except Exception as exc:
            logger.error("[adversarial cap] loop failed: %s", exc)
            result = {"error": str(exc), "rounds": [], "converged": False}

        return self._result(result)

    def _result(self, payload: dict) -> dict:
        return {"capability": self.capability_name, "payload": payload}
