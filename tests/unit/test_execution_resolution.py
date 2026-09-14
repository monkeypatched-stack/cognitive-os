"""Tests for resource & capability resolution (Step 9.5).

Validates CapabilityResolver's five resolution dimensions (capabilities,
actor availability, permissions, resources, capacity) individually and in
combination, and confirms resolution genuinely fails BEFORE execution would
start — this module never dispatches a step, only checks.
"""

from __future__ import annotations

from src.monkey_brain.kernel.pipeline.planning.domain import PlanningOperator
from src.monkey_brain.kernel.pipeline.execution_runtime.domain import (
    ExecutionStep,
    ExecutionPlan,
    ExecutionRequest,
)
from src.monkey_brain.kernel.pipeline.execution_runtime.handlers import (
    ExecutionRegistry,
)
from src.monkey_brain.kernel.pipeline.execution_runtime.resolution import (
    ExecutionEnvironment,
    ResolutionIssue,
    ResolutionReport,
    CapabilityResolver,
)


def _plan_request(*operators: PlanningOperator, actor_id: str = "alice") -> tuple[ExecutionPlan, ExecutionRequest]:
    steps = tuple(ExecutionStep(operator=op) for op in operators)
    plan = ExecutionPlan(steps=steps)
    request = ExecutionRequest(plan=plan, actor_id=actor_id)
    return plan, request


# ═══════════════════════════════════════════════════════════════════════════
# Fully resolved (success) case
# ═══════════════════════════════════════════════════════════════════════════


class TestFullyResolved:
    def test_all_requirements_met_resolves(self):
        nav = PlanningOperator(name="Navigate", required_capabilities=("navigation",))
        buy = PlanningOperator(
            name="AcquireItem",
            required_capabilities=("shopping", "payment"),
            metadata={"item": "milk", "quantity": 2},
        )
        plan, request = _plan_request(nav, buy)

        env = ExecutionEnvironment(
            available_capabilities=("navigation", "shopping", "payment"),
            available_actors=frozenset({"alice"}),
            resource_pool={"milk": 5},
        )
        report = CapabilityResolver(environment=env).resolve(plan, request)

        assert report.resolved is True
        assert report.issues == ()

    def test_empty_plan_resolves_trivially(self):
        plan, request = _plan_request()
        report = CapabilityResolver().resolve(plan, request)
        assert report.resolved is True


# ═══════════════════════════════════════════════════════════════════════════
# Capability resolution — "fail before starting if unavailable"
# ═══════════════════════════════════════════════════════════════════════════


class TestCapabilityResolution:
    def test_missing_capability_fails_resolution(self):
        nav = PlanningOperator(name="Navigate", required_capabilities=("navigation",))
        plan, request = _plan_request(nav)
        env = ExecutionEnvironment(available_capabilities=())  # navigation not available

        report = CapabilityResolver(environment=env).resolve(plan, request)

        assert report.resolved is False
        assert any(i.kind == "capability" and i.subject == "navigation" for i in report.issues)

    def test_operator_with_no_registered_handler_fails(self):
        never_registered = PlanningOperator(name="NeverRegisteredOp")
        plan, request = _plan_request(never_registered)

        report = CapabilityResolver(registry=ExecutionRegistry()).resolve(plan, request)

        assert report.resolved is False
        assert any(i.kind == "capability" and i.subject == "NeverRegisteredOp" for i in report.issues)

    def test_step_with_no_operator_is_skipped_not_a_failure(self):
        plan = ExecutionPlan(steps=(ExecutionStep(operator=None),))
        request = ExecutionRequest(plan=plan, actor_id="alice")

        report = CapabilityResolver().resolve(plan, request)

        assert report.resolved is True


# ═══════════════════════════════════════════════════════════════════════════
# Actor availability
# ═══════════════════════════════════════════════════════════════════════════


