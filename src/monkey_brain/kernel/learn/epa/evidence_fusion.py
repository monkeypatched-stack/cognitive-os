"""Evidence Fusion Engine — combines solver outputs into epistemic updates.

Each solver produces Evidence. The fusion engine:
    1. Collects evidence from all solvers
    2. Weights by solver reliability
    3. Resolves contradictions
    4. Produces a posterior confidence update

This is Layer 7 + Layer 8 of the Cognitive Knowledge Framework.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.knowledge.interface import Evidence
from src.knowledge.item import Modality
from src.knowledge.pack import KnowledgePack
from src.monkey_brain.kernel.learn.epa.epistemic import (
    EpistemicState,
    KnowledgeConfidence,
)


@dataclass
class SolverReliability:
    """Reliability of a solver type."""

    name: str = ""
    base_reliability: float = 0.5  # prior reliability [0,1]
    success_count: int = 0
    failure_count: int = 0
    avg_confidence_delta: float = 0.0

    @property
    def empirical_reliability(self) -> float:
        total = self.success_count + self.failure_count
        if total == 0:
            return self.base_reliability
        return self.success_count / total

    def record_outcome(self, success: bool, confidence_delta: float = 0.0) -> None:
        if success:
            self.success_count += 1
        else:
            self.failure_count += 1
        self.avg_confidence_delta = self.avg_confidence_delta * 0.9 + confidence_delta * 0.1


@dataclass
class FusionResult:
    """Result of evidence fusion."""

    updated_confidence: KnowledgeConfidence = field(default_factory=KnowledgeConfidence)
    evidence_applied: int = 0
    contradictions_resolved: int = 0
    net_confidence_change: float = 0.0
    solver_reliabilities: dict[str, float] = field(default_factory=dict)
    dominant_evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "updated_confidence": self.updated_confidence.to_dict(),
            "evidence_applied": self.evidence_applied,
            "contradictions_resolved": self.contradictions_resolved,
            "net_confidence_change": self.net_confidence_change,
            "solver_reliabilities": self.solver_reliabilities,
            "dominant_evidence": self.dominant_evidence,
        }


class EvidenceFusionEngine:
    """Fuses evidence from multiple solvers into epistemic updates.

    Layer 7: Each solver produces Evidence.
    Layer 8: Fusion updates KnowledgePack posterior confidence.
    """

    # Solver type → base reliability
    SOLVER_RELIABILITY: dict[str, float] = {
        "graph": 0.85,  # graph reachability is exact
        "sat": 0.90,  # SAT proofs are definitive
        "monte_carlo": 0.7,  # statistical, variance
        "coherence": 0.75,  # coherence check (status embedding)
        "rule_engine": 0.8,  # deterministic rules
        "llm": 0.5,  # semantic hypothesis, uncertain
        "simulator": 0.8,  # behavioral evidence
        "sensor": 0.9,  # direct observation
    }

    def __init__(self):
        self._solver_reliabilities: dict[str, SolverReliability] = {}
        self._evidence_log: list[dict] = []
        self._max_log = 1000

    def get_reliability(self, solver_name: str) -> SolverReliability:
        if solver_name not in self._solver_reliabilities:
            base = self.SOLVER_RELIABILITY.get(solver_name, 0.5)
            self._solver_reliabilities[solver_name] = SolverReliability(
                name=solver_name,
                base_reliability=base,
            )
        return self._solver_reliabilities[solver_name]

    def fuse(
        self,
        evidence_list: list[Evidence],
        knowledge_pack: KnowledgePack,
        epistemic: EpistemicState,
    ) -> FusionResult:
        """Fuse evidence from multiple solvers into an epistemic update.

        Algorithm:
        1. Weight each evidence by solver reliability
        2. Apply weighted evidence to knowledge items
        3. Resolve contradictions (conflicting evidence)
        4. Compute posterior confidence
        """
        prior_confidence = epistemic.belief.confidence
        evidence_applied = 0
        contradictions = 0
        total_delta = 0.0
        solver_scores: dict[str, list[float]] = {}
        best_evidence = ""
        best_score = 0.0

        for evidence in evidence_list:
            solver = self.get_reliability(evidence.source)
            weight = solver.empirical_reliability

            # Weight the confidence delta by solver reliability
            weighted_delta = evidence.confidence_delta * weight

            # Apply to knowledge pack items
            for item in knowledge_pack:
                if item.modality == evidence.modality or evidence.modality == Modality.SIMULATION:
                    item.use()
                    evidence_applied += 1

                    # Update item confidence
                    if weighted_delta > 0:
                        item.consistency = min(1.0, item.consistency + weighted_delta * 0.1)
                        item.uncertainty = max(0.0, item.uncertainty - weighted_delta * 0.05)
                    else:
                        item.consistency = max(0.0, item.consistency + weighted_delta * 0.1)
                        item.uncertainty = min(1.0, item.uncertainty - weighted_delta * 0.05)

            solver_scores.setdefault(evidence.source, []).append(weighted_delta)
            total_delta += weighted_delta

            if abs(weighted_delta) > best_score:
                best_score = abs(weighted_delta)
                best_evidence = evidence.source

        # Update solver reliabilities
        for source, deltas in solver_scores.items():
            avg_delta = sum(deltas) / len(deltas)
            solver = self.get_reliability(source)
            solver.record_outcome(avg_delta > 0, avg_delta)

        # Compute posterior confidence from updated knowledge pack
        fusion = knowledge_pack.fuse()

        # Blend prior and posterior
        blend = 0.7  # 70% posterior, 30% prior
        posterior = KnowledgeConfidence(
            provenance=max(
                prior_confidence.provenance * (1 - blend) + fusion.fused_confidence * blend,
                0,
            ),
            semantic_consistency=prior_confidence.semantic_consistency,
            freshness=prior_confidence.freshness,
            completeness=max(
                prior_confidence.completeness * (1 - blend) + fusion.agreement * blend,
                0,
            ),
            validation=prior_confidence.validation,
            simulation_success=prior_confidence.simulation_success,
        )

        result = FusionResult(
            updated_confidence=posterior,
            evidence_applied=evidence_applied,
            contradictions_resolved=contradictions,
            net_confidence_change=posterior.composite - prior_confidence.composite,
            solver_reliabilities={name: r.empirical_reliability for name, r in self._solver_reliabilities.items()},
            dominant_evidence=best_evidence,
        )

        self._evidence_log.append(
            {
                "evidence_count": len(evidence_list),
                "applied": evidence_applied,
                "net_change": result.net_confidence_change,
            }
        )
        if len(self._evidence_log) > self._max_log:
            self._evidence_log = self._evidence_log[-self._max_log :]

        return result

    def get_stats(self) -> dict[str, Any]:
        return {
            "total_fusions": len(self._evidence_log),
            "solver_reliabilities": {name: r.empirical_reliability for name, r in self._solver_reliabilities.items()},
        }
