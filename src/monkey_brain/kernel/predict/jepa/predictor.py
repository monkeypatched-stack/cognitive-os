"""JEPAPredictor — wraps JEPAWorldModel with ExecutionGraph-aware inputs.

JEPAWorldModel.infer_epistemic() takes raw dicts (S, B, A, G, M).
JEPAPredictor bridges from ExecutionGraph + EPA state to those dicts,
calls the model, and converts the result to a typed JEPAPrediction.

Inputs to JEPAPredictor.predict():
  graph        — ExecutionGraph (current node states)
  epa_state    — PredictedEPAState from previous predict phase (optional)
  action       — the planned action string (default "execute")
  goal         — goal object or string (optional)
  mesh         — mesh summary dict (optional)
  history      — list of previous world state dicts (optional; for training)

Output:
  JEPAPrediction — typed prediction with per-component signals
"""

from __future__ import annotations

import logging
from typing import Any

from src.monkey_brain.kernel.predict.jepa.prediction import JEPAPrediction

logger = logging.getLogger(__name__)


class JEPAPredictor:
    """Wraps JEPAWorldModel with graph-aware I/O translation."""

    def __init__(self, model: Any) -> None:
        self._model = model

    def predict(
        self,
        graph: Any,
        *,
        epa_state: Any = None,
        action: str = "execute",
        goal: Any = None,
        mesh: dict | None = None,
        history: list[dict] | None = None,
    ) -> JEPAPrediction:
        """Translate ExecutionGraph → JEPA inputs → JEPAPrediction."""
        S = _graph_to_world_state(graph)
        B = _epa_to_belief(epa_state)
        A = _graph_to_affordances(graph, epa_state)
        G = _goal_to_dict(goal)
        M = mesh or {}

        try:
            result = self._model.infer_epistemic(S, action, B=B, A=A, G=G, M=M)
        except Exception as e:
            logger.debug("[jepa] infer_epistemic failed: %s", e)
            return JEPAPrediction()

        return _build_prediction(graph, S, A, result)


def _graph_to_world_state(graph: Any) -> dict[str, float]:
    """Map ExecutionGraph node states to a world state dict for JEPA encoding.

    JEPA encodes dicts of numeric values.  We represent each node as a scalar
    completion probability: COMPLETE=1.0, FAILED=0.0, RUNNING=0.7, PENDING=0.5.
    """
    from src.monkey_brain.kernel.execute.graph import NodeState

    S = {}
    for node in graph.get_step_nodes():
        state = graph.get_state(node.id)
        if state == NodeState.COMPLETE:
            S[node.id] = 1.0
        elif state == NodeState.FAILED:
            S[node.id] = 0.0
        elif state == NodeState.RUNNING:
            S[node.id] = 0.7
        elif state == NodeState.SKIPPED:
            S[node.id] = 0.5
        else:
            S[node.id] = 0.4  # PENDING
    return S


def _epa_to_belief(epa_state: Any) -> Any:
    """Convert PredictedEPAState to a belief-like object JEPA can encode."""
    if epa_state is None:
        return _NullBelief()
    return _DictBelief(epa_state.B_hat if hasattr(epa_state, "B_hat") else {})


def _graph_to_affordances(graph: Any, epa_state: Any) -> set[str]:
    """Return the set of capability names currently in the graph (planned affordances)."""
    if epa_state is not None and hasattr(epa_state, "A_hat"):
        return set(epa_state.A_hat)
    return {node.label for node in graph.get_step_nodes()}


def _goal_to_dict(goal: Any) -> Any:
    """Convert goal object to something JEPA's goal encoder can handle."""
    if goal is None:
        return None
    if isinstance(goal, dict):
        return _DictGoal(goal)
    return _DictGoal({"objective": getattr(goal, "name", str(goal))})


