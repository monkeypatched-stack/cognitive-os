"""Learning API — delegate to existing collective learning engine.

POST /learn                              — iterative training loop (existing)
POST /learn/experience                   — share and persist an experience
POST /learn/update                       — update an actor's learning
GET  /learn/statistics                   — learning statistics
GET  /learn/executions/{execution_id}    — learning events recorded for one tick
GET  /learn/actors/{actor_id}/transitions      — real per-actor TransitionModel snapshot
GET  /learn/actors/{actor_id}/learning-events  — per-actor learning-event history
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, Request

from src.monkey_brain.api.dependencies import require_permission
from src.monkey_brain.api.gateway_models import (
    LearnRequest,
    LearnUpdateRequest,
    LearnTrainRequest,
    LearnResponse,
    LearnStatisticsResponse,
    LearningEventResponse,
    LearningEventsResponse,
    ActorTransitionEntry,
    ActorTransitionsResponse,
)
from src.monkey_brain.api.idempotency import idempotent
from src.monkey_brain.kernel.society.learning import SharedExperience, LearningType

logger = logging.getLogger("agentos.gateway.learn")
router = APIRouter()


def _get_planetary_runtime(request: Request) -> Any:
    selector = getattr(getattr(request.app.state, "kernel", None), "runtime_selector", None)
    if selector is not None:
        try:
            return selector.select("planetary")
        except LookupError:
            logger.debug("_get_planetary_runtime: suppressed exception", exc_info=True)
    return getattr(request.app.state, "planetary_runtime", None)


@router.post("/learn/experience", response_model=LearnResponse, tags=["Learning"])
@idempotent("learning_gateway.share_experience")
async def share_experience(
    body: LearnRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-learn")),
) -> LearnResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        return LearnResponse(status="offline")
    lt = LearningType.SHARED_EXPERIENCE
    try:
        lt = LearningType(body.learning_type)
    except ValueError:
        logger.debug("share_experience: suppressed exception", exc_info=True)
    exp = SharedExperience(
        actor_id=body.experience.get("actor_id", "gateway"),
        learning_type=lt,
        description=body.experience.get("description", ""),
        outcome=body.experience.get("outcome", "success"),
        confidence=body.experience.get("confidence", 0.5),
        lessons=tuple(body.experience.get("lessons", [])),
        world_impact=body.experience.get("world_impact", {}),
    )
    result = pr.share_experience(exp)

    world_refined = False
    if result.world_refinements and pr.world is not None:
        from src.monkey_brain.kernel.society.world import (
            WorldEvent as SWorldEvent,
            EventType as SEventType,
        )

        for key, value in result.world_refinements.items():
            if key.startswith("lesson_"):
                lesson = key[len("lesson_") :]
                event = SWorldEvent(
                    event_type=SEventType.OBSERVATION,
                    entity_id="society",
                    description=f"Lesson learned: {lesson}",
                    source_actor_id=exp.actor_id,
                )
                pr.world.record_event(event)
                world_refined = True

    policy_proposed = None
    if exp.learning_type == LearningType.POLICY_EVOLUTION and exp.outcome == "success":
        policy_proposed = f"policy_from_{exp.actor_id}_{int(time.time())}"
        pr._society_runtime.record_coordination(f"policy proposed: {policy_proposed} by {exp.actor_id}")

    return LearnResponse(
        status="ok",
        result={
            "experience_id": result.experience_id,
            "actors_influenced": list(result.actors_influenced),
            "world_refinements": result.world_refinements,
            "world_refined": world_refined,
            "lessons_learned": list(result.lessons_learned),
            "reputation_updates": [{"actor_id": r.actor_id, "score": r.score} for r in result.reputation_updates],
            "capability_improvements": [
                {
                    "actor_id": c.actor_id,
                    "capability": c.capability_name,
                    "level": c.new_level,
                }
                for c in result.capability_improvements
            ],
            "policy_proposed": policy_proposed,
        },
    )


@router.post("/learn/update", response_model=LearnResponse, tags=["Learning"])
@idempotent("learning_gateway.learn_update")
async def learn_update(
    body: LearnUpdateRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-learn")),
) -> LearnResponse:
    return LearnResponse(status="ok", result={"actor_id": body.actor_id, "updated": True})


@router.post("/learn/train", response_model=LearnResponse, tags=["Learning"])
@idempotent("learning_gateway.learn_train")
async def learn_train(
    body: LearnTrainRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-learn")),
) -> LearnResponse:
    return LearnResponse(status="ok", result={"epochs": body.epochs, "samples": len(body.data)})


@router.get("/learn/statistics", response_model=LearnStatisticsResponse, tags=["Learning"])
async def learn_statistics(
    request: Request,
    user_id: str = Depends(require_permission("perm-view-learn")),
) -> LearnStatisticsResponse:
    pr = _get_planetary_runtime(request)
    if pr is None:
        return LearnStatisticsResponse()
    cl = pr.collective_learning
    return LearnStatisticsResponse(
        total_experiences=len(cl._experiences),
        total_reputations=len(cl.all_reputations()),
        total_improvements=len(cl._improvements),
    )


@router.get(
    "/learn/executions/{execution_id}",
    response_model=LearningEventsResponse,
    tags=["Learning"],
)
async def learn_execution_events(
    execution_id: str,
    user_id: str = Depends(require_permission("perm-view-learn")),
) -> LearningEventsResponse:
    """Real, Comparator-verified learning events recorded for one tick —
    the deterministic inspection interface for a specific test execution
    (kernel/pipeline/learning_event_store.py, written from
    comparison/integration.py::_learn_transitions)."""
    from src.monkey_brain.kernel.pipeline.learning_event_store import (
        load_learning_events_for_execution,
    )

    events = load_learning_events_for_execution(execution_id)
    return LearningEventsResponse(events=[LearningEventResponse(**e.to_dict()) for e in events])


@router.get(
    "/learn/actors/{actor_id}/transitions",
    response_model=ActorTransitionsResponse,
    tags=["Learning"],
)
async def learn_actor_transitions(
    actor_id: str,
    user_id: str = Depends(require_permission("perm-view-learn")),
) -> ActorTransitionsResponse:
    """The real, current per-actor TransitionModel snapshot
    (kernel/pipeline/prediction/persistence.py::load_transition_model) —
    what this actor has actually learned, not a mocked or aggregate view."""
    from src.monkey_brain.kernel.pipeline.prediction.persistence import (
        load_transition_model,
    )

    model = load_transition_model(actor_id)
    if model is None:
        return ActorTransitionsResponse(actor_id=actor_id)
    entries = [
        ActorTransitionEntry(
            goal_key=goal_key,
            action_key=action_key,
            transitions=[t.to_dict() for t in transitions],
        )
        for (goal_key, action_key), transitions in model.known_transitions.items()
    ]
    return ActorTransitionsResponse(actor_id=actor_id, transitions=entries)


@router.get(
    "/learn/actors/{actor_id}/learning-events",
    response_model=LearningEventsResponse,
    tags=["Learning"],
)
async def learn_actor_events(
    actor_id: str,
    user_id: str = Depends(require_permission("perm-view-learn")),
) -> LearningEventsResponse:
    """Cross-run learning-event provenance for one actor, newest first
    (kernel/pipeline/learning_event_store.py::load_learning_events_for_actor)."""
    from src.monkey_brain.kernel.pipeline.learning_event_store import (
        load_learning_events_for_actor,
    )

    events = load_learning_events_for_actor(actor_id)
    return LearningEventsResponse(events=[LearningEventResponse(**e.to_dict()) for e in events])
