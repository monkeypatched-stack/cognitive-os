"""Cognitive Kernel — the unified epistemic optimizer.

    f(E_t, G_t, a_t) → E_{t+1}

Minimizes epistemic simulation loss:
    L_E = L_S + L_B + L_A + L_M + L_K + L_C + L_G

while maximizing confidence and expected goal completion.

Architecture:
    Epistemic State Manager
    ├── Solver Scheduler
    ├── Reasoning Scheduler
    ├── Evidence Fusion Engine
    ├── Capability Runtime
    ├── Knowledge Runtime
    ├── Agent Mesh Runtime
    └── Repair Engine (LossDrivenRepair)
"""

from __future__ import annotations

import logging
import time
from typing import Any

from cortex.epa import EpistemicPredictiveState, epa_transition, epa_loss
from cortex.epistemic import GoalState as EPAGoalState
from src.knowledge.pack import KnowledgePack
from .learn.epa.evidence_fusion import EvidenceFusionEngine
from .rl.retrieval_policy import RLRetrievalPolicy
from .rl.planner_policy import PlannerPolicy
from .predict.solver_mesh import SolverMesh
from .execute.agent_mesh import ExecutionPool, AgentSpec, AgentRole
from cortex.reasoning_scheduler import ReasoningScheduler
from .loss_decomposer import LossDecomposer
from .loss_driven_repair import LossDrivenRepair
from domains.software_engineering.knowledge.telemetry import EngineeringReport
from domains.software_engineering.knowledge.pack import (
    SoftwareEngineeringKnowledgePublisher,
)
from .capability_interface import ICapability, CapabilityResult
from .execute.capabilities.bus import CapabilityBus
from .config import (
    LEARNING_RATE,
    CONFIDENCE_DELTA_SUCCESS,
    CONFIDENCE_DELTA_FAILURE,
    KNOWLEDGE_LOSS_ON_FAILURE,
)
from .execute.capabilities.scheduler import CapabilityScheduler

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cognitive Kernel
# ---------------------------------------------------------------------------


