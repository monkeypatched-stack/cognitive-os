"""Resolves a request to the correct vertical's capability bus.

API/route code should never construct a vertical's bus directly — it asks
this router for one instead. Today only Grocery is registered, so
resolve_vertical() always returns it, but this is the real seam where a
second vertical would be registered and selected, instead of a route
module hardcoding which vertical it runs and importing that vertical's
capability classes directly.

Named `vertical_router`, not `capability_router`, to avoid colliding with
the existing, differently-scoped `runtime/capability_router.py` (which
maps a capability string to a Broca-agent group label — an unrelated
concept). This module never imports a vertical's own module (e.g.
grocery.py) — a vertical imports this module and registers itself, the
same "specific registers into generic" direction used throughout the
domain/vertical split.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger("agentos.domains.vertical_router")


@dataclass(frozen=True)
class VerticalRuntime:
    """Everything a route needs from a vertical to execute a request,
    without needing to know how that vertical builds any of it."""
    bus: Any
    context_projector: Callable[[dict, dict], None]
    integrity_check: Callable[[Any], dict]
    planner: Any
    plan_validator: Any
    domain_event_resolver: Callable[[str, bool, Any], str | None] | None = None
    """True Multi-Actor Coordination: (capability_name, success, result)
    -> a real business event name (e.g. "OrderCreated"), or None for
    capabilities with no world-mutation meaning to broadcast. result is
    passed too since success=True alone doesn't always determine the
    right event (e.g. a fully-backordered order). Optional/defaulted so
    a vertical that doesn't register one just publishes untagged action
    events, same as before this field existed."""
    pre_execute_hook: Callable[[dict], None] | None = None
    """Qualification Gap Closure, Phase 9: called once, before any step
    of a tick executes, given the real execution context dict — the same
    injection principle as context_projector/domain_event_resolver above
    (a domain-agnostic executor has no business knowing what a "shared
    budget" is), used by grocery's ensure_shared_budget_from_question to
    set up real, tick-scoped state every step of that tick can then see
    regardless of plan order. None (the default) preserves exactly the
    prior behavior for any vertical that doesn't register one."""
    propose_transition: Any = None
    """Pre-commit negotiation gate: Callable[[Action, dict], ProposedTransition
    | None]. Same injection principle as pre_execute_hook — a
    domain-agnostic executor has no business knowing what "OrderCreation"
    or a "shared budget" mean, so a vertical that wants a class of action
    gated builds the ProposedTransition itself and returns None for
    anything it doesn't recognize as shared-state-mutating. None (the
    default) preserves exactly the prior behavior — no gate, no pause —
    for any vertical that doesn't register one."""


_VERTICAL_REGISTRY: dict[str, Callable[[], VerticalRuntime]] = {}


def register_vertical(name: str, builder: Callable[[], VerticalRuntime]) -> None:
    """Called by a vertical module (e.g. grocery.py) to register itself."""
    _VERTICAL_REGISTRY[name] = builder


def resolve_vertical(name: str = "grocery") -> VerticalRuntime:
    """Resolve the named vertical's runtime. Defaults to the only vertical
    that exists today — the default lives here, not in each caller, so a
    route never has to name a vertical to get one."""
    builder = _VERTICAL_REGISTRY.get(name)
    if builder is None:
        raise KeyError(f"no vertical registered for {name!r} (registered: {sorted(_VERTICAL_REGISTRY)})")
    return builder()


def _build_execution_engine(vertical: VerticalRuntime, context_stream: Any = None,
                            connectivity_check: Callable[[str], tuple[bool, str, str]] | None = None,
                            edge_governance: Any = None) -> Any:
    """Verify an already-resolved vertical's bus is intact, and return a
    ready execution engine.

    Fail CLOSED against the real bus about to execute this request: a
    configuration-drift regression that silently dropped a
    security-critical capability (delegation, authorization, payment
    confirmation) must refuse to build an engine at all, not hand back
    one that would run through an unknowingly unprotected pipeline.

    context_stream (True Multi-Actor Coordination): threaded through so
    ActionExecutor._publish_action_event actually fires for real capability
    outcomes. Before this, the live /prompt path built its ActionExecutor
    with context_stream=None — confirmed by tracing this exact call chain
    — so that method's own existing publish logic was a silent no-op in
    production; nothing downstream could ever react to "OrderCreated" etc.
    because no such event was ever actually published. None (the default)
    preserves prior behavior for any other caller that doesn't pass one.

    edge_governance (Edge-First authority activation): ActionExecutor has
    accepted this parameter since Cloud/Edge Actor Convergence, but nothing
    in this factory chain ever forwarded it — a kernel.edge.local_
    governance.LocalGovernanceEvaluator built and passed in by the caller
    (PlanetaryRuntime.__init__) was therefore always silently dropped,
    making every already-built edge-authority mechanism (signed policy
    snapshots, fail-closed local governance) unreachable in production
    despite having full test coverage in isolation. None (the default)
    preserves prior behavior for any caller that doesn't pass one.
    """
    from src.monkey_brain.kernel.pipeline.capability_runtime import CapabilityRuntime

    # Register the already-resolved vertical bus with the Kernel facade when
    # booted. The vertical remains its compatibility backend; the Kernel owns
    # global discovery without creating another bus.
    try:
        from src.monkey_brain.kernel.kernel import Kernel
        kernel = Kernel._instance
        if kernel is not None:
            kernel.capability_registry.attach_bus(vertical.bus)
    except Exception:
        logger.debug("_build_execution_engine: suppressed exception", exc_info=True)

    integrity_check = vertical.integrity_check(vertical.bus)
    if not integrity_check["integrity_ok"]:
        raise RuntimeError(f"runtime integrity violation: {integrity_check['reason']}")
    transition_gate = None
    if vertical.propose_transition is not None:
        from src.monkey_brain.kernel.society.transition_gate import TransitionGate
        transition_gate = TransitionGate()

    return CapabilityRuntime(
        capability_bus=vertical.bus,
        context_projector=vertical.context_projector,
        context_stream=context_stream,
        domain_event_resolver=vertical.domain_event_resolver,
        pre_execute_hook=vertical.pre_execute_hook,
        propose_transition=vertical.propose_transition,
        transition_gate=transition_gate,
        connectivity_check=connectivity_check,
        edge_governance=edge_governance,
    )


def build_execution_engine(name: str = "grocery", context_stream: Any = None,
                           connectivity_check: Callable[[str], tuple[bool, str, str]] | None = None,
                           edge_governance: Any = None) -> Any:
    """Resolve a vertical and return a ready execution engine. A route
    should never construct a bus, run a security check, or wire an
    executor itself; it just asks for something it can execute with.

    connectivity_check (Cloud/Edge Actor Convergence, Section 11/31):
    kernel/pipeline/offline_safety.py::make_connectivity_check(planetary_runtime)
    — None (the default) preserves exactly the prior behavior. Every
    existing caller of this function is unaffected; only
    PlanetaryRuntime._attach_society passes one, and only when
    OFFLINE_SAFETY_GATE_ENABLED is explicitly set.

    edge_governance: see _build_execution_engine's own docstring."""
    return _build_execution_engine(
        resolve_vertical(name), context_stream=context_stream,
        connectivity_check=connectivity_check, edge_governance=edge_governance,
    )


def build_runtime_engine(
    observation_provider: Any, name: str = "grocery", context_stream: Any = None,
    transition_model: Any = None, current_plans: dict[str, Any] | None = None,
    connectivity_check: Callable[[str], tuple[bool, str, str]] | None = None,
    edge_governance: Any = None,
) -> Any:
    """Assemble a fully-wired cognitive runtime engine for a vertical —
    base runtime, planner, plan validator, and execution engine — so a
    route never wires any of this itself. Engine initiation is the
    router's job, not the API route's; a route should only ever call this
    once and get back something ready to `.tick(state)`.

    transition_model: an optional real, previously-learned TransitionModel
    (kernel/pipeline/prediction/persistence.py::load_transition_model) —
    threaded straight through to build_comparison_integrated_runtime,
    which already accepts it. None (the default) keeps the existing
    "start learning from zero" behavior for an actor with no prior model.

    current_plans: an optional goal_key -> CurrentPlanRecord mapping
    (kernel/pipeline/planning/current_plan_store.py::load_current_plan,
    now goal-scoped) — same threading pattern as transition_model. None
    (the default, and the normal case — see current_plan_store.py's own
    docstring on why eager preload was removed) means this actor's policy
    starts with no Current Plan for any goal yet; each goal's first real
    tick always "replaces" (bootstrap case, kernel/pipeline/planning/
    plan_hysteresis.py::decide) and is lazily loaded from Redis
    per-goal_key by _run_decide itself, not preloaded here.

    connectivity_check / edge_governance: previously silently dropped —
    this function never forwarded either to _build_execution_engine, so
    an actor wired here (api/routes/actors.py::create_actor, the real
    POST /actors path) never got the offline-safety gate or edge
    authority evaluation regardless of OFFLINE_SAFETY_GATE_ENABLED. None
    (the default) preserves prior behavior for any caller that doesn't
    pass one; PlanetaryRuntime's own callers should pass the same
    instances it already builds for its shared execution engine."""
    from src.monkey_brain.kernel.pipeline.comparison.integration import build_comparison_integrated_runtime

    # resolve the vertical
    vertical = resolve_vertical(name)

    # Build the execution engine before constructing the comparison-integrated
    # policy. The policy captures the runtime's executor while it is
    # configured; assigning `_execution_engine` afterward leaves the policy
    # holding the empty default executor, so real capabilities (including
    # AskActor and RespondToInquiry) are silently simulated or rejected.
    execution_engine = _build_execution_engine(
        vertical, context_stream=context_stream,
        connectivity_check=connectivity_check, edge_governance=edge_governance,
    )
    engine = build_comparison_integrated_runtime(
        observation_provider=observation_provider,
        planning_engine=vertical.planner,
        plan_validator=vertical.plan_validator,
        execution_engine=execution_engine,
        transition_model=transition_model,
        current_plans=current_plans,
    )
    return engine