class TestActorAvailability:
    def test_unavailable_actor_fails_resolution(self):
        nav = PlanningOperator(name="Navigate", required_capabilities=("navigation",))
        plan, request = _plan_request(nav, actor_id="bob")
        env = ExecutionEnvironment(
            available_capabilities=("navigation",),
            available_actors=frozenset({"alice"}),
        )

        report = CapabilityResolver(environment=env).resolve(plan, request)

        assert report.resolved is False
        assert any(i.kind == "actor" and i.subject == "bob" for i in report.issues)

    def test_empty_available_actors_means_no_restriction(self):
        nav = PlanningOperator(name="Navigate", required_capabilities=("navigation",))
        plan, request = _plan_request(nav, actor_id="anyone")
        env = ExecutionEnvironment(available_capabilities=("navigation",))  # available_actors=() by default

        report = CapabilityResolver(environment=env).resolve(plan, request)

        assert report.resolved is True


# ═══════════════════════════════════════════════════════════════════════════
# Permissions
# ═══════════════════════════════════════════════════════════════════════════


class TestPermissionResolution:
    def test_missing_permission_fails_resolution(self):
        op = PlanningOperator(name="AcquireItem", metadata={"required_permission": "shop"})
        plan, request = _plan_request(op)
        env = ExecutionEnvironment(granted_permissions=())

        report = CapabilityResolver(environment=env).resolve(plan, request)

        assert report.resolved is False
        assert any(i.kind == "permission" and i.subject == "shop" for i in report.issues)

    def test_granted_permission_resolves(self):
        op = PlanningOperator(name="AcquireItem", metadata={"required_permission": "shop"})
        plan, request = _plan_request(op)
        env = ExecutionEnvironment(granted_permissions=("shop",))

        report = CapabilityResolver(environment=env).resolve(plan, request)

        assert report.resolved is True

    def test_operator_with_no_required_permission_is_unaffected(self):
        op = PlanningOperator(name="Navigate")  # no required_permission in metadata
        plan, request = _plan_request(op)

        report = CapabilityResolver().resolve(plan, request)

        assert not any(i.kind == "permission" for i in report.issues)


# ═══════════════════════════════════════════════════════════════════════════
# Resource availability
# ═══════════════════════════════════════════════════════════════════════════


class TestResourceResolution:
    def test_sufficient_resource_resolves(self):
        op = PlanningOperator(name="AcquireItem", metadata={"item": "milk", "quantity": 2})
        plan, request = _plan_request(op)
        env = ExecutionEnvironment(resource_pool={"milk": 5})

        report = CapabilityResolver(environment=env).resolve(plan, request)

        assert report.resolved is True

    def test_insufficient_resource_fails_resolution(self):
        op = PlanningOperator(name="AcquireItem", metadata={"item": "milk", "quantity": 10})
        plan, request = _plan_request(op)
        env = ExecutionEnvironment(resource_pool={"milk": 2})

        report = CapabilityResolver(environment=env).resolve(plan, request)

        assert report.resolved is False
        assert any(i.kind == "resource" and i.subject == "milk" for i in report.issues)

    def test_multiple_steps_needing_same_resource_are_summed(self):
        op1 = PlanningOperator(name="AcquireItem", metadata={"item": "milk", "quantity": 2})
        op2 = PlanningOperator(name="AcquireItem", metadata={"item": "milk", "quantity": 2})
        plan, request = _plan_request(op1, op2)
        env = ExecutionEnvironment(resource_pool={"milk": 3})  # 3 available, but 4 needed total

        report = CapabilityResolver(environment=env).resolve(plan, request)

        assert report.resolved is False

    def test_resource_via_reserve_resource_metadata_key(self):
        """ReserveResource-style operators use 'resource', not 'item'."""
        op = PlanningOperator(
            name="ReserveResource",
            metadata={"resource": "delivery_slot", "quantity": 1},
        )
        plan, request = _plan_request(op)
        env = ExecutionEnvironment(resource_pool={"delivery_slot": 0})

        report = CapabilityResolver(environment=env).resolve(plan, request)

        assert report.resolved is False
        assert any(i.subject == "delivery_slot" for i in report.issues)

    def test_operator_with_no_resource_metadata_is_unaffected(self):
        op = PlanningOperator(name="Navigate")  # no item/resource metadata
        plan, request = _plan_request(op)

        report = CapabilityResolver().resolve(plan, request)

        assert not any(i.kind == "resource" for i in report.issues)