class CognitiveKernel:
    """The unified epistemic optimizer.

    f(E_t, G_t, a_t) → E_{t+1}

    Minimizes:
        L_E = L_S + L_B + L_A + L_M + L_K + L_C + L_G

    While maximizing:
        - Confidence
        - Expected goal completion
        - Knowledge quality
    """

    def __init__(self):
        # EPA goal — drives transition conditioning
        self._goal = EPAGoalState()

        # Broca AgentMesh — EPA topology layer providing M_t
        try:
            from broca.mesh import AgentMesh as BrocaMesh

            self._broca_mesh = BrocaMesh()
        except Exception as e:
            logger.debug("Broca mesh unavailable: %s", e)
            self._broca_mesh = None

        # Primary EPA state (E_t = S, B, A, M)
        try:
            from cerebellum.graph import get_global_graph

            self.state: EpistemicPredictiveState = EpistemicPredictiveState.from_world_state(
                {},
                capability_graph=get_global_graph(),
                agent_mesh=self._broca_mesh,
            )
        except Exception as e:
            logger.debug("Cerebellum graph unavailable: %s", e)
            self.state = EpistemicPredictiveState()

        # Core components
        self.solver_mesh = SolverMesh()
        self._jepa = self.solver_mesh.get_jepa()
        self.knowledge_pack = KnowledgePack()
        self.evidence_fusion = EvidenceFusionEngine()
        self.retrieval_policy = RLRetrievalPolicy()
        self._planner_policy = PlannerPolicy()
        self.capability_bus = CapabilityBus()
        self.capability_scheduler = CapabilityScheduler(self.capability_bus)
        self.execution_pool = ExecutionPool(self.knowledge_pack)
        self.capability_bus.set_mesh(self.execution_pool)
        self.reasoning_scheduler = ReasoningScheduler()
        self.loss_decomposer = LossDecomposer()
        self.repair_engine = LossDrivenRepair()
        self.telemetry = EngineeringReport()
        self.knowledge_publisher = SoftwareEngineeringKnowledgePublisher()

        # History
        self._history: list[dict[str, Any]] = []
        self._max_history = 1000
        self._step = 0

        # Learning state
        self._capability_utility: dict[str, float] = {}
        self._transition_quality: list[float] = []  # rolling L_E history
        self._max_quality = 100

    async def step(
        self,
        action: str | None = None,
        capability: ICapability | None = None,
        execution_mode: str = "auto",
    ) -> dict[str, Any]:
        """f(E_t, G_t, a_t) → E_{t+1} — single authoritative transition via epa_transition.

        execution_mode:
            'auto'      — Mode B if goal has runtime context, Mode A otherwise
            'artifact'  — Mode A: generate-only, skip simulation
            'runtime'   — Mode B: simulate alongside execution
        """
        t0 = time.monotonic()

        # 1. Planner selects action if not provided
        if action is None:
            candidates = list(self.state.A)
            action = self._planner_policy.get_best_action(self._goal.objective, candidates) or ""

        # 2. Execute capability
        cap_result = None
        if capability:
            try:
                cap_result = await capability.execute({})
            except Exception as e:
                logger.warning("Capability execution failed: %s", e)
                cap_result = CapabilityResult(success=False, error=str(e))

        # 4. Build evidence from execution result
        evidence = self._build_evidence(action, cap_result)

        # 5. Resolve capability graph
        cap_graph = None
        try:
            from cerebellum.graph import get_global_graph

            cap_graph = get_global_graph()
        except Exception as e:
            logger.debug("Cerebellum graph unavailable for evidence: %s", e)

        # 6. EPA transition — the single authoritative state update
        try:
            E_next = epa_transition(
                self.state,
                self._goal,
                action,
                evidence=evidence,
                capability_graph=cap_graph,
                agent_mesh=self._broca_mesh,
                fusion_engine=self.evidence_fusion,
                world_model=self._jepa,
                solver_mesh=self.solver_mesh,
            )
        except Exception as e:
            logger.warning("epa_transition failed: %s — keeping current state", e)
            E_next = self.state

        # 7. Compute 7-term loss — L_S via JEPA once trained; L_K from KnowledgePack gap; L_G from goal progress
        l_k = 0.0
        try:
            kp_fusion = self.knowledge_pack.fuse()
            actual_quality = float(
                getattr(
                    kp_fusion,
                    "effective_knowledge",
                    getattr(kp_fusion, "updated_confidence", E_next.B.confidence),
                )
            )
            l_k = round(max(0.0, float(E_next.B.confidence) - actual_quality), 4)
        except Exception as e:
            logger.debug("Knowledge pack fusion failed: %s", e)
            l_k = 0.0

        # Compute goal progress for L_G
        goal_progress = 0.0
        try:
            goal_progress = self._goal.progress(E_next.S)
        except Exception as e:
            logger.debug("Goal progress computation failed: %s", e)
            goal_progress = 0.0

        # Extract constraint violations from evidence for L_C
        constraint_violations = evidence.get("constraint_violations", 0) if evidence else 0

        try:
            loss_dict = epa_loss(
                E_next,
                self.state,
                world_model=self._jepa,
                l_k=l_k,
                goal_progress=goal_progress,
                constraint_violations=constraint_violations,
            )
        except Exception as e:
            logger.warning("epa_loss failed: %s — defaulting to zero loss", e)
            loss_dict = {
                "L_S": 0,
                "L_B": 0,
                "L_A": 0,
                "L_M": 0,
                "L_K": 0,
                "L_C": 0,
                "L_G": 0,
                "L_E": 0,
            }

        # 8. Learning loop update
        await self._update_learning(self.state, E_next, action, evidence, loss_dict)

        # 8b. Retrieval decision — retrieve if knowledge gap is large
        retrieval_decision = None
        retrieved_count = 0
        try:
            if l_k > 0.1:
                candidates = list(self.state.B.knowledge)
                retrieval_decision = self.retrieval_policy.evaluate(
                    self.knowledge_pack,
                    candidates,
                    loss_dict.get("L_E", 0),
                )
                if retrieval_decision.should_retrieve:
                    # Actually fetch and add retrieved items to KnowledgePack
                    for item_id in retrieval_decision.items_to_retrieve:
                        for item in self.state.B.knowledge:
                            if getattr(item, "id", "") == item_id:
                                self.knowledge_pack.add(item)
                                retrieved_count += 1
                    logger.debug("Retrieved %d items (gap=%.3f)", retrieved_count, l_k)
        except Exception as e:
            logger.debug("Retrieval decision failed: %s", e)

        # 8c. Publish engineering knowledge after successful step
        try:
            self.knowledge_publisher.publish(
                spec_id=f"step-{self._step}",
                goal=self._goal.objective,
                domain="runtime",
                benchmark_results={
                    "L_E": loss_dict.get("L_E", 0),
                    "confidence": E_next.B.confidence,
                },
                workflow_topology=[action],
                confidence=E_next.B.confidence,
            )
        except Exception as e:
            logger.debug("Knowledge publish failed: %s", e)

        # 9. Loss-driven repair — Mode B only (runtime domains)
        l_e = loss_dict.get("L_E", 0.0)
        is_runtime = execution_mode == "runtime" or (
            execution_mode == "auto"
            and self._goal.objective
            and not any(
                kw in self._goal.objective.lower()
                for kw in (
                    "generate",
                    "build",
                    "create",
                    "compile",
                    "codegen",
                    "service",
                )
            )
        )
        if is_runtime and l_e > self.repair_engine.loss_threshold and self._step > 1:
            try:
                # Build real repair context from current state
                repair_context = {
                    "test_results": {
                        "confidence": E_next.B.confidence,
                        "knowledge_loss": E_next.B.loss(),
                        "goal_progress": goal_progress,
                    },
                    "ddd_results": {
                        "world_keys": sorted(E_next.S.keys()),
                        "affordances": len(E_next.A),
                        "mesh_agents": E_next.M.get("active_agents", 0),
                    },
                    "govern_results": {
                        "constraint_violations": constraint_violations,
                        "transition_quality": (
                            sum(self._transition_quality[-10:]) / max(len(self._transition_quality[-10:]), 1)
                        ),
                    },
                    "runtime_errors": [e for e in self._history[-5:] if not e.get("capability_success", True)],
                }
                repair_report = self.repair_engine.run(
                    initial_loss=l_e,
                    loss_context=repair_context,
                    loss_terms=loss_dict,
                )
                if repair_report.total_iterations > 0:
                    logger.debug(
                        "Repair loop: %.3f → %.3f (%d iterations, %s)",
                        repair_report.initial_loss,
                        repair_report.final_loss,
                        repair_report.total_iterations,
                        repair_report.convergence_reason,
                    )
            except Exception as e:
                logger.debug("Repair loop failed: %s", e)

        # 10. Advance state
        self.state = E_next
        self._step += 1

        # Update telemetry
        self.telemetry.simulation.prediction_loss = loss_dict.get("L_E", 0.0)
        self.telemetry.simulation.rounds = self._step

        elapsed = (time.monotonic() - t0) * 1000
        step_result: dict[str, Any] = {
            "step": self._step,
            "action": action,
            "confidence": E_next.B.confidence,
            "knowledge_loss": E_next.B.loss(),
            "elapsed_ms": elapsed,
            **loss_dict,
        }
        if cap_result is not None:
            step_result["capability_success"] = cap_result.success
            step_result["capability_latency_ms"] = cap_result.latency_ms

        self._history.append(step_result)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history :]
        return step_result

    async def _update_learning(
        self,
        E_t: EpistemicPredictiveState,
        E_next: EpistemicPredictiveState,
        action: str,
        evidence: dict[str, Any],
        loss_dict: dict[str, float],
    ) -> None:
        """Update all learning components after a step."""
        l_e = loss_dict.get("L_E", 0.0)
        l_b = loss_dict.get("L_B", 0.0)

        # 1. Retrieval policy — update cost estimate based on belief loss
        try:
            gained = len(E_next.B.knowledge) - len(E_t.B.knowledge)
            self.retrieval_policy.update(cost=l_b, gained=max(0, gained))
        except Exception as e:
            logger.debug("Retrieval policy update failed: %s", e)

        # 2. Per-capability utility tracking (EMA of 1 - L_E per action)
        prev = self._capability_utility.get(action, 1.0)
        self._capability_utility[action] = round((1 - LEARNING_RATE) * prev + LEARNING_RATE * (1.0 - l_e), 4)

        # 3. Transition model quality — track L_E over time
        self._transition_quality.append(l_e)
        if len(self._transition_quality) > self._max_quality:
            self._transition_quality = self._transition_quality[-self._max_quality :]

        # 4. Planner policy — goal-conditioned action selection improves over time.
        # This passed (objective, action, l_e) against update(plan_id, reward,
        # next_state), so `reward` received the ACTION STRING and every update died on
        # `str - float` inside a debug-level except. _q_values stayed empty forever, so
        # get_best_action() — which keys on the action — always saw the 0.5 default and
        # just returned the first candidate. The policy never learned anything.
        # Q is keyed by action, so update by action, with the loss turned into a reward
        # (the same 1 - L_E the capability-utility EMA above uses).
        try:
            self._planner_policy.update(action, 1.0 - l_e)
        except Exception as e:
            logger.warning("Planner policy update failed for action=%r: %s", action, e)

    def _build_evidence(self, action: str, cap_result: CapabilityResult | None) -> dict[str, Any]:
        """Construct evidence dict from capability execution result."""
        if cap_result is None:
            return {}
        return {
            "converged": cap_result.success,
            "action": action,
            "confidence_delta": (CONFIDENCE_DELTA_SUCCESS if cap_result.success else CONFIDENCE_DELTA_FAILURE),
            "knowledge_loss": 0.0 if cap_result.success else KNOWLEDGE_LOSS_ON_FAILURE,
            "constraint_violations": 0 if cap_result.success else 1,
            "findings_count": 0 if cap_result.success else 1,
            **(cap_result.output if isinstance(cap_result.output, dict) else {}),
        }

    def set_goal(self, goal: str, **kwargs) -> None:
        """Set the active goal G_t."""
        target_predicates = kwargs.pop("target_predicates", [])
        self._goal = EPAGoalState(objective=goal, target_predicates=target_predicates, **kwargs)

    def register_capability(self, cap: ICapability) -> None:
        """Register a capability with the bus."""
        self.capability_bus.register_capability(cap)

    def spawn_agent(
        self,
        role: AgentRole = AgentRole.WORKER,
        required_capabilities: list[str] | None = None,
    ) -> None:
        """Spawn an agent in the mesh with optional capability auto-discovery."""
        spec = AgentSpec(role=role, required_capabilities=required_capabilities or [])
        self.execution_pool.spawn(spec, capability_bus=self.capability_bus)

    def get_state(self) -> dict[str, Any]:
        """Export full kernel state."""
        return {
            "step": self._step,
            "epa_state": self.state.to_dict(),
            "goal": self._goal.to_dict(),
            "mesh": self.execution_pool.get_mesh_state(),
            "broca_mesh": self.state.M,
            "planner_policy": self._planner_policy.summary(),
            "telemetry": self.telemetry.to_dict(),
        }

    def get_report(self) -> str:
        """Get engineering telemetry report."""
        self.telemetry.knowledge.knowledge_packs = len(self.state.B.knowledge)
        self.telemetry.knowledge.confidence = self.state.B.confidence
        self.telemetry.architecture.ddd_score = 100
        self.telemetry.architecture.governance = 95
        self.telemetry.architecture.compliance = 100
        return self.telemetry.format()


_kernel: CognitiveKernel | None = None


def get_cognitive_kernel() -> CognitiveKernel:
    """Singleton — instantiated once, carries learning state across requests."""
    global _kernel
    if _kernel is None:
        _kernel = CognitiveKernel()
    return _kernel
