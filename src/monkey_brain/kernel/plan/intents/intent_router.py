"""Intent router — maps classified intents to handlers.

Bellman owns execution policy.
This module provides:
  - classified_intent_is(): confidence threshold check
  - query_plan_from_classification(): extract or build query plan
  - unsupported_intent_answer(): fallback for unmatched intents
  - classify_and_check_support(): classify question and return support status
"""

from __future__ import annotations

from typing import Any


from src.monkey_brain.kernel.plan.intents.intent_registry import INTENT_REGISTRY
from src.monkey_brain.kernel.plan.classifier.embed_classifier import classify_intent
from src.monkey_brain.kernel.config import INTENT_MIN_CONFIDENCE


def classified_intent_is(
    classified_intent: dict,
    *intents: str,
    min_confidence: float = INTENT_MIN_CONFIDENCE,
) -> bool:
    """Check if classified intent matches any of the provided intents."""
    return (
        str(classified_intent.get("intent") or "") in intents
        and float(classified_intent.get("confidence") or 0.0) >= min_confidence
    )


def query_plan_from_classification(classified_intent: dict, question: str) -> dict:
    """Extract or build query plan from classification."""
    plan = classified_intent.get("query_plan")
    if plan:
        return plan
    intent_name = classified_intent.get("intent", "unknown")
    return {
        "intent": intent_name,
        "question": question,
        "filters": classified_intent.get("filters", {}),
        "entities": classified_intent.get("entities", []),
    }


def unsupported_intent_answer(classified_intent: dict) -> str:
    """Generate fallback answer for unsupported intents."""
    candidates = classified_intent.get("candidates") or []
    top = candidates[0] if candidates else {}
    top_intent = top.get("intent") or "unknown"
    confidence = top.get("confidence", 0)
    return (
        "I do not have a supported intent for that question yet.\n"
        f"Closest match: {top_intent} ({confidence}).\n"
        "I logged it so we can add a new intent."
    )


def classify_and_check_support(question: str) -> dict[str, Any]:
    """Classify a question and return whether the intent is supported.

    Returns:
        {
            "intent": str,
            "confidence": float,
            "question": str,
            "supported": bool,
        }
    """
    result = classify_intent(question, {"verb": "query", "target": "general"})

    if not result:
        return {
            "intent": "unknown",
            "confidence": 0.0,
            "question": question,
            "supported": False,
        }

    intent_name = result.get("intent", "unknown")
    confidence = result.get("confidence", 0)
    supported = intent_name in INTENT_REGISTRY

    return {
        "intent": intent_name,
        "confidence": confidence,
        "question": question,
        "supported": supported,
    }


def get_pipeline_for_intent(intent_name: str) -> str | None:
    """Return the intent name as a pipeline key, or None if the intent is not registered.

    IntentRoute does not carry a separate pipeline_id — the intent name is the
    routing key used throughout the system. If that ever changes, update here.
    """
    return intent_name if intent_name in INTENT_REGISTRY else None


async def execute_intent(
    mongo_client: Any,
    question: str,
    classified_intent: dict,
    **kwargs: Any,
) -> tuple[str, list, list, bool] | None:
    """Execute a classified intent by looking up its handler in the registry."""
    intent_name = classified_intent.get("intent")
    if not intent_name:
        return None

    route = INTENT_REGISTRY.get(intent_name)
    if not route:
        return None

    try:
        result = await route.handler(mongo_client, question, force=True)
        return result
    except Exception as e:
        import logging

        logging.getLogger("agentos.router").error(f"Handler error for {intent_name}: {e}")
        return None
