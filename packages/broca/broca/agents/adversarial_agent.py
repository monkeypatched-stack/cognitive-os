"""AdversarialAgent — runs the G→A→R adversarial refinement loop as an ETASS step.

Pipeline position: runs after world-model simulation, before governance gate.

Generate  →  Attack  →  Repair  →  Attack Again  →  Repair  →  …
                                    (until attacks find nothing or max_rounds)

Discovers AdversarialFalsificationCapability from Cerebellum to execute the loop.
Falls back to calling src.cortex.world_model_simulation.run_adversarial_refinement_loop
directly when the capability is not available.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

from ._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.adversarial")

_REPO = Path(os.environ.get("MONKEYBRAIN_REPO", str(Path(__file__).parents[4])))


def _ensure_cortex() -> None:
    src = _REPO / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


class AdversarialAgent(BaseETASSAgent):
    """ETASS agent that adversarially falsifies a design through repeated attack/repair cycles.

    Context keys (all optional)
    ---------------------------
    prompt      str   Design description or question under test.
                      Falls back to context.get("question") or context.get("goal").
    world_state dict  Pre-captured entity snapshot. If absent, agent captures it.
    max_rounds  int   Maximum G→A→R cycles (default 3).
    timeout     float Seconds per attack round (default 90).
    db          any   AsyncIOMotorDatabase — needed for world-state capture.
    """

    agent_type = "adversarial_falsification"
    description = (
        "Adversarially falsifies a design via repeated attack/repair cycles using "
        "heuristic, algorithmic, graph, model_checker, sat_smt, constraint, and "
        "optimizer solvers. Confidence increases when attacks consistently fail."
    )

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict[str, Any]) -> dict[str, Any]:
        _ensure_cortex()

        prompt: str = context.get("prompt") or context.get("question") or context.get("goal") or ""
        world_state: dict = context.get("world_state") or {}
        max_rounds: int = int(context.get("max_rounds", 3))
        timeout: float = float(context.get("timeout", 90.0))
        db = context.get("db")

        if not prompt:
            self._reward(False, 0.0)
            return self._result(
                payload={"error": "no prompt/question/goal provided"},
                observations=["adversarial loop requires a design description"],
            )

        # Capture world state if not provided
        if not world_state and db is not None:
            try:
                from src.cortex.world_model_simulation import _capture_world_state

                world_state = await _capture_world_state(db)
                logger.info(
                    "[adversarial] captured world state: %d collections",
                    len(world_state.get("collections", {})),
                )
            except Exception as exc:
                logger.warning("[adversarial] world state capture failed: %s", exc)

        # Try capability first, fall back to direct call
        result: dict[str, Any] = {}
        try:
            cap = self._discover_capability("adversarial_falsification")
            if cap is not None:
                raw = await cap.execute(
                    {
                        "prompt": prompt,
                        "world_state": world_state,
                        "max_rounds": max_rounds,
                        "timeout": timeout,
                    }
                )
                result = raw.get("payload", raw) if isinstance(raw, dict) else {}
        except Exception as exc:
            logger.debug("[adversarial] capability path failed: %s — using direct call", exc)

        if not result:
            from src.cortex.world_model_simulation import (
                run_adversarial_refinement_loop,
            )

            result = await run_adversarial_refinement_loop(
                prompt=prompt,
                world_state=world_state,
                max_rounds=max_rounds,
                timeout_per_round=timeout,
            )

        converged = result.get("converged", False)
        rounds_run = len(result.get("rounds", []))
        total_findings = sum(len(r.get("attack", {}).get("findings", [])) for r in result.get("rounds", []))
        confidence_delta = result.get("total_confidence_delta", 0.0)

        self._reward(converged, 0.9 if converged else max(0.3, 0.6 - 0.1 * total_findings))

        obs = [
            f"rounds run: {rounds_run}/{max_rounds}",
            f"total findings: {total_findings}",
            f"converged: {converged}",
            f"confidence delta: {confidence_delta:+.3f}",
        ]
        return self._result(payload=result, observations=obs)

    def _discover_capability(self, name: str) -> Any:
        """Discover a Cerebellum capability by name via the runtime."""
        try:
            runtime = getattr(self, "_runtime", None)
            if runtime is None:
                return None
            cap = runtime.get_capability(name)
            return cap
        except Exception:
            return None
