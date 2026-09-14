"""retrieval.py — Layer 4 & 9: Retrieval as a decision problem, not a search problem.

Most RAG systems ask: What is relevant?
This system asks:     What information most reduces uncertainty?

That is a completely different optimization objective.

Retrieval policy:
    argmax_i IG(K_i) − Cost(K_i)

Where:
    IG(K_i) = expected reduction in simulation loss if we retrieve K_i
    Cost(K_i) = latency + token cost + compute (normalized to [0,1])

ShouldRetrieve is a decision function, not a search function.
It considers:
    - Current epistemic state (which confidence components are weakest)
    - Expected Information Gain (IG) for each candidate modality
    - Simulation loss breakdown (which loss components are highest)
    - Retrieval cost (varies by source type)
    - Latency budget (hard constraint: never exceed)
"""

from __future__ import annotations

from dataclasses import dataclass

from cortex.knowledge.confidence import ConfidenceVector

# ---------------------------------------------------------------------------
# Retrieval cost table
# ---------------------------------------------------------------------------

RETRIEVAL_COSTS: dict[str, float] = {
    "sensor_read": 0.02,
    "cache_hit": 0.01,
    "db_query": 0.05,
    "file_read": 0.03,
    "vector_search": 0.08,
    "graph_traversal": 0.10,
    "web_search": 0.20,
    "llm_inference": 0.35,
    "external_api": 0.25,
    "simulation_run": 0.50,
    "human_review": 0.90,
}

# Latency estimate in seconds per source type
RETRIEVAL_LATENCY: dict[str, float] = {
    "sensor_read": 0.01,
    "cache_hit": 0.001,
    "db_query": 0.05,
    "file_read": 0.02,
    "vector_search": 0.20,
    "graph_traversal": 0.15,
    "web_search": 2.00,
    "llm_inference": 5.00,
    "external_api": 1.50,
    "simulation_run": 30.0,
    "human_review": 3600.0,
}

# Modality → which confidence components it primarily raises if retrieved
_MODALITY_IG_PROFILE: dict[str, dict[str, float]] = {
    #                 P     F     S     V     E     M     R
    "ontology": {"semantic": 0.8, "verification": 0.6, "provenance": 0.4},
    "fact": {"empirical": 0.7, "semantic": 0.5},
    "rule": {"semantic": 0.9, "verification": 0.7},
    "procedure": {"semantic": 0.7, "verification": 0.6, "empirical": 0.4},
    "policy": {"semantic": 0.8, "provenance": 0.6},
    "constraint": {"semantic": 0.9, "verification": 0.8},
    "world_model": {"empirical": 0.7, "semantic": 0.5},
    "simulator": {"empirical": 0.9, "verification": 0.6},
    "embedding": {"retrieval": 0.9, "semantic": 0.4},
    "code": {"verification": 0.8, "semantic": 0.7, "empirical": 0.5},
    "test": {"verification": 0.95, "empirical": 0.85},
    "image": {"empirical": 0.6, "modality": 0.5},
    "video": {"empirical": 0.7, "modality": 0.5},
    "cad": {"semantic": 0.7, "verification": 0.6, "empirical": 0.5},
    "telemetry": {"empirical": 0.95, "freshness": 0.90},
    "provenance": {"provenance": 0.95, "verification": 0.7},
    "evidence": {"empirical": 0.90, "verification": 0.75},
}


# ---------------------------------------------------------------------------
# RetrievalDecision — the structured output of ShouldRetrieve
# ---------------------------------------------------------------------------


@dataclass
class RetrievalDecision:
    """Outcome of a retrieval policy decision."""

    should_retrieve: bool
    reason: str
    expected_ig: float  # ΔL_sim if retrieved
    retrieval_cost: float
    net_value: float  # expected_ig − retrieval_cost
    recommended_modality: str  # which modality to retrieve
    recommended_source: str  # which source type to use


# ---------------------------------------------------------------------------
# Information Gain computation
# ---------------------------------------------------------------------------


