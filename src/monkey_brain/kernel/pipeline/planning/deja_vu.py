"""Deja Vu — detect that a Planetary Tick invalidated an actor's standing
plan, and automatically replay that actor's reasoning under the updated
world state, toward the SAME objective, replacing the stale plan only if
the fresh reasoning actually diverges.

Composes existing pieces rather than rebuilding a planning path:
    ContextConstructionEngine.build() -- the same actor-scoped context
        assembly a regular tick already runs (kernel/pipeline/planning/
        context_engine.py).
    LLMPlanner.plan() -- the same planner a regular tick already calls
        (kernel/pipeline/llm_planner.py).
    plan_hysteresis.score_plan()/decide() -- the same keep-vs-replace
        scorer plan_hysteresis already implements for the regular tick's
        Decide stage (kernel/pipeline/comparison/integration.py::
        _run_decide) -- reused here without a fresh Predict-stage
        prediction_result (Deja Vu replays REASONING, not a full
        Observe->Predict->Execute cycle, so score_plan's
        probability/expected_utility terms default to 0 here, same
        already-graceful behavior that function has whenever no
        prediction exists yet -- cost/risk still score meaningfully).
    current_plan_store.{load,save}_current_plan -- the same persistence
        a regular tick already uses.

Deliberately does NOT re-execute anything: Deja Vu re-evaluates whether
the actor's PRIOR REASONING is still valid, not whether to take the
actions again -- re-running Execute here could cause duplicate real-world
side effects (e.g. double-ordering) that the architecture spec's own
framing ("replace the previous reasoning with the new result") does not
ask for.

"Which actors are affected" is answered without a separate dependency-
snapshot mechanism: an actor's persisted CurrentPlanRecord.plan is the
full serialized belief_state.Plan (every step's parameters), so checking
whether a perturbed entity_id appears anywhere in it is a cheap,
storage-free sufficient signal that the plan MIGHT depend on that entity
-- no new field on CurrentPlanRecord, no snapshot to keep in sync.
"""

from __future__ import annotations

import dataclasses
import logging
import time
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

logger = logging.getLogger("agentos.pipeline.planning.deja_vu")


def _plan_references_entity(plan_dict: dict[str, Any], entity_id: str) -> bool:
    """True iff entity_id appears anywhere in the persisted plan's
    serialized form -- see module docstring."""

    def _contains(value: Any) -> bool:
        if isinstance(value, str):
            return value == entity_id
        if isinstance(value, dict):
            return any(_contains(v) for v in value.values())
        if isinstance(value, (list, tuple)):
            return any(_contains(v) for v in value)
        return False

    return _contains(plan_dict)


