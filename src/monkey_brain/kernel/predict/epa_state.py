"""EPA state types for the Predict–Execute–Learn loss loop.

PredictedEPAState   — produced by SolverMesh before execution (Ŝ, B̂, Â, M̂)
ObservedEPAState    — produced by epa_transition after execution (S, B, A, M)
EPALossVector       — per-component prediction error + composite loss
compute_epa_loss()  — computes the loss vector from predicted vs observed state

These are lightweight kernel-internal types.  They mirror the shape of
EpistemicPredictiveState (cortex/epa.py) but do not import it, keeping the
kernel free of cortex dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PredictedEPAState:
    """Expected cognitive state before execution, produced by SolverMesh.

    Ŝ   — predicted world state: node_id → expected completion probability
    B_hat — predicted belief (confidence, uncertainty)
    A_hat — predicted affordance set (capability names expected to be available)
    M_hat — predicted mesh state
    constraint_status — whether constraints are predicted to hold
    goal_progress     — predicted fraction of goal predicates satisfied
    knowledge_expectation — predicted knowledge quality signal
    """

    S_hat: dict[str, float] = field(default_factory=dict)
    B_hat: dict[str, float] = field(default_factory=dict)
    A_hat: set[str] = field(default_factory=set)
    M_hat: dict[str, Any] = field(default_factory=dict)
    constraint_status: bool = True
    goal_progress: float = 0.0
    knowledge_expectation: float = 0.5
    solver_contributions: dict[str, Any] = field(default_factory=dict)


@dataclass
class ObservedEPAState:
    """Actual cognitive state after execution, derived from ExecutionGraph.

    S   — actual world state: node_id → 1.0 (complete) / 0.0 (failed) / 0.5 (other)
    B   — actual belief: confidence, uncertainty
    A   — actual affordance set (capabilities that succeeded)
    M   — actual mesh state
    constraints   — constraint violations discovered during execution
    goal_progress — fraction of nodes that completed successfully
    knowledge     — knowledge quality signal from node results
    """

    S: dict[str, float] = field(default_factory=dict)
    B: dict[str, float] = field(default_factory=dict)
    A: set[str] = field(default_factory=set)
    M: dict[str, Any] = field(default_factory=dict)
    constraints: list[str] = field(default_factory=list)
    goal_progress: float = 0.0
    knowledge: float = 0.5


@dataclass
class EPALossVector:
    """Per-component prediction error between PredictedEPAState and ObservedEPAState.

    Each component measures how wrong the Solver Mesh prediction was.

    L_S — world state error    — mean |Ŝ[n] - S[n]| over all nodes
    L_B — belief error         — |B̂.confidence - B.confidence|
    L_A — affordance error     — Jaccard distance(Â, A)
    L_M — mesh state error     — mean |M̂[k] - M[k]|
    L_K — knowledge divergence — |knowledge_expectation - knowledge|
    L_C — constraint error     — 1.0 if constraints violated, else 0.0
    L_G — goal progress error  — |predicted_goal_progress - actual_goal_progress|
    composite — weighted sum (canonical adaptation signal for Fix phase)
    """

    L_S: float = 0.0
    L_B: float = 0.0
    L_A: float = 0.0
    L_M: float = 0.0
    L_K: float = 0.0
    L_C: float = 0.0
    L_G: float = 0.0
    composite: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return {
            "L_S": self.L_S,
            "L_B": self.L_B,
            "L_A": self.L_A,
            "L_M": self.L_M,
            "L_K": self.L_K,
            "L_C": self.L_C,
            "L_G": self.L_G,
            "composite": self.composite,
        }


# ── Loss computation ───────────────────────────────────────────────────────────

_WEIGHTS = {
    "L_S": 0.25,
    "L_B": 0.20,
    "L_A": 0.15,
    "L_M": 0.10,
    "L_K": 0.10,
    "L_C": 0.10,
    "L_G": 0.10,
}


def compute_epa_loss(
    predicted: PredictedEPAState,
    observed: ObservedEPAState,
) -> EPALossVector:
    """Compute per-component prediction error."""

    # L_S — world state: mean absolute error over shared node_ids
    shared_keys = set(predicted.S_hat) & set(observed.S)
    if shared_keys:
        L_S = sum(abs(predicted.S_hat[k] - observed.S[k]) for k in shared_keys) / len(shared_keys)
    elif predicted.S_hat or observed.S:
        L_S = 1.0  # completely different node sets
    else:
        L_S = 0.0

    # L_B — belief confidence error
    p_conf = predicted.B_hat.get("confidence", 0.5)
    o_conf = observed.B.get("confidence", 0.5)
    L_B = abs(p_conf - o_conf)

    # L_A — affordance Jaccard distance (1 - IoU)
    p_A = predicted.A_hat
    o_A = observed.A
    union = p_A | o_A
    L_A = 1.0 - (len(p_A & o_A) / len(union)) if union else 0.0

    # L_M — mesh state: mean absolute error over shared numeric keys
    shared_m = {
        k
        for k in set(predicted.M_hat) & set(observed.M)
        if isinstance(predicted.M_hat[k], (int, float)) and isinstance(observed.M[k], (int, float))
    }
    if shared_m:
        L_M = sum(abs(float(predicted.M_hat[k]) - float(observed.M[k])) for k in shared_m) / len(shared_m)
        L_M = min(L_M, 1.0)
    else:
        L_M = 0.0

    # L_K — knowledge divergence
    L_K = abs(predicted.knowledge_expectation - observed.knowledge)

    # L_C — constraint error
    L_C = 1.0 if observed.constraints else (0.0 if predicted.constraint_status else 0.5)

    # L_G — goal progress error
    L_G = abs(predicted.goal_progress - observed.goal_progress)

    # Composite (weighted sum)
    lv = EPALossVector(L_S=L_S, L_B=L_B, L_A=L_A, L_M=L_M, L_K=L_K, L_C=L_C, L_G=L_G)
    lv.composite = sum(_WEIGHTS[k] * getattr(lv, k) for k in _WEIGHTS)

    return lv


# ── Derivation helpers ────────────────────────────────────────────────────────


def observed_from_graph(graph: Any) -> ObservedEPAState:
    """Build ObservedEPAState directly from ExecutionGraph post-execution states."""
    from src.monkey_brain.kernel.execute.graph import NodeState

    nodes = graph.get_step_nodes()
    S: dict[str, float] = {}
    A: set[str] = set()
    total = len(nodes)
    complete = 0

    for node in nodes:
        state = graph.get_state(node.id)
        if state == NodeState.COMPLETE:
            S[node.id] = 1.0
            A.add(node.label)
            complete += 1
        elif state == NodeState.FAILED:
            S[node.id] = 0.0
        elif state == NodeState.SKIPPED:
            S[node.id] = 0.5
        else:
            S[node.id] = 0.3  # pending/running

    goal_progress = (complete / total) if total > 0 else 0.0
    confidence = goal_progress  # simple proxy

    return ObservedEPAState(
        S=S,
        B={"confidence": confidence, "uncertainty": 1.0 - confidence},
        A=A,
        M=(graph.get_execution_summary() if hasattr(graph, "get_execution_summary") else {}),
        constraints=[],
        goal_progress=goal_progress,
        knowledge=confidence,
    )