def expected_information_gain(
    current_confidence: ConfidenceVector,
    candidate_modality: str,
    simulation_loss_components: dict[str, float],
) -> float:
    """E[IG] = expected reduction in simulation loss if we retrieve this modality.

    The gain is proportional to:
      - How much headroom exists in the confidence components this modality improves
      - How much weight those components have in the current simulation loss

    Diminishing returns: gain shrinks as confidence approaches 1.0.
    """
    ig_profile = _MODALITY_IG_PROFILE.get(candidate_modality, {})
    if not ig_profile:
        return 0.0

    # Simulation loss component → confidence component mapping
    _LOSS_TO_CONF = {
        "state": ["empirical", "freshness"],
        "action": ["semantic", "verification"],
        "affordance": ["semantic", "ontology"],
        "constraint": ["verification", "semantic"],
        "goal": ["empirical", "freshness"],
        "knowledge": ["provenance", "verification", "retrieval"],
    }

    total_gain = 0.0
    for conf_component, ig_strength in ig_profile.items():
        current_val = getattr(current_confidence, conf_component, 1.0)
        headroom = 1.0 - current_val  # max possible improvement

        # Which simulation loss components does improving this confidence help?
        loss_relevance = sum(
            simulation_loss_components.get(loss_comp, 0.0)
            for loss_comp, conf_comps in _LOSS_TO_CONF.items()
            if conf_component in conf_comps
        ) / max(1, sum(1 for v in _LOSS_TO_CONF.values() if conf_component in v))

        total_gain += headroom * ig_strength * loss_relevance * 0.7

    return round(min(1.0, total_gain), 4)


# ---------------------------------------------------------------------------
# ShouldRetrieve — the decision function
# ---------------------------------------------------------------------------


def should_retrieve(
    current_confidence: ConfidenceVector,
    simulation_loss_components: dict[str, float],
    *,
    candidate_modality: str = "fact",
    retrieval_source: str = "vector_search",
    latency_budget: float = 5.0,
) -> RetrievalDecision:
    """Retrieval as a decision problem, not a search problem.

    Returns a RetrievalDecision with:
        should_retrieve: True only if E[IG] > cost AND latency fits budget
        net_value:       E[IG] − cost (can be negative)
        reason:          human-readable rationale

    The caller uses this to decide whether to invoke a retrieval mechanism.
    The retrieval mechanism itself (which document, which vector store) is
    a separate concern.
    """
    ig = expected_information_gain(current_confidence, candidate_modality, simulation_loss_components)
    cost = RETRIEVAL_COSTS.get(retrieval_source, 0.20)
    latency = RETRIEVAL_LATENCY.get(retrieval_source, 1.0)
    net = round(ig - cost, 4)

    if latency > latency_budget:
        return RetrievalDecision(
            should_retrieve=False,
            reason=f"latency {latency:.1f}s exceeds budget {latency_budget:.1f}s",
            expected_ig=ig,
            retrieval_cost=cost,
            net_value=net,
            recommended_modality=candidate_modality,
            recommended_source=retrieval_source,
        )

    if net > 0:
        return RetrievalDecision(
            should_retrieve=True,
            reason=f"E[IG]={ig:.3f} > cost={cost:.3f} for {candidate_modality} via {retrieval_source}",
            expected_ig=ig,
            retrieval_cost=cost,
            net_value=net,
            recommended_modality=candidate_modality,
            recommended_source=retrieval_source,
        )

    return RetrievalDecision(
        should_retrieve=False,
        reason=f"E[IG]={ig:.3f} ≤ cost={cost:.3f} — not worth retrieving",
        expected_ig=ig,
        retrieval_cost=cost,
        net_value=net,
        recommended_modality=candidate_modality,
        recommended_source=retrieval_source,
    )


def best_retrieval(
    current_confidence: ConfidenceVector,
    simulation_loss_components: dict[str, float],
    *,
    budget: float = 0.20,
    latency_budget: float = 5.0,
) -> RetrievalDecision | None:
    """Find the highest net-value retrieval that fits within budget.

    Iterates over candidate modalities and source types, returns the
    argmax_i IG(K_i) − Cost(K_i) that passes the budget constraint.
    Returns None if no retrieval is worth doing.
    """
    best: RetrievalDecision | None = None
    affordable_sources = [s for s, c in RETRIEVAL_COSTS.items() if c <= budget]

    for modality in _MODALITY_IG_PROFILE:
        for source in affordable_sources:
            decision = should_retrieve(
                current_confidence,
                simulation_loss_components,
                candidate_modality=modality,
                retrieval_source=source,
                latency_budget=latency_budget,
            )
            if decision.should_retrieve and (best is None or decision.net_value > best.net_value):
                best = decision

    return best
