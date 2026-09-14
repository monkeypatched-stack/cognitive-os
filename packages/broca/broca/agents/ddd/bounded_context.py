"""BoundedContextAgent — owns a business capability, coordinates its aggregates."""

from __future__ import annotations
import logging
from typing import Any
from broca.agents.ddd._base_ddd import BaseDDDAgent

logger = logging.getLogger("broca.agents.ddd.bounded_context")


class BoundedContextAgent(BaseDDDAgent):
    """Bounded context agent — owns a well-defined business capability.

    Perceive: reads the question, identifies which aggregate to activate.
    Reason:   determines scope — can this context own the request?
    Act:      activates the right aggregate and records context boundary.
    Learn:    tracks which aggregates handle which request patterns.
    """

    agent_type = "bounded_context"
    description = (
        "Bounded context agent — owns a business capability, enforces context boundaries, coordinates aggregates"
    )
    ddd_layer = "bounded_context"
    workload_spec = "architecture_review"

    def __init__(self, context_name: str = "software_engineering", **kwargs) -> None:
        super().__init__(**kwargs)
        self._context_name = context_name
        self._aggregate_calls: dict[str, int] = {}

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        question = context.get("question", "").lower()
        return {
            "layer": self.ddd_layer,
            "context_name": self._context_name,
            "question": question,
            "pipeline_state": {k: v for k, v in context.items() if k != "question"},
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        question = perception.get("question", "")

        # Identify aggregate scope
        if any(w in question for w in ("pr", "pull request", "merge", "review")):
            aggregate = "pull_request"
        elif any(w in question for w in ("test", "suite", "coverage", "unit")):
            aggregate = "test_suite"
        elif any(w in question for w in ("deploy", "production", "staging", "canary")):
            aggregate = "deployment"
        elif any(w in question for w in ("pipeline", "ci", "build")):
            aggregate = "pipeline"
        else:
            aggregate = "generic"

        return {
            "layer": self.ddd_layer,
            "context_name": self._context_name,
            "activate_aggregate": aggregate,
            "action": "activate_aggregate",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        aggregate = decision.get("activate_aggregate", "generic")
        self._aggregate_calls[aggregate] = self._aggregate_calls.get(aggregate, 0) + 1
        return {
            "action": "context_boundary_enforced",
            "context": self._context_name,
            "activated_aggregate": aggregate,
            "aggregate_call_counts": dict(self._aggregate_calls),
        }

    def learn(self, outcome: dict[str, Any]) -> None:
        pass
