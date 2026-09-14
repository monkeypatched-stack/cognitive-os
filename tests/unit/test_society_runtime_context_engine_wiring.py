"""Regression test for a real init-ordering bug found while testing CCB-400
(a 5-store commerce-network scenario): PlanetaryRuntime.__init__ used to call
_load_actors() (which re-registers every persisted actor through
SocietyRuntime.register_actor()) BEFORE _attach_society() ran on any society
(the step that sets _context_engine/_society_activation). Since
SocietyRuntime.register_actor() only wires an actor's cognitive engine with
context_engine/society_activation when at least one of those is already set
on `self` at call time, every actor restored across a server restart
silently got a bare engine — CognitiveMemory experiences, KnowledgeGraph
search, active_memberships/policies/shared_goals never reached its prompt
again, even though it kept planning normally (Observed facts are a separate,
unaffected mechanism), which is what made the loss easy to miss.

This test isolates the exact mechanism at the SocietyRuntime level (no Redis
involved) rather than round-tripping a real PlanetaryRuntime through shared
Redis, to avoid polluting the live server's persisted actor/society state.
"""

from __future__ import annotations

from src.monkey_brain.kernel.society.runtime import SocietyRuntime
from src.monkey_brain.kernel.society.domain import (
    ActorProfile,
    ActorIdentity,
    ActorType,
)
from src.monkey_brain.kernel.pipeline.planning.context_engine import (
    ContextConstructionEngine,
)


def _engine_of(state) -> object | None:
    cognitive_engine = state.actor_runtime._actor._cognitive_engine
    if cognitive_engine is None:
        return None
    return cognitive_engine._engine


def test_register_actor_before_attach_gets_no_context_engine():
    """Mirrors the exact bug: register_actor() called while _context_engine
    is still unset on the SocietyRuntime (as _load_actors() used to do)."""
    sr = SocietyRuntime()
    assert sr._context_engine is None
    state = sr.register_actor(
        ActorProfile(identity=ActorIdentity(name="Early", actor_type=ActorType.AI_AGENT)),
    )
    engine = _engine_of(state)
    assert engine is None or getattr(engine, "_context_engine", None) is None


def test_register_actor_after_attach_gets_context_engine_wired():
    """The fix's invariant: once _attach_society() (or equivalent direct
    assignment) has set _context_engine before register_actor() runs, the
    resulting actor's engine has it wired — this is what _load_actors()
    now gets, since PlanetaryRuntime.__init__ attaches every society first."""
    sr = SocietyRuntime()
    context_engine = ContextConstructionEngine()
    sr._context_engine = context_engine

    state = sr.register_actor(
        ActorProfile(identity=ActorIdentity(name="Late", actor_type=ActorType.AI_AGENT)),
    )
    engine = _engine_of(state)
    assert engine is not None
    assert engine._context_engine is context_engine
