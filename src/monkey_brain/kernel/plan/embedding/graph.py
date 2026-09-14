"""Graph and ontology embedding providers."""

from __future__ import annotations

import re as _re
from typing import Any

import numpy as np

from src.monkey_brain.kernel.plan.embedding._utils import _l2, _graph_features
from src.monkey_brain.kernel.plan.embedding.provider import Embedding, EmbeddingEmbedder


class GraphEmbedder(EmbeddingEmbedder):
    """Graph structural features.

    Parses JSON adjacency lists or text arrow notation, extracts topology metrics.
    Replace: GraphSAGE / R-GCN / GraphTransformer.
    """

    @property
    def name(self) -> str:
        return "graph"

    def embed(self, item: Any) -> Embedding:
        content = str(getattr(item, "content", item) or "")
        feat = _graph_features(content)
        feat[9] = float((getattr(item, "provenance", 0.75) + 1.0 - getattr(item, "uncertainty", 0.25)) / 2)
        feat[10] = float(getattr(item, "provenance", 0.5))
        return Embedding(
            vector=_l2(np.tanh(feat)),
            modality=str(getattr(item, "modality", "")),
            provider=self.name,
        )


class OntologyEmbedder(EmbeddingEmbedder):
    """OWL / RDF / ontology feature extractor.

    Ontologies are typed graphs with class hierarchies, property chains, and axioms.
    Extracts subClassOf chains, property counts, and restriction axioms lexically.
    Replace: OWL-RL reasoner embedding / BioPortal encoder.
    """

    @property
    def name(self) -> str:
        return "ontology"

    def embed(self, item: Any) -> Embedding:
        content = str(getattr(item, "content", item) or "")
        feat = _graph_features(content)
        feat[9] = (
            min(
                len(_re.findall(r"\bsubClassOf\b|\brdfs:subClassOf\b|\bisA\b", content, _re.I)),
                50,
            )
            / 50.0
        )
        feat[10] = (
            min(
                len(
                    _re.findall(
                        r"\bObjectProperty\b|\bDataProperty\b|\bAnnotationProperty\b",
                        content,
                        _re.I,
                    )
                ),
                50,
            )
            / 50.0
        )
        feat[11] = (
            min(
                len(
                    _re.findall(
                        r"\bRestriction\b|\bEquivalentClass\b|\bDisjointWith\b",
                        content,
                        _re.I,
                    )
                ),
                30,
            )
            / 30.0
        )
        feat[12] = min(len(_re.findall(r"\bowl:\w+|\brdfs:\w+|\brdf:\w+", content)), 100) / 100.0
        feat[13] = float((getattr(item, "provenance", 0.9) + 1.0 - getattr(item, "uncertainty", 0.1)) / 2)
        feat[14] = float(getattr(item, "provenance", 0.5))
        return Embedding(
            vector=np.tanh(feat),
            modality=str(getattr(item, "modality", "")),
            provider=self.name,
        )
