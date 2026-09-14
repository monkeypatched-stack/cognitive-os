"""ActionOperator — sparse mask over world tensor for a specific action.

Phase 4 of the World-Policy Separation Refactor.

An ActionOperator represents a single action as a sparse mask over the
shared world tensor. It filters which transitions the action enables,
without duplicating any transition data.

Formally:
    P(S'|S,a) = rownorm(mask_a ⊙ W[S])

where:
    W = shared world tensor
    mask_a = sparse action operator (this class)

Architectural invariant:
    Actions reference existing transitions.
    Actions never duplicate transitions.
    Actions never own probabilities.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("agentos.action_operator")


class ActionOperator:
    """Sparse mask over world tensor for a specific action.

    The mask selects a subset of transitions that this action enables.
    Applied to the world tensor, it produces the action-specific transition
    distribution P(S'|S,a).

    Usage:
        op = ActionOperator("query_knowledge", world_tensor)
        op.enable("s1", "s2", weight=1.0)
        op.enable("s1", "s3", weight=0.5)
        filtered = op.apply("s1")  # → {"s2": 1.0, "s3": 0.5} (normalized)
    """

    def __init__(self, action_name: str, world: Any) -> None:
        self.action_name = action_name
        self._world = world
        # Sparse mask: (src_state, dst_state) → weight
        self._mask: dict[tuple[str, str], float] = {}

    def enable(self, src: str, dst: str, weight: float = 1.0) -> None:
        """Enable a transition for this action.

        The transition must exist in the world tensor (or be added separately).
        Weight controls the relative strength of this transition within the action.
        """
        if weight > 0:
            self._mask[(src, dst)] = weight

    def disable(self, src: str, dst: str) -> None:
        """Disable a transition for this action."""
        self._mask.pop((src, dst), None)

    def is_enabled(self, src: str, dst: str) -> bool:
        """Check if a transition is enabled by this action."""
        return (src, dst) in self._mask

    def apply(self, state: str) -> dict[str, float]:
        """Apply the mask to get filtered transitions from a state.

        Returns a normalized distribution over destination states:
            P(S'|S,a) = rownorm(mask_a ⊙ W[S])

        Only returns transitions that are:
        1. Enabled in this action's mask
        2. Present in the world tensor
        """
        result: dict[str, float] = {}
        for (src, dst), mask_weight in self._mask.items():
            if src != state:
                continue
            # Get world probability
            world_prob = self._world.feature(src, dst, _PROBABILITY_FEATURE)
            if world_prob > 0:
                result[dst] = mask_weight * world_prob

        # Normalize to distribution
        total = sum(result.values())
        if total > 0:
            for dst in result:
                result[dst] /= total

        return result

    def transitions(self) -> list[tuple[str, str, float]]:
        """List all enabled transitions: [(src, dst, weight), ...]."""
        return [(src, dst, w) for (src, dst), w in self._mask.items()]

    @property
    def size(self) -> int:
        """Number of enabled transitions."""
        return len(self._mask)

    def summary(self) -> dict:
        states = set()
        for src, dst in self._mask:
            states.add(src)
            states.add(dst)
        return {
            "action": self.action_name,
            "transitions": self.size,
            "states": len(states),
        }


# Lazy import to avoid circular dependency
_PROBABILITY_FEATURE = None


def _get_probability_feature():
    global _PROBABILITY_FEATURE
    if _PROBABILITY_FEATURE is None:
        from src.monkey_brain.kernel.compile.tensor import Feature

        _PROBABILITY_FEATURE = Feature.PROBABILITY
    return _PROBABILITY_FEATURE


# Patch ActionOperator to use the lazy feature
_orig_apply = ActionOperator.apply


def _patched_apply(self, state: str) -> dict[str, float]:
    global _PROBABILITY_FEATURE
    _PROBABILITY_FEATURE = _get_probability_feature()
    return _orig_apply(self, state)


ActionOperator.apply = _patched_apply


class ActionLegality:
    """Computes legal actions for a given state.

    Legal actions are the intersection of:
    - World transitions (what's physically possible)
    - Actor capabilities (what the actor can do)
    - Business constraints (OPA rules, policies)
    - Physical constraints (resource limits, safety)

    Usage:
        legality = ActionLegality(world_tensor)
        legal = legality.compute("current_state", capabilities={"query", "execute"}, constraints={})
        # → ["query_knowledge", "execute_plan"]
    """

    def __init__(self, world: Any) -> None:
        self._world = world

    def compute(
        self,
        state: str,
        capabilities: set[str] | None = None,
        constraints: dict[str, Any] | None = None,
        action_operators: dict[str, ActionOperator] | None = None,
    ) -> list[str]:
        """Compute legal actions for a given state.

        Args:
            state: Current world state
            capabilities: Set of action names the actor can perform (None = all)
            constraints: Business/physical constraints (None = no constraints)
            action_operators: Dict of action_name → ActionOperator (None = use world successors)

        Returns:
            List of legal action names, sorted by name.
        """
        # Start with actions that have transitions from this state
        if action_operators:
            candidates = {
                name for name, op in action_operators.items() if any(s == state for (s, _, _) in op.transitions())
            }
        else:
            # No operators defined — use world successors as proxy
            candidates = {dst for dst, _ in self._world.successors(state)}

        # Filter by capabilities
        if capabilities is not None:
            candidates &= capabilities

        # Filter by constraints
        if constraints:
            candidates = self._apply_constraints(state, candidates, constraints, action_operators)

        return sorted(candidates)

    def _candidate_feature(
        self,
        state: str,
        candidate: str,
        feature: Any,
        action_operators: dict[str, "ActionOperator"] | None,
    ) -> float:
        """The value of `feature` (cost/latency/confidence) for a candidate at `state`.

        In OPERATOR mode a candidate is an ACTION NAME, not a world state, so
        `world.feature(state, action_name)` reads a non-existent edge and returns 0 —
        which used to make every cost/latency/confidence constraint a silent no-op.
        Instead, take the EXPECTED feature over the action's enabled transitions,
        weighted by P(S'|S,a): Σ_s' P(s'|s,a)·feature(s→s'). In FALLBACK mode a candidate
        is a destination state, so the direct edge feature is correct.
        """
        if action_operators and candidate in action_operators:
            dist = action_operators[candidate].apply(state)  # P(S'|S,a), normalized
            if not dist:
                return 0.0
            return sum(p * self._world.feature(state, dst, feature) for dst, p in dist.items())
        return self._world.feature(state, candidate, feature)

    def _apply_constraints(
        self,
        state: str,
        candidates: set[str],
        constraints: dict[str, Any],
        action_operators: dict[str, "ActionOperator"] | None = None,
    ) -> set[str]:
        """Apply business/physical constraints to filter candidates."""
        filtered = set(candidates)

        # Domain constraint
        allowed_domains = constraints.get("allowed_domains")
        if allowed_domains is not None:
            state_domain = self._world.domain_of(state)
            if state_domain not in allowed_domains:
                return set()  # State itself is in a forbidden domain

        # Forbidden states constraint
        forbidden = constraints.get("forbidden_states", set())
        if state in forbidden:
            return set()

        # Cost constraint — expected cost of the action's transitions must be within budget.
        max_cost = constraints.get("max_cost")
        if max_cost is not None:
            filtered = {
                a for a in filtered if self._candidate_feature(state, a, _COST_FEATURE, action_operators) <= max_cost
            }

        # Latency constraint
        max_latency = constraints.get("max_latency_ms")
        if max_latency is not None:
            filtered = {
                a
                for a in filtered
                if self._candidate_feature(state, a, _LATENCY_FEATURE, action_operators) <= max_latency
            }

        # Confidence constraint
        min_confidence = constraints.get("min_confidence", 0.0)
        if min_confidence > 0:
            filtered = {
                a
                for a in filtered
                if self._candidate_feature(state, a, _CONFIDENCE_FEATURE, action_operators) >= min_confidence
            }

        return filtered


# Lazy feature imports
_COST_FEATURE = None
_LATENCY_FEATURE = None
_CONFIDENCE_FEATURE = None


def _init_features():
    global _COST_FEATURE, _LATENCY_FEATURE, _CONFIDENCE_FEATURE
    from src.monkey_brain.kernel.compile.tensor import Feature

    _COST_FEATURE = Feature.COST
    _LATENCY_FEATURE = Feature.LATENCY
    _CONFIDENCE_FEATURE = Feature.CONFIDENCE


_init_features()
