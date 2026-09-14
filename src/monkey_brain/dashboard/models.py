"""DashboardModel — canonical cognitive state representation.

Every field in this model is computed by the backend from existing
runtime artifacts (IntentIR, ExecutionGraph, SimulationGraph, etc.).
No frontend should derive or compute any of these values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── Graph View ────────────────────────────────────────────────────────────────


@dataclass
class GraphNodeState:
    node_id: str
    agent: str
    state: str = "pending"
    reward: float = 0.0
    latency_ms: float = 0.0
    error: str = ""


@dataclass
class GraphEdge:
    src: str
    dst: str
    rel: str = "depends_on"


@dataclass
class GraphView:
    graph_id: str = ""
    nodes: list[GraphNodeState] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    execution_order: list[list[str]] = field(default_factory=list)
    node_count: int = 0
    edge_count: int = 0
    depth: int = 0


# ── Planning Section ─────────────────────────────────────────────────────────


@dataclass
class PlanningSection:
    intent: str = ""
    intent_confidence: float = 0.0
    selected_workload: str = ""
    selected_capabilities: list[str] = field(default_factory=list)
    selected_agents: list[str] = field(default_factory=list)
    planner_latency_ms: float = 0.0
    graph_id: str = ""
    node_count: int = 0
    edge_count: int = 0
    execution_layers: int = 0


# ── Execution Section ────────────────────────────────────────────────────────


@dataclass
class ExecutionSection:
    completed_nodes: int = 0
    running_nodes: int = 0
    failed_nodes: int = 0
    total_nodes: int = 0
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    execution_latency_ms: float = 0.0
    llm_answered: bool = False


# ── Simulation Section ───────────────────────────────────────────────────────


@dataclass
class SolverResult:
    solver_name: str
    prediction: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    execution_time_ms: float = 0.0


@dataclass
class SimulationSection:
    active_solvers: list[str] = field(default_factory=list)
    solver_results: list[SolverResult] = field(default_factory=list)
    consensus_score: float = 0.0
    disagreement_score: float = 0.0
    predicted_node_states: dict[str, str] = field(default_factory=dict)
    predicted_rewards: dict[str, float] = field(default_factory=dict)
    grounding_score: float = 0.0
    feasibility_verdict: str = "unknown"


# ── Learning Section ─────────────────────────────────────────────────────────


@dataclass
class LearningSection:
    epistemic_loss: float = 0.0
    topological_loss: float = 0.0
    raw_loss: float = 0.0
    perplexity: float = 0.0
    bellman_q: float = 0.0
    td_error: float = 0.0
    replay_size: int = 0
    policy_updates: int = 0
    converged: bool = False


# ── Mutation Timeline ────────────────────────────────────────────────────────


@dataclass
class Mutation:
    timestamp: str = ""
    runtime: str = ""
    actor: str = ""
    node: str = ""
    operation: str = ""
    before: str = ""
    after: str = ""
    reason: str = ""


# ── Bellman Section ──────────────────────────────────────────────────────────


@dataclass
class BellmanSection:
    q_table_entries: int = 0
    current_state: str = ""
    selected_action: str = ""
    reward: float = 0.0
    td_error: float = 0.0
    learning_rate: float = 0.0
    discount_factor: float = 0.0
    policy_confidence: float = 0.0
    q_values: dict[str, float] = field(default_factory=dict)


# ── Consensus Section ────────────────────────────────────────────────────────


@dataclass
class ConsensusSection:
    participating_solvers: list[str] = field(default_factory=list)
    rejected_solvers: list[str] = field(default_factory=list)
    consensus_score: float = 0.0
    disagreement_score: float = 0.0
    byzantine_agreement: float = 0.0


# ── Health Section ───────────────────────────────────────────────────────────


@dataclass
class ComponentHealth:
    name: str
    status: str = "unknown"
    latency_ms: float = 0.0
    message: str = ""


@dataclass
class HealthSection:
    components: list[ComponentHealth] = field(default_factory=list)
    overall_status: str = "unknown"


# ── Metrics Section ──────────────────────────────────────────────────────────


@dataclass
class MetricsSection:
    planner_latency_ms: float = 0.0
    execution_latency_ms: float = 0.0
    simulation_latency_ms: float = 0.0
    learning_latency_ms: float = 0.0
    total_runtime_ms: float = 0.0
    event_count: int = 0
    mutation_count: int = 0
    graph_snapshots: int = 0
    replay_buffer_size: int = 0
    policy_updates: int = 0
    counters: dict[str, int] = field(default_factory=dict)
    gauges: dict[str, float] = field(default_factory=dict)
    histograms: dict[str, dict[str, float]] = field(default_factory=dict)


# ── Observability Section ────────────────────────────────────────────────────


@dataclass
class ObservabilitySection:
    traces: list[dict[str, Any]] = field(default_factory=list)
    recent_logs: list[dict[str, Any]] = field(default_factory=list)
    alerts: list[dict[str, Any]] = field(default_factory=list)


# ── Timeline Section ─────────────────────────────────────────────────────────


@dataclass
class TimelineEntry:
    timestamp: str = ""
    phase: str = ""
    event: str = ""
    details: str = ""


# ── DashboardModel ───────────────────────────────────────────────────────────


@dataclass
class DashboardModel:
    run_id: str = ""
    graph_id: str = ""
    timestamp: str = ""
    status: str = "idle"

    planning: PlanningSection = field(default_factory=PlanningSection)
    execution: ExecutionSection = field(default_factory=ExecutionSection)
    simulation: SimulationSection = field(default_factory=SimulationSection)
    learning: LearningSection = field(default_factory=LearningSection)
    mutations: list[Mutation] = field(default_factory=list)
    bellman: BellmanSection = field(default_factory=BellmanSection)
    consensus: ConsensusSection = field(default_factory=ConsensusSection)
    graph: GraphView = field(default_factory=GraphView)
    observability: ObservabilitySection = field(default_factory=ObservabilitySection)
    health: HealthSection = field(default_factory=HealthSection)
    metrics: MetricsSection = field(default_factory=MetricsSection)
    timeline: list[TimelineEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        def _dc(obj: Any) -> Any:
            if hasattr(obj, "__dataclass_fields__"):
                return {k: _dc(v) for k, v in obj.__dict__.items()}
            if isinstance(obj, list):
                return [_dc(i) for i in obj]
            if isinstance(obj, dict):
                return {k: _dc(v) for k, v in obj.items()}
            return obj

        return _dc(self)
