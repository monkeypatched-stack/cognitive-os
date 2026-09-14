"""Operator library (Step 8.2) — explicit, parameterized planning operators.

An operator TEMPLATE is a pure factory for a PlanningOperator (the immutable data
type from domain.py): given domain-specific parameters (an item name, a
destination, ...), it returns a fully-described PlanningOperator. Templates hold
no state, perform no execution, and never mutate runtime state — they only
describe an action. Performing one is the execution runtime's job
(execution.py and its action-execution collaborators), untouched by this
module.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from src.monkey_brain.kernel.pipeline.planning.domain import PlanningOperator


@runtime_checkable
class OperatorTemplate(Protocol):
    """Interface every operator template satisfies.

    A template is a pure, parameterized factory for a PlanningOperator. Building
    an operator has no side effects: no execution, no runtime state mutation.
    """

    name: str

    def build(self, **params: Any) -> PlanningOperator: ...


class AcquireItem:
    """Acquire a quantity of an item from a source (e.g. purchase, pick up)."""

    name = "AcquireItem"

    def build(
        self,
        *,
        item: str,
        quantity: float = 1.0,
        unit: str = "unit",
        estimated_cost: float = 0.2,
        estimated_duration: float = 180.0,
    ) -> PlanningOperator:
        return PlanningOperator(
            name=self.name,
            description=f"Acquire {quantity} {unit} of {item}",
            preconditions=("at_location", f"{item}_in_stock"),
            effects=(f"{item}_acquired",),
            estimated_cost=estimated_cost,
            estimated_duration=estimated_duration,
            required_capabilities=("shopping", "payment"),
            metadata={"item": item, "quantity": quantity, "unit": unit},
        )


class Navigate:
    """Travel from the current location to a destination."""

    name = "Navigate"

    def build(
        self,
        *,
        destination: str,
        from_location: str = "current_location",
        estimated_cost: float = 0.1,
        estimated_duration: float = 600.0,
    ) -> PlanningOperator:
        return PlanningOperator(
            name=self.name,
            description=f"Navigate from {from_location} to {destination}",
            preconditions=("location_known",),
            effects=("at_location",),
            estimated_cost=estimated_cost,
            estimated_duration=estimated_duration,
            required_capabilities=("navigation",),
            metadata={"destination": destination, "from_location": from_location},
        )


class QueryInventory:
    """Check whether an item is available, without acquiring it. Read-only."""

    name = "QueryInventory"

    def build(
        self,
        *,
        item: str,
        estimated_cost: float = 0.02,
        estimated_duration: float = 5.0,
    ) -> PlanningOperator:
        return PlanningOperator(
            name=self.name,
            description=f"Query inventory for {item}",
            preconditions=(),
            effects=(f"{item}_availability_known",),
            estimated_cost=estimated_cost,
            estimated_duration=estimated_duration,
            required_capabilities=("inventory_query",),
            metadata={"item": item},
        )


class Wait:
    """Pause for a fixed duration (e.g. waiting for a delivery window)."""

    name = "Wait"

    def build(
        self,
        *,
        duration_seconds: float,
        reason: str = "",
        estimated_cost: float = 0.0,
    ) -> PlanningOperator:
        return PlanningOperator(
            name=self.name,
            description=f"Wait {duration_seconds}s" + (f" ({reason})" if reason else ""),
            preconditions=(),
            effects=(f"waited:{duration_seconds}s",),
            estimated_cost=estimated_cost,
            estimated_duration=duration_seconds,
            required_capabilities=(),
            metadata={"duration_seconds": duration_seconds, "reason": reason},
        )


class Notify:
    """Send a notification to a recipient. No runtime state mutation."""

    name = "Notify"

    def build(
        self,
        *,
        recipient: str,
        message: str,
        estimated_cost: float = 0.01,
        estimated_duration: float = 1.0,
    ) -> PlanningOperator:
        return PlanningOperator(
            name=self.name,
            description=f"Notify {recipient}: {message}",
            preconditions=(),
            effects=(f"notified:{recipient}",),
            estimated_cost=estimated_cost,
            estimated_duration=estimated_duration,
            required_capabilities=("messaging",),
            metadata={"recipient": recipient, "message": message},
        )


class ReserveResource:
    """Reserve a quantity of a resource for later use, without consuming it."""

    name = "ReserveResource"

    def build(
        self,
        *,
        resource: str,
        quantity: float = 1.0,
        duration_seconds: float = 0.0,
        estimated_cost: float = 0.05,
        estimated_duration: float = 10.0,
    ) -> PlanningOperator:
        return PlanningOperator(
            name=self.name,
            description=f"Reserve {quantity} of {resource}",
            preconditions=(f"{resource}_available",),
            effects=(f"{resource}_reserved",),
            estimated_cost=estimated_cost,
            estimated_duration=estimated_duration,
            required_capabilities=("reservation",),
            metadata={
                "resource": resource,
                "quantity": quantity,
                "duration_seconds": duration_seconds,
            },
        )


# Registry — name -> template instance. Templates are stateless, so one shared
# instance per template is sufficient; 8.3+ can look operators up by name here
# rather than hardcoding imports.
OPERATOR_TEMPLATES: dict[str, OperatorTemplate] = {
    t.name: t
    for t in (
        AcquireItem(),
        Navigate(),
        QueryInventory(),
        Wait(),
        Notify(),
        ReserveResource(),
    )
}