def _build_prediction(
    graph: Any,
    S: dict[str, float],
    A: set[str],
    jepa_result: dict,
) -> JEPAPrediction:
    """Convert infer_epistemic() output to typed JEPAPrediction.

    The latent_signal_W from JEPA represents the expected direction of world
    state change.  We use it to modulate PENDING node probabilities — nodes on
    a positive trajectory are predicted to complete, negative to fail.
    """

    sig_W = jepa_result.get("latent_signal_W", jepa_result.get("latent_signal", 0.0))
    sig_B = jepa_result.get("latent_signal_B", 0.0)
    sig_G = jepa_result.get("latent_signal_G", 0.0)
    sig_A = jepa_result.get("latent_signal_A", 0.0)
    avg_loss = jepa_result.get("epistemic_avg_loss", jepa_result.get("avg_loss", 1.0))
    trained = jepa_result.get("epistemic_trained", jepa_result.get("trained", False))

    # Predicted world state: modulate PENDING node probs with latent_signal_W
    predicted_world_state: dict[str, float] = {}
    nodes = graph.get_step_nodes()
    for node in nodes:
        base = S.get(node.id, 0.4)
        if base == 1.0 or base == 0.0:
            # Already terminal — JEPA doesn't override deterministic outcomes
            predicted_world_state[node.id] = base
        else:
            # Modulate with world signal (bounded to [0.05, 0.95])
            delta = sig_W * 0.3  # 30% max influence per step
            predicted_world_state[node.id] = round(max(0.05, min(0.95, base + delta)), 4)

    # Predicted goal progress
    total = len(nodes)
    predicted_complete = sum(p for p in predicted_world_state.values())
    predicted_goal_progress = round(predicted_complete / total, 4) if total > 0 else 0.0

    # Predicted beliefs
    conf_signal = float((1.0 + sig_B) / 2.0)  # map [-1,1] → [0,1]
    predicted_beliefs = {
        "confidence": round(conf_signal, 4),
        "uncertainty": round(1.0 - conf_signal, 4),
        "epistemic_avg_loss": avg_loss,
    }

    # Predicted affordances: modulate with sig_A
    predicted_actions = set(A)
    if sig_A < -0.3 and predicted_actions:
        # Negative affordance signal: likely some capabilities will become unavailable
        n_drop = max(1, int(len(predicted_actions) * 0.1))
        predicted_actions = set(list(predicted_actions)[n_drop:])

    # Confidence: anti-correlated with avg_loss, grows with training
    if trained:
        prediction_confidence = round(max(0.25, min(0.85, 1.0 - avg_loss * 0.5)), 4)
    else:
        prediction_confidence = 0.25  # untrained prior

    return JEPAPrediction(
        predicted_world_state=predicted_world_state,
        predicted_beliefs=predicted_beliefs,
        predicted_actions=predicted_actions,
        predicted_goal_progress=predicted_goal_progress,
        prediction_confidence=prediction_confidence,
        latent_embedding=jepa_result.get("z_E", []),
        component_signals={
            "latent_signal_W": sig_W,
            "latent_signal_B": sig_B,
            "latent_signal_K": jepa_result.get("latent_signal_K", 0.0),
            "latent_signal_A": sig_A,
            "latent_signal_G": sig_G,
            "latent_signal_M": jepa_result.get("latent_signal_M", 0.0),
            "epistemic_avg_loss": avg_loss,
        },
        trained=trained,
    )


# ── Lightweight belief/goal adapters ─────────────────────────────────────────


class _NullBelief:
    """Default belief when no EPA state is available."""

    confidence = 0.5
    knowledge: list = []
    uncertainty = None


class _DictBelief:
    def __init__(self, d: dict) -> None:
        self.confidence = d.get("confidence", 0.5)
        self.knowledge: list = []
        self.uncertainty = None


class _DictGoal:
    def __init__(self, d: dict) -> None:
        self.objective = d.get("objective", "")
        self.target_predicates: list = d.get("predicates", [])
        self.success_criteria: dict = d.get("criteria", {})
        self.priority: float = d.get("priority", 0.5)
