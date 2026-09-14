"""Resource & capability resolution (Step 9.5) — resolve execution targets
before execution starts.

CapabilityResolver checks an ExecutionPlan's requirements — required
capabilities, the requesting actor's availability, resource quantities,
permissions, and capacity — against an ExecutionEnvironment describing
what's actually available, all *before* any step runs. A plan that can't be
resolved fails fast, as a ResolutionReport, rather than partway through
execution.

ExecutionEnvironment is deliberately separate from ExecutionContext (Step
9.1): ExecutionContext is the execution phase's in-flight scope (the
request, which steps have completed so far); ExecutionEnvironment is the
static description of what's available — actors, resources, permissions,
capacity — resolved once, up front. Keeping them separate avoids
retroactively adding fields to Step 9.1's already-delivered domain model,
the same discipline Step 9.4 followed for retry/recovery configuration.

Operator-level requirements this step checks (required permission, required
resource + quantity) are read from PlanningOperator.metadata, reusing the
same convention Step 8's AcquireItem/ReserveResource operators and Step
8.4's InventoryConstraintEvaluator already established, rather than adding
new fields to PlanningOperator.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.monkey_brain.kernel.pipeline.execution_runtime.domain import (
    ExecutionPlan,
    ExecutionRequest,
)
from src.monkey_brain.kernel.pipeline.execution_runtime.handlers import (
    ExecutionRegistry,
)


@dataclass(frozen=True)
class ExecutionEnvironment:
    """What's actually available to carry out an ExecutionRequest — the
    static facts a CapabilityResolver checks a plan's requirements against."""

    available_capabilities: tuple[str, ...] = ()
    available_actors: frozenset[str] = field(default_factory=frozenset)
    """Empty means 'no restriction' — any actor_id resolves. Non-empty means
    only these actor_ids are available."""
    resource_pool: dict[str, float] = field(default_factory=dict)
    granted_permissions: tuple[str, ...] = ()
    actor_capacity: dict[str, int] = field(default_factory=dict)
    """actor_id -> max steps that actor may be assigned in one request.
    An actor with no entry has no capacity limit."""


@dataclass(frozen=True)
class ResolutionIssue:
    """One requirement that failed to resolve."""

    kind: str = ""
    """'capability' | 'actor' | 'resource' | 'permission' | 'capacity'"""
    subject: str = ""
    message: str = ""


@dataclass(frozen=True)
class ResolutionReport:
    """The result of resolving an ExecutionRequest's requirements against an
    ExecutionEnvironment, before any step executes."""

    request_id: str = ""
    resolved: bool = True
    issues: tuple[ResolutionIssue, ...] = ()


class CapabilityResolver:
    """Resolves an ExecutionRequest's requirements before execution starts.

    A plan is only resolved if every operator's required capability has a
    registered handler AND is actually available in the environment, the
    requesting actor is available, required resources/permissions/capacity
    are sufficient. Any single unresolved requirement fails the whole
    resolution — "fail before starting," not "fail on whichever issue is
    found first and silently ignore the rest."
    """

    def __init__(
        self,
        environment: ExecutionEnvironment | None = None,
        registry: ExecutionRegistry | None = None,
    ) -> None:
        self._environment = environment or ExecutionEnvironment()
        self._registry = registry or ExecutionRegistry()

    def resolve(self, plan: ExecutionPlan, request: ExecutionRequest) -> ResolutionReport:
        issues: list[ResolutionIssue] = []
        issues.extend(self._resolve_actor(request))
        issues.extend(self._resolve_capabilities(plan))
        issues.extend(self._resolve_permissions(plan))
        issues.extend(self._resolve_resources(plan))
        issues.extend(self._resolve_capacity(plan, request))
        return ResolutionReport(request_id=request.request_id, resolved=not issues, issues=tuple(issues))

    # ── Available actors ─────────────────────────────────────────────────

    def _resolve_actor(self, request: ExecutionRequest) -> list[ResolutionIssue]:
        if not self._environment.available_actors:
            return []  # no restriction configured
        if request.actor_id not in self._environment.available_actors:
            return [
                ResolutionIssue(
                    kind="actor",
                    subject=request.actor_id,
                    message=f"actor '{request.actor_id}' is not available",
                )
            ]
        return []

    # ── Required capabilities ────────────────────────────────────────────

    def _resolve_capabilities(self, plan: ExecutionPlan) -> list[ResolutionIssue]:
        issues: list[ResolutionIssue] = []
        available = set(self._environment.available_capabilities)
        for step in plan.steps:
            if step.operator is None:
                continue
            if self._registry.get(step.operator.name) is None:
                issues.append(
                    ResolutionIssue(
                        kind="capability",
                        subject=step.operator.name,
                        message=f"no execution handler registered for operator '{step.operator.name}'",
                    )
                )
                continue
            for capability in sorted(set(step.operator.required_capabilities) - available):
                issues.append(
                    ResolutionIssue(
                        kind="capability",
                        subject=capability,
                        message=f"capability '{capability}' required by step '{step.step_id}' is not available",
                    )
                )
        return issues

    # ── Permissions ──────────────────────────────────────────────────────

    def _resolve_permissions(self, plan: ExecutionPlan) -> list[ResolutionIssue]:
        issues: list[ResolutionIssue] = []
        granted = set(self._environment.granted_permissions)
        for step in plan.steps:
            if step.operator is None:
                continue
            required = step.operator.metadata.get("required_permission")
            if required and required not in granted:
                issues.append(
                    ResolutionIssue(
                        kind="permission",
                        subject=required,
                        message=f"permission '{required}' required by step '{step.step_id}' is not granted",
                    )
                )
        return issues

    # ── Resource availability ────────────────────────────────────────────

    def _resolve_resources(self, plan: ExecutionPlan) -> list[ResolutionIssue]:
        needed: dict[str, float] = {}
        for step in plan.steps:
            if step.operator is None:
                continue
            resource = step.operator.metadata.get("item") or step.operator.metadata.get("resource")
            if resource is None:
                continue
            needed[resource] = needed.get(resource, 0) + step.operator.metadata.get("quantity", 1)

        issues: list[ResolutionIssue] = []
        for resource, quantity in needed.items():
            available = self._environment.resource_pool.get(resource, 0)
            if quantity > available:
                issues.append(
                    ResolutionIssue(
                        kind="resource",
                        subject=resource,
                        message=f"insufficient '{resource}': need {quantity}, have {available}",
                    )
                )
        return issues

    # ── Capacity ─────────────────────────────────────────────────────────

    def _resolve_capacity(self, plan: ExecutionPlan, request: ExecutionRequest) -> list[ResolutionIssue]:
        capacity = self._environment.actor_capacity.get(request.actor_id)
        if capacity is None:
            return []
        step_count = len(plan.steps)
        if step_count > capacity:
            return [
                ResolutionIssue(
                    kind="capacity",
                    subject=request.actor_id,
                    message=f"actor '{request.actor_id}' capacity ({capacity}) exceeded by {step_count} steps",
                )
            ]
        return []
