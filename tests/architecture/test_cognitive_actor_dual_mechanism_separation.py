"""Actor Runtime review, Phase 5 (cleanup, C-2): CognitiveActor carries
two genuinely separate cognitive mechanisms that happen to share
vocabulary --

    tick() / _cognitive_tick() / execute_cognitive_loop()
        the REAL async, LLM-driven engine every /prompt request uses
        (delegates to belief_runtime.py's canonical pipeline)

    plan() / simulate() / execute() / learn() / compile_phi() / cognitive_cycle()
        a separate, synchronous, non-LLM local-graph-pathfinding cycle,
        genuinely live via kernel/compile/actor_runtime.py::ActorRuntime.
        cognitive_cycle() and kernel/compile/society_runtime.py, but never
        reached by a real grocery/commerce prompt request

This is not a bug (both are real, both are used, just by different
callers) -- Phase 5 was documentation-only, adding prominent cross-
references at both definition sites rather than renaming public methods
callers already depend on. This test proves the naming overlap is
COSMETIC ONLY: neither mechanism's methods call into the other's, so a
change to one can never silently affect the other's behavior.
"""
from __future__ import annotations

import inspect

from src.monkey_brain.kernel.compile.cognitive_actor import CognitiveActor


class TestTheTwoMechanismsNeverCallEachOther:
    def test_the_async_engine_never_calls_the_sync_local_graph_methods(self):
        # compile_phi() is a deliberate exception -- a genuinely shared,
        # single-purpose utility (compile the actor's Bellman policy into
        # a sparse transition operator) both mechanisms legitimately call
        # after their own learning step (execute_cognitive_loop calls it
        # directly; cognitive_cycle's own learn() feeds the same Bellman
        # state). Reuse of one real utility is not the C-2 finding --
        # the concern is plan/simulate/execute/cognitive_cycle, the
        # methods that could be mistaken for the real engine's own
        # Plan/Execute stages.
        sync_only_methods = ("cognitive_cycle(", ".plan(", ".simulate(")
        for async_method_name in ("tick", "_cognitive_tick", "execute_cognitive_loop"):
            source = inspect.getsource(getattr(CognitiveActor, async_method_name))
            for sync_call in sync_only_methods:
                assert sync_call not in source, (
                    f"CognitiveActor.{async_method_name} must never call the synchronous "
                    f"local-graph mechanism ({sync_call}) -- these are separate engines"
                )

    def test_the_sync_local_graph_cycle_never_calls_the_async_engine(self):
        source = inspect.getsource(CognitiveActor.cognitive_cycle)
        for async_call in ("_cognitive_tick(", "await self.tick(", "execute_cognitive_loop("):
            assert async_call not in source, (
                f"CognitiveActor.cognitive_cycle must never call the async LLM-driven "
                f"engine ({async_call}) -- these are separate engines"
            )

    def test_cognitive_cycle_learn_never_touches_governance_or_delegation(self):
        """Section 12 of the review ('learning cannot silently expand
        authority') applies equally to this synchronous mechanism --
        proven structurally: learn() has no reference to governance,
        delegation, or OPA."""
        source = inspect.getsource(CognitiveActor.learn)
        for forbidden in ("ensure_governed", "delegation", "GovernancePolicy", "OPA"):
            assert forbidden not in source

    def test_synchronous_execute_never_reaches_the_governance_boundary(self):
        """The C-2 finding's most important consequence: CognitiveActor.
        execute() (the synchronous local-graph step) must never be
        confused with a real, governed capability call -- it has no path
        to ensure_governed at all."""
        source = inspect.getsource(CognitiveActor.execute)
        assert "ensure_governed" not in source
        assert "ActionExecutor" not in source
