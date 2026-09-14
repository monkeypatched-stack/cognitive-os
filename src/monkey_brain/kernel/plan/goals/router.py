"""Goal Router — maps a classified intent dict to a typed Goal object."""

from __future__ import annotations

from src.monkey_brain.kernel.plan.goals.goal import Goal, build_goal_from_question


def route_question(question: str, classified_intent: dict | None = None) -> Goal:
    """Build a Goal from a classified intent.

    Delegates to build_goal_from_question, which derives GoalType and
    required_outputs from intent_registry.py's IntentDefinition config
    (resolve_goal_type / resolve_required_outputs), falling back to
    GoalType.QUERY for intents with no registered definition.
    Returns a Goal with name="" when no intent is provided.
    """
    intent = classified_intent or {}
    intent_name = intent.get("intent", "")

    # 1. Build a Goal object from the question, intent name, and group. Set the confidence from the intent dict.
    #  # FL - STEP 14
    goal = build_goal_from_question(
        question=question,
        intent_name=intent_name,
        group=intent.get("group", ""),
    )
    goal.confidence = float(intent.get("confidence", 0.0))
    return goal
