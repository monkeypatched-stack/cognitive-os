"""Tests for the operator library (Step 8.2).

Validates the OperatorTemplate interface, each sample operator's
preconditions/effects, and purity (no shared state, no side effects, no
coupling to execution/runtime code).
"""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.pipeline.planning.domain import PlanningOperator
from src.monkey_brain.kernel.pipeline.planning.operators import (
    OperatorTemplate,
    AcquireItem,
    Navigate,
    QueryInventory,
    Wait,
    Notify,
    ReserveResource,
    OPERATOR_TEMPLATES,
)

# ═══════════════════════════════════════════════════════════════════════════
# OperatorTemplate interface
# ═══════════════════════════════════════════════════════════════════════════


class TestOperatorTemplateInterface:
    @pytest.mark.parametrize(
        "template_cls",
        [
            AcquireItem,
            Navigate,
            QueryInventory,
            Wait,
            Notify,
            ReserveResource,
        ],
    )
    def test_satisfies_operator_template_protocol(self, template_cls):
        template = template_cls()
        assert isinstance(template, OperatorTemplate)
        assert isinstance(template.name, str)
        assert template.name

    def test_registry_contains_all_six_sample_operators(self):
        expected = {
            "AcquireItem",
            "Navigate",
            "QueryInventory",
            "Wait",
            "Notify",
            "ReserveResource",
        }
        assert set(OPERATOR_TEMPLATES.keys()) == expected

    def test_registry_lookup_returns_correct_template(self):
        assert isinstance(OPERATOR_TEMPLATES["AcquireItem"], AcquireItem)
        assert isinstance(OPERATOR_TEMPLATES["Navigate"], Navigate)


# ═══════════════════════════════════════════════════════════════════════════
# AcquireItem
# ═══════════════════════════════════════════════════════════════════════════


class TestAcquireItem:
    def test_build_returns_planning_operator(self):
        op = AcquireItem().build(item="milk", quantity=2, unit="L")
        assert isinstance(op, PlanningOperator)
        assert op.name == "AcquireItem"

    def test_preconditions_and_effects_reference_item(self):
        op = AcquireItem().build(item="milk")
        assert "milk_in_stock" in op.preconditions
        assert "at_location" in op.preconditions
        assert op.effects == ("milk_acquired",)

    def test_required_capabilities(self):
        op = AcquireItem().build(item="eggs")
        assert "shopping" in op.required_capabilities
        assert "payment" in op.required_capabilities

    def test_metadata_carries_parameters(self):
        op = AcquireItem().build(item="milk", quantity=2, unit="L")
        assert op.metadata == {"item": "milk", "quantity": 2, "unit": "L"}


# ═══════════════════════════════════════════════════════════════════════════
# Navigate
# ═══════════════════════════════════════════════════════════════════════════


class TestNavigate:
    def test_preconditions_and_effects(self):
        op = Navigate().build(destination="store")
        assert op.preconditions == ("location_known",)
        assert op.effects == ("at_location",)
        assert op.metadata["destination"] == "store"

    def test_required_capabilities(self):
        op = Navigate().build(destination="store")
        assert op.required_capabilities == ("navigation",)


# ═══════════════════════════════════════════════════════════════════════════
# QueryInventory — read-only, no preconditions
# ═══════════════════════════════════════════════════════════════════════════


class TestQueryInventory:
    def test_no_preconditions_read_only(self):
        op = QueryInventory().build(item="milk")
        assert op.preconditions == ()
        assert op.effects == ("milk_availability_known",)

    def test_required_capabilities(self):
        op = QueryInventory().build(item="milk")
        assert op.required_capabilities == ("inventory_query",)


# ═══════════════════════════════════════════════════════════════════════════
# Wait
# ═══════════════════════════════════════════════════════════════════════════


class TestWait:
    def test_duration_drives_effect_and_estimated_duration(self):
        op = Wait().build(duration_seconds=30.0)
        assert op.effects == ("waited:30.0s",)
        assert op.estimated_duration == 30.0
        assert op.preconditions == ()

    def test_no_required_capabilities(self):
        op = Wait().build(duration_seconds=1.0)
        assert op.required_capabilities == ()


# ═══════════════════════════════════════════════════════════════════════════
# Notify
# ═══════════════════════════════════════════════════════════════════════════


class TestNotify:
    def test_preconditions_and_effects(self):
        op = Notify().build(recipient="alice", message="milk delivered")
        assert op.preconditions == ()
        assert op.effects == ("notified:alice",)

    def test_required_capabilities(self):
        op = Notify().build(recipient="alice", message="x")
        assert op.required_capabilities == ("messaging",)


# ═══════════════════════════════════════════════════════════════════════════
# ReserveResource
# ═══════════════════════════════════════════════════════════════════════════


class TestReserveResource:
    def test_preconditions_and_effects_reference_resource(self):
        op = ReserveResource().build(resource="delivery_slot", quantity=1)
        assert op.preconditions == ("delivery_slot_available",)
        assert op.effects == ("delivery_slot_reserved",)

    def test_required_capabilities(self):
        op = ReserveResource().build(resource="delivery_slot")
        assert op.required_capabilities == ("reservation",)


# ═══════════════════════════════════════════════════════════════════════════
# Purity — no shared/mutable state, deterministic given same inputs
# ═══════════════════════════════════════════════════════════════════════════


class TestPurity:
    def test_same_params_produce_equal_operators(self):
        a = AcquireItem().build(item="milk", quantity=2, unit="L")
        b = AcquireItem().build(item="milk", quantity=2, unit="L")
        assert a == b  # frozen dataclasses compare by value

    def test_different_params_produce_different_operators(self):
        a = AcquireItem().build(item="milk")
        b = AcquireItem().build(item="eggs")
        assert a != b

    def test_template_instances_hold_no_mutable_state(self):
        """A single template instance is reused across builds — it must not
        accumulate state between calls."""
        template = AcquireItem()
        template.build(item="milk")
        second = template.build(item="eggs")
        assert second.metadata["item"] == "eggs"  # unaffected by the prior call

    def test_build_has_no_side_effects_or_return_value_other_than_operator(self):
        op = Wait().build(duration_seconds=5.0)
        assert isinstance(op, PlanningOperator)
        # PlanningOperator itself is frozen — nothing about build() could have
        # mutated it after construction even if it tried.
        with pytest.raises(Exception):
            op.name = "Different"


# ═══════════════════════════════════════════════════════════════════════════
# Ownership boundary — no coupling to execution/runtime
# ═══════════════════════════════════════════════════════════════════════════


class TestOwnershipBoundary:
    def test_no_runtime_or_execution_imports(self):
        """operators.py must not import CognitiveRuntime or ExecutionEngine —
        templates only describe actions; ExecutionEngine performs them."""
        import inspect
        import src.monkey_brain.kernel.pipeline.planning.operators as mod

        source = inspect.getsource(mod)
        forbidden = [
            "belief_runtime",
            "action_executor",
            "from src.monkey_brain.kernel.pipeline.execution import",
        ]
        for imp in forbidden:
            assert imp not in source, f"operators.py must not import: {imp}"
