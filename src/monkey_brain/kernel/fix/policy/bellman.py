"""Bellman Learning Loop — calculates loss between query and simulation states.

This module:
1. Runs query and simulation in parallel
2. Creates embeddings for both final states
3. Calculates embedding loss
4. Updates Bellman policy Q-values

Implements the ILearningLoop interface for interchangeable RL algorithms.
"""

from __future__ import annotations

import asyncio
import logging
import numpy as np
import time
from typing import Any, Dict, Optional

from src.monkey_brain.kernel.execute.runtime.executor import UnifiedExecutor

logger = logging.getLogger(__name__)
from src.monkey_brain.kernel.execute.provider.llm_explorer import LLMExplorer
from src.monkey_brain.kernel.learn.epa.interface import (
    ILearningLoop,
    LearningLoopConfig,
)
from src.monkey_brain.kernel.fix.policy.policy import BellmanPolicy
from src.monkey_brain.kernel.fix.policy.state_models import (
    RLState,
    LearningLoopResult,
    ExecutionDetails,
    ExecutionProfiling,
)


def _saturate(value: float, scale: float) -> float:
    """Squash an unbounded non-negative quantity into [0, 1): v / (v + scale).

    Keeps one large-magnitude feature (answer length, latency) from dominating the
    cosine, while staying monotonic — twice the value still reads as more.
    """
    v = max(0.0, float(value))
    return v / (v + scale) if scale > 0 else 0.0


