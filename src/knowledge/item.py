"""Knowledge Item — atomic unit of knowledge with multi-dimensional confidence.

Each knowledge item k_i carries:
    provenance       — source reliability
    freshness        — temporal relevance
    modality         — document, image, CAD, video, telemetry, code, graph, ontology, DB, simulation
    completeness     — coverage of the domain
    consistency      — coherence with other knowledge
    verification_history — list of verification outcomes
    simulation_success   — historical accuracy when used in simulation
    uncertainty      — epistemic uncertainty (variance over predictions)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class Modality(StrEnum):
    DOCUMENT = "document"
    IMAGE = "image"
    CAD = "cad"
    VIDEO = "video"
    TELEMETRY = "telemetry"
    CODE = "code"
    GRAPH = "graph"
    ONTOLOGY = "ontology"
    DATABASE = "database"
    SIMULATION = "simulation"
    SENSOR = "sensor"
    MANUAL = "manual"


# Modality-specific weight priors — some modalities are inherently more reliable
MODALITY_WEIGHTS: dict[Modality, dict[str, float]] = {
    Modality.DOCUMENT: {
        "base_confidence": 0.7,
        "decay_rate": 0.01,
        "verification_boost": 0.15,
    },
    Modality.IMAGE: {
        "base_confidence": 0.5,
        "decay_rate": 0.02,
        "verification_boost": 0.20,
    },
    Modality.CAD: {
        "base_confidence": 0.8,
        "decay_rate": 0.005,
        "verification_boost": 0.10,
    },
    Modality.VIDEO: {
        "base_confidence": 0.4,
        "decay_rate": 0.03,
        "verification_boost": 0.25,
    },
    Modality.TELEMETRY: {
        "base_confidence": 0.9,
        "decay_rate": 0.05,
        "verification_boost": 0.05,
    },
    Modality.CODE: {
        "base_confidence": 0.85,
        "decay_rate": 0.01,
        "verification_boost": 0.10,
    },
    Modality.GRAPH: {
        "base_confidence": 0.75,
        "decay_rate": 0.01,
        "verification_boost": 0.10,
    },
    Modality.ONTOLOGY: {
        "base_confidence": 0.9,
        "decay_rate": 0.002,
        "verification_boost": 0.05,
    },
    Modality.DATABASE: {
        "base_confidence": 0.95,
        "decay_rate": 0.001,
        "verification_boost": 0.02,
    },
    Modality.SIMULATION: {
        "base_confidence": 0.6,
        "decay_rate": 0.04,
        "verification_boost": 0.15,
    },
    Modality.SENSOR: {
        "base_confidence": 0.85,
        "decay_rate": 0.08,
        "verification_boost": 0.05,
    },
    Modality.MANUAL: {
        "base_confidence": 0.5,
        "decay_rate": 0.02,
        "verification_boost": 0.20,
    },
}


@dataclass
class VerificationRecord:
    """A single verification event for a knowledge item."""

    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    method: str = ""  # "simulation", "cross-reference", "expert-review", "automated"
    passed: bool = True
    delta_confidence: float = 0.0  # how much confidence changed
    notes: str = ""


@dataclass
class KnowledgeItem:
    """Atomic unit of knowledge with multi-dimensional confidence.

    K = {k_1, k_2, ..., k_n} where each k_i is a KnowledgeItem.
    """

    id: str = ""
    content: str = ""  # the actual knowledge (text, reference, etc.)
    modality: Modality = Modality.DOCUMENT
    source: str = ""  # provenance label

    # Multi-dimensional confidence
    provenance: float = 0.5  # source reliability [0,1]
    freshness: float = 1.0  # temporal relevance [0,1]
    completeness: float = 0.5  # domain coverage [0,1]
    consistency: float = 0.5  # coherence with other knowledge [0,1]
    uncertainty: float = 0.5  # epistemic uncertainty (high = uncertain) [0,1]

    # Simulation feedback
    simulation_predictions: int = 0
    simulation_successes: int = 0
    simulation_errors: list[float] = field(default_factory=list)

    # Verification
    verification_history: list[VerificationRecord] = field(default_factory=list)

    # Metadata
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_used_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    use_count: int = 0
    tags: list[str] = field(default_factory=list)

    @property
    def simulation_success_rate(self) -> float:
        if self.simulation_predictions == 0:
            return 0.5  # prior
        return self.simulation_successes / self.simulation_predictions

    @property
    def verification_score(self) -> float:
        """Fraction of verifications that passed."""
        if not self.simulation_predictions:
            return 0.5
        passed = sum(1 for v in self.verification_history if v.passed)
        return passed / len(self.verification_history)

    @property
    def composite_confidence(self) -> float:
        """Weighted geometric mean across all confidence dimensions.

        Geometric mean penalises low dimensions — a knowledge item with
        freshness=0.01 cannot compensate by having provenance=1.0.
        """
        dims = {
            "provenance": max(self.provenance, 1e-9),
            "freshness": max(self.freshness, 1e-9),
            "completeness": max(self.completeness, 1e-9),
            "consistency": max(self.consistency, 1e-9),
            "simulation_success": max(self.simulation_success_rate, 1e-9),
            "verification": max(self.verification_score, 1e-9),
            "certainty": max(1.0 - self.uncertainty, 1e-9),
        }
        weights = {
            "provenance": 0.20,
            "freshness": 0.12,
            "completeness": 0.12,
            "consistency": 0.12,
            "simulation_success": 0.18,
            "verification": 0.12,
            "certainty": 0.14,
        }
        log_sum = sum(weights[k] * math.log(v) for k, v in dims.items())
        return math.exp(log_sum)

    @property
    def information_gain_potential(self) -> float:
        """Expected information gain if this item is retrieved.

        High for uncertain items with high provenance and freshness.
        Low for items already well-verified or stale.
        """
        return self.uncertainty * self.provenance * self.freshness

    def decay(self, steps: int = 1) -> KnowledgeItem:
        """Decay freshness over time. Returns self (mutates)."""
        decay_rate = MODALITY_WEIGHTS.get(self.modality, {}).get("decay_rate", 0.02)
        self.freshness *= (1.0 - decay_rate) ** steps
        self.freshness = max(0.0, self.freshness)
        return self

    def record_simulation(self, success: bool, error: float = 0.0) -> None:
        """Record a simulation outcome."""
        self.simulation_predictions += 1
        if success:
            self.simulation_successes += 1
        self.simulation_errors.append(error)
        if len(self.simulation_errors) > 50:
            self.simulation_errors = self.simulation_errors[-50:]

    def verify(self, passed: bool, method: str = "simulation", notes: str = "") -> None:
        """Record a verification event."""
        base_boost = MODALITY_WEIGHTS.get(self.modality, {}).get("verification_boost", 0.1)
        delta = base_boost if passed else -base_boost
        self.verification_history.append(
            VerificationRecord(
                method=method,
                passed=passed,
                delta_confidence=delta,
                notes=notes,
            )
        )
        self.consistency = max(0.0, min(1.0, self.consistency + delta))

    def use(self) -> None:
        """Record that this item was used."""
        self.use_count += 1
        self.last_used_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "modality": self.modality.value,
            "source": self.source,
            "provenance": self.provenance,
            "freshness": self.freshness,
            "completeness": self.completeness,
            "consistency": self.consistency,
            "uncertainty": self.uncertainty,
            "simulation_success_rate": self.simulation_success_rate,
            "composite_confidence": self.composite_confidence,
            "information_gain_potential": self.information_gain_potential,
            "use_count": self.use_count,
            "tags": self.tags,
            "verification_count": len(self.verification_history),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> KnowledgeItem:
        item = cls(
            id=d.get("id", ""),
            content=d.get("content", ""),
            modality=Modality(d.get("modality", "document")),
            source=d.get("source", ""),
            provenance=d.get("provenance", 0.5),
            freshness=d.get("freshness", 1.0),
            completeness=d.get("completeness", 0.5),
            consistency=d.get("consistency", 0.5),
            uncertainty=d.get("uncertainty", 0.5),
            tags=d.get("tags", []),
        )
        item.use_count = d.get("use_count", 0)
        item.simulation_predictions = d.get("simulation_predictions", [])
        item.simulation_successes = d.get("simulation_successes", [])
        item.simulation_errors = d.get("simulation_errors", [])
        item.created_at = d.get("created_at", "")
        item.last_used_at = d.get("last_used_at", "")
        return item
