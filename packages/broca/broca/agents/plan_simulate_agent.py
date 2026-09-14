"""PlanSimulateAgent — runs a generated execution plan through ExecutorAgent
in dry-run mode.

Exists as its own capability (rather than just passing dry_run=True to the
"executor" node directly) because GraphScheduler always calls
BrocaCapabilityBus.execute(capability_name, {}) with no per-node args (see
graph_scheduler.py's _execute_node) — two graph nodes can't share one
capability name and differ only by an argument the caller never gets to set.
This one hard-codes dry_run=True regardless of what's in context; the
"executor" capability's own default (dry_run=False) is reserved for the real
post-simulation execution node.
"""

from __future__ import annotations

import logging
from typing import Any

from ._base import BaseETASSAgent
from .executor import ExecutorAgent

logger = logging.getLogger("broca.agents.plan_simulate")


class PlanSimulateAgent(BaseETASSAgent):
    agent_type = "plan_simulate"
    description = "Runs the generated execution plan through the simulator (dry-run) before real execution"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict) -> dict:
        executor = ExecutorAgent(runtime=self._runtime)
        result = await executor.handle({**context, "dry_run": True})
        payload = result.payload if hasattr(result, "payload") else (result if isinstance(result, dict) else {})

        # Reward on the DRY-RUN OUTCOME, not on `executed`. ExecutorAgent hardcodes
        # `executed: True` in every payload — success or failure, dry-run or real — so
        # `payload.get("executed", True)` was a constant and this agent handed out a
        # positive reward on every simulation regardless of whether the plan validated.
        # `all_ok` is the actual signal: did every step (would) succeed.
        ok = bool(payload.get("all_ok", False))
        self._reward(ok, 0.8 if ok else 0.3)
        return self._result(
            payload={**payload, "simulated": True},
            observations=[
                f"simulated {len(payload.get('steps', []))} plan steps"
                + ("" if ok else " — dry run surfaced failing step(s)")
            ],
        )
