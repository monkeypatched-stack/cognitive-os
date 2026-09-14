"""Knowledge Pack — collection of KnowledgeItems with multimodal evidence fusion.

A Knowledge Pack is the system's structured memory. It:
    - Stores knowledge items across modalities
    - Fuses evidence into a single epistemic state
    - Tracks how knowledge evolves over time
    - Decides when to retrieve (optimization, not default)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from src.knowledge.item import KnowledgeItem, Modality


@dataclass
class ModalityEvidence:
    """Aggregated evidence from a single modality."""

    modality: Modality
    items: list[KnowledgeItem] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.items)

    @property
    def avg_confidence(self) -> float:
        if not self.items:
            return 0.0
        return sum(k.composite_confidence for k in self.items) / len(self.items)

    @property
    def max_confidence(self) -> float:
        if not self.items:
            return 0.0
        return max(k.composite_confidence for k in self.items)

    @property
    def avg_information_gain(self) -> float:
        if not self.items:
            return 0.0
        return sum(k.information_gain_potential for k in self.items) / len(self.items)


@dataclass
class FusionResult:
    """Result of multimodal evidence fusion."""

    fused_confidence: float = 0.0
    modality_scores: dict[str, float] = field(default_factory=dict)
    agreement: float = 0.0  # how much modalities agree (0=disagree, 1=agree)
    dominant_modality: str = ""
    total_items: int = 0
    effective_knowledge: float = 0.0  # confidence × agreement

    def to_dict(self) -> dict[str, Any]:
        return {
            "fused_confidence": self.fused_confidence,
            "modality_scores": self.modality_scores,
            "agreement": self.agreement,
            "dominant_modality": self.dominant_modality,
            "total_items": self.total_items,
            "effective_knowledge": self.effective_knowledge,
        }


class KnowledgePack:
    """Collection of knowledge items with multimodal fusion.

    Session 1 + 2: Defines K = {k_1, ..., k_n} and computes
    C_knowledge = f(modality_evidence_1, ..., modality_evidence_m)
    """

    def __init__(self):
        self._items: dict[str, KnowledgeItem] = {}
        self._fusion_cache: FusionResult | None = None

    def add(self, item: KnowledgeItem) -> None:
        """Add a knowledge item."""
        self._items[item.id] = item
        self._fusion_cache = None  # invalidate

    def remove(self, item_id: str) -> bool:
        if item_id in self._items:
            del self._items[item_id]
            self._fusion_cache = None
            return True
        return False

    def get(self, item_id: str) -> KnowledgeItem | None:
        return self._items.get(item_id)

    def query(
        self,
        modality: Modality | None = None,
        min_confidence: float = 0.0,
        tags: list[str] | None = None,
        limit: int = 100,
    ) -> list[KnowledgeItem]:
        """Query knowledge items with optional filters."""
        results = list(self._items.values())
        if modality:
            results = [k for k in results if k.modality == modality]
        if min_confidence > 0:
            results = [k for k in results if k.composite_confidence >= min_confidence]
        if tags:
            results = [k for k in results if any(t in k.tags for t in tags)]
        results.sort(key=lambda k: k.composite_confidence, reverse=True)
        return results[:limit]

    @property
    def size(self) -> int:
        return len(self._items)

    @property
    def modalities(self) -> set[Modality]:
        return {k.modality for k in self._items.values()}

    def by_modality(self) -> dict[Modality, list[KnowledgeItem]]:
        result: dict[Modality, list[KnowledgeItem]] = {}
        for item in self._items.values():
            result.setdefault(item.modality, []).append(item)
        return result

    def decay(self, steps: int = 1) -> None:
        """Decay freshness for all items."""
        for item in self._items.values():
            item.decay(steps)
        self._fusion_cache = None

    def record_simulation(self, item_ids: list[str], success: bool, error: float = 0.0) -> None:
        """Record simulation outcomes for specific items."""
        for item_id in item_ids:
            if item_id in self._items:
                self._items[item_id].record_simulation(success, error)
        self._fusion_cache = None

    def fuse(self) -> FusionResult:
        """Multimodal evidence fusion.

        Combines evidence from all modalities into a single epistemic state.

        Fusion strategy:
        1. Compute per-modality confidence (weighted by item count and quality)
        2. Compute cross-modality agreement
        3. Fused confidence = weighted_mean × agreement
        """
        if self._fusion_cache is not None:
            return self._fusion_cache

        if not self._items:
            self._fusion_cache = FusionResult()
            return self._fusion_cache

        # Group by modality
        by_mod = self.by_modality()
        mod_scores: dict[str, float] = {}
        mod_weights: dict[str, float] = {}

        for mod, items in by_mod.items():
            confidences = [k.composite_confidence for k in items]
            # Weighted mean: items with higher provenance contribute more
            weights = [k.provenance for k in items]
            total_w = sum(weights) or 1.0
            weighted_mean = sum(c * w for c, w in zip(confidences, weights)) / total_w
            mod_scores[mod.value] = weighted_mean
            mod_weights[mod.value] = len(items) * max(confidences)

        # Fused confidence: weighted average across modalities
        total_weight = sum(mod_weights.values()) or 1.0
        fused = sum(mod_scores[mod] * mod_weights[mod] / total_weight for mod in mod_scores)

        # Agreement: how much modalities agree (coefficient of variation)
        if len(mod_scores) > 1:
            values = list(mod_scores.values())
            mean_v = sum(values) / len(values)
            if mean_v > 0:
                std_v = math.sqrt(sum((v - mean_v) ** 2 for v in values) / len(values))
                cv = std_v / mean_v  # coefficient of variation
                agreement = max(0.0, 1.0 - cv)  # high CV = low agreement
            else:
                agreement = 0.0
        else:
            agreement = 1.0  # single modality always agrees with itself

        dominant = max(mod_scores, key=mod_scores.get) if mod_scores else ""

        effective = fused * agreement

        result = FusionResult(
            fused_confidence=fused,
            modality_scores=mod_scores,
            agreement=agreement,
            dominant_modality=dominant,
            total_items=len(self._items),
            effective_knowledge=effective,
        )
        self._fusion_cache = result
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "size": self.size,
            "modalities": [m.value for m in self.modalities],
            "fusion": self.fuse().to_dict(),
            "items": [
                k.to_dict()
                for k in sorted(
                    self._items.values(),
                    key=lambda k: k.composite_confidence,
                    reverse=True,
                )[:20]
            ],  # top 20
        }

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self):
        return iter(self._items.values())