def replay_affected_actors(pr: Any, touched_entity_ids: set[str]) -> list[str]:
    """For every actor with a persisted Current Plan (per standing goal)
    that references one of touched_entity_ids, replays that actor's
    reasoning under the current world state toward the SAME objective
    (record.goal), then decides keep-vs-replace and persists the result.
    Returns the actor_ids actually replayed (whether kept or replaced), so
    the caller (PlanetaryRuntime._run_cycle) can log/stream which actors
    got a Deja Vu pass this cycle. Non-fatal per actor/goal: one replay
    failing does not stop the others or the Planetary Tick itself.

    Current Plans are goal-scoped (kernel/pipeline/planning/
    current_plan_store.py) — there is no longer a single "the" current
    plan to load per actor, so this checks/replays each of the actor's
    standing goals (ActorProfile.goals) independently rather than guessing
    one. BeliefState.goal is tick-scoped pipeline state, not part of the
    persisted actor runtime, so it isn't available at this layer.
    """
    if not touched_entity_ids:
        return []

    from src.monkey_brain.kernel.pipeline.belief_state import Goal
    from src.monkey_brain.kernel.pipeline.llm_planner import LLMPlanner
    from src.monkey_brain.kernel.pipeline.planning.current_plan_store import (
        CurrentPlanRecord,
        load_current_plan,
        plan_to_dict,
        save_current_plan,
    )
    from src.monkey_brain.kernel.pipeline.planning.goal_key import canonicalize_goal
    from src.monkey_brain.kernel.pipeline.planning.plan_hysteresis import decide, score_plan

    engine = getattr(pr, "context_engine", None)
    if engine is None:
        return []
    planner = LLMPlanner()

    replayed: list[str] = []
    for state in pr.all_active_actors():
        actor_id = state.actor_id
        actor_goals = tuple(getattr(getattr(state, "profile", None), "goals", ()) or ())
        for raw_goal in actor_goals:
            goal_key = canonicalize_goal(raw_goal)
            current = load_current_plan(actor_id, goal_key)
            if current is None or not current.plan:
                continue
            if not any(_plan_references_entity(current.plan, eid) for eid in touched_entity_ids):
                continue

            try:
                context = engine.build(actor_id, Goal(name=current.goal))
                # LLMPlanner.plan() reads context.metadata["_legacy_belief"].tenant_id
                # to tenant-scope its promoted-plan lookup (see llm_planner.py) --
                # engine.build() itself never sets this (only belief_runtime.py's
                # _generate_plan does, for a regular tick). Without it every Deja
                # Vu replay would resolve promoted plans under tenant_id="default"
                # regardless of the actor's real tenant, silently never matching a
                # capability promoted for a real (non-default) tenant. `state.actor`
                # is the same duck-typed registered actor object CognitiveActor
                # exposes `.tenant_id` on elsewhere (compile/cognitive_actor.py) --
                # getattr, not a hard dependency, since a bare ActorProtocol
                # implementation isn't guaranteed to carry one.
                context.metadata["_legacy_belief"] = SimpleNamespace(
                    tenant_id=getattr(getattr(state, "actor", None), "tenant_id", "") or "default",
                )
                # LLMPlanner.plan() is async (see llm_planner.py/model_backend.py --
                # a real awaited Ollama call, genuinely cancellable, not a
                # synchronous call hidden inside asyncio.to_thread). This whole
                # function already runs inside its OWN asyncio.to_thread
                # (integration.py::_run_cycle), i.e. a plain worker thread with
                # no running event loop of its own -- asyncio.run() here starts
                # one just for this call, safely, since nothing else in this
                # thread needs one.
                import asyncio

                new_plan = asyncio.run(planner.plan(context))
            except Exception:
                logger.warning(
                    "replay_affected_actors: replay failed for actor %r goal %r (non-fatal)",
                    actor_id,
                    goal_key,
                    exc_info=True,
                )
                continue
            if not getattr(new_plan, "steps", None):
                continue  # nothing real generated -- leave the existing plan alone

            # canonicalize_goal(new_plan.goal) must equal goal_key here by
            # construction (the replay was seeded from Goal(name=current.goal),
            # itself already goal_key-consistent) -- same invariant
            # kernel/pipeline/comparison/integration.py::_run_decide asserts
            # for the regular tick path.
            new_score, components = score_plan(new_plan, None)
            verdict = decide(new_score, current)
            replayed.append(actor_id)

            if verdict.action == "replace":
                record = CurrentPlanRecord(
                    plan_id=uuid4().hex,
                    actor_id=actor_id,
                    goal=getattr(new_plan, "goal", current.goal) or current.goal,
                    steps=tuple(s.action for s in new_plan.steps),
                    step_descriptions=tuple(s.description for s in new_plan.steps),
                    cost=float(getattr(new_plan, "cost", 0.0) or 0.0),
                    risk=float(getattr(new_plan, "risk", 0.0) or 0.0),
                    confidence=float(getattr(new_plan, "confidence", 0.0) or 0.0),
                    score=new_score,
                    score_components=components,
                    replaced_plan_id=current.plan_id,
                    plan=plan_to_dict(new_plan),
                )
                save_current_plan(actor_id, goal_key, record)
                logger.info("Deja Vu: replaced plan for actor %r goal %r (%s)", actor_id, goal_key, verdict.reason)
            else:
                updated = dataclasses.replace(
                    current,
                    kept_count=current.kept_count + 1,
                    last_kept_at=time.time(),
                )
                save_current_plan(actor_id, goal_key, updated)
                logger.info("Deja Vu: kept plan for actor %r goal %r (%s)", actor_id, goal_key, verdict.reason)

    return replayed
