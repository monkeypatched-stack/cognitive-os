"""JEPAPrediction — structured output type for the JEPA world model predictor.

JEPA is a predictive model only.  It never executes workloads, modifies the
graph, or updates policies.  Its sole output is a prediction of how the world
evolves from the current EPA state given a chosen action.

This type is returned by JEPAPredictor.predict() and consumed by PredictEngine
during Solver Mesh aggregation.  It contributes to PredictedEPAState, which is
the reference state for EPA loss computation.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class JEPAPrediction:
    """Predicted world evolution from the JEPA world model.

    predicted_world_state  — node_id → predicted completion probability after execution
                             (derived by modulating current states with latent_signal_W)
    predicted_beliefs      — predicted belief quality: confidence, uncertainty
    predicted_actions      — predicted set of capabilities that will be available post-execution
    predicted_goal_progress — predicted fraction of goal predicates satisfied
    prediction_confidence  — JEPA's self-assessed confidence (anti-correlated with avg_loss)
    latent_embedding       — z_E ∈ R^80 — full unified epistemic latent
    component_signals      — per-component signals for diagnostic use:
                              latent_signal_W, _B, _K, _A, _G, _M
    trained                — whether the JEPA model has seen any training steps
    """

    predicted_world_state: dict[str, float] = field(default_factory=dict)
    predicted_beliefs: dict[str, float] = field(default_factory=dict)
    predicted_actions: set[str] = field(default_factory=set)
    predicted_goal_progress: float = 0.0
    prediction_confidence: float = 0.25  # starts low; grows with training
    latent_embedding: list[float] = field(default_factory=list)
    component_signals: dict[str, float] = field(default_factory=dict)
    trained: bool = False
