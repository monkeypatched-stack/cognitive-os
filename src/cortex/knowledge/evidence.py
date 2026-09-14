"""evidence.py — Layer 7: Solver Evidence and Evidence Fusion Engine.

Every solver in the Solver Selection Constitution produces evidence,
not just findings.  Evidence feeds directly into K_t.

Solver          Evidence Type        ConfidenceVector Component Updated
─────────────────────────────────────────────────────────────────────
Graph           reachability         verification  (formal proof / disproof)
SAT/SMT         proof                verification  (strongest: formal)
Monte Carlo     failure_probability  empirical     (sampled distribution)
JEPA            prediction           empirical + modality
Constraint      constraint_sat       semantic      (structural consistency)
LLM / Heuristic semantic_hypothesis  semantic      (weakest: plausible hypothesis)
Simulator       behavioral           empirical + verification

Evidence is Bayesian: positive evidence reinforces the target component,
negative evidence (violation found) degrades it.  The learning_rate
determines how fast the posterior moves from the prior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cortex.knowledge.pack import KnowledgeNode, KnowledgePack

# ---------------------------------------------------------------------------
# SolverEvidence — the token each solver emits
# ---------------------------------------------------------------------------


@dataclass
class SolverEvidence:
    """One piece of evidence produced by a solver.

    positive=True  → solver found NO violation (reinforces confidence)
    positive=False → solver found a violation  (degrades confidence)
    """

    solver: str  # graph | sat_smt | monte_carlo | jepa | constraint | heuristic | simulator
    evidence_type: str  # reachability | proof | failure_probability | prediction | constraint_sat | semantic_hypothesis | behavioral
    claim: str  # natural-language description of what was checked
    confidence: float  # strength of this piece of evidence ∈ [0,1]
    positive: bool = True  # reinforcing or contradicting
    affected_node_ids: list[str] = field(default_factory=list)  # which KnowledgeNodes this touches
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "solver": self.solver,
            "evidence_type": self.evidence_type,
            "claim": self.claim,
            "confidence": self.confidence,
            "positive": self.positive,
            "affected_node_ids": self.affected_node_ids,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# Solver → ConfidenceVector component mapping
# ---------------------------------------------------------------------------

# Each solver primarily updates specific components of C(K) = ⟨P,F,S,V,E,M,R⟩.
# Multiple entries: each (component, weight) pair gets a proportional update.
_SOLVER_COMPONENT_MAP: dict[str, list[tuple[str, float]]] = {
    "graph": [("verification", 0.90), ("empirical", 0.10)],
    "sat_smt": [("verification", 1.00)],
    "monte_carlo": [("empirical", 0.85), ("verification", 0.15)],
    "jepa": [("empirical", 0.55), ("modality", 0.45)],
    "constraint": [("semantic", 0.80), ("verification", 0.20)],
    "heuristic": [("semantic", 0.70), ("retrieval", 0.30)],
    "simulator": [("empirical", 0.60), ("verification", 0.40)],
    "optimizer": [("empirical", 0.70), ("semantic", 0.30)],
}

# Evidence type → how strong the update learning rate should be
_EVIDENCE_STRENGTH: dict[str, float] = {
    "proof": 0.50,  # formal: strong and certain
    "reachability": 0.40,
    "constraint_sat": 0.35,
    "behavioral": 0.35,
    "failure_probability": 0.30,
    "prediction": 0.25,
    "semantic_hypothesis": 0.15,  # LLM hypothesis: weakest — plausible not proven
}


# ---------------------------------------------------------------------------
# EvidenceFusionEngine — updates K_t from solver evidence
# ---------------------------------------------------------------------------


class BayesianEvidenceFusionEngine:
    """Fuses evidence from all solvers to update K_t.

    Layer 7 of the Cognitive Knowledge Framework.

    Each piece of solver evidence is mapped to one or more ConfidenceVector
    components and applied as a Bayesian-style update.  The update is proportional
    to the evidence confidence and the solver's learning rate for that component.

    Usage:
        engine = BayesianEvidenceFusionEngine()
        updated_pack = engine.fuse(pack, evidences)
    """

    def fuse(
        self,
        pack: KnowledgePack,
        evidences: list[SolverEvidence],
        *,
        global_lr_scale: float = 1.0,
    ) -> KnowledgePack:
        """Apply all evidence to the pack, returning an updated KnowledgePack.

        If SolverEvidence has affected_node_ids: update only those nodes.
        Otherwise: broadcast to all nodes (pack-wide evidence).
        """
        from copy import deepcopy

        updated_pack = deepcopy(pack)

        for ev in evidences:
            target_nodes = (
                [updated_pack.nodes[nid] for nid in ev.affected_node_ids if nid in updated_pack.nodes]
                if ev.affected_node_ids
                else list(updated_pack.nodes.values())
            )
            component_updates = _SOLVER_COMPONENT_MAP.get(ev.solver, [("semantic", 0.5)])
            lr = _EVIDENCE_STRENGTH.get(ev.evidence_type, 0.20) * global_lr_scale

            for node in target_nodes:
                for component, weight in component_updates:
                    node.confidence = node.confidence.update(
                        component,
                        ev.confidence * weight,
                        positive=ev.positive,
                        learning_rate=lr,
                    )

        return updated_pack

    def fuse_into_node(
        self,
        node: KnowledgeNode,
        evidences: list[SolverEvidence],
    ) -> KnowledgeNode:
        """Apply evidence to a single node — used when nodes are managed outside a pack."""
        conf = node.confidence
        for ev in evidences:
            for component, weight in _SOLVER_COMPONENT_MAP.get(ev.solver, [("semantic", 0.5)]):
                lr = _EVIDENCE_STRENGTH.get(ev.evidence_type, 0.20)
                conf = conf.update(
                    component,
                    ev.confidence * weight,
                    positive=ev.positive,
                    learning_rate=lr,
                )
        from copy import copy

        updated = copy(node)
        updated.confidence = conf
        return updated

    @staticmethod
    def from_adversarial_result(adv_dict: dict[str, Any]) -> list[SolverEvidence]:
        """Convert an adversarial pass result dict to a list of SolverEvidence.

        Called by the G→A→R loop to feed solver outputs back into K_t.
        """
        evidences: list[SolverEvidence] = []
        for f in adv_dict.get("findings", []):
            solver = f.get("solver", "heuristic")
            evidences.append(
                SolverEvidence(
                    solver=solver,
                    evidence_type=_finding_type_to_evidence(f.get("finding_type", "")),
                    claim=f.get("description", ""),
                    confidence=float(f.get("confidence", 0.50)),
                    positive=False,  # findings are violations → degrade confidence
                    metadata={"severity": f.get("severity", "medium")},
                )
            )
        # If no findings: each solver that ran produced positive evidence
        if not adv_dict.get("findings"):
            for solver in adv_dict.get("solvers_run", []):
                evidences.append(
                    SolverEvidence(
                        solver=solver,
                        evidence_type=_solver_to_evidence_type(solver),
                        claim=f"Solver {solver} found no violations",
                        confidence=0.70,
                        positive=True,
                    )
                )
        return evidences


def _finding_type_to_evidence(finding_type: str) -> str:
    _MAP = {
        "unreachable_state": "reachability",
        "violated_invariant": "constraint_sat",
        "breaking_use_case": "semantic_hypothesis",
        "missing_entity": "semantic_hypothesis",
        "semantic_duplicate": "prediction",
        "high_prediction_error": "prediction",
    }
    return _MAP.get(finding_type, "semantic_hypothesis")


def _solver_to_evidence_type(solver: str) -> str:
    _MAP = {
        "graph": "reachability",
        "sat_smt": "proof",
        "monte_carlo": "failure_probability",
        "jepa": "prediction",
        "constraint": "constraint_sat",
        "heuristic": "semantic_hypothesis",
        "simulator": "behavioral",
        "optimizer": "behavioral",
    }
    return _MAP.get(solver, "semantic_hypothesis")
