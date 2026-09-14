"""Runtime Validator — validates all execution components before execution.

The runtime should validate:
- IntentIR
- Capability contracts
- Response schemas
- Repository consistency
- Policy selection

Reject invalid executions early.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger("agentos.runtime_validator")


class ValidationError(Exception):
    """Raised when validation fails."""

    pass


@runtime_checkable
class Validatable(Protocol):
    """Interface for objects that can be validated."""

    def validate(self) -> list[str]: ...


class IntentIRValidator:
    """Validates IntentIR structure and content."""

    REQUIRED_FIELDS = [
        "schema_version",
        "intent_ir_id",
        "run_id",
        "intent_type",
        "goal",
    ]
    VALID_INTENT_TYPES = {
        "planned",
        "query",
        "create",
        "update",
        "delete",
        "analyze",
        "compare",
    }

    def validate(self, intent_ir: dict[str, Any]) -> list[str]:
        """Validate IntentIR. Returns list of errors."""
        errors = []

        for field in self.REQUIRED_FIELDS:
            if field not in intent_ir:
                errors.append(f"Missing required field: {field}")

        if "intent_type" in intent_ir:
            if intent_ir["intent_type"] not in self.VALID_INTENT_TYPES:
                errors.append(f"Invalid intent_type: {intent_ir['intent_type']}")

        if "goal" in intent_ir:
            goal = intent_ir["goal"]
            if not isinstance(goal, dict):
                errors.append("goal must be a dict")
            elif not goal.get("name"):
                errors.append("goal.name is required")

        return errors


class CapabilityContractValidator:
    """Validates that capabilities meet their contracts."""

    def validate_capability(self, capability: Any) -> list[str]:
        """Validate a capability implements required interface."""
        errors = []

        if not hasattr(capability, "execute"):
            errors.append("Capability must have execute() method")

        if not hasattr(capability, "name"):
            errors.append("Capability must have name attribute")

        if not hasattr(capability, "description"):
            errors.append("Capability must have description attribute")

        return errors

    def validate_step(self, step: dict[str, Any]) -> list[str]:
        """Validate an execution step."""
        errors = []

        if not step.get("capability_name"):
            errors.append("Step must have capability_name")

        if not step.get("step_id"):
            errors.append("Step must have step_id")

        return errors


class ResponseSchemaValidator:
    """Validates response objects meet expected schemas."""

    REQUIRED_FIELDS = ["answer", "success"]

    def validate_response(self, response: dict[str, Any]) -> list[str]:
        """Validate a response object."""
        errors = []

        for field in self.REQUIRED_FIELDS:
            if field not in response:
                errors.append(f"Missing required field: {field}")

        if "answer" in response and not isinstance(response["answer"], str):
            errors.append("answer must be a string")

        if "success" in response and not isinstance(response["success"], bool):
            errors.append("success must be a boolean")

        return errors


class RepositoryConsistencyValidator:
    """Validates repository state consistency."""

    def validate_state_consistency(
        self,
        state: dict[str, Any],
        expected_schema: dict[str, type] | None = None,
    ) -> list[str]:
        """Validate state against expected schema."""
        errors = []

        if expected_schema:
            for field, expected_type in expected_schema.items():
                if field not in state:
                    errors.append(f"Missing state field: {field}")
                elif not isinstance(state[field], expected_type):
                    errors.append(
                        f"State field {field} has wrong type: expected {expected_type}, got {type(state[field])}"
                    )

        return errors


class RuntimeValidator:
    """Master validator that coordinates all validation."""

    def __init__(self):
        self.intent_validator = IntentIRValidator()
        self.capability_validator = CapabilityContractValidator()
        self.response_validator = ResponseSchemaValidator()
        self.repository_validator = RepositoryConsistencyValidator()

    def validate_intent_ir(self, intent_ir: dict[str, Any]) -> None:
        """Validate IntentIR or raise ValidationError."""
        errors = self.intent_validator.validate(intent_ir)
        if errors:
            raise ValidationError(f"IntentIR validation failed: {'; '.join(errors)}")

    def validate_capability(self, capability: Any) -> None:
        """Validate capability or raise ValidationError."""
        errors = self.capability_validator.validate_capability(capability)
        if errors:
            raise ValidationError(f"Capability validation failed: {'; '.join(errors)}")

    def validate_step(self, step: dict[str, Any]) -> None:
        """Validate execution step or raise ValidationError."""
        errors = self.capability_validator.validate_step(step)
        if errors:
            raise ValidationError(f"Step validation failed: {'; '.join(errors)}")

    def validate_response(self, response: dict[str, Any]) -> None:
        """Validate response or raise ValidationError."""
        errors = self.response_validator.validate_response(response)
        if errors:
            raise ValidationError(f"Response validation failed: {'; '.join(errors)}")

    def validate_all(
        self,
        intent_ir: dict[str, Any] | None = None,
        capability: Any = None,
        steps: list[dict[str, Any]] | None = None,
        response: dict[str, Any] | None = None,
    ) -> list[str]:
        """Validate all components. Returns list of all errors."""
        all_errors = []

        if intent_ir:
            all_errors.extend(self.intent_validator.validate(intent_ir))

        if capability:
            all_errors.extend(self.capability_validator.validate_capability(capability))

        if steps:
            for i, step in enumerate(steps):
                step_errors = self.capability_validator.validate_step(step)
                all_errors.extend(f"Step {i}: {e}" for e in step_errors)

        if response:
            all_errors.extend(self.response_validator.validate_response(response))

        return all_errors