class BellmanLearningLoop(ILearningLoop):
    """Reinforcement learning loop for Bellman policy updates.

    Implements ILearningLoop interface for interchangeable RL algorithms.
    """

    def __init__(
        self,
        executor: Optional[UnifiedExecutor] = None,
        llm_explorer: Optional[LLMExplorer] = None,
        bellman_policy: Optional[BellmanPolicy] = None,
        config: Optional[LearningLoopConfig] = None,
    ):
        # Use dependency injection or create defaults
        self.executor = executor or UnifiedExecutor()
        self.llm_explorer = llm_explorer or LLMExplorer()
        self.bellman_policy = bellman_policy or BellmanPolicy()
        self.config = config or LearningLoopConfig()

    async def calculate_embedding_loss(self, query_state: RLState, simulation_state: RLState) -> float:
        """How far the simulator's prediction is from what the query actually returned.

        Two terms, because structure alone cannot tell agreement from contradiction:

          * structural — cosine over the per-feature-normalized state vector (hit
            counts, latency, whether an LLM spoke).
          * lexical    — token overlap (Jaccard) between the two ANSWERS. This is the
            term that notices the simulator said 12% and reality said 99%.

        Without the lexical term this measured almost nothing. Every feature was raw
        (`len(answer)` in the thousands against hit counts in single digits), and the
        "normalization" divided each feature by their SUM — which cosine, being
        scale-invariant, ignores entirely. So the vector's direction was pinned to the
        length axis and the loss came out ~0.008 whether the two answers agreed
        perfectly or contradicted each other outright. reward = 1 - loss was therefore
        a constant ~0.99, and BellmanPolicy learned nothing from it.
        """
        try:
            query_embedding = self._state_to_embedding(query_state)
            sim_embedding = self._state_to_embedding(simulation_state)

            norm_query = float(np.linalg.norm(query_embedding))
            norm_sim = float(np.linalg.norm(sim_embedding))
            if norm_query == 0.0 or norm_sim == 0.0:
                structural_sim = 0.0
            else:
                structural_sim = float(np.dot(query_embedding, sim_embedding) / (norm_query * norm_sim))

            lexical_sim = self._answer_agreement(query_state.answer, simulation_state.answer)

            # Content carries the weight; structure is corroborating evidence.
            similarity = 0.7 * lexical_sim + 0.3 * structural_sim
            return max(0.0, min(1.0, 1.0 - similarity))

        except Exception as e:
            logger.warning("Error calculating embedding loss: %s", e)
            return 0.5  # Neutral loss on error

    @staticmethod
    def _answer_agreement(a: str, b: str) -> float:
        """Jaccard overlap of the two answers' tokens — 1.0 identical, 0.0 disjoint.

        Two empty answers agree vacuously; one empty and one not is total disagreement.
        """
        ta = set((a or "").lower().split())
        tb = set((b or "").lower().split())
        if not ta and not tb:
            return 1.0
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / len(ta | tb)

    def _state_to_embedding(self, state: RLState) -> np.ndarray:
        """Convert state to embedding vector (simplified)."""
        try:
            # Extract key features from state
            features = []

            # Every feature is squashed into [0, 1] on its OWN scale. Previously they
            # were raw and wildly incommensurate — len(answer) in the thousands sitting
            # beside counts in the single digits — so the vector pointed almost entirely
            # along the length axis and cosine could see nothing else.
            answer = state.answer or ""
            features.extend(
                [
                    _saturate(len(answer), 500.0),  # length, saturating
                    answer.count(" ") / max(1, len(answer)),  # word density (already 0..1)
                    1.0 if "success" in answer.lower() else 0.0,
                ]
            )

            features.extend(
                [
                    _saturate(len(state.semantic_hits), 10.0),  # retrieval evidence
                    _saturate(len(state.graph_paths), 10.0),
                    1.0 if state.llm_answered else 0.0,
                ]
            )

            profiling = state.execution_details.profiling
            features.extend(
                [
                    _saturate(profiling.total_latency, 5000.0),  # ms, saturating
                    _saturate(len(profiling.state_snapshots), 10.0),
                ]
            )

            # NOT re-normalized by the sum: dividing every component by their total is a
            # positive rescale, and cosine similarity is invariant to it — the old code's
            # "normalization" step changed the loss by exactly nothing.

            # Ensure consistent embedding size based on config
            embedding_size = self.config.embedding_dimension
            if len(features) < embedding_size:
                features.extend([0.0] * (embedding_size - len(features)))
            elif len(features) > embedding_size:
                features = features[:embedding_size]

            return np.array(features)

        except Exception as e:
            logger.warning("Error creating embedding: %s", e)
            return np.zeros(self.config.embedding_dimension)

    async def run_query_and_simulation(
        self,
        question: str,
        mongo_client: Any = None,
    ) -> tuple[RLState, RLState]:
        """Run query and simulation in parallel."""
        try:
            query_task = asyncio.create_task(self.executor.execute(question, mongo_client, question_source="query"))
            simulation_task = asyncio.create_task(
                self.executor.execute(question, mongo_client, question_source="simulation")
            )

            # Wait for both to complete
            query_result, simulation_result = await asyncio.gather(query_task, simulation_task)

            # Create RLState objects from results
            query_state = RLState(
                answer=query_result[0] if query_result else "",
                semantic_hits=query_result[1] if query_result else [],
                graph_paths=query_result[2] if query_result else [],
                llm_answered=query_result[3] if query_result else False,
                intent="knowledge_base",  # Default intent, would be determined dynamically
                execution_details=ExecutionDetails(
                    profiling=ExecutionProfiling(total_latency=0)  # Would be filled with actual data
                ),
            )

            simulation_state = RLState(
                answer=simulation_result[0] if simulation_result else "",
                semantic_hits=simulation_result[1] if simulation_result else [],
                graph_paths=simulation_result[2] if simulation_result else [],
                llm_answered=simulation_result[3] if simulation_result else False,
                intent="knowledge_base",  # Default intent, would be determined dynamically
                execution_details=ExecutionDetails(
                    profiling=ExecutionProfiling(total_latency=0)  # Would be filled with actual data
                ),
            )

            return query_state, simulation_state

        except Exception as e:
            logger.error("Error running query and simulation: %s", e)
            return RLState(), RLState()

    async def update_policy(
        self,
        query_state: RLState,
        simulation_state: RLState,
        pipeline_id: str,
        profiling_data: Optional[Dict[str, float]] = None,
        question: str = "",
    ):
        """Update Bellman policy based on embedding loss."""
        try:
            # Calculate embedding loss
            loss = await self.calculate_embedding_loss(query_state, simulation_state)

            # Create transition for Bellman update
            from src.monkey_brain.kernel.fix.policy.transition import Transition

            # The Q-key must match what BellmanPolicy.select() reads back, or the update
            # is write-only. select() looks up (hash_state(state), capability_name) and
            # is called WITHOUT a state, so the state side must hash {} — `{"goal": ...}`
            # hashed to something select could never produce.
            transition = Transition(
                state={},
                action=pipeline_id,
                next_state={"success": query_state.llm_answered},
                reward=1.0 - loss,  # Higher reward for lower loss
                done=True,
            )

            # Update Bellman policy
            self.bellman_policy.update(transition)

            logger.info("Bellman policy updated: loss=%.3f reward=%.3f", loss, 1.0 - loss)

            # Log to Lemon if available
            if profiling_data is not None:
                self._log_profiling_metrics(profiling_data, question, loss)

        except Exception as e:
            logger.error("Error updating Bellman policy: %s", e)

    def _get_lemon_instance(self):
        """Get Lemon instance for metrics logging."""
        try:
            from src.introspection.lemon import Lemon

            return Lemon()
        except Exception:
            return None

    async def complete_learning_loop(
        self,
        question: str,
        mongo_client: Any = None,
        pipeline_id: str = "",
    ) -> LearningLoopResult:
        """Complete reinforcement learning loop for a question with profiling.

        `pipeline_id` is the STABLE identity of the capability/workload that ran — it is
        the action key the Bellman Q-table is keyed on, so it must match what
        BellmanPolicy.select() looks up (a workload's first-step capability_name).
        """
        logger.info("=== BELLMAN LEARNING LOOP === question=%r", question)
        start_time = time.monotonic()
        profiling_data = {
            "start_time": start_time,
            "query_time": 0,
            "simulation_time": 0,
            "embedding_time": 0,
            "policy_update_time": 0,
        }

        try:
            query_start = time.monotonic()
            query_state, simulation_state = await self.run_query_and_simulation(question, mongo_client)
            profiling_data["query_time"] = (time.monotonic() - query_start) * 1000

            embed_start = time.monotonic()
            loss = await self.calculate_embedding_loss(query_state, simulation_state)
            profiling_data["embedding_time"] = (time.monotonic() - embed_start) * 1000
            logger.debug("Embedding loss: %.3f", loss)

            policy_start = time.monotonic()
            # The action key was `f"pipeline_{int(time.time())}"` — a TIMESTAMP. Every
            # loop therefore learned a Q-value for a brand-new key that would never be
            # seen again: the table grew one dead entry per run and nothing could ever be
            # exploited. An action key has to be a stable identity of the thing that ran.
            # The caller knows which capability was selected; default to the state's own
            # capability so repeated runs of the same work share an entry.
            pipeline_id = pipeline_id or query_state.intent or "unknown_capability"
            await self.update_policy(query_state, simulation_state, pipeline_id, profiling_data, question)
            profiling_data["policy_update_time"] = (time.monotonic() - policy_start) * 1000

            total_time = (time.monotonic() - start_time) * 1000
            profiling_data["total_time"] = total_time
            logger.info(
                "Learning loop completed: total=%.2fms query=%.2fms embed=%.2fms policy=%.2fms",
                total_time,
                profiling_data["query_time"],
                profiling_data["embedding_time"],
                profiling_data["policy_update_time"],
            )

            self._log_profiling_metrics(profiling_data, question, loss)

            return LearningLoopResult(
                query_state=query_state,
                simulation_state=simulation_state,
                loss=loss,
                success=True,
                profiling=profiling_data,
            )

        except Exception as e:
            logger.error("Learning loop failed: %s", e)
            return LearningLoopResult(
                query_state=RLState(),
                simulation_state=RLState(),
                loss=0.0,
                success=False,
                profiling={},
                error=str(e),
            )

    def _log_profiling_metrics(self, profiling_data: Dict[str, float], question: str, loss: float):
        """Log profiling metrics to Lemon/Elasticsearch."""
        try:
            # Get Lemon instance
            lemon = self._get_lemon_instance()
            if not lemon:
                return

            # Log timing metrics (with defensive checks)
            if "total_time" in profiling_data:
                lemon.histogram("bellman.loop_time_ms", profiling_data["total_time"])
            if "query_time" in profiling_data:
                lemon.histogram("bellman.query_time_ms", profiling_data["query_time"])
            if "embedding_time" in profiling_data:
                lemon.histogram("bellman.embedding_time_ms", profiling_data["embedding_time"])
            if "policy_update_time" in profiling_data:
                lemon.histogram(
                    "bellman.policy_update_time_ms",
                    profiling_data["policy_update_time"],
                )

            # Log counters
            lemon.counter("bellman.loops_completed")
            lemon.histogram("bellman.loss", loss)
            lemon.histogram("bellman.reward", 1.0 - loss)

            logger.debug("Profiling metrics logged to Lemon")

        except Exception as e:
            logger.warning("Failed to log profiling metrics: %s", e)


def create_bellman_learning_loop(
    executor: Optional[UnifiedExecutor] = None,
    llm_explorer: Optional[LLMExplorer] = None,
    bellman_policy: Optional[BellmanPolicy] = None,
    config: Optional[LearningLoopConfig] = None,
) -> BellmanLearningLoop:
    return BellmanLearningLoop(executor, llm_explorer, bellman_policy, config)
