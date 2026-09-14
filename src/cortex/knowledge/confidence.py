"""confidence.py — Layer 2: Confidence as a vector, not a scalar.

C(K) = ⟨P, F, S, V, E, M, R⟩

  P  Provenance        — authority and trust of the source
  F  Freshness         — temporal relevance; staleness decays trust
  S  Semantic          — internal consistency; contradictions reduce it
  V  Verification      — external validation: proofs, audits, tests
  E  Empirical         — observed evidence from experiment/simulation
  M  Modality          — reliability inherent to the evidence type
  R  Retrieval         — confidence that we retrieved the *right* thing

Each component evolves independently as evidence arrives.
Bayesian in spirit: posterior = prior updated by evidence weight.

The scalar projection weights are domain-tunable; defaults are chosen
so that formal verification (V) and empirical evidence (E) dominate
over unchecked provenance or retrieval confidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, NamedTuple


class ConfidenceComponent(NamedTuple):
    """Named index into a ConfidenceVector."""

    PROVENANCE: int = 0
    FRESHNESS: int = 1
    SEMANTIC: int = 2
    VERIFICATION: int = 3
    EMPIRICAL: int = 4
    MODALITY: int = 5
    RETRIEVAL: int = 6


COMPONENT = ConfidenceComponent()


@dataclass
class ConfidenceVector:
    """C(K) = ⟨P, F, S, V, E, M, R⟩ — seven independent epistemic axes.

    No single number can capture how trustworthy a knowledge item is.
    A document from ISO may have perfect provenance but stale freshness.
    Telemetry may have high empirical evidence but low semantic consistency.
    A retrieved chunk may have high similarity but low retrieval confidence
    (the query was ambiguous).

    All components ∈ [0.0, 1.0].
    """

    provenance: float = 1.0  # P: source authority
    freshness: float = 1.0  # F: temporal relevance
    semantic: float = 1.0  # S: internal consistency
    verification: float = 1.0  # V: formal proof / audit / test result
    empirical: float = 1.0  # E: observed simulation or experimental evidence
    modality: float = 1.0  # M: reliability of this evidence type
    retrieval: float = 1.0  # R: confidence the right item was retrieved

    # Default projection weights — domain teams tune these
    _W: ClassVar[tuple[float, ...]] = (0.20, 0.12, 0.15, 0.22, 0.18, 0.08, 0.05)

    # ---------------------------------------------------------------------------
    # Scalar projection
    # ---------------------------------------------------------------------------

    def scalar(self, weights: tuple[float, ...] | None = None) -> float:
        """Project to [0,1] for SimulationLoss and retrieval decisions."""
        w = weights or self._W
        components = (
            self.provenance,
            self.freshness,
            self.semantic,
            self.verification,
            self.empirical,
            self.modality,
            self.retrieval,
        )
        return round(sum(wi * ci for wi, ci in zip(w, components)), 4)

    def loss(self) -> float:
        """L = 1 − scalar — used directly as L_knowledge."""
        return round(max(0.0, 1.0 - self.scalar()), 4)

    # ---------------------------------------------------------------------------
    # Bayesian-style update
    # ---------------------------------------------------------------------------

    def update(
        self,
        component: str,
        evidence_confidence: float,
        *,
        positive: bool = True,
        learning_rate: float = 0.30,
    ) -> "ConfidenceVector":
        """Return a new ConfidenceVector with one component updated by evidence.

        Positive evidence (reinforcement):
            posterior = prior + (1 − prior) × evidence × lr
        Negative evidence (contradiction):
            posterior = prior × (1 − evidence × lr)

        This keeps all values in [0, 1] and applies diminishing returns —
        already-confident components are harder to move further.
        """
        current = getattr(self, component, None)
        if current is None:
            return self
        if positive:
            updated = current + (1.0 - current) * evidence_confidence * learning_rate
        else:
            updated = current * (1.0 - evidence_confidence * learning_rate)
        kwargs = {
            "provenance": self.provenance,
            "freshness": self.freshness,
            "semantic": self.semantic,
            "verification": self.verification,
            "empirical": self.empirical,
            "modality": self.modality,
            "retrieval": self.retrieval,
            component: round(max(0.0, min(1.0, updated)), 4),
        }
        return ConfidenceVector(**kwargs)

    def apply_evidence_batch(self, evidence_updates: list[dict]) -> "ConfidenceVector":
        """Apply a list of {"component", "confidence", "positive"} updates in sequence."""
        result = self
        for ev in evidence_updates:
            result = result.update(
                ev["component"],
                float(ev["confidence"]),
                positive=ev.get("positive", True),
                learning_rate=float(ev.get("learning_rate", 0.30)),
            )
        return result

    # ---------------------------------------------------------------------------
    # Fusion across multiple items
    # ---------------------------------------------------------------------------

    @classmethod
    def harmonic_fuse(cls, vectors: list["ConfidenceVector"]) -> "ConfidenceVector":
        """Fuse multiple ConfidenceVectors component-wise using harmonic mean.

        Harmonic mean: one very weak component across any item pulls the
        fused result down — the weakest link semantics.
        """
        if not vectors:
            return cls()

        def _hmean(vals: list[float]) -> float:
            pos = [v for v in vals if v > 0]
            if not pos:
                return 0.0
            return len(pos) / sum(1.0 / v for v in pos)

        return cls(
            provenance=round(_hmean([v.provenance for v in vectors]), 4),
            freshness=round(_hmean([v.freshness for v in vectors]), 4),
            semantic=round(_hmean([v.semantic for v in vectors]), 4),
            verification=round(_hmean([v.verification for v in vectors]), 4),
            empirical=round(_hmean([v.empirical for v in vectors]), 4),
            modality=round(_hmean([v.modality for v in vectors]), 4),
            retrieval=round(_hmean([v.retrieval for v in vectors]), 4),
        )

    # ---------------------------------------------------------------------------
    # Serialisation
    # ---------------------------------------------------------------------------

    def to_dict(self) -> dict[str, float]:
        return {
            "provenance": self.provenance,
            "freshness": self.freshness,
            "semantic": self.semantic,
            "verification": self.verification,
            "empirical": self.empirical,
            "modality": self.modality,
            "retrieval": self.retrieval,
            "scalar": self.scalar(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ConfidenceVector":
        return cls(
            provenance=float(d.get("provenance", 1.0)),
            freshness=float(d.get("freshness", 1.0)),
            semantic=float(d.get("semantic", 1.0)),
            verification=float(d.get("verification", 1.0)),
            empirical=float(d.get("empirical", 1.0)),
            modality=float(d.get("modality", 1.0)),
            retrieval=float(d.get("retrieval", 1.0)),
        )

    @classmethod
    def from_scalar(cls, value: float) -> "ConfidenceVector":
        """Bootstrap from a legacy scalar — treats all components equally."""
        v = max(0.0, min(1.0, float(value)))
        return cls(
            provenance=v,
            freshness=v,
            semantic=v,
            verification=v,
            empirical=v,
            modality=v,
            retrieval=v,
        )