# ═══════════════════════════════════════════════════════════════════════════
# Capacity
# ═══════════════════════════════════════════════════════════════════════════


class TestCapacityResolution:
    def test_within_capacity_resolves(self):
        ops = tuple(PlanningOperator(name="Navigate") for _ in range(2))
        plan, request = _plan_request(*ops)
        env = ExecutionEnvironment(available_capabilities=(), actor_capacity={"alice": 5})

        report = CapabilityResolver(environment=env).resolve(plan, request)

        assert not any(i.kind == "capacity" for i in report.issues)

    def test_exceeding_capacity_fails_resolution(self):
        ops = tuple(PlanningOperator(name="Navigate") for _ in range(5))
        plan, request = _plan_request(*ops)
        env = ExecutionEnvironment(available_capabilities=("navigation",), actor_capacity={"alice": 3})

        report = CapabilityResolver(environment=env).resolve(plan, request)

        assert report.resolved is False
        assert any(i.kind == "capacity" and i.subject == "alice" for i in report.issues)

    def test_actor_with_no_configured_capacity_is_unlimited(self):
        ops = tuple(PlanningOperator(name="Navigate") for _ in range(100))
        plan, request = _plan_request(*ops)
        env = ExecutionEnvironment(available_capabilities=("navigation",))  # no actor_capacity entry

        report = CapabilityResolver(environment=env).resolve(plan, request)

        assert not any(i.kind == "capacity" for i in report.issues)


# ═══════════════════════════════════════════════════════════════════════════
# Multiple simultaneous issues
# ═══════════════════════════════════════════════════════════════════════════


class TestMultipleIssues:
    def test_all_failing_dimensions_are_reported_not_just_the_first(self):
        op = PlanningOperator(
            name="AcquireItem",
            required_capabilities=("shopping",),
            metadata={"item": "milk", "quantity": 5, "required_permission": "shop"},
        )
        plan, request = _plan_request(op, actor_id="bob")
        env = ExecutionEnvironment(
            available_capabilities=(),  # missing 'shopping'
            available_actors=frozenset({"alice"}),  # 'bob' unavailable
            resource_pool={"milk": 1},  # insufficient
            granted_permissions=(),  # missing 'shop'
        )

        report = CapabilityResolver(environment=env).resolve(plan, request)

        kinds = {i.kind for i in report.issues}
        assert report.resolved is False
        assert kinds == {"capability", "actor", "resource", "permission"}


# ═══════════════════════════════════════════════════════════════════════════
# Resolution never executes anything
# ═══════════════════════════════════════════════════════════════════════════


class TestResolutionIsReadOnly:
    def test_resolve_does_not_dispatch_any_step(self):
        """A resolver must only check — never actually run a handler."""
        calls = []

        class SpyHandler:
            operator_name = "Navigate"
            from src.monkey_brain.kernel.pipeline.execution_runtime.handlers import (
                ExecutionCapability,
            )

            capability = ExecutionCapability(name="navigation")

            def validate(self, step):
                return None

            def execute(self, step, context):
                calls.append(step.step_id)
                raise AssertionError("resolver must not execute steps")

        registry = ExecutionRegistry(handlers={})
        registry.register(SpyHandler())
        op = PlanningOperator(name="Navigate", required_capabilities=("navigation",))
        plan, request = _plan_request(op)
        env = ExecutionEnvironment(available_capabilities=("navigation",))

        CapabilityResolver(environment=env, registry=registry).resolve(plan, request)

        assert calls == []


# ═══════════════════════════════════════════════════════════════════════════
# Ownership boundary
# ═══════════════════════════════════════════════════════════════════════════


class TestOwnershipBoundary:
    def test_no_planning_engine_or_cognitive_runtime_coupling(self):
        import inspect
        import src.monkey_brain.kernel.pipeline.execution_runtime.resolution as mod

        source = inspect.getsource(mod)
        forbidden = [
            "belief_runtime",
            "planning_engine",
            "from src.monkey_brain.kernel.pipeline.planner import",
        ]
        for imp in forbidden:
            assert imp not in source, f"resolution.py must not import: {imp}"
