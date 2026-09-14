"""Operator execution framework (Step 9.2) — separates planning operators
from execution handlers.

A PlanningOperator (Step 8.2) describes an action; an ExecutionHandler here
actually carries it out (or simulates it — these sample handlers simulate,
matching ActionExecutor's own default behavior with no capability_bus).
ExecutionRegistry maps operator name -> ExecutionHandler, giving the
"PlanningOperator -> ExecutionHandler" mapping this step's spec asks for.

Handlers never plan: they receive an already-decided ExecutionStep
(operator + parameters already chosen by the planning stage) and only
validate its parameters and produce an outcome.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from src.monkey_brain.kernel.pipeline.execution_runtime.domain import (
    ExecutionContext,
    ExecutionError,
    ExecutionOutcome,
    ExecutionStatus,
    ExecutionStep,
)


@dataclass(frozen=True)
class ExecutionCapability:
    """One thing an ExecutionHandler declares it can provide.

    Lightweight by design — full availability/permission/capacity resolution
    is Step 9.5's job. This is just "what a handler says it does," useful for
    discovery (ExecutionRegistry.capabilities()) before that resolution runs.
    """

    name: str = ""
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ExecutionHandler(Protocol):
    """Interface every execution handler satisfies.

    Handles exactly one named operator. Never plans — only validates
    parameters and executes (or simulates) the one operator it owns.
    """

    operator_name: str
    capability: ExecutionCapability

    def validate(self, step: ExecutionStep) -> ExecutionError | None:
        """None if step.parameters are usable by this handler; otherwise the
        ExecutionError describing what's wrong. Pure — no side effects."""
        ...

    def execute(self, step: ExecutionStep, context: ExecutionContext) -> ExecutionOutcome:
        """Carry out (or simulate) the operator. Always a complete, safe
        entry point on its own — calls validate() itself and returns a
        FAILED outcome rather than raising if the step is invalid."""
        ...


class SimulatedHandler:
    """Base for handlers that simulate their operator's effect rather than
    invoking a real capability — matching ActionExecutor's own default
    behavior when no capability_bus is configured. Subclasses set
    operator_name / capability / required_parameters and implement
    _produce_output(); validate() and execute() are shared here so each
    concrete handler only states what makes it different.
    """

    operator_name: str = ""
    capability: ExecutionCapability = ExecutionCapability()
    required_parameters: tuple[str, ...] = ()

    def validate(self, step: ExecutionStep) -> ExecutionError | None:
        missing = [p for p in self.required_parameters if p not in step.parameters]
        if missing:
            return ExecutionError(
                step_id=step.step_id,
                code="MISSING_PARAMETERS",
                message=f"missing required parameter(s): {missing}",
                retryable=False,
            )
        return None

    def execute(self, step: ExecutionStep, context: ExecutionContext) -> ExecutionOutcome:
        started_at = time.time()
        error = self.validate(step)
        if error is not None:
            return ExecutionOutcome(
                step_id=step.step_id,
                status=ExecutionStatus.FAILED,
                error=error,
                started_at=started_at,
                ended_at=time.time(),
            )
        output = self._produce_output(step)
        return ExecutionOutcome(
            step_id=step.step_id,
            status=ExecutionStatus.SUCCEEDED,
            output=output,
            started_at=started_at,
            ended_at=time.time(),
        )

    def _produce_output(self, step: ExecutionStep) -> dict[str, Any]:
        raise NotImplementedError


# ── Sample handlers — one per Step 8.2 operator ─────────────────────────────


class NavigateHandler(SimulatedHandler):
    operator_name = "Navigate"
    capability = ExecutionCapability(name="navigation", description="Move to a destination")
    required_parameters = ("destination",)

    def _produce_output(self, step: ExecutionStep) -> dict[str, Any]:
        return {"arrived_at": step.parameters["destination"], "simulated": True}


class AcquireItemHandler(SimulatedHandler):
    operator_name = "AcquireItem"
    capability = ExecutionCapability(name="shopping", description="Acquire an item")
    required_parameters = ("item",)

    def _produce_output(self, step: ExecutionStep) -> dict[str, Any]:
        return {
            "acquired": step.parameters["item"],
            "quantity": step.parameters.get("quantity", 1),
            "simulated": True,
        }


