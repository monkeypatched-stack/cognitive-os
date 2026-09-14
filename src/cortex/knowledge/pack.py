"""pack.py — Layer 1: KnowledgeNode and KnowledgePack.

A KnowledgePack is a typed, multimodal knowledge graph.
Nodes are concrete IKnowledge implementations.
Edges are typed (supports, contradicts, depends_on, simulates).

There is no distinction between PDF, SOP, image, CAD, source code, or video.
They are different modalities of the same graph.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from cortex.knowledge.confidence import ConfidenceVector
from cortex.knowledge.interface import (
    MODALITIES,
    EdgeType,
    default_confidence_for,
)

# ---------------------------------------------------------------------------
# KnowledgeNode — concrete IKnowledge for any modality
# ---------------------------------------------------------------------------


@dataclass
class KnowledgeNode:
    """A single node in the knowledge graph.

    Implements IKnowledge.  All modalities share this representation;
    the `modality` field determines how confidence is initialised and
    how the node is fused during evidence updates.
    """

    id: str
    modality: str = "fact"
    ontology: str = ""
    confidence: ConfidenceVector = field(default_factory=ConfidenceVector)
    provenance: str = ""
    timestamp: str = ""
    dependencies: list[str] = field(default_factory=list)
    content: str = ""  # text, URI, hash, or summary
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.modality not in MODALITIES:
            self.modality = "fact"
        # If confidence was not explicitly set, apply the modality profile
        if all(
            v == 1.0
            for v in (
                self.confidence.provenance,
                self.confidence.semantic,
                self.confidence.empirical,
            )
        ):
            self.confidence = default_confidence_for(self.modality)

    # ── IKnowledge interface ─────────────────────────────────────────────────

    def supports(self, claim: str) -> float:
        """Confidence this node supports the claim.

        Heuristic: keyword overlap between content and claim, weighted
        by the node's scalar confidence.
        """
        if not claim or not self.content:
            return 0.0
        claim_terms = set(re.findall(r"\b\w{3,}\b", claim.lower()))
        content_terms = set(re.findall(r"\b\w{3,}\b", self.content.lower()))
        overlap = len(claim_terms & content_terms) / max(len(claim_terms), 1)
        return round(overlap * self.confidence.scalar(), 4)

    def contradicts(self, claim: str) -> float:
        """Confidence this node contradicts the claim.

        Heuristic: negation patterns in content relative to claim terms.
        Kept simple — real contradiction detection should use an LLM or
        formal reasoner wired through the solver dispatch.
        """
        if not claim or not self.content:
            return 0.0
        negation_patterns = {
            "not",
            "no",
            "never",
            "cannot",
            "must not",
            "prohibited",
            "denied",
            "rejected",
            "failed",
            "invalid",
            "false",
        }
        claim_terms = set(re.findall(r"\b\w{3,}\b", claim.lower()))
        content_lower = self.content.lower()
        negations_in_content = sum(1 for n in negation_patterns if n in content_lower)
        overlap = len(claim_terms & set(re.findall(r"\b\w{3,}\b", content_lower))) / max(len(claim_terms), 1)
        if negations_in_content > 0 and overlap > 0.2:
            return round(
                min(1.0, overlap * negations_in_content * 0.3) * self.confidence.scalar(),
                4,
            )
        return 0.0

    def simulates(self, scenario: str) -> dict[str, Any]:
        """Simulate the scenario — only meaningful for simulator/world_model nodes."""
        if self.modality not in ("simulator", "world_model"):
            return {
                "supported": False,
                "reason": f"{self.modality} nodes cannot simulate",
            }
        return {
            "supported": True,
            "node_id": self.id,
            "scenario": scenario,
            "confidence": self.confidence.empirical,
        }

    # ── Serialisation ────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "modality": self.modality,
            "ontology": self.ontology,
            "confidence": self.confidence.to_dict(),
            "provenance": self.provenance,
            "timestamp": self.timestamp,
            "dependencies": self.dependencies,
            "content": self.content,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "KnowledgeNode":
        conf_raw = d.get("confidence", {})
        conf = (
            ConfidenceVector.from_dict(conf_raw)
            if isinstance(conf_raw, dict)
            else ConfidenceVector.from_scalar(float(conf_raw))
        )
        return cls(
            id=d["id"],
            modality=d.get("modality", "fact"),
            ontology=d.get("ontology", ""),
            confidence=conf,
            provenance=d.get("provenance", ""),
            timestamp=d.get("timestamp", ""),
            dependencies=d.get("dependencies", []),
            content=d.get("content", ""),
            metadata=d.get("metadata", {}),
        )


# ---------------------------------------------------------------------------
# KnowledgePack — the typed multimodal knowledge graph
# ---------------------------------------------------------------------------


@dataclass
class KnowledgePack:
    """A typed, multimodal knowledge graph.

    Nodes: KnowledgeNode (IKnowledge implementations)
    Edges: typed relationships — supports, contradicts, depends_on, simulates

    KnowledgePack
    ├── Ontology
    ├── Facts
    ├── Rules
    ├── Procedures
    ├── World Models
    ├── Simulators
    ├── Policies
    ├── Constraints
    ├── Embeddings
    ├── Images / Video / CAD / Telemetry
    ├── Code / Tests
    └── Provenance / Evidence
    """

    id: str = ""
    name: str = ""
    ontology_domain: str = ""  # e.g. "manufacturing", "software", "finance"
    nodes: dict[str, KnowledgeNode] = field(default_factory=dict)
    # edges: {source_id: [(target_id, edge_type), ...]}
    edges: dict[str, list[tuple[str, str]]] = field(default_factory=dict)

    # ── Node management ──────────────────────────────────────────────────────

    def add(self, node: KnowledgeNode) -> "KnowledgePack":
        self.nodes[node.id] = node
        return self

    def remove(self, node_id: str) -> "KnowledgePack":
        self.nodes.pop(node_id, None)
        self.edges.pop(node_id, None)
        for adj in self.edges.values():
            adj[:] = [(t, e) for t, e in adj if t != node_id]
        return self

    def get(self, node_id: str) -> KnowledgeNode | None:
        return self.nodes.get(node_id)

    # ── Edge management ──────────────────────────────────────────────────────

    def link(self, source_id: str, target_id: str, edge_type: str) -> "KnowledgePack":
        self.edges.setdefault(source_id, []).append((target_id, edge_type))
        return self

    def supports(self, source_id: str, target_id: str) -> "KnowledgePack":
        return self.link(source_id, target_id, EdgeType.SUPPORTS)

    def contradicts(self, source_id: str, target_id: str) -> "KnowledgePack":
        return self.link(source_id, target_id, EdgeType.CONTRADICTS)

    def depends_on(self, source_id: str, target_id: str) -> "KnowledgePack":
        return self.link(source_id, target_id, EdgeType.DEPENDS_ON)

    # ── Graph queries ────────────────────────────────────────────────────────

    def by_modality(self, modality: str) -> list[KnowledgeNode]:
        return [n for n in self.nodes.values() if n.modality == modality]

    def contradicting_pairs(self) -> list[tuple[str, str]]:
        """Return all (source, target) pairs linked by CONTRADICTS edges."""
        return [(src, tgt) for src, adj in self.edges.items() for tgt, etype in adj if etype == EdgeType.CONTRADICTS]

    def transitive_dependencies(self, node_id: str) -> set[str]:
        """BFS: all nodes that node_id transitively depends on."""
        visited: set[str] = set()
        queue = [node_id]
        while queue:
            current = queue.pop()
            for tgt, etype in self.edges.get(current, []):
                if etype == EdgeType.DEPENDS_ON and tgt not in visited:
                    visited.add(tgt)
                    queue.append(tgt)
        return visited

    # ── Pack-level confidence ────────────────────────────────────────────────

    def fused_confidence(self) -> ConfidenceVector:
        """Fuse all node confidence vectors — contradiction penalty applied."""
        if not self.nodes:
            return ConfidenceVector()
        fused = ConfidenceVector.harmonic_fuse(list(self.nodes[n].confidence for n in self.nodes))
        # Contradiction penalty: each contradicting pair reduces semantic component
        n_contradictions = len(self.contradicting_pairs())
        if n_contradictions > 0:
            penalty = min(0.30, n_contradictions * 0.05)
            fused = fused.update("semantic", penalty, positive=False, learning_rate=1.0)
        return fused

    def knowledge_loss(self) -> float:
        return self.fused_confidence().loss()

    # ── Serialisation ────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "ontology_domain": self.ontology_domain,
            "nodes": {nid: n.to_dict() for nid, n in self.nodes.items()},
            "edges": {src: [{"target": tgt, "type": etype} for tgt, etype in adj] for src, adj in self.edges.items()},
            "fused_confidence": self.fused_confidence().to_dict(),
            "knowledge_loss": self.knowledge_loss(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "KnowledgePack":
        pack = cls(
            id=d.get("id", ""),
            name=d.get("name", ""),
            ontology_domain=d.get("ontology_domain", ""),
        )
        for nid, ndata in d.get("nodes", {}).items():
            pack.nodes[nid] = KnowledgeNode.from_dict(ndata)
        for src, adj in d.get("edges", {}).items():
            pack.edges[src] = [(e["target"], e["type"]) for e in adj]
        return pack
