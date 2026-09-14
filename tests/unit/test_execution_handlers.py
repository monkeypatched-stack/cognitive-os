"""Tests for the operator execution framework (Step 9.2).

Validates the ExecutionHandler interface, each of the 6 sample handlers
(matching Step 8.2's 6 planning operators one-to-one), ExecutionRegistry
dispatch (including the "no operator"/"no handler" failure paths), and
"never perform planning" (handlers only validate + execute, no planning
decisions).
"""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.pipeline.planning.domain import PlanningOperator
from src.monkey_brain.kernel.pipeline.execution_runtime.domain import (
    ExecutionStep,
    ExecutionContext,
    ExecutionStatus,
)
from src.monkey_brain.kernel.pipeline.execution_runtime.handlers import (
    ExecutionCapability,
    ExecutionHandler,
    SimulatedHandler,
    NavigateHandler,
    AcquireItemHandler,
    QueryInventoryHandler,
    WaitHandler,
    NotifyHandler,
    ReserveResourceHandler,
    DEFAULT_HANDLERS,
    ExecutionRegistry,
)


def _step(operator_name: str, **parameters) -> ExecutionStep:
    return ExecutionStep(operator=PlanningOperator(name=operator_name), parameters=parameters)


# ═══════════════════════════════════════════════════════════════════════════
# ExecutionHandler interface
# ═══════════════════════════════════════════════════════════════════════════


class TestExecutionHandlerInterface:
    @pytest.mark.parametrize(
        "handler_cls",
        [
            NavigateHandler,
            AcquireItemHandler,
            QueryInventoryHandler,
            WaitHandler,
            NotifyHandler,
            ReserveResourceHandler,
        ],
    )
    def test_satisfies_execution_handler_protocol(self, handler_cls):
        handler = handler_cls()
        assert isinstance(handler, ExecutionHandler)
        assert isinstance(handler.operator_name, str) and handler.operator_name
        assert isinstance(handler.capability, ExecutionCapability)

    def test_default_handlers_cover_all_six_operators(self):
        expected = {
            "Navigate",
            "AcquireItem",
            "QueryInventory",
            "Wait",
            "Notify",
            "ReserveResource",
        }
        assert {h.operator_name for h in DEFAULT_HANDLERS} == expected


# ═══════════════════════════════════════════════════════════════════════════
# Per-handler validation + execution
# ═══════════════════════════════════════════════════════════════════════════


class TestNavigateHandler:
    def test_execute_succeeds_with_destination(self):
        outcome = NavigateHandler().execute(_step("Navigate", destination="store"), ExecutionContext())
        assert outcome.status == ExecutionStatus.SUCCEEDED
        assert outcome.output["arrived_at"] == "store"

    def test_validate_fails_without_destination(self):
        error = NavigateHandler().validate(_step("Navigate"))
        assert error is not None
        assert error.code == "MISSING_PARAMETERS"

    def test_execute_fails_without_destination(self):
        outcome = NavigateHandler().execute(_step("Navigate"), ExecutionContext())
        assert outcome.status == ExecutionStatus.FAILED
        assert outcome.error.code == "MISSING_PARAMETERS"


class TestAcquireItemHandler:
    def test_execute_succeeds_with_item(self):
        outcome = AcquireItemHandler().execute(_step("AcquireItem", item="milk", quantity=2), ExecutionContext())
        assert outcome.status == ExecutionStatus.SUCCEEDED
        assert outcome.output == {"acquired": "milk", "quantity": 2, "simulated": True}

    def test_quantity_defaults_to_one(self):
        outcome = AcquireItemHandler().execute(_step("AcquireItem", item="eggs"), ExecutionContext())
        assert outcome.output["quantity"] == 1

    def test_validate_fails_without_item(self):
        assert AcquireItemHandler().validate(_step("AcquireItem")) is not None


class TestQueryInventoryHandler:
    def test_execute_succeeds(self):
        outcome = QueryInventoryHandler().execute(_step("QueryInventory", item="milk"), ExecutionContext())
        assert outcome.status == ExecutionStatus.SUCCEEDED
        assert outcome.output["item"] == "milk"
        assert outcome.output["in_stock"] is True


class TestWaitHandler:
    def test_execute_succeeds_with_duration(self):
        outcome = WaitHandler().execute(_step("Wait", duration_seconds=30.0), ExecutionContext())
        assert outcome.status == ExecutionStatus.SUCCEEDED
        assert outcome.output["waited_seconds"] == 30.0

    def test_validate_fails_without_duration(self):
        assert WaitHandler().validate(_step("Wait")) is not None


class TestNotifyHandler:
    def test_execute_succeeds_with_recipient_and_message(self):
        outcome = NotifyHandler().execute(
            _step("Notify", recipient="alice", message="done"),
            ExecutionContext(),
        )
        assert outcome.status == ExecutionStatus.SUCCEEDED
        assert outcome.output["notified"] == "alice"

    def test_validate_fails_without_message(self):
        error = NotifyHandler().validate(_step("Notify", recipient="alice"))
        assert error is not None
        assert "message" in error.message


