"""WorldLearner — fuses actor observations into a consensus world model.

The world tensor is a CONSENSUS BELIEF:

    W = f(O₁, O₂, ..., Oₙ)

where:
    Oᵢ = actor i's observations
    f  = WorldLearner (this module)
    W  = consensus world model

Every actor contributes evidence through observations.
The WorldLearner fuses this evidence into a shared belief about reality.

This is NOT:
    - A consensus of policies (policies are per-actor)
    - A consensus of rewards (rewards are per-experience)
    - A ground truth (it's a belief, subject to revision)

This IS:
    - A consensus of observations
    - A shared belief about environmental dynamics
    - The best estimate of P(S'|S) given all available evidence

Architectural invariant:
    World learning updates only SparseTransitionTensor.
    It never modifies policy storage.
    It is the SOLE authority for world updates.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("agentos.world_learner")


@dataclass
class Observation:
    """A proposed transition from an actor.

    Actors propose observations; the WorldLearner decides whether
    and how to incorporate them into the shared world model.

    The effective weight used in fusion is: weight * trust
    where:
      - weight: how strongly the actor believes this (0-1)
      - trust: reliability of the source in the trust fabric (0-1)
    """

    src: str
    dst: str
    domain: str = "default"
    dst_domain: str | None = None
    latency_ms: float = 0.0
    cost: float = 0.0
    confidence: float = 0.0
    ts: float | None = None
    origin: str = ""  # which actor proposed this
    weight: float = 1.0  # how strongly the actor believes this (0-1)
    trust: float = 1.0  # trust score of the source (0-1, from trust fabric)


class WorldLearner:
    """Fuses actor observations into a consensus world model.

    The world tensor is a CONSENSUS BELIEF:

        W = f(O₁, O₂, ..., Oₙ)

    where Oᵢ = actor i's observations, f = this learner.

    Every actor contributes evidence through observations.
    This learner fuses the evidence into a shared belief about reality.

    Thread-safe: all mutations are guarded by a lock.
    """

    def __init__(self, tensor: Any, trust_network: Any = None) -> None:
        self._tensor = tensor
        self._trust_network = trust_network  # Optional: for looking up trust scores
        self._update_count: int = 0
        self._proposal_count: int = 0
        self._rejected_count: int = 0
        self._lock = threading.Lock()

    def _get_source_trust(self, source: str) -> float:
        """Look up trust score for a source in the trust network.

        Returns trust score (0-1) from trust fabric if available,
        otherwise defaults to 1.0 (neutral trust).
        """
        if not self._trust_network:
            return 1.0

        # Trust is directed: "self" (local runtime) trusts the source
        try:
            trust_score = self._trust_network.trust("self", source)
            return max(0.0, min(1.0, trust_score))  # Clamp to [0, 1]
        except Exception:
            # If trust lookup fails, default to neutral
            return 1.0

    def propose(self, observation: Observation) -> bool:
        """Accept or reject a proposed observation.

        The WorldLearner decides whether this observation should be
        incorporated into the shared world model. Returns True if
        accepted, False if rejected.

        Default policy: accept all proposals. Override this method
        to implement custom filtering (e.g. confidence thresholds,
        domain restrictions, deduplication).
        """
        self._proposal_count += 1

        # Default policy: accept all observations
        # Override this for custom filtering
        return True

    def observe_transition(
        self,
        src: str,
        dst: str,
        *,
        domain: str = "default",
        dst_domain: str | None = None,
        latency_ms: float = 0.0,
        cost: float = 0.0,
        confidence: float = 0.0,
        ts: float | None = None,
        origin: str = "",
        weight: float = 1.0,
        trust: float = 1.0,
    ) -> bool:
        """Record a single observed transition S → S' with trust weighting.

        Updates the world tensor's frequency count for this edge, weighted by
        the trust score of the source. This implements: W' = f(W, O, T)

        Args:
            trust: reliability of the source in trust fabric (0-1). Default 1.0
                   (backward compatible, no trust penalty).

        The effective weight applied to the tensor is: weight * trust
        This allows high-confidence observations from trusted sources to move
        the world state more, while low-trust observations have less influence.

        Returns True if the observation was accepted and recorded.
        """
        obs = Observation(
            src=src,
            dst=dst,
            domain=domain,
            dst_domain=dst_domain,
            latency_ms=latency_ms,
            cost=cost,
            confidence=confidence,
            ts=ts,
            origin=origin,
            weight=weight,
            trust=trust,
        )

        if not self.propose(obs):
            self._rejected_count += 1
            logger.debug(
                "[world_learner] rejected observation: %s → %s (origin=%s, trust=%.2f)",
                src,
                dst,
                origin,
                trust,
            )
            return False

        # Compute effective weight: how much this observation influences world state
        effective_weight = weight * trust

        with self._lock:
            self._tensor.observe(
                src,
                dst,
                domain=domain,
                dst_domain=dst_domain,
                latency_ms=latency_ms,
                cost=cost,
                confidence=confidence,
                ts=ts,
                weight=effective_weight,  # Apply trust weighting to tensor
            )
            self._update_count += 1
            logger.debug(
                "[world_learner] recorded observation: %s → %s (weight=%.2f, trust=%.2f, effective=%.2f)",
                src,
                dst,
                weight,
                trust,
                effective_weight,
            )
            return True

    def batch_observe(self, transitions: list[dict]) -> int:
        """Record a batch of observed transitions with trust weighting.

        Each transition is a dict with at least {src, dst}.
        Optional: domain, dst_domain, latency_ms, cost, confidence, ts, origin, weight, trust.

        The trust field (0-1) comes from the trust fabric and weights how much
        this observation influences the world state.

        Returns the number of transitions accepted and recorded.
        """
        count = 0
        for t in transitions:
            if self.observe_transition(
                t["src"],
                t["dst"],
                domain=t.get("domain", "default"),
                dst_domain=t.get("dst_domain"),
                latency_ms=t.get("latency_ms", 0.0),
                cost=t.get("cost", 0.0),
                confidence=t.get("confidence", 0.0),
                ts=t.get("ts"),
                origin=t.get("origin", ""),
                weight=t.get("weight", 1.0),
                trust=t.get("trust", 1.0),  # Default 1.0 for backward compatibility
            ):
                count += 1
        return count

    @property
    def update_count(self) -> int:
        return self._update_count

    @property
    def proposal_count(self) -> int:
        return self._proposal_count

    @property
    def rejected_count(self) -> int:
        return self._rejected_count

    def summary(self) -> dict:
        return {
            "update_count": self._update_count,
            "proposal_count": self._proposal_count,
            "rejected_count": self._rejected_count,
            "acceptance_rate": self._update_count / max(self._proposal_count, 1),
            "tensor_nnz": self._tensor.nnz(),
            "tensor_states": len(self._tensor.states()),
        }
