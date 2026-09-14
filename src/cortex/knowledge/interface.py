"""interface.py — Layer 1: IKnowledge — the common interface for all knowledge modalities.

There is no distinction between PDF, SOP, image, CAD, source code, or video.
They are different modalities of knowledge — all nodes in the same typed graph.

Every knowledge node exposes:
  supports()    — does this item support a claim? returns confidence ∈ [0,1]
  contradicts() — does this item contradict a claim? returns confidence ∈ [0,1]
  simulates()   — can this item simulate a scenario? returns a result dict

The KnowledgePack is a graph of IKnowledge nodes with typed edges:
  SUPPORTS     — this item supports another's claim
  CONTRADICTS  — this item contradicts another's claim
  DEPENDS_ON   — this item requires another to be valid
  SIMULATES    — this item can simulate the other's scenario
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from cortex.knowledge.confidence import ConfidenceVector


@runtime_checkable
class IKnowledge(Protocol):
    """Common interface for every node in the knowledge graph.

    All modalities implement this — documents, images, CAD, telemetry, code,
    tests, ontologies, rules, procedures, world models, simulators.
    """

    id: str
    modality: str  # ontology | fact | rule | procedure | world_model |
    # simulator | policy | constraint | embedding |
    # image | video | cad | telemetry | code | test |
    # provenance | evidence
    ontology: str  # which domain ontology this item belongs to
    confidence: ConfidenceVector
    provenance: str  # source authority
    timestamp: str  # ISO datetime of last verification
    dependencies: list[str]  # ids of items this item requires

    def supports(self, claim: str) -> float:
        """Confidence ∈ [0,1] that this item supports the claim."""
        ...

    def contradicts(self, claim: str) -> float:
        """Confidence ∈ [0,1] that this item contradicts the claim."""
        ...

    def simulates(self, scenario: str) -> dict[str, Any]:
        """Simulate the scenario and return a result dict."""
        ...


class EdgeType:
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    DEPENDS_ON = "depends_on"
    SIMULATES = "simulates"


# Canonical modality identifiers — used across all knowledge nodes
MODALITIES = {
    # Structured knowledge
    "ontology",
    "fact",
    "rule",
    "procedure",
    "policy",
    "constraint",
    # Computational
    "world_model",
    "simulator",
    "embedding",
    "code",
    "test",
    # Physical / sensor
    "image",
    "video",
    "cad",
    "telemetry",
    # Meta
    "provenance",
    "evidence",
}

# Modality → which ConfidenceVector components it primarily carries
MODALITY_CONFIDENCE_PROFILE: dict[str, dict[str, float]] = {
    #                       P     F     S     V     E     M     R
    "ontology": {
        "provenance": 0.95,
        "freshness": 0.70,
        "semantic": 0.95,
        "verification": 0.85,
        "empirical": 0.60,
        "modality": 0.90,
        "retrieval": 0.85,
    },
    "fact": {
        "provenance": 0.80,
        "freshness": 0.85,
        "semantic": 0.90,
        "verification": 0.90,
        "empirical": 0.85,
        "modality": 0.90,
        "retrieval": 0.80,
    },
    "rule": {
        "provenance": 0.90,
        "freshness": 0.75,
        "semantic": 0.95,
        "verification": 0.95,
        "empirical": 0.70,
        "modality": 0.90,
        "retrieval": 0.85,
    },
    "procedure": {
        "provenance": 0.85,
        "freshness": 0.80,
        "semantic": 0.90,
        "verification": 0.85,
        "empirical": 0.75,
        "modality": 0.85,
        "retrieval": 0.80,
    },
    "policy": {
        "provenance": 0.92,
        "freshness": 0.75,
        "semantic": 0.90,
        "verification": 0.90,
        "empirical": 0.65,
        "modality": 0.88,
        "retrieval": 0.82,
    },
    "constraint": {
        "provenance": 0.88,
        "freshness": 0.70,
        "semantic": 0.95,
        "verification": 0.95,
        "empirical": 0.65,
        "modality": 0.90,
        "retrieval": 0.85,
    },
    "world_model": {
        "provenance": 0.75,
        "freshness": 0.80,
        "semantic": 0.85,
        "verification": 0.80,
        "empirical": 0.85,
        "modality": 0.85,
        "retrieval": 0.78,
    },
    "simulator": {
        "provenance": 0.70,
        "freshness": 0.85,
        "semantic": 0.80,
        "verification": 0.85,
        "empirical": 0.95,
        "modality": 0.88,
        "retrieval": 0.75,
    },
    "embedding": {
        "provenance": 0.70,
        "freshness": 0.80,
        "semantic": 0.85,
        "verification": 0.70,
        "empirical": 0.80,
        "modality": 0.85,
        "retrieval": 0.90,
    },
    "code": {
        "provenance": 0.80,
        "freshness": 0.85,
        "semantic": 0.90,
        "verification": 0.90,
        "empirical": 0.85,
        "modality": 0.92,
        "retrieval": 0.85,
    },
    "test": {
        "provenance": 0.75,
        "freshness": 0.90,
        "semantic": 0.85,
        "verification": 0.98,
        "empirical": 0.95,
        "modality": 0.92,
        "retrieval": 0.88,
    },
    "image": {
        "provenance": 0.65,
        "freshness": 0.80,
        "semantic": 0.75,
        "verification": 0.70,
        "empirical": 0.85,
        "modality": 0.80,
        "retrieval": 0.75,
    },
    "video": {
        "provenance": 0.60,
        "freshness": 0.85,
        "semantic": 0.70,
        "verification": 0.70,
        "empirical": 0.88,
        "modality": 0.78,
        "retrieval": 0.72,
    },
    "cad": {
        "provenance": 0.85,
        "freshness": 0.75,
        "semantic": 0.92,
        "verification": 0.90,
        "empirical": 0.80,
        "modality": 0.92,
        "retrieval": 0.85,
    },
    "telemetry": {
        "provenance": 0.55,
        "freshness": 0.98,
        "semantic": 0.80,
        "verification": 0.75,
        "empirical": 0.98,
        "modality": 0.90,
        "retrieval": 0.80,
    },
    "provenance": {
        "provenance": 1.00,
        "freshness": 0.80,
        "semantic": 0.85,
        "verification": 0.95,
        "empirical": 0.70,
        "modality": 0.85,
        "retrieval": 0.90,
    },
    "evidence": {
        "provenance": 0.70,
        "freshness": 0.90,
        "semantic": 0.80,
        "verification": 0.88,
        "empirical": 0.95,
        "modality": 0.85,
        "retrieval": 0.82,
    },
}


def default_confidence_for(modality: str) -> ConfidenceVector:
    """Return the default ConfidenceVector for a given modality."""
    profile = MODALITY_CONFIDENCE_PROFILE.get(modality, {})
    if not profile:
        return ConfidenceVector()
    return ConfidenceVector(**{k: v for k, v in profile.items()})
