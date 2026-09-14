"""IKnowledge — common interface for all knowledge modalities.

Every knowledge node in the Cognitive Knowledge Framework exposes:
    id, modality, ontology, confidence, provenance, timestamp, dependencies
    supports(), contradicts(), simulates()

No distinction between PDF, SOP, Image, CAD, Source Code, Video —
they're different modalities of knowledge sharing one interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from src.knowledge.item import KnowledgeItem, Modality


class KnowledgeRelation(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    DEPENDS_ON = "depends_on"
    REPLACES = "replaces"
    EXTENDS = "extends"
    SIMILAR_TO = "similar_to"


@dataclass
class KnowledgeEdge:
    """A typed relation between two knowledge nodes."""

    source_id: str = ""
    target_id: str = ""
    relation: KnowledgeRelation = KnowledgeRelation.SUPPORTS
    weight: float = 1.0  # strength of the relation
    evidence: str = ""  # what supports this edge
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class Evidence:
    """Evidence produced by a solver or observation that updates confidence."""

    source: str = ""  # "simulation", "observation", "solver:graph", etc.
    modality: Modality = Modality.SIMULATION
    content: Any = None
    confidence_delta: float = 0.0  # how much this evidence changes confidence
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)


class IKnowledge(ABC):
    """Common interface for all knowledge modalities."""

    @property
    @abstractmethod
    def id(self) -> str: ...

    @property
    @abstractmethod
    def modality(self) -> Modality: ...

    @property
    @abstractmethod
    def ontology(self) -> str: ...

    @property
    @abstractmethod
    def confidence(self) -> float: ...

    @property
    @abstractmethod
    def provenance(self) -> str: ...

    @property
    @abstractmethod
    def timestamp(self) -> str: ...

    @property
    @abstractmethod
    def dependencies(self) -> list[str]: ...

    @abstractmethod
    def supports(self, other: IKnowledge) -> float:
        """How much this knowledge supports another (0-1)."""
        ...

    @abstractmethod
    def contradicts(self, other: IKnowledge) -> float:
        """How much this knowledge contradicts another (0-1)."""
        ...

    @abstractmethod
    def simulates(self, other: IKnowledge) -> float:
        """How much this knowledge can simulate/predict another (0-1)."""
        ...

    @abstractmethod
    def apply_evidence(self, evidence: Evidence) -> None:
        """Update confidence based on new evidence."""
        ...

    @abstractmethod
    def decay(self, steps: int = 1) -> None:
        """Decay freshness over time."""
        ...

    def to_knowledge_item(self) -> KnowledgeItem:
        """Convert to a KnowledgeItem for fusion."""
        return KnowledgeItem(
            id=self.id,
            modality=self.modality,
            source=self.provenance,
            provenance=0.8,  # default, should be overridden
            tags=[self.ontology],
        )


class ConcreteKnowledge(IKnowledge):
    """Concrete implementation of IKnowledge backed by a KnowledgeItem."""

    def __init__(
        self,
        item: KnowledgeItem,
        ontology: str = "general",
        dependencies: list[str] | None = None,
    ):
        self._item = item
        self._ontology = ontology
        self._dependencies = dependencies or []
        self._edges: list[KnowledgeEdge] = []

    @property
    def id(self) -> str:
        return self._item.id

    @property
    def modality(self) -> Modality:
        return self._item.modality

    @property
    def ontology(self) -> str:
        return self._ontology

    @property
    def confidence(self) -> float:
        return self._item.composite_confidence

    @property
    def provenance(self) -> str:
        return self._item.source

    @property
    def timestamp(self) -> str:
        return self._item.created_at

    @property
    def dependencies(self) -> list[str]:
        return list(self._dependencies)

    def supports(self, other: IKnowledge) -> float:
        """Heuristic: overlap in ontology + high confidence."""
        if self._ontology == other.ontology:
            return self.confidence * 0.8
        return self.confidence * 0.2

    def contradicts(self, other: IKnowledge) -> float:
        """Heuristic: different modality + same ontology = potential contradiction."""
        if self._ontology == other.ontology and self.modality != other.modality:
            return (1.0 - self.confidence) * 0.3
        return 0.0

    def simulates(self, other: IKnowledge) -> float:
        """Only simulation modality can simulate."""
        if self.modality == Modality.SIMULATION:
            return self.confidence * 0.9
        return 0.0

    def apply_evidence(self, evidence: Evidence) -> None:
        """Update confidence based on new evidence."""
        delta = evidence.confidence_delta
        if delta > 0:
            self._item.consistency = min(1.0, self._item.consistency + delta * 0.5)
            self._item.uncertainty = max(0.0, self._item.uncertainty - delta * 0.3)
        else:
            self._item.consistency = max(0.0, self._item.consistency + delta * 0.5)
            self._item.uncertainty = min(1.0, self._item.uncertainty - delta * 0.2)

    def decay(self, steps: int = 1) -> None:
        self._item.decay(steps)

    @property
    def item(self) -> KnowledgeItem:
        return self._item
