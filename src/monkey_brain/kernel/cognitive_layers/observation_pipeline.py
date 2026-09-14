"""Observation Pipeline - Single Responsibility.

Responsibility: Sensor fusion, world state estimation, observation aggregation.
Depends on: sensor fusion, world state estimator, trust network, world coordinator
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("agentos.cognitive.layers.observation_pipeline")


class ObservationPipeline:
    """Sensor fusion, world estimation, and observation aggregation.

    Responsibility: Fuse multi-modal observations from simulation and execution,
    estimate world state with trust weighting, aggregate policy deltas.
    """

    def __init__(self) -> None:
        self._trust_network: Any = None

    def fuse_observations(
        self,
        question: str,
        simulation_graph: dict,
        publish_world_update_fn: Any = None,
    ) -> Any:
        """Fuse multi-modal observations from simulation into a FusedState.

        Best-effort: fusion failures never block execution.
        """
        try:
            from src.monkey_brain.kernel.sensor.fusion import (
                SensorFusion,
                ModalityObservation,
            )

            observations = []

            if question:
                observations.append(ModalityObservation(modality="document", content=question, confidence=0.7))

            nodes = simulation_graph.get("nodes", []) if isinstance(simulation_graph, dict) else []
            if nodes:
                graph_desc = " ".join(
                    n.get("agent", n.get("name", n.get("id", ""))) for n in nodes if isinstance(n, dict)
                )
                if graph_desc:
                    observations.append(ModalityObservation(modality="graph", content=graph_desc, confidence=0.6))

            metadata = simulation_graph.get("metadata", {}) if isinstance(simulation_graph, dict) else {}
            summary = metadata.get("summary", {})
            if summary:
                telemetry = (
                    f"grounding={summary.get('grounding_score', 0)} "
                    f"feasibility={summary.get('feasibility_verdict', 'unknown')}"
                )
                observations.append(ModalityObservation(modality="telemetry", content=telemetry, confidence=0.8))

            if not observations:
                return None

            fusion = SensorFusion()
            result = fusion.fuse(observations)

            if result.fusion_confidence > 0 and publish_world_update_fn:
                publish_world_update_fn(
                    "fused_state",
                    question[:50],
                    domain="sensor",
                    weight=result.fusion_confidence,
                    source="sensor_fusion",
                )

            return result
        except Exception as exc:
            logger.debug("[sensor] fusion skipped: %s", exc)
            return None

    def estimate_world_state(
        self,
        observations: list[dict],
        question: str,
        simulation_graph: dict | None,
        publish_world_update_fn: Any = None,
    ) -> None:
        """Estimate world state from execution observations with trust weighting.

        W' = f(W, O, T) where T is agent trust from the fabric.
        Best-effort: estimation failures never block execution.
        """
        if not observations:
            return
        try:
            from src.monkey_brain.kernel.sensor.world_state import (
                WorldStateEstimator,
                ExecutionOutcome,
            )

            outcomes = [
                ExecutionOutcome(
                    node_id=obs.get("node_id", obs.get("agent", "")),
                    agent=obs.get("agent", ""),
                    success=obs.get("status") == "complete" or obs.get("success", False),
                    reward=obs.get("reward", 0.5),
                    latency_ms=obs.get("latency_ms", 0.0),
                    error=obs.get("error", ""),
                )
                for obs in observations
                if isinstance(obs, dict)
            ]

            if not outcomes:
                return

            estimator = WorldStateEstimator()
            state = estimator.estimate(outcomes, question, simulation_graph)

            if self._trust_network is None:
                from src.monkey_brain.kernel.compile.trust import TrustNetwork

                self._trust_network = TrustNetwork()

            if state.fusion_confidence > 0 and publish_world_update_fn:
                for outcome in outcomes:
                    agent_name = outcome.agent or outcome.node_id
                    if not agent_name:
                        continue
                    agent_trust = self._trust_network.trust("self", agent_name)
                    publish_world_update_fn(
                        agent_name,
                        "exec_state",
                        domain="execution",
                        weight=outcome.reward if outcome.success else -0.5,
                        source="world_state_estimator",
                        metadata={
                            "latency_ms": outcome.latency_ms,
                            "trust": agent_trust,
                        },
                    )

        except Exception as exc:
            logger.debug("[world_state] estimation with trust weighting skipped: %s", exc)

    def aggregate_policy_delta(self, all_observations: list[dict]) -> dict[str, float]:
        """Aggregate per-agent reward signals from all batch observations."""
        policy_delta: dict[str, float] = {}
        for obs in all_observations:
            agent = obs.get("agent", "")
            reward = obs.get("reward", 0.5)
            if agent:
                policy_delta[agent] = max(policy_delta.get(agent, 0), reward)
        return policy_delta

    def derive_policy_delta(self, act_result: dict, loss_result: dict) -> dict[str, float]:
        """Derive per-agent reward signals from execution and comparison."""
        delta: dict[str, float] = {}
        comparison_reward = loss_result.get("reward", 0.5)

        for obs in act_result.get("observations", []):
            agent = obs.get("agent", "")
            if not agent:
                continue
            reward = obs.get("reward", 0.5)
            if obs.get("status") == "complete":
                reward = max(reward, 0.7)
            elif obs.get("status") == "failed":
                reward = min(reward, 0.2)
            delta[agent] = (reward + comparison_reward) / 2

        return delta