class TestReserveResourceHandler:
    def test_execute_succeeds_with_resource(self):
        outcome = ReserveResourceHandler().execute(
            _step("ReserveResource", resource="delivery_slot"),
            ExecutionContext(),
        )
        assert outcome.status == ExecutionStatus.SUCCEEDED
        assert outcome.output["reserved"] == "delivery_slot"


# ═══════════════════════════════════════════════════════════════════════════
# Timing and outcome shape (SimulatedHandler base)
# ═══════════════════════════════════════════════════════════════════════════


class TestSimulatedHandlerBase:
    def test_outcome_has_step_id_and_timing(self):
        step = _step("Navigate", destination="store")
        outcome = NavigateHandler().execute(step, ExecutionContext())
        assert outcome.step_id == step.step_id
        assert outcome.started_at > 0
        assert outcome.ended_at >= outcome.started_at

    def test_failed_outcome_still_has_timing(self):
        outcome = NavigateHandler().execute(_step("Navigate"), ExecutionContext())
        assert outcome.started_at > 0
        assert outcome.ended_at >= outcome.started_at

    def test_never_performs_planning(self):
        """A handler must not select operators or make planning decisions —
        it only acts on the operator it was already given."""
        handler = NavigateHandler()
        assert not hasattr(handler, "plan")
        assert not hasattr(handler, "decompose")
        assert not hasattr(handler, "generate_candidates")


# ═══════════════════════════════════════════════════════════════════════════
# ExecutionRegistry — the PlanningOperator -> ExecutionHandler mapping
# ═══════════════════════════════════════════════════════════════════════════


class TestExecutionRegistry:
    def test_dispatches_to_the_correct_handler(self):
        registry = ExecutionRegistry()
        outcome = registry.dispatch(_step("Navigate", destination="store"))
        assert outcome.status == ExecutionStatus.SUCCEEDED
        assert outcome.output["arrived_at"] == "store"

    def test_capabilities_lists_all_registered_handlers(self):
        registry = ExecutionRegistry()
        names = {c.name for c in registry.capabilities()}
        assert names == {
            "navigation",
            "shopping",
            "inventory_query",
            "wait",
            "messaging",
            "reservation",
        }

    def test_step_with_no_operator_fails_cleanly(self):
        step = ExecutionStep(operator=None, parameters={})
        outcome = ExecutionRegistry().dispatch(step)
        assert outcome.status == ExecutionStatus.FAILED
        assert outcome.error.code == "NO_OPERATOR"

    def test_unregistered_operator_fails_cleanly(self):
        outcome = ExecutionRegistry().dispatch(_step("NeverRegisteredOperator"))
        assert outcome.status == ExecutionStatus.FAILED
        assert outcome.error.code == "NO_HANDLER"

    def test_custom_handler_registration(self):
        class StubHandler:
            operator_name = "StubOp"
            capability = ExecutionCapability(name="stub")

            def validate(self, step):
                return None

            def execute(self, step, context):
                from src.monkey_brain.kernel.pipeline.execution_runtime.domain import (
                    ExecutionOutcome,
                )

                return ExecutionOutcome(
                    step_id=step.step_id,
                    status=ExecutionStatus.SUCCEEDED,
                    output={"stub": True},
                )

        registry = ExecutionRegistry(handlers={})
        registry.register(StubHandler())

        outcome = registry.dispatch(_step("StubOp"))
        assert outcome.status == ExecutionStatus.SUCCEEDED
        assert outcome.output == {"stub": True}

    def test_registration_does_not_affect_other_registry_instances(self):
        class StubHandler:
            operator_name = "AnotherStubOp"
            capability = ExecutionCapability(name="stub2")

            def validate(self, step):
                return None

            def execute(self, step, context):
                from src.monkey_brain.kernel.pipeline.execution_runtime.domain import (
                    ExecutionOutcome,
                )

                return ExecutionOutcome(step_id=step.step_id, status=ExecutionStatus.SUCCEEDED)

        registry_a = ExecutionRegistry()
        registry_a.register(StubHandler())

        assert ExecutionRegistry().get("AnotherStubOp") is None

    def test_get_returns_none_for_unregistered_operator(self):
        assert ExecutionRegistry().get("NotRegistered") is None

    def test_context_defaults_when_not_provided(self):
        """dispatch() without an explicit context must not raise."""
        registry = ExecutionRegistry()
        outcome = registry.dispatch(_step("Navigate", destination="store"))
        assert outcome.status == ExecutionStatus.SUCCEEDED


# ═══════════════════════════════════════════════════════════════════════════
# Ownership boundary
# ═══════════════════════════════════════════════════════════════════════════


class TestOwnershipBoundary:
    def test_no_planning_engine_or_cognitive_runtime_coupling(self):
        import inspect
        import src.monkey_brain.kernel.pipeline.execution_runtime.handlers as mod

        source = inspect.getsource(mod)
        forbidden = [
            "belief_runtime",
            "planning_engine",
            "from src.monkey_brain.kernel.pipeline.planner import",
        ]
        for imp in forbidden:
            assert imp not in source, f"handlers.py must not import: {imp}"
