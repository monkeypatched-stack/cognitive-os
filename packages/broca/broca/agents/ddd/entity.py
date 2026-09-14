"""EntityAgent — manages entity lifecycle, identity, and state transitions."""

from __future__ import annotations
import logging
from typing import Any
from broca.agents.ddd._base_ddd import BaseDDDAgent

logger = logging.getLogger("broca.agents.ddd.entity")


class EntityAgent(BaseDDDAgent):
    """Entity agent — owns lifecycle and identity.

    Perceive: reads entity ID, type, current state, requested transition.
    Reason:   validates whether the transition is allowed.
    Act:      executes the transition or rejects it.
    Learn:    tracks which transitions succeed/fail most frequently.
    """

    agent_type = "entity"
    description = "Entity agent — manages entity lifecycle, validates state transitions, maintains identity"
    ddd_layer = "entity"
    workload_spec = "source_control"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._transition_log: list[dict] = []

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "layer": self.ddd_layer,
            "entity_id": context.get("entity_id", "unknown"),
            "entity_type": context.get("entity_type", "generic"),
            "current_state": context.get("current_state", "created"),
            "target_state": context.get("target_state"),
            "allowed_transitions": context.get("allowed_transitions", {}),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        current = perception.get("current_state", "created")
        target = perception.get("target_state")
        allowed = perception.get("allowed_transitions", {}).get(current, [])

        valid = target in allowed if target else False
        return {
            "layer": self.ddd_layer,
            "entity_id": perception.get("entity_id"),
            "entity_type": perception.get("entity_type"),
            "current_state": current,
            "target_state": target,
            "valid_transition": valid,
            "allowed": allowed,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        entry = {
            "entity_id": decision.get("entity_id"),
            "from": decision.get("current_state"),
            "to": decision.get("target_state"),
            "valid": decision.get("valid_transition"),
        }
        self._transition_log.append(entry)

        if decision.get("valid_transition"):
            return {
                "action": "state_transitioned",
                "entity_id": decision.get("entity_id"),
                "entity_type": decision.get("entity_type"),
                "from_state": decision.get("current_state"),
                "to_state": decision.get("target_state"),
            }
        return {
            "action": "transition_rejected",
            "entity_id": decision.get("entity_id"),
            "entity_type": decision.get("entity_type"),
            "current_state": decision.get("current_state"),
            "requested": decision.get("target_state"),
            "allowed": decision.get("allowed"),
        }

    def learn(self, outcome: dict[str, Any]) -> None:
        if outcome.get("action") == "transition_rejected":
            logger.debug(
                "[entity] rejected transition: %s → %s",
                outcome.get("current_state"),
                outcome.get("requested"),
            )
