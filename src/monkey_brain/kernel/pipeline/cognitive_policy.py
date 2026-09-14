"""CognitivePolicy — controls the execution lifecycle.

The default policy runs:

    Observe → Believe → Plan → Execute → Observe Outcome
    → Learn → Compile Φ → Predict → Commit

But other policies might:
    - Loop through Plan → Predict → Reject → Plan → Accept → Execute
    - Skip execution for simulation
    - Run multiple planning cycles before committing
    - Execute without learning (inference-only)

The policy makes the lifecycle flexible without changing CognitiveRuntime.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Awaitable

from src.monkey_brain.kernel.pipeline.execution_state import CognitiveState

logger = logging.getLogger("agentos.pipeline.cognitive_policy")

# Type alias for stage functions
StageFn = Callable[[CognitiveState], Awaitable[CognitiveState]]


async def run_stages(state: CognitiveState, stages: list[tuple[str, StageFn]]) -> CognitiveState:
    """
    Run an ordered list of (name, stage_fn) pairs over state, in sequence.
    """
    for stage_name, stage_fn in stages:
        stage_start = time.time()
        try:
            state = await stage_fn(state)
        except Exception as e:
            logger.error("[policy] %s failed: %s", stage_name, e, exc_info=True)
            state.record_error(stage_name, e)
        finally:
            state.record_stage(stage_name, (time.time() - stage_start) * 1000)
    return state


class CognitivePolicy:
    """Controls the execution lifecycle of the cognitive runtime.

    The default policy defines the standard 9-stage sequence.
    Subclass or replace to implement alternative control flows.

    The policy is pure control flow — it decides which stages run,
    in what order, and whether to loop. It does not implement stages.
    """

    def __init__(self) -> None:
        """Initialize with the default stage sequence."""
        self._stages: list[tuple[str, StageFn]] = []

    def configure(
        self,
        observe: StageFn,
        believe: StageFn,
        plan: StageFn,
        execute: StageFn,
        observe_outcome: StageFn,
        learn: StageFn,
        compile_phi: StageFn,
        predict: StageFn,
        commit: StageFn,
    ) -> None:
        """Configure the default 9-stage lifecycle."""
        self._stages = [
            ("observe", observe),
            ("believe", believe),
            ("plan", plan),
            ("execute", execute),
            ("observe_outcome", observe_outcome),
            ("learn", learn),
            ("compile_phi", compile_phi),
            ("predict", predict),
            ("commit", commit),
        ]

    async def execute(self, state: CognitiveState) -> CognitiveState:
        """Execute the cognitive lifecycle.

        Override this method to implement custom control flow.
        The default implementation runs all stages in sequence.
        """
        return await run_stages(state, self._stages)

    def stages_from(self, *names: str) -> list[tuple[str, StageFn]]:
        """Pull a named subset of the configured stages, in the order given —
        so callers building a stage-subset runner (ReasoningRuntime/
        ExecutionRuntime) don't reach into self._stages directly."""
        by_name = dict(self._stages)
        return [(name, by_name[name]) for name in names]


class RecursivePlanningPolicy(CognitivePolicy):
    """Example: loops Plan → Predict until confidence is high enough.

    Demonstrates how alternative policies work without changing CognitiveRuntime.
    """

    def __init__(self, max_iterations: int = 3, confidence_threshold: float = 0.8) -> None:
        super().__init__()
        self._max_iterations = max_iterations
        self._confidence_threshold = confidence_threshold
        self._plan_fn: StageFn | None = None
        self._predict_fn: StageFn | None = None
        self._other_stages: list[tuple[str, StageFn]] = []

    def configure(
        self,
        observe: StageFn,
        believe: StageFn,
        plan: StageFn,
        execute: StageFn,
        observe_outcome: StageFn,
        learn: StageFn,
        compile_phi: StageFn,
        predict: StageFn,
        commit: StageFn,
    ) -> None:
        self._plan_fn = plan
        self._predict_fn = predict
        self._other_stages = [
            ("observe", observe),
            ("believe", believe),
            ("execute", execute),
            ("observe_outcome", observe_outcome),
            ("learn", learn),
            ("compile_phi", compile_phi),
            ("commit", commit),
        ]

    async def execute(self, state: CognitiveState) -> CognitiveState:
        """Loop: Observe → Believe → [Plan → Predict] × N → Execute → Learn → Commit."""
        # Pre-plan stages
        for name, fn in self._other_stages[:2]:  # observe, believe
            await self._run_stage(state, name, fn)

        # Planning loop
        for i in range(self._max_iterations):
            state = await self._run_stage(state, "plan", self._plan_fn)
            state = await self._run_stage(state, "predict", self._predict_fn)

            confidence = state.belief.confidence()
            if confidence >= self._confidence_threshold:
                logger.info(
                    "[policy] confidence %.2f >= %.2f after %d iterations",
                    confidence,
                    self._confidence_threshold,
                    i + 1,
                )
                break

        # Post-plan stages
        for name, fn in self._other_stages[2:]:  # execute through commit
            state = await self._run_stage(state, name, fn)

        return state

    async def _run_stage(self, state: CognitiveState, name: str, fn: StageFn) -> CognitiveState:
        stage_start = time.time()
        try:
            state = await fn(state)
        except Exception as e:
            logger.error("[policy] %s failed: %s", name, e, exc_info=True)
            state.record_error(name, e)
        finally:
            state.record_stage(name, (time.time() - stage_start) * 1000)
        return state
