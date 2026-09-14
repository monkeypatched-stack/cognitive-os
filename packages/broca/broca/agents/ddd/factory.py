"""FactoryAgent — creates domain objects with validated parameters and defaults."""

from __future__ import annotations
import logging
import uuid
from typing import Any, Callable
from broca.agents.ddd._base_ddd import BaseDDDAgent

logger = logging.getLogger("broca.agents.ddd.factory")


class FactoryAgent(BaseDDDAgent):
    """Factory agent — owns creation.

    Perceive: reads requested object type and creation parameters.
    Reason:   validates parameters, checks if type is registered.
    Act:      creates the object via registered creator; emits creation event.
    Learn:    tracks which types are created most frequently.
    """

    agent_type = "factory"
    description = (
        "Factory agent — creates domain objects with validated parameters, applies defaults, emits creation events"
    )
    ddd_layer = "factory"
    workload_spec = "code_generation"

    def __init__(self, creators: dict[str, Callable[..., Any]] | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._creators: dict[str, Callable[..., Any]] = creators or {}
        self._creation_counts: dict[str, int] = {}

    def register_creator(self, type_name: str, creator: Callable[..., Any]) -> None:
        self._creators[type_name] = creator

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "layer": self.ddd_layer,
            "object_type": context.get("type", ""),
            "params": context.get("params", {}),
            "known_types": list(self._creators.keys()),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        obj_type = perception.get("object_type", "")
        can_create = obj_type in self._creators
        return {
            "layer": self.ddd_layer,
            "object_type": obj_type,
            "can_create": can_create,
            "params": perception.get("params", {}),
            "known_types": perception.get("known_types"),
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        obj_type = decision.get("object_type", "")

        if not decision.get("can_create"):
            return {
                "action": "creation_failed",
                "object_type": obj_type,
                "reason": "unknown_type",
                "known_types": decision.get("known_types"),
            }

        try:
            creator = self._creators[obj_type]
            params = decision.get("params", {})
            obj = creator(**params)
            obj_id = getattr(obj, "id", None) or str(uuid.uuid4())
            self._creation_counts[obj_type] = self._creation_counts.get(obj_type, 0) + 1
            return {
                "action": "created",
                "object_type": obj_type,
                "object_id": obj_id,
                "domain_event": f"{obj_type}_created",
            }
        except Exception as exc:
            logger.error("[factory] creation failed for %s: %s", obj_type, exc)
            return {
                "action": "creation_failed",
                "object_type": obj_type,
                "error": str(exc),
            }

    def learn(self, outcome: dict[str, Any]) -> None:
        if outcome.get("action") == "created":
            logger.debug(
                "[factory] top created types: %s",
                sorted(self._creation_counts.items(), key=lambda x: -x[1])[:5],
            )
