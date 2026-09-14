"""ValueObjectAgent — validates, transforms, and composes immutable values."""

from __future__ import annotations
import logging
from typing import Any, Callable
from broca.agents.ddd._base_ddd import BaseDDDAgent

logger = logging.getLogger("broca.agents.ddd.value_object")


class ValueObjectAgent(BaseDDDAgent):
    """Value object agent — owns transformation and validation.

    Perceive: reads raw value and expected constraints.
    Reason:   validates constraints and determines transformation pipeline.
    Act:      transforms the value or reports validation failures.
    Learn:    no state — value objects are stateless by definition.
    """

    agent_type = "value_object"
    description = "Value object agent — validates constraints, transforms values, composes immutable domain types"
    ddd_layer = "value_object"
    workload_spec = "code_generation"

    def __init__(
        self,
        validators: list[Callable] | None = None,
        transformations: list[Callable] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._validators: list[Callable] = validators or []
        self._transformations: list[Callable] = transformations or []

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "layer": self.ddd_layer,
            "value_type": context.get("value_type", "generic"),
            "raw_value": context.get("value"),
            "constraints": context.get("constraints", {}),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        raw = perception.get("raw_value")
        constraints = perception.get("constraints", {})
        errors = []

        # Built-in constraint checks
        if "min_length" in constraints and isinstance(raw, str) and len(raw) < constraints["min_length"]:
            errors.append(f"too_short: min {constraints['min_length']}")
        if "max_length" in constraints and isinstance(raw, str) and len(raw) > constraints["max_length"]:
            errors.append(f"too_long: max {constraints['max_length']}")
        if "non_empty" in constraints and constraints["non_empty"] and not raw:
            errors.append("must_not_be_empty")
        if "allowed_values" in constraints and raw not in constraints["allowed_values"]:
            errors.append(f"not_in_allowed_values: {constraints['allowed_values']}")

        # Custom validator functions
        for v in self._validators:
            try:
                if not v(raw):
                    errors.append(f"validator_failed: {getattr(v, '__name__', 'anonymous')}")
            except Exception as exc:
                errors.append(f"validator_error: {exc}")

        return {
            "layer": self.ddd_layer,
            "value_type": perception.get("value_type"),
            "raw_value": raw,
            "valid": len(errors) == 0,
            "errors": errors,
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        if not decision.get("valid"):
            return {
                "action": "validation_failed",
                "value_type": decision.get("value_type"),
                "errors": decision.get("errors"),
            }

        value = decision.get("raw_value")
        for transform in self._transformations:
            try:
                value = transform(value)
            except Exception as exc:
                return {"action": "transform_failed", "error": str(exc)}

        return {
            "action": "value_validated",
            "value_type": decision.get("value_type"),
            "value": value,
        }

    def learn(self, outcome: dict[str, Any]) -> None:
        pass  # value objects are stateless
