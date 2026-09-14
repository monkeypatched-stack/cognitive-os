"""DashboardService — assembles DashboardModel from runtime artifacts.

The service reads from:
- RunStore (IntentIR, ExecutionGraph)
- SimulationRuntime (SimulationGraph)
- ComparatorRuntime (ComparisonResult)
- GraphManager (Q-table, learning state)
- Lemon (metrics, traces, logs, health)

No data is computed here — it is read and organized.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from src.monkey_brain.dashboard.models import (
    DashboardModel,
    PlanningSection,
    ExecutionSection,
    SimulationSection,
    LearningSection,
    Mutation,
    BellmanSection,
    ConsensusSection,
    GraphView,
    GraphNodeState,
    GraphEdge,
    ComponentHealth,
    HealthSection,
    MetricsSection,
    ObservabilitySection,
    TimelineEntry,
    SolverResult,
)

logger = logging.getLogger("agentos.dashboard")


def _gauge(gauges: dict, prefix: str) -> float:
    for k, v in gauges.items():
        if k.startswith(prefix):
            return float(v)
    return 0.0


def _counter(counters: dict, prefix: str) -> int:
    for k, v in counters.items():
        if k.startswith(prefix):
            return int(v)
    return 0


class DashboardService:
    """Assembles a complete DashboardModel from runtime state."""

    def __init__(self, app: Any = None):
        self._app = app

    def build(self, run_id: str | None = None) -> DashboardModel:
        from src.monkey_brain.kernel.plan.goals.run_store import get_run_store
        from src.introspection.lemon import get_lemon

        store = get_run_store()
        lemon = get_lemon()
        model = DashboardModel()

        if not run_id and store.run_ids():
            run_id = store.run_ids()[-1]

        model.run_id = run_id or ""
        model.timestamp = _now_iso()

        if run_id:
            stored = store.get_run(run_id)
            if stored:
                model.graph_id = stored.graph_snapshot.get("graph_id", "") if stored.graph_snapshot else ""
                model.planning = self._build_planning(stored, run_id)
                model.graph = self._build_graph(stored)

        model.execution = self._build_execution(store)
        model.simulation = self._build_simulation(store)
        model.learning = self._build_learning(store)
        model.bellman = self._build_bellman(store)
        model.mutations = self._build_mutations(store)
        model.timeline = self._build_timeline(store)

        if lemon:
            model.health = self._build_health(lemon)
            model.metrics = self._build_metrics(lemon)
            model.observability = self._build_observability(lemon)
            model.consensus = self._build_consensus(lemon)
        else:
            model.health = HealthSection(overall_status="degraded")
            model.metrics = MetricsSection()

        model.status = "ready" if model.graph_id else "idle"
        return model

    def _build_planning(self, stored: Any, run_id: str) -> PlanningSection:
        snap = stored.graph_snapshot or {}
        nodes = snap.get("nodes", [])
        edges = snap.get("edges", [])
        order = snap.get("execution_order", [])
        agents = [n.get("agent", n.get("name", "")) for n in nodes if n.get("agent") or n.get("name")]

        return PlanningSection(
            intent=stored.intent_ir.intent_type if stored.intent_ir else "",
            intent_confidence=stored.intent_ir.confidence if stored.intent_ir else 0.0,
            selected_workload=snap.get("workload_id", ""),
            selected_capabilities=list(set(agents)),
            selected_agents=agents,
            planner_latency_ms=0.0,
            graph_id=snap.get("graph_id", ""),
            node_count=len(nodes),
            edge_count=len(edges),
            execution_layers=len(order),
        )

    def _build_graph(self, stored: Any) -> GraphView:
        snap = stored.graph_snapshot or {}
        nodes = snap.get("nodes", [])
        edges = snap.get("edges", [])
        order = snap.get("execution_order", [])

        node_states = []
        for n in nodes:
            node_states.append(
                GraphNodeState(
                    node_id=n.get("id", ""),
                    agent=n.get("agent", n.get("name", "")),
                    state=n.get("state", "pending"),
                )
            )

        graph_edges = []
        for e in edges:
            graph_edges.append(
                GraphEdge(
                    src=e.get("from", ""),
                    dst=e.get("to", ""),
                    rel=e.get("type", "depends_on"),
                )
            )

        depth = max(len(order), 1) if order else 0

        return GraphView(
            graph_id=snap.get("graph_id", ""),
            nodes=node_states,
            edges=graph_edges,
            execution_order=order,
            node_count=len(nodes),
            edge_count=len(edges),
            depth=depth,
        )

    def _build_execution(self, store: Any) -> ExecutionSection:
        graph = None
        for run_id in reversed(store.run_ids()):
            stored = store.get_run(run_id)
            if stored and stored.graph_snapshot and stored.target == "execute":
                graph = stored.graph_snapshot
                break
        if graph is None:
            return ExecutionSection()

        nodes = graph.get("nodes", [])
        completed = sum(1 for n in nodes if n.get("state") == "complete")
        failed = sum(1 for n in nodes if n.get("state") == "failed")
        running = sum(1 for n in nodes if n.get("state") == "running")

        return ExecutionSection(
            completed_nodes=completed,
            running_nodes=running,
            failed_nodes=failed,
            total_nodes=len(nodes),
            execution_latency_ms=0.0,
        )

    def _build_simulation(self, store: Any) -> SimulationSection:
        graph = None
        for run_id in reversed(store.run_ids()):
            stored = store.get_run(run_id)
            if stored and stored.graph_snapshot and stored.target == "simulate":
                graph = stored.graph_snapshot
                break
        if graph is None:
            return SimulationSection()

        meta = graph.get("metadata", {})
        solver_contributions = meta.get("solver_contributions", [])
        summary = meta.get("summary", {})

        solver_results = []
        for sc in solver_contributions:
            solver_results.append(
                SolverResult(
                    solver_name=sc.get("solver", ""),
                    prediction=sc.get("predicted_state", {}),
                    confidence=sc.get("confidence", 0.0),
                    execution_time_ms=sc.get("latency_ms", 0.0),
                )
            )

        return SimulationSection(
            active_solvers=[s.get("solver", "") for s in solver_contributions],
            solver_results=solver_results,
            consensus_score=meta.get("consensus_score", 0.0),
            disagreement_score=meta.get("disagreement_score", 0.0),
            predicted_node_states=summary.get("predicted_state", {}),
            predicted_rewards={},
            grounding_score=summary.get("grounding_score", 0.0),
            feasibility_verdict=summary.get("feasibility_verdict", "unknown"),
        )

    def _build_learning(self, store: Any) -> LearningSection:
        try:
            from src.monkey_brain.kernel.graph_manager import GraphManager

            gm = GraphManager()
            gm.load()
            q_values = list(gm.q_table.snapshot().values())
            avg_q = sum(q_values) / max(len(q_values), 1) if q_values else 0.5
            return LearningSection(
                epistemic_loss=1.0 - avg_q,
                topological_loss=0.0,
                raw_loss=gm._last_loss,
                perplexity=gm._last_loss,
                bellman_q=avg_q,
                td_error=0.0,
                replay_size=len(gm.q_table._replay_buffer),
                policy_updates=gm._epoch_count,
                converged=gm.converged(),
            )
        except Exception:
            return LearningSection()

    def _build_bellman(self, store: Any) -> BellmanSection:
        try:
            from src.monkey_brain.kernel.graph_manager import GraphManager

            gm = GraphManager()
            gm.load()
            q_values = gm.q_table.snapshot()
            avg_q = sum(q_values.values()) / max(len(q_values), 1) if q_values else 0.5
            best_action = max(q_values, key=q_values.get) if q_values else ""
            return BellmanSection(
                q_table_entries=len(q_values),
                current_state="current",
                selected_action=best_action,
                reward=avg_q,
                td_error=0.0,
                learning_rate=gm.q_table._learning_rate,
                discount_factor=gm.q_table._discount,
                policy_confidence=avg_q,
                q_values=q_values,
            )
        except Exception:
            return BellmanSection()

    def _build_mutations(self, store: Any) -> list[Mutation]:
        mutations = []
        for run_id in reversed(store.run_ids()[-5:]):
            stored = store.get_run(run_id)
            if not stored:
                continue
            snap = stored.graph_snapshot or {}
            target = stored.target
            mutations.append(
                Mutation(
                    timestamp=snap.get("metadata", {}).get("timestamp", ""),
                    runtime=target,
                    actor="planner" if target == "execute" else "simulator",
                    node="",
                    operation=("ExecutionGraph Created" if target == "execute" else "SimulationGraph Created"),
                    before="",
                    after=snap.get("graph_id", ""),
                    reason=f"Graph stored for {target}",
                )
            )
        return mutations

    def _build_timeline(self, store: Any) -> list[TimelineEntry]:
        entries = []
        for run_id in reversed(store.run_ids()[-10:]):
            stored = store.get_run(run_id)
            if not stored:
                continue
            snap = stored.graph_snapshot or {}
            target = stored.target
            entries.append(
                TimelineEntry(
                    timestamp=snap.get("metadata", {}).get("timestamp", ""),
                    phase=target,
                    event=f"{target} graph created",
                    details=f"graph_id={snap.get('graph_id', '')}",
                )
            )
        return entries

    def _build_health(self, lemon: Any) -> HealthSection:
        health = lemon.health.summary()
        checks = health.get("checks", {})
        components = []
        for name, status in checks.items():
            components.append(
                ComponentHealth(
                    name=name,
                    status=(status.get("status", "unknown") if isinstance(status, dict) else str(status)),
                )
            )
        overall = health.get("overall", "unknown")
        return HealthSection(components=components, overall_status=overall)

    def _build_metrics(self, lemon: Any) -> MetricsSection:
        metrics = lemon.metrics.export()
        counters = metrics.get("counters", {})
        gauges = metrics.get("gauges", {})
        histograms = metrics.get("histograms", {})

        return MetricsSection(
            planner_latency_ms=_gauge(gauges, "api.plan.latency"),
            execution_latency_ms=_gauge(gauges, "runtime.workload.total_latency"),
            simulation_latency_ms=_gauge(gauges, "sim.latency"),
            learning_latency_ms=_gauge(gauges, "api.learn.elapsed"),
            total_runtime_ms=_gauge(gauges, "runtime.workload.total_latency"),
            event_count=sum(counters.values()),
            mutation_count=_counter(counters, "plan.agents_selected"),
            graph_snapshots=1 if counters else 0,
            replay_buffer_size=0,
            policy_updates=_counter(counters, "policy.updates"),
            counters={k: int(v) for k, v in counters.items()},
            gauges={k: float(v) for k, v in gauges.items()},
            histograms={k: v for k, v in histograms.items()},
        )

    def _build_observability(self, lemon: Any) -> ObservabilitySection:
        traces = [t.to_dict() for t in list(lemon.tracer._traces.values())[-5:]]
        logs = [e.to_dict() for e in lemon.logger.get_entries(limit=20)]
        alerts = [a.to_dict() for a in lemon.alerts.get_active()]
        return ObservabilitySection(
            traces=traces,
            recent_logs=logs,
            alerts=alerts,
        )

    def _build_consensus(self, lemon: Any) -> ConsensusSection:
        return ConsensusSection(
            participating_solvers=[],
            rejected_solvers=[],
            consensus_score=0.0,
            disagreement_score=0.0,
            byzantine_agreement=0.0,
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
