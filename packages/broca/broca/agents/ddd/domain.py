"""DomainAgent — reasons about business strategy across bounded contexts."""

from __future__ import annotations
import logging
from typing import Any
from broca.agents.ddd._base_ddd import BaseDDDAgent

logger = logging.getLogger("broca.agents.ddd.domain")


class DomainAgent(BaseDDDAgent):
    """Top-level domain agent — coordinates bounded contexts, enforces ubiquitous language.

    Perceive: reads goal, active domain, registered contexts from state.
    Reason:   decides which bounded context should own this work.
    Act:      delegates and records domain-level intent.
    Learn:    tracks which context routing worked for future queries.
    """

    agent_type = "domain"
    description = "Domain agent — owns business strategy, coordinates bounded contexts, enforces ubiquitous language"
    ddd_layer = "domain"
    workload_spec = "source_control"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._context_routing: dict[str, int] = {}  # context_name → success count

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "layer": self.ddd_layer,
            "goal": context.get("goal", ""),
            "question": context.get("question", ""),
            "domain": context.get("domain", "software_engineering"),
            "active_contexts": context.get("active_contexts", []),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        question = perception.get("question", "").lower()

        # Route to the most relevant bounded context
        if any(w in question for w in ("deploy", "release", "rollout")):
            target_context = "deployment"
        elif any(w in question for w in ("test", "coverage", "ci")):
            target_context = "testing"
        elif any(w in question for w in ("review", "pr", "merge")):
            target_context = "code_review"
        elif any(w in question for w in ("generate", "code", "implement")):
            target_context = "code_generation"
        else:
            target_context = "software_engineering"

        return {
            "layer": self.ddd_layer,
            "target_context": target_context,
            "domain": perception.get("domain"),
            "action": "delegate_to_context",
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        context_name = decision.get("target_context")
        self._context_routing[context_name] = self._context_routing.get(context_name, 0) + 1
        return {
            "action": "domain_routed",
            "target_context": context_name,
            "domain": decision.get("domain"),
            "routing_history": dict(self._context_routing),
        }

    def learn(self, outcome: dict[str, Any]) -> None:
        pass
