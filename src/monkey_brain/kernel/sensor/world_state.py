"""World-State Estimation — builds multimodal state vectors from execution results.

After each execution, the estimator fuses node outcomes, execution metadata,
and sensor observations into a state vector that feeds into the world tensor.
This gives the tensor richer signal for next-action prediction.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass


from src.monkey_brain.kernel.sensor.fusion import (
    SensorFusion,
    ModalityObservation,
    FusedState,
)

logger = logging.getLogger("agentos.world_state")


@dataclass
class ExecutionOutcome:
    """Result of executing a node in the graph."""

    node_id: str
    agent: str
    success: bool
    reward: float = 0.5
    latency_ms: float = 0.0
    error: str = ""


class WorldStateEstimator:
    """Builds multimodal state vectors from execution outcomes.

    Fuses execution results (success/failure, reward, latency) with
    node-level sensor observations into a state vector that represents
    the world after execution.  The vector feeds into the tensor for
    improved next-action prediction.
    """

    def __init__(self, fusion: SensorFusion | None = None):
        self._fusion = fusion or SensorFusion()

    def estimate(
        self,
        outcomes: list[ExecutionOutcome],
        question: str = "",
        simulation_graph: dict | None = None,
    ) -> FusedState:
        """Estimate world state from execution outcomes.

        Creates modality observations from:
        - Document: the question/query
        - Graph: node execution results (structure + outcomes)
        - Telemetry: timing and success rates
        """
        observations = []

        # Document: question
        if question:
            observations.append(ModalityObservation(modality="document", content=question, confidence=0.7))

        # Graph: node outcomes
        if outcomes:
            success_count = sum(1 for o in outcomes if o.success)
            total = len(outcomes)
            graph_desc = " ".join(f"{o.agent}:{'ok' if o.success else 'fail'}" for o in outcomes)
            observations.append(
                ModalityObservation(
                    modality="graph",
                    content=graph_desc,
                    confidence=success_count / max(total, 1),
                )
            )

        # Telemetry: timing and success rate
        if outcomes:
            avg_latency = sum(o.latency_ms for o in outcomes) / len(outcomes)
            success_rate = sum(1 for o in outcomes if o.success) / len(outcomes)
            observations.append(
                ModalityObservation(
                    modality="telemetry",
                    content=f"latency={avg_latency:.0f}ms success={success_rate:.2f}",
                    confidence=0.8,
                )
            )

        # Simulation graph: predicted vs actual
        if simulation_graph:
            nodes = simulation_graph.get("nodes", []) if isinstance(simulation_graph, dict) else []
            if nodes:
                sim_desc = " ".join(n.get("agent", n.get("name", "")) for n in nodes if isinstance(n, dict))
                observations.append(
                    ModalityObservation(
                        modality="simulation",
                        content=sim_desc,
                        confidence=0.6,
                    )
                )

        return self._fusion.fuse(observations)
