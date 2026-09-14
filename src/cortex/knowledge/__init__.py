"""cortex.knowledge — Cognitive Knowledge Framework (10 layers).

Layer 1   IKnowledge interface — typed multimodal knowledge graph
Layer 2   ConfidenceVector C(K) = ⟨P, F, S, V, E, M, R⟩
Layer 3   EpistemicState — kernel owns K_t alongside S_t
Layer 4   Retrieval policy — argmax_i IG(K_i) − Cost(K_i)
Layer 5   Simulation updates both world and knowledge
Layer 6   Closed learning loop
Layer 7   Solver evidence feeds into K_t via EvidenceFusionEngine
Layer 8   Bayesian knowledge evolution — Posterior(K_t | evidence)
Layer 9   ShouldRetrieve as a decision function, not a search function
Layer 10  The full Cognitive Loop
"""

from __future__ import annotations

from cortex.knowledge.confidence import ConfidenceVector
from cortex.knowledge.evidence import BayesianEvidenceFusionEngine, SolverEvidence
from cortex.knowledge.interface import IKnowledge, MODALITIES, default_confidence_for
from cortex.knowledge.pack import KnowledgeNode, KnowledgePack
from cortex.knowledge.retrieval import (
    RetrievalDecision,
    best_retrieval,
    should_retrieve,
)

__all__ = [
    "ConfidenceVector",
    "EvidenceFusionEngine",
    "IKnowledge",
    "KnowledgeNode",
    "KnowledgePack",
    "MODALITIES",
    "RetrievalDecision",
    "SolverEvidence",
    "best_retrieval",
    "default_confidence_for",
    "should_retrieve",
]