class QueryInventoryHandler(SimulatedHandler):
    operator_name = "QueryInventory"
    capability = ExecutionCapability(name="inventory_query", description="Check item availability")
    required_parameters = ("item",)

    def _produce_output(self, step: ExecutionStep) -> dict[str, Any]:
        return {"item": step.parameters["item"], "in_stock": True, "simulated": True}


class WaitHandler(SimulatedHandler):
    operator_name = "Wait"
    capability = ExecutionCapability(name="wait", description="Pause for a fixed duration")
    required_parameters = ("duration_seconds",)

    def _produce_output(self, step: ExecutionStep) -> dict[str, Any]:
        return {
            "waited_seconds": step.parameters["duration_seconds"],
            "simulated": True,
        }


class NotifyHandler(SimulatedHandler):
    operator_name = "Notify"
    capability = ExecutionCapability(name="messaging", description="Send a notification")
    required_parameters = ("recipient", "message")

    def _produce_output(self, step: ExecutionStep) -> dict[str, Any]:
        return {
            "notified": step.parameters["recipient"],
            "message": step.parameters["message"],
            "simulated": True,
        }


class ReserveResourceHandler(SimulatedHandler):
    operator_name = "ReserveResource"
    capability = ExecutionCapability(name="reservation", description="Reserve a resource")
    required_parameters = ("resource",)

    def _produce_output(self, step: ExecutionStep) -> dict[str, Any]:
        return {
            "reserved": step.parameters["resource"],
            "quantity": step.parameters.get("quantity", 1),
            "simulated": True,
        }


DEFAULT_HANDLERS: tuple[ExecutionHandler, ...] = (
    NavigateHandler(),
    AcquireItemHandler(),
    QueryInventoryHandler(),
    WaitHandler(),
    NotifyHandler(),
    ReserveResourceHandler(),
)


class ExecutionRegistry:
    """Maps operator name -> ExecutionHandler and dispatches ExecutionSteps
    to the right one."""

    def __init__(self, handlers: dict[str, ExecutionHandler] | None = None) -> None:
        self._handlers: dict[str, ExecutionHandler] = (
            dict(handlers) if handlers is not None else {h.operator_name: h for h in DEFAULT_HANDLERS}
        )

    def register(self, handler: ExecutionHandler) -> None:
        self._handlers[handler.operator_name] = handler

    def get(self, operator_name: str) -> ExecutionHandler | None:
        return self._handlers.get(operator_name)

    def capabilities(self) -> tuple[ExecutionCapability, ...]:
        return tuple(h.capability for h in self._handlers.values())

    def dispatch(self, step: ExecutionStep, context: ExecutionContext | None = None) -> ExecutionOutcome:
        """Look up the handler for step.operator.name and execute it.
        A step with no operator, or an operator with no registered handler,
        fails cleanly (a real ExecutionOutcome/ExecutionError) rather than
        raising — a scheduler can inspect and react to this like any other
        failure."""
        started_at = time.time()
        operator_name = step.operator.name if step.operator is not None else None

        if operator_name is None:
            return ExecutionOutcome(
                step_id=step.step_id,
                status=ExecutionStatus.FAILED,
                error=ExecutionError(
                    step_id=step.step_id,
                    code="NO_OPERATOR",
                    message="step has no operator to execute",
                    retryable=False,
                ),
                started_at=started_at,
                ended_at=time.time(),
            )

        handler = self._handlers.get(operator_name)
        if handler is None:
            return ExecutionOutcome(
                step_id=step.step_id,
                status=ExecutionStatus.FAILED,
                error=ExecutionError(
                    step_id=step.step_id,
                    code="NO_HANDLER",
                    message=f"no handler registered for operator '{operator_name}'",
                    retryable=False,
                ),
                started_at=started_at,
                ended_at=time.time(),
            )

        return handler.execute(step, context or ExecutionContext())
