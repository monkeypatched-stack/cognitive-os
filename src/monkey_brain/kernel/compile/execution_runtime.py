"""PerActorExecutionRuntime — Graph execution kernel.

Executes compiled execution graphs against the world, discovering transitions.
Decoupled from planning and simulation for independent testing.
"""

from __future__ import annotations

import logging
from typing import Any

from src.monkey_brain.kernel.compile import _obs
from src.monkey_brain.kernel.compile.tensor import Feature, SparseTransitionTensor
from src.monkey_brain.kernel.compile.execution_graph import ExecutionPlanSnapshot

logger = logging.getLogger("agentos.compile.execution_runtime")


class PerActorExecutionRuntime:
    """Graph execution kernel — executes plans against the world.

    Injected INTO per-actor CognitiveRuntime.

    Owns:
      - Execution of plans against global world (read-only)
      - Discovery of new transitions
      - Integration of discoveries into local belief
    """

    def __init__(self, actor_id: str) -> None:
        self.actor_id = actor_id
        self._execution_history: list[dict] = []
        self._discoveries: list[tuple] = []

    def execute(
        self,
        execution_graph: ExecutionPlanSnapshot,
        world: SparseTransitionTensor,
        local_belief: SparseTransitionTensor,
    ) -> ExecutionPlanSnapshot:
        """Execute plan against world, discover and record transitions.

        Args:
            execution_graph: ExecutionPlanSnapshot with plan
            world: global world (read-only)
            local_belief: actor's belief (updated with discoveries)

        Returns:
            ExecutionPlanSnapshot with observed_path, discoveries, epistemic_loss
        """
        assert execution_graph is not None, "execution_graph cannot be None"
        assert world is not None, "world cannot be None"
        assert local_belief is not None, "local_belief cannot be None"

        plan = execution_graph.plan
        goal = execution_graph.goal
        horizon = execution_graph.horizon

        assert isinstance(plan, list) and len(plan) > 0, "plan must be non-empty list"
        assert isinstance(horizon, int) and 0 < horizon <= 10000, "horizon must be in (0, 10000]"

        observed: list[tuple[str, str | None]] = []
        discoveries: list[tuple] = []

        if not plan:
            return execution_graph

        cur: str | None = plan[0]
        seen: set[str] = set()

        for step in range(horizon):
            if cur is None or cur in seen:
                break

            seen.add(cur)

            try:
                succ = {d: world.feature(cur, d, Feature.PROBABILITY) for d, _ in world.successors(cur)}
            except (KeyError, AttributeError, TypeError) as e:
                logger.debug("[execution_runtime] world.successors(%r) failed: %s", cur, e)
                break
            except RuntimeError as e:
                logger.warning(
                    "[execution_runtime] world access failed at step %d: %s",
                    step,
                    e,
                    exc_info=True,
                )
                break

            assert isinstance(succ, dict), "successors must be dict"
            assert all(isinstance(v, (int, float)) and v >= 0 for v in succ.values()), (
                "successor probabilities must be non-negative"
            )

            if not succ:
                break

            actual = max(succ, key=succ.get)
            assert isinstance(actual, str) and actual, "successor state must be non-empty string"
            observed.append((cur, actual))

            try:
                domain = world.domain_of(cur)
                dst_domain = world.domain_of(actual)
                reward = world.feature(cur, actual, Feature.REWARD)

                assert isinstance(reward, (int, float)), "reward must be numeric"

                if not local_belief.has_edge(cur, actual):
                    discoveries.append((cur, actual, reward))

                local_belief.observe(
                    cur,
                    actual,
                    domain=domain,
                    dst_domain=dst_domain,
                    reward=reward,
                    weight=1.0,
                )
            except (KeyError, AttributeError, TypeError) as e:
                logger.debug(
                    "[execution_runtime] belief update failed for edge (%r, %r): %s",
                    cur,
                    actual,
                    e,
                )
            except RuntimeError as e:
                logger.warning("[execution_runtime] belief update failed: %s", e, exc_info=True)

            if goal is not None and actual == goal:
                break

            cur = actual

        from src.monkey_brain.kernel.compile.sparse import epistemic_loss

        predicted = execution_graph.predicted_distribution or {}
        observed_end = {observed[-1][1]: 1.0} if observed and observed[-1][1] else {}

        try:
            loss_matrix = epistemic_loss(
                {("_end", k): v for k, v in predicted.items()},
                {("_end", k): v for k, v in observed_end.items()},
            )
            assert isinstance(loss_matrix, (int, float)) and loss_matrix >= 0.0, (
                f"epistemic_loss must be non-negative, got {loss_matrix}"
            )
            execution_graph.epistemic_loss = loss_matrix
        except (KeyError, TypeError, ValueError) as e:
            logger.warning(
                "[execution_runtime] epistemic loss computation failed: %s",
                e,
                exc_info=True,
            )
            execution_graph.epistemic_loss = 0.0

        execution_graph.observed_path = observed
        execution_graph.discoveries = discoveries
        execution_graph.reached_goal = bool(observed) and observed[-1][1] == goal

        assert isinstance(execution_graph.epistemic_loss, (int, float)) and execution_graph.epistemic_loss >= 0.0, (
            "epistemic_loss post-condition failed"
        )
        assert isinstance(execution_graph.observed_path, list), "observed_path must be list"
        assert all(len(t) == 2 for t in execution_graph.observed_path), "observed_path entries must be 2-tuples"

        self._record_execution(execution_graph)

        logger.info(
            "[execution_runtime] %s executed %s→%s: %d steps, %d discoveries, loss=%.4f, goal=%s",
            self.actor_id,
            plan[0] if plan else "?",
            goal,
            len(observed),
            len(discoveries),
            execution_graph.epistemic_loss,
            "reached" if execution_graph.reached_goal else "missed",
        )
        _obs.event(
            "execution.completed",
            actor=self.actor_id,
            steps=len(observed),
            discoveries=len(discoveries),
            reached_goal=execution_graph.reached_goal,
            loss=round(execution_graph.epistemic_loss, 4),
        )

        return execution_graph

    def _record_execution(self, graph: ExecutionPlanSnapshot) -> None:
        """Record execution for auditing."""
        import time

        execution = {
            "timestamp": time.time(),
            "graph_id": graph.graph_id,
            "plan": graph.plan,
            "observed_path": graph.observed_path,
            "discoveries": graph.discoveries,
            "goal": graph.goal,
            "reached_goal": graph.reached_goal,
            "epistemic_loss": graph.epistemic_loss,
        }
        self._execution_history.append(execution)
        self._discoveries.extend(graph.discoveries)

    def execution_history(self, limit: int = 10) -> list[dict]:
        """Recent executions."""
        return self._execution_history[-limit:]

    def discoveries(self) -> list[tuple]:
        """All discoveries made."""
        return list(self._discoveries)

    def checkpoint(self, path: str) -> None:
        """Persist execution history and discoveries for auditing and recovery."""
        import json

        try:
            state = {
                "execution_history": self._execution_history,
                "discoveries": self._discoveries,
            }
            with open(path, "w") as f:
                json.dump(state, f)
            logger.info(
                "[execution_runtime] %s checkpointed %d executions, %d discoveries to %s",
                self.actor_id,
                len(self._execution_history),
                len(self._discoveries),
                path,
            )
            _obs.event(
                "execution.checkpoint",
                actor=self.actor_id,
                executions=len(self._execution_history),
                discoveries=len(self._discoveries),
            )
        except (IOError, OSError) as e:
            logger.error("[execution_runtime] checkpoint failed: %s", e, exc_info=True)
            raise

    def restore(self, path: str) -> None:
        """Restore execution history and discoveries from checkpoint."""
        import json

        try:
            with open(path) as f:
                state = json.load(f)
            self._execution_history = state.get("execution_history", [])
            self._discoveries = state.get("discoveries", [])
            logger.info(
                "[execution_runtime] %s restored %d executions, %d discoveries from %s",
                self.actor_id,
                len(self._execution_history),
                len(self._discoveries),
                path,
            )
            _obs.event(
                "execution.restore",
                actor=self.actor_id,
                executions=len(self._execution_history),
                discoveries=len(self._discoveries),
            )
        except (IOError, OSError, json.JSONDecodeError) as e:
            logger.error("[execution_runtime] restore failed: %s", e, exc_info=True)
            raise

    def summary(self) -> dict[str, Any]:
        """Execution runtime summary."""
        return {
            "type": "PerActorExecutionRuntime",
            "actor": self.actor_id,
            "executions": len(self._execution_history),
            "total_discoveries": len(self._discoveries),
        }
