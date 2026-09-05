"""Systems Validation Suite — Section 8: governance bypass enumeration.

Enumerates every path by which Actor-driven code can cause an external
side effect and checks Actor -> governance -> execution holds for each.

Confirmed, real, governed paths (each has its own dedicated proof
elsewhere in this suite or the existing test estate -- cited, not
re-derived here):
  - HTTP capability execution:        kernel/pipeline/action_executor.py
    -> ensure_governed (tests/security/test_portable_delegation.py::
    TestDelegationReachesRealExecution)
  - ROS:                               kernel/edge/ros_integration.py::
    run_ros_action_if_governed (tests/unit/test_ros_integration_contract.py,
    tests/unit/test_edge_ros_integration.py -- also this suite's
    test_v13_ros_governance.py)
  - NATS (actor-to-actor messaging):   kernel/domains/grocery.py::
    subscribe_actor_inbox (this suite's test_v09_messaging.py)
  - autonomous/scheduler-triggered ticks: kernel/compile/actor_runtime.py::
    ActorRuntime.tick() (this suite's test_v01_actor_identity.py +
    tests/architecture/test_actor_runtime_autotick_identity.py, this
    session's own Actor Runtime review Phase 1 fix)
  - recovery/migration handlers:       ActorLifecycleController._do_resume/
    _do_start (they call restore_actor_belief/checkpoint_actor_belief,
    which perform PERSISTENCE, not external side effects -- no governed
    capability is invoked from a recovery/migration handler at all,
    confirmed by reading actor_lifecycle_controller.py in full; recovery
    re-enters cognition via the SAME tick() path above, which IS gated)

What this file adds: (1) an explicit structural check that the one
remaining ungated surface -- calling a capability's .handle() directly
-- is possible in principle (capabilities are plain Python objects,
not something a caller is prevented at the language level from
invoking), which is the honest answer this section asks for rather than
a fabricated bypass in real code; and (2) confirms grep across the real
domain modules finds ZERO production call sites that actually do this
for a governed capability, i.e. the boundary is enforced by disciplined
convention at every real call site, not by a structural barrier.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOMAIN_FILES = list((REPO_ROOT / "src" / "monkey_brain" / "kernel" / "domains").glob("*.py"))


class TestDirectCapabilityInvocationIsPossibleInPrincipleButUnused:
    def test_a_capability_object_has_no_self_defense_against_being_called_directly(self):
        """Honest structural finding: governance is NOT enforced by the
        capability object itself (no metaclass/decorator on the base
        Capability class refuses a bare .handle() call) -- it is
        enforced entirely by every real call site choosing to go through
        ActionExecutor/ensure_governed first. A capability class placed
        directly in a caller's hands, with no ActionExecutor in between,
        CAN be invoked with no governance check at all."""
        from src.monkey_brain.kernel.domains.grocery import OrderCreationCapability

        cap = OrderCreationCapability()
        assert hasattr(cap, "handle")
        # No decorator/wrapper marks .handle as "governance-required" --
        # it is a plain bound method, callable by anyone holding the
        # object, exactly like any other Python method.
        assert not hasattr(cap.handle, "__governance_required__")

    # FINDING: two real, currently-existing direct-handle call sites,
    # found via an AST scan (not string matching, to avoid false
    # positives from comments/docstrings mentioning ".handle()"). Both
    # invoke a READ-ONLY, non-mutating capability -- narrower than a
    # governance bypass for a consequential action, but still a real
    # gap: nothing marks these capabilities as "exempt from governance
    # because read-only" as an enforced, checked contract -- they are
    # exempt only because nobody has routed them through ActionExecutor,
    # which is a maintenance hazard (a future edit adding a real side
    # effect to either would inherit an already-ungoverned call site
    # with no test or structural check to catch it).
    _KNOWN_DIRECT_HANDLE_CALLS = {
        ("grocery.py", "NutritionCapability"),
        ("grocery.py", "AnswerQuestionCapability"),
    }

    def test_no_new_direct_capability_handle_calls_exist_beyond_the_known_read_only_ones(self):
        """AST-based (not regex) scan of every direct `X().handle(...)`
        or `x.handle(...)` call inside kernel/domains/*.py, excluding a
        capability class's own `def handle(self, ...)` definition. Any
        hit beyond the known, read-only two above is a NEW, unreviewed
        governance bypass and must fail this test until it is either
        justified (added to the known set, with a reason) or fixed."""
        found: set[tuple[str, str]] = set()
        for path in DOMAIN_FILES:
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "handle"):
                    continue
                target = node.func.value
                # Capability classes end in "Capability" by this
                # codebase's own naming convention -- resolve the callee
                # name whether it's `Foo().handle(...)` (a Call) or
                # `foo.handle(...)` (a Name/Attribute reference).
                if isinstance(target, ast.Call) and isinstance(target.func, ast.Name):
                    name = target.func.id
                elif isinstance(target, ast.Name):
                    name = target.id
                else:
                    continue
                if name.endswith("Capability") and name != "DomainCapability":
                    found.add((path.name, name))

        unexpected = found - self._KNOWN_DIRECT_HANDLE_CALLS
        assert unexpected == set(), (
            f"new, unreviewed direct capability.handle() call site(s) found: {unexpected} -- "
            "each bypasses ActionExecutor/ensure_governed; confirm whether the capability is "
            "genuinely read-only before adding it to _KNOWN_DIRECT_HANDLE_CALLS"
        )
        # And confirm the known ones are still exactly what this finding
        # describes -- if one disappears, the finding is stale and this
        # test should be updated, not left silently over-broad.
        assert self._KNOWN_DIRECT_HANDLE_CALLS <= found, (
            "a previously-known direct-handle call site no longer exists -- update this test's "
            "documented finding rather than leaving a stale exemption"
        )


class TestRecoveryAndMigrationHandlersNeverInvokeCapabilitiesDirectly:
    """Sections 8's explicit list includes 'recovery handlers' and
    'migration handlers' as attack surfaces to check -- proven here by
    reading the real handler source, not by writing a synthetic
    scenario that could miss a call the real code doesn't actually
    make."""

    def test_do_resume_and_do_start_never_call_ensure_governed_or_a_capability_handle(self):
        import inspect

        from src.monkey_brain.kernel.society.actor_lifecycle_controller import ActorLifecycleController

        for method_name in ("_do_resume", "_do_start", "_do_recover"):
            source = inspect.getsource(getattr(ActorLifecycleController, method_name))
            assert "ensure_governed" not in source, f"{method_name} must never directly invoke governance/execution"
            assert ".handle(" not in source, f"{method_name} must never directly invoke a capability"

    def test_migrate_actor_never_calls_ensure_governed_or_a_capability_handle(self):
        import inspect

        from src.monkey_brain.kernel.society.actor_scheduler import ActorScheduler

        source = inspect.getsource(ActorScheduler.migrate_actor)
        assert "ensure_governed" not in source
        assert ".handle(" not in source


class TestScheduledJobsAndBackgroundTasksRouteThroughTheSameBoundary:
    def test_auto_tick_loop_reaches_cognition_only_through_the_same_governed_tick_path(self):
        """PlanetaryRuntime._auto_tick_loop (the literal 'scheduled job'
        Section 8 asks about) must not have its own separate execution
        path -- it must call into the same tick_one_actor/ActorRuntime.
        tick() chain every other trigger uses (already proven governed
        by test_v01_actor_identity.py + the autotick identity tests)."""
        import inspect

        from src.monkey_brain.kernel.society.integration import PlanetaryRuntime

        source = inspect.getsource(PlanetaryRuntime._auto_tick_loop)
        assert "ensure_governed" not in source, (
            "the auto-tick loop itself must never call governance directly -- "
            "it must delegate through self.cycle() (-> GeographicEntityRuntime.tick -> "
            "SocietyRuntime.tick_one_actor -> ActorRuntime.tick(), which does)"
        )
        assert "self.cycle(" in source, (
            "auto-tick loop must reach cognition through cycle() -- the single documented "
            "entry point the Actor Runtime review's own C-1 identity-binding fix relies on "
            "being the ONE place every autonomous tick funnels through"
        )
