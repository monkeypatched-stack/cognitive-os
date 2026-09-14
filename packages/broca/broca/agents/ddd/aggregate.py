"""AggregateAgent — enforces invariants and maintains transactional consistency."""

from __future__ import annotations
import logging
from typing import Any
from broca.agents.ddd._base_ddd import BaseDDDAgent

logger = logging.getLogger("broca.agents.ddd.aggregate")


class AggregateAgent(BaseDDDAgent):
    """Aggregate agent — the consistency boundary.

    Perceive: reads entity states and preconditions from context.
    Reason:   evaluates invariants — can the operation proceed?
    Act:      commits or rejects the state transition; emits domain event.
    Learn:    tracks which invariant violations occur most frequently.
    """

    agent_type = "aggregate"
    description = "Aggregate agent — enforces invariants, maintains consistency boundaries, coordinates child entities"
    ddd_layer = "aggregate"
    workload_spec = "governance_review"

    def __init__(self, aggregate_name: str = "generic", **kwargs) -> None:
        super().__init__(**kwargs)
        self._aggregate_name = aggregate_name
        self._invariant_violations: dict[str, int] = {}

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "layer": self.ddd_layer,
            "aggregate": self._aggregate_name,
            "entities": context.get("entities", []),
            "preconditions": context.get("preconditions", {}),
            "operation": context.get("operation", ""),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        preconditions = perception.get("preconditions", {})
        violations = []

        # Check preconditions passed via context
        for key, expected in preconditions.items():
            actual = perception.get(key, perception.get("preconditions", {}).get(key))
            if actual is False or actual == expected:
                violations.append(f"{key}_violated")

        return {
            "layer": self.ddd_layer,
            "aggregate": self._aggregate_name,
            "consistent": len(violations) == 0,
            "violations": violations,
            "action": "commit" if len(violations) == 0 else "reject",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        for v in decision.get("violations", []):
            self._invariant_violations[v] = self._invariant_violations.get(v, 0) + 1

        if decision.get("consistent"):
            return {
                "action": "state_committed",
                "aggregate": self._aggregate_name,
                "consistent": True,
                "domain_event": f"{self._aggregate_name}_state_changed",
            }
        return {
            "action": "state_rejected",
            "aggregate": self._aggregate_name,
            "consistent": False,
            "violations": decision.get("violations"),
            "domain_event": f"{self._aggregate_name}_invariant_violated",
        }

    def learn(self, outcome: dict[str, Any]) -> None:
        if outcome.get("action") == "state_rejected":
            logger.debug(
                "[aggregate:%s] invariant violations: %s",
                self._aggregate_name,
                self._invariant_violations,
            )
