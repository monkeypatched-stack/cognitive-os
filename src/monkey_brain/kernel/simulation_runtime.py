"""SimulationRuntime — independent runtime for the world-model simulation flow.

    Cognitive Runtime  | /plan | /execute  | /replay
    Simulation Runtime | /plan | /simulate | /replay
    Comparator Runtime | /plan | /compare  | /replay

Compile Intent is shared with CognitiveRuntime/ComparatorRuntime (see
plan.goals.compile.compile_intent) — classifying a question into a signed
IntentIR is the same operation no matter what runs afterward. This runtime
simulates the planner's canonical ExecutionGraph without rebuilding topology.
Lemon (observability) and PersistenceManager (memory management) are
Kernel-level dependencies injected via boot(), not built by this runtime
itself.
"""

from __future__ import annotations

import logging
from typing import Any

from src.monkey_brain.kernel.plan.goals.intent_ir import IntentIR, validate_intent_ir
from src.monkey_brain.persistence.events import EventType, PersistenceEvent
from src.monkey_brain.kernel.plan.goals.run_store import get_run_store
from src.monkey_brain.kernel.temporal_graph import (
    assert_same_topology,
    clone_graph_snapshot,
)

logger = logging.getLogger("agentos.simulation_runtime")


class SimulationRuntime:
    """Owns the Simulation Graph — predicted outcomes without side effects.

    Simulation Graph: what the system PREDICTS will happen.
    Execution Graph (CognitiveRuntime): what actually gets executed.
    ComparatorRuntime diffs them to produce loss for learning.
    """

    def __init__(self) -> None:
        self._event_bus: Any = None
        self.lemon: Any = None
        self.persistence: Any = None

    async def _publish(self, event_type: str, payload: dict) -> None:
        if self._event_bus is not None:
            await self._event_bus.publish(event_type, payload)

    @classmethod
    async def boot(
        cls,
        app: Any,
        *,
        lemon: Any = None,
        persistence: Any = None,
        event_bus: Any = None,
    ) -> "SimulationRuntime":
        """Attach Kernel-injected dependencies and register on app.state.

        No heavy subsystem wiring needed — simulate() takes db/mongo_client
        per call, so there's nothing else to stand up at boot time.
        """
        rt = cls()
        rt.lemon = lemon
        rt.persistence = persistence
        rt._event_bus = event_bus
        app.state.simulation_runtime = rt
        if lemon is not None:
            lemon.info("SimulationRuntime boot complete", component="bootstrap")
        logger.info("SimulationRuntime boot complete")
        await rt._publish("simulation_runtime.booted", {})
        return rt

    async def shutdown(self, app: Any) -> None:
        """No owned subsystems to tear down today (simulate() takes db/
        mongo_client per call, nothing is held open at boot time) — exists
        so Kernel.shutdown() can treat every runtime uniformly rather than
        assuming only CognitiveRuntime ever needs a shutdown path.
        """
        await self._publish("simulation_runtime.shutdown", {})
        logger.info("SimulationRuntime shutdown complete")

    async def compile_intent(
        self,
        question: str,
        *,
        run_id: str | None = None,
        lemon: Any = None,
        store: bool = True,
    ) -> IntentIR | None:
        from src.monkey_brain.kernel.plan.goals.compile import (
            compile_intent as _compile,
        )

        ir = await _compile(
            question,
            target="simulate",
            run_id=run_id,
            lemon=lemon or self.lemon,
            store=store,
        )
        await self._publish(
            "intent.compiled" if ir is not None else "intent.compile_failed",
            {
                "run_id": run_id,
                "target": "simulate",
                "intent_type": ir.intent_type if ir else None,
            },
        )
        return ir

    async def run(self, intent_ir: IntentIR, mongo_client: Any) -> dict[str, Any]:
        """Run simulation from the compiled IntentIR and execution graph."""
        from src.monkey_brain.runtime.simulation_pipeline import SimulationPipeline

        errors = validate_intent_ir(intent_ir)
        if errors:
            logger.warning(
                "run=%r SimulationRuntime.run: IntentIR validation failed: %s",
                intent_ir.run_id,
                "; ".join(errors),
            )
            await self._publish("simulation.failed", {"run_id": intent_ir.run_id, "errors": errors})
            return {"error": "intent_ir validation failed", "detail": errors}

        question = intent_ir.execution_metadata.get("question", "")
        logger.info("[simulation] Running SimulationPipeline for question: %s", question[:50])

        graph_source = get_run_store().get_graph(intent_ir.run_id)
        if graph_source is None:
            await self._publish(
                "simulation.failed",
                {"run_id": intent_ir.run_id, "error": "missing execution graph"},
            )
            return {
                "error": "missing execution graph",
                "detail": "Simulation requires the planner's ExecutionGraph stored by run_id",
            }

        # 1. Normalize the execution graph snapshot to ensure it has the expected structure and fields.
        execution_graph = self._normalize_execution_graph(clone_graph_snapshot(graph_source))

        # 2. Build a SimulationPipeline with the normalized execution graph and run the simulation.
        pipeline = SimulationPipeline(execution_graph=execution_graph)
        sim_result = await pipeline.simulate(intent_ir)
        logger.info(
            "[simulation] Pipeline returned: capability=%s, predictions=%d",
            sim_result.capability,
            len(sim_result.predictions),
        )

        # 3. Convert to dict for API response.
        result = sim_result.to_dict()
        result["run_id"] = intent_ir.run_id
        result["simulation_id"] = f"sim-{intent_ir.run_id[:12]}"
        result["execution_graph"] = execution_graph
        result["intent_ir"] = intent_ir.to_dict()
        result["intent"] = {
            "intent": intent_ir.intent_type,
            "confidence": intent_ir.confidence,
            "workload_id": intent_ir.parameters.get("workload_id", ""),
        }
        result["context"] = {
            "question": question,
            "goal_type": intent_ir.execution_metadata.get("goal_type", ""),
            "run_id": intent_ir.run_id,
        }

        simulation_graph = sim_result.simulation_graph
        simulation_graph["state"] = {
            "predicted_state": sim_result.simulation_graph.get("metadata", {})
            .get("summary", {})
            .get("predicted_state", {}),
            "predicted_outcome": {
                "capability": sim_result.capability,
                "implementation": sim_result.implementation,
            },
            "confidence": sim_result.grounding_score,
            "run_id": intent_ir.run_id,
            "question": question,
        }

        # 4. Assert that the simulation graph has the same topology as the execution graph. This is a sanity check to ensure that the simulation did not alter the structure of the graph.
        assert_same_topology(execution_graph, simulation_graph, context="/simulate")

        # 5. Persist the simulation snapshot to the database for future reference or analysis.
        await self._persist_snapshot(
            mongo_client=mongo_client,
            snapshot=simulation_graph,
            run_id=intent_ir.run_id,
            question=question,
        )

        # 6. Publish an event indicating that the simulation run has completed, including the run_id for tracking.
        await self._publish("simulation.run", {"run_id": intent_ir.run_id})
        return result

    async def _persist_snapshot(
        self, mongo_client: Any, snapshot: dict[str, Any], *, run_id: str, question: str
    ) -> None:
        if self.persistence is None:
            return
        event = PersistenceEvent(
            event_type=EventType.METRIC_RECORDED,
            entity_type="simulation_snapshot",
            entity_id=run_id,
            data={
                "run_id": run_id,
                "question": question,
                "graph_id": snapshot.get("graph_id", ""),
                "timestamp": snapshot.get("timestamp", ""),
                "snapshot": snapshot,
                "source": "simulation_runtime",
            },
            source="simulation_runtime",
        )
        await self.persistence.persist(event)

    def _normalize_execution_graph(self, graph_source: Any) -> dict[str, Any]:
        if hasattr(graph_source, "to_dict"):
            graph_source = graph_source.to_dict()
        graph_source = graph_source or {}
        if not isinstance(graph_source, dict):
            raise TypeError(f"execution graph must be a dict-like artifact, got {type(graph_source)!r}")

        nodes = []
        for node in graph_source.get("nodes", []):
            if isinstance(node, dict):
                normalized = dict(node)
            else:
                normalized = {
                    "id": getattr(node, "id", ""),
                    "type": getattr(node, "type", ""),
                    "label": getattr(node, "label", ""),
                    "state": getattr(node, "state", "pending"),
                    "annotations": dict(getattr(node, "annotations", {})),
                }
            normalized.setdefault("id", "")
            normalized.setdefault("type", "step")
            normalized.setdefault("label", normalized["id"])
            normalized.setdefault("state", "pending")
            nodes.append(normalized)

        edges = []
        for edge in graph_source.get("edges", []):
            if isinstance(edge, dict):
                normalized = dict(edge)
            else:
                normalized = {
                    "from": getattr(edge, "from", getattr(edge, "src", "")),
                    "to": getattr(edge, "to", getattr(edge, "dst", "")),
                    "type": getattr(edge, "type", getattr(edge, "rel", "depends_on")),
                }
            edges.append(
                {
                    "from": normalized.get("from") or normalized.get("src", ""),
                    "to": normalized.get("to") or normalized.get("dst", ""),
                    "type": normalized.get("type") or normalized.get("rel", "depends_on"),
                }
            )

        metadata = dict(graph_source.get("metadata", {}))
        metadata.setdefault("graph_id", graph_source.get("graph_id", metadata.get("graph_id", "")))
        execution_order = self._normalize_execution_order(
            graph_source.get("execution_order", metadata.get("execution_order", []))
        )
        metadata.setdefault("execution_order", execution_order)
        return {
            "graph_id": graph_source.get("graph_id", metadata.get("graph_id", "")),
            "graph_type": graph_source.get("graph_type", metadata.get("graph_type", "execution")),
            "timestamp": graph_source.get("timestamp", metadata.get("timestamp", "")),
            "solver": graph_source.get("solver", metadata.get("solver", "")),
            "nodes": nodes,
            "edges": edges,
            "execution_order": execution_order,
            "state": dict(graph_source.get("state", metadata.get("state", {}))),
            "annotations": dict(graph_source.get("annotations", metadata.get("annotations", {}))),
            "metadata": metadata,
        }

    def _normalize_execution_order(self, order: Any) -> list[list[str]]:
        """Preserve nested list structure for execution_order."""
        if not isinstance(order, list):
            return [[str(order)] if order is not None else []]
        result: list[list[str]] = []
        for item in order:
            if isinstance(item, list):
                result.append([str(n) for n in item])
            elif isinstance(item, str):
                result.append([item])
            elif item is not None:
                result.append([str(item)])
        return result

    async def replay(self, run_id: str, mongo_client: Any) -> dict[str, Any]:
        from src.monkey_brain.kernel.plan.goals.run_store import replay as _replay

        result = await _replay(run_id, mongo_client)
        await self._publish("run.replayed", {"run_id": run_id, "target": "simulate"})
        return result

    def has_stored_run(self, run_id: str) -> bool:
        from src.monkey_brain.kernel.plan.goals.run_store import get_run_store

        return get_run_store().has(run_id)


def get_simulation_runtime() -> SimulationRuntime:
    from src.monkey_brain.kernel.kernel import Kernel

    kernel = Kernel._instance
    try:
        runtime = kernel.runtime_selector.select("simulation") if kernel is not None else None
    except LookupError:
        runtime = None
    if runtime is None:
        raise RuntimeError("SimulationRuntime is not booted; resolve it through Kernel boot")
    return runtime
