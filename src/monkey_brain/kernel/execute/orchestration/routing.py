"""Execution routing — normalization, intent resolution, goal routing, and response helpers."""

from __future__ import annotations

import logging
from typing import Any, Optional

from src.monkey_brain.kernel.plan.goals.router import route_question
from src.monkey_brain.kernel.execute.models import SELF_HEALING_INTENT

logger = logging.getLogger("agentos.executor")

NO_INTENT_MESSAGE = "No intent could be classified for this question."


def normalize_question(question: str) -> str:
    """Strip control chars, remove internal markers, truncate to 10 000 chars."""
    cleaned = "".join(" " if ord(c) < 32 and c not in ("\t", "\n") else c for c in question)
    # "Graph Details" is an internal graph-serialization marker injected upstream;
    # strip everything from it onward so it never reaches intent resolution.
    return cleaned[:10_000].split("Graph Details", 1)[0].strip()


def unsupported_response() -> tuple[str, list, list, bool]:
    return (NO_INTENT_MESSAGE, [], [], False)


def fallback_to_predicates(question: str) -> Optional[dict]:
    """Try each registered predicate; return the first matching intent dict or None."""
    from src.monkey_brain.kernel.plan.intents.intent_registry import INTENT_REGISTRY

    for intent_name, route in INTENT_REGISTRY.items():
        if route.predicate and route.predicate(question):
            logger.info("Predicate match: %s", intent_name)
            return {
                "intent": intent_name,
                "confidence": 1.0,
                "workload_id": intent_name,
            }
    return None


def create_goal(
    question: str,
    lemon: Any = None,
    trace_id: str = "",
) -> tuple[str, Optional[dict], Any]:
    """Normalize → self-healing check → intent resolution → goal routing.

    Returns (normalized_question, intent_dict, goal).
    goal is None when the question cannot be routed.
    lemon is optional; when provided, observe_intent is called after resolution.
    """

    # 1. Normalize the question to remove control characters and internal markers, and truncate it to a maximum length.
    # FL - STEP 7
    normalized = normalize_question(question)

    if is_self_healing(normalized):
        return route_self_healing(normalized)

    # 2. Resolve the intent using predicate matching. If no intent is found, return None for both intent and goal.
    # FL - STEP 8
    intent = resolve_intent(normalized)
    if not intent:
        if lemon:
            lemon.counter("routing.intent.unresolved")
        return normalized, None, None

    if lemon:
        lemon.observe_intent(
            raw_text=normalized,
            classified_intent=intent.get("intent", ""),
            confidence=float(intent.get("confidence", 1.0)),
            trace_id=trace_id,
        )

    # 3. Resolve the goal by routing the question to the appropriate handler. If no goal is found, return None for the goal.
    # FL - STEP  9
    return normalized, intent, resolve_goal_from_intent(normalized, intent)


def is_self_healing(question: str) -> bool:
    from src.monkey_brain.kernel.plan.intents.predicates.self_healing_workload import (
        is_self_healing_question,
    )

    return is_self_healing_question(question)


def route_self_healing(question: str) -> tuple[str, dict, Any]:
    from src.monkey_brain.kernel.plan.intents.intent_registry import INTENT_REGISTRY

    intent: dict = {
        "intent": SELF_HEALING_INTENT,
        "confidence": 1.0,
        "workload_id": SELF_HEALING_INTENT,
    }
    route = INTENT_REGISTRY.get(SELF_HEALING_INTENT)
    goal = route_question(question, intent) if route else None
    return question, intent, goal


def resolve_intent(question: str) -> Optional[dict]:
    """Predicate match only — registry check. Returns intent dict or None.

    The intent classifier has been removed from the planning pipeline.
    The execution graph is synthesized directly by the planner.
    Intent resolution relies solely on registered predicates.
    """
    from src.monkey_brain.kernel.plan.intents.intent_registry import INTENT_REGISTRY

    # all intents just go. to predicates
    intent = fallback_to_predicates(question)
    if not intent:
        return None

    intent_name = intent.get("intent")
    if INTENT_REGISTRY.get(intent_name) is None:
        logger.warning(
            "No route registered for intent %r — treating as unresolved. "
            "Register a route in INTENT_REGISTRY to support it.",
            intent_name,
        )
        return None

    return intent


def resolve_goal_from_intent(question: str, intent: dict) -> Any:
    """route_question → GoalRegistry registration.

    Assumes `intent` already has a registered route — resolve_intent()
    guarantees that before returning an intent.
    """
    try:
        from src.monkey_brain.kernel.plan.goals.registry import GoalRegistry

        goal = route_question(question, intent)
        if goal and goal.name:
            GoalRegistry().register_goal(goal)
        return goal
    except Exception as e:
        logger.error("Goal resolution failed for intent %s: %s", intent.get("intent"), e)
        return None
