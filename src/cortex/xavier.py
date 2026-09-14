"""Xavier — Simulation, Feedback & Learning Engine.

The closed learning loop of AgentOS.

Xavier predicts, evaluates, learns, and optimizes.
Xavier never executes capabilities directly.
"""

from __future__ import annotations

from typing import Any

from src.cortex.world_model import WorldModel
from src.cortex.simulator import Simulator, SimulationResult
from src.cortex.cost import CostEngine
from src.cortex.reward import ExecutionRewardEngine
from src.cortex.loss import LossEngine
from src.cortex.feedback import FeedbackEngine, FeedbackRecord
from src.cortex.experience import ExperienceStore, Experience
from src.monkey_brain.kernel.fix.policy.policy import BellmanPolicy
from src.monkey_brain.kernel.learn.observer.observer import Observer
from src.monkey_brain.kernel.learn.learning import Learning
from src.monkey_brain.kernel.fix.policy.transition import Transition
from src.monkey_brain.kernel.execute.provider.llm_explorer import LLMExplorer


class Xavier:
    """Simulation, Feedback & Learning Engine.

    Xavier predicts, evaluates, learns, and optimizes.
    Xavier never executes capabilities directly.

    Flow:
    1. LLM Explorer generates candidates (exploration)
    2. Simulator evaluates candidates
    3. Policy selects best candidate (exploitation)
    4. Runtime executes (Wolverine)
    5. Xavier evaluates outcome
    6. Reward/Loss computed
    7. Policy updated
    8. World Model updated
    9. Experience stored
    """

    def __init__(
        self,
        policy: BellmanPolicy | None = None,
        observer: Observer | None = None,
        learning: Learning | None = None,
    ):
        # Core engines
        self.world_model = WorldModel()
        self.simulator = Simulator(self.world_model)
        self.cost_engine = CostEngine()
        self.reward_engine = ExecutionRewardEngine()
        self.loss_engine = LossEngine()
        self.feedback_engine = FeedbackEngine()
        self.experience_store = ExperienceStore()
        self.llm_explorer = LLMExplorer()

        # Dependencies
        self._policy = policy or BellmanPolicy()
        self._observer = observer or Observer()
        self._learning = learning or Learning()
        self._lemon = None

    def set_lemon(self, lemon) -> None:
        self._lemon = lemon
        self.feedback_engine.set_lemon(lemon)
        self.llm_explorer.set_lemon(lemon)
        self._policy.set_lemon(lemon)

    # --- Exploration (LLM generates candidates) ---

    def explore(
        self,
        question: str,
        intent: str,
        state: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Generate candidate workflows using LLM (exploration)."""
        candidates = self.llm_explorer.generate_candidates(question, intent)

        if self._lemon:
            self._lemon.counter("xavier.explorations")

        return [
            {
                "name": c.name,
                "description": c.description,
                "steps": c.steps,
                "cost": c.estimated_cost,
                "latency": c.estimated_latency_ms,
            }
            for c in candidates
        ]

    # --- Simulation (evaluate without side effects) ---

    def simulate(
        self,
        pipeline_id: str,
        capabilities: list[str],
        state: dict[str, Any] | None = None,
    ) -> SimulationResult:
        """Simulate a pipeline execution (no side effects)."""
        result = self.simulator.simulate(pipeline_id, capabilities, state)

        if self._lemon:
            self._lemon.counter("xavier.simulations")

        return result

    # --- Selection (Policy evaluates) ---

    def select(
        self,
        candidates: list[dict[str, Any]],
        state: Any = None,
    ) -> dict[str, Any]:
        """Select best candidate using Policy (exploitation)."""
        from monkeypatched_sdk.models import Pipeline, PipelineStep

        pipelines = []
        for c in candidates:
            steps = [PipelineStep(capability_name=s.get("capability", "retrieve")) for s in c.get("steps", [])]
            pipelines.append(Pipeline(steps=steps, metadata=c))

        if not pipelines:
            return candidates[0] if candidates else {}

        selected = self._policy.select(pipelines, state)
        return selected.metadata

    # --- Evaluation (after execution) ---

    def evaluate(
        self,
        question: str,
        intent: str,
        pipeline_id: str,
        capabilities: list[str],
        answer: str,
        success: bool,
        latency_ms: float,
        state: dict[str, Any] | None = None,
        next_state: dict[str, Any] | None = None,
        ground_truth: dict[str, Any] | None = None,
        user_feedback: dict[str, Any] | None = None,
    ) -> FeedbackRecord:
        """Evaluate an execution and update learning."""
        # Record feedback
        record = self.feedback_engine.record(
            question=question,
            intent=intent,
            pipeline_id=pipeline_id,
            capabilities=capabilities,
            answer=answer,
            success=success,
            latency_ms=latency_ms,
            ground_truth=ground_truth,
            user_feedback=user_feedback,
        )

        # Compute reward
        reward_result = self.reward_engine.compute(
            success=success,
            latency_ms=latency_ms,
            user_feedback=user_feedback,
            ground_truth=ground_truth,
            answer=answer,
            capabilities=capabilities,
        )
        record.reward = reward_result.total_reward
        record.reward_components = reward_result.components

        # Compute loss
        loss_result = self.loss_engine.compute(
            success=success,
            latency_ms=latency_ms,
            ground_truth=ground_truth,
            answer=answer,
            intent=intent,
            capabilities=capabilities,
        )
        record.loss = loss_result.total_loss
        record.loss_components = loss_result.components

        # Compute cost
        cost_result = self.cost_engine.estimate(latency_ms=latency_ms)

        # Update world model
        self.world_model.update_pipeline(
            pipeline_id,
            success=success,
            reward=reward_result.normalized,
            cost=cost_result.total_cost,
            latency_ms=latency_ms,
        )
        for cap in capabilities:
            self.world_model.update_capability(cap, success, latency_ms, cost_result.total_cost / len(capabilities))

        # Update policy
        if state and next_state:
            transition = Transition(
                state=state,
                action=pipeline_id,
                reward=reward_result.normalized,
                next_state=next_state,
                done=True,
            )
            self._policy.update(transition)

        # Store experience
        experience = Experience(
            state=state or {},
            action=pipeline_id,
            reward=reward_result.normalized,
            loss=loss_result.total_loss,
            next_state=next_state or {},
            done=True,
            metadata={
                "cost": cost_result.total_cost,
                "latency_ms": latency_ms,
            },
        )
        self.experience_store.store(experience)

        # Emit telemetry
        if self._lemon:
            self._lemon.counter("xavier.executions_evaluated")
            self._lemon.histogram("xavier.reward", reward_result.total_reward)
            self._lemon.histogram("xavier.loss", loss_result.total_loss)
            self._lemon.histogram("xavier.cost", cost_result.total_cost)

        return record

    # --- Summary ---

    def summary(self) -> dict:
        return {
            "world_model": self.world_model.summary(),
            "feedback": self.feedback_engine.summary(),
            "experience_store": self.experience_store.summary(),
            "policy": self._policy.summary() if self._policy else None,
            "llm_explorer": self.llm_explorer.summary(),
        }
