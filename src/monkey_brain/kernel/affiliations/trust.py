from __future__ import annotations
import logging

logger = logging.getLogger("agentos.trust")


class TrustEngine:
    """Single abstraction for all trust calculations.

    Used by SocietyRuntime (actor-to-actor) and AffiliationManager (affiliation trust).
    Trust evolves based on goal outcomes — recommendations, obligations, achievements.
    Asymmetric: trust decays faster than it grows.
    """

    _DEFAULT = 0.5

    def __init__(self):
        self._trust: dict[tuple[str, str], float] = {}

    def _key(self, source: str, target: str) -> tuple[str, str]:
        return (source, target)

    def get_trust(self, source: str, target: str) -> float:
        return self._trust.get(self._key(source, target), self._DEFAULT)

    def set_trust(self, source: str, target: str, level: float) -> None:
        self._trust[self._key(source, target)] = max(0.0, min(1.0, level))

    @staticmethod
    def compute_delta(
        *,
        goal_achieved: bool = True,
        recommendation_valid: bool | None = None,
        obligation_met: bool | None = None,
        growth_rate: float = 0.05,
        decay_rate: float = -0.08,
    ) -> float:
        """Pure trust-delta formula, shared by this engine's own dict-backed
        store and by AffiliationManager's per-affiliation Affiliation.trust_level
        updates -- both apply the same rule, just against different stores.
        growth_rate/decay_rate let a caller substitute a relationship-specific
        AffiliationType.trust_model rate for the goal-outcome-only case; the
        recommendation/obligation deltas stay fixed since they're independent
        signals, not the type's baseline trust evolution rate."""
        delta = 0.0

        if recommendation_valid is not None:
            delta += 0.05 if recommendation_valid else -0.12

        if obligation_met is not None:
            delta += 0.03 if obligation_met else -0.15

        if recommendation_valid is None and obligation_met is None:
            delta += growth_rate if goal_achieved else decay_rate

        return delta

    def update_from_outcome(
        self,
        source: str,
        target: str,
        goal_achieved: bool = True,
        recommendation_valid: bool | None = None,
        obligation_met: bool | None = None,
    ) -> None:
        current = self.get_trust(source, target)
        delta = self.compute_delta(
            goal_achieved=goal_achieved,
            recommendation_valid=recommendation_valid,
            obligation_met=obligation_met,
        )

        self.set_trust(source, target, current + delta)
        logger.debug(
            "Trust %s->%s: %.2f -> %.2f (delta=%.3f)",
            source,
            target,
            current,
            self.get_trust(source, target),
            delta,
        )

    def query_trusted(self, source: str, min_trust: float = 0.5) -> list[str]:
        return [target for (s, target), trust in self._trust.items() if s == source and trust >= min_trust]

    def all_trust(self, source: str) -> dict[str, float]:
        return {target: trust for (s, target), trust in self._trust.items() if s == source}
