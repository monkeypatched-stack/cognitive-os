"""A plan must know whether it is asking to read or to change (plan.py).

/plan built every goal with:

    goal_type=GoalType.QUERY,
    confidence=1.0,

hardcoded. So "build a simple todo agent" was a query, and so was "delete every work order".

This is a safety gate, not a label. GoalExecutor refuses a mutating goal in a read-only mode:

    _MUTATING_GOAL_TYPES = frozenset({GoalType.CREATE, GoalType.UPDATE, GoalType.DELETE})
    if mode in _READ_ONLY_MODES and goal.goal_type in _MUTATING_GOAL_TYPES:   # executor.py:210

Because nothing ever produced a mutating goal_type, that gate had never fired.
"""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.plan.goals.goal import GoalType
from src.monkey_brain.kernel.plan.goals.goal_type import (
    MATCHED_CONFIDENCE,
    UNMATCHED_CONFIDENCE,
    classify_goal_type,
    is_mutating,
    mutating_verbs,
)


@pytest.mark.parametrize(
    "question,expected",
    [
        ("build a simple todo agent", GoalType.CREATE),  # was QUERY
        ("create a work order for line 3", GoalType.CREATE),
        ("delete every work order", GoalType.DELETE),  # was QUERY
        ("remove the calibration record", GoalType.DELETE),
        ("update the calibration interval for P-301", GoalType.UPDATE),
        ("mark the work order complete", GoalType.UPDATE),
        ("how many instruments are overdue?", GoalType.AGGREGATE),
        ("summarize downtime last week", GoalType.ANALYZE),
        ("which pumps are due for calibration?", GoalType.SEARCH),
        ("show me the tablet line", GoalType.QUERY),
    ],
)
def test_the_request_is_classified_from_the_question(question, expected):
    goal_type, confidence = classify_goal_type(question)
    assert goal_type is expected
    assert confidence == MATCHED_CONFIDENCE


def test_the_mutation_gate_can_now_actually_fire():
    """The whole point: a destructive request must produce a goal_type the executor refuses
    in a read-only mode. Previously every request produced QUERY, so it never could."""
    goal_type, _ = classify_goal_type("delete every work order")
    assert is_mutating(goal_type) is True

    from src.monkey_brain.kernel.plan.goals.executor import _MUTATING_GOAL_TYPES

    assert goal_type in _MUTATING_GOAL_TYPES, "the executor would not refuse this"


def test_a_read_is_not_treated_as_a_mutation():
    goal_type, _ = classify_goal_type("which pumps are due for calibration?")
    assert is_mutating(goal_type) is False


# ------------------------------------------------------------------ the leading verb decides


def test_the_earliest_verb_carries_the_request():
    """Same two verbs, opposite requests. Only their position separates them — which is why
    detect_intent()'s fixed if-order (CREATE tested first, always) could not tell them apart.
    """
    assert classify_goal_type("build a todo agent that deletes old tasks")[0] is GoalType.CREATE
    assert classify_goal_type("delete the todo agent I built")[0] is GoalType.DELETE


def test_a_question_about_a_destructive_action_is_still_a_question():
    """'show me how to delete the audit log' is a request for an explanation, not a deletion.
    It is not reclassified — but the destructive verb is surfaced, so a caller enforcing
    policy can see what the sentence names."""
    goal_type, _ = classify_goal_type("show me how to delete the audit log")
    assert goal_type is GoalType.QUERY
    assert mutating_verbs("show me how to delete the audit log") == ["delete"]


# ------------------------------------------------------------------ the old bugs, specifically


def test_keywords_match_whole_words_only():
    """detect_intent() matched on split() tokens, so 'set' inside 'asset' was safe by accident.
    Matching on substrings here would type 'asset status' as an UPDATE."""
    assert classify_goal_type("asset status")[0] is GoalType.QUERY
    assert classify_goal_type("this is the list")[0] is not GoalType.UPDATE


def test_multi_word_keywords_actually_match():
    """detect_intent()'s own keyword list contained 'how many' but matched against
    set(question.split()) — so it could never fire. It was dead code."""
    assert classify_goal_type("how many pumps are overdue")[0] is GoalType.AGGREGATE


def test_an_unrecognised_request_does_not_claim_confidence_it_lacks():
    """The old code asserted confidence=1.0 on a value it never computed."""
    goal_type, confidence = classify_goal_type("blorp the frobnicator")
    assert goal_type is GoalType.QUERY  # the safe default
    assert confidence == UNMATCHED_CONFIDENCE  # but honestly held
    assert confidence < MATCHED_CONFIDENCE


def test_empty_question_is_safe():
    assert classify_goal_type("")[0] is GoalType.QUERY
    assert classify_goal_type("   ")[1] == UNMATCHED_CONFIDENCE


# ------------------------------------------------------------------ the route


def test_the_plan_route_no_longer_hardcodes_the_goal_type():
    from pathlib import Path

    src = Path("src/monkey_brain/api/routes/plan.py").read_text()
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert "goal_type=GoalType.QUERY" not in code, "every plan is a query again"
    assert "classify_goal_type(payload.question)" in code
    assert "confidence=goal_confidence" in code
