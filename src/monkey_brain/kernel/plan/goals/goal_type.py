"""What is the user actually asking for — a read, or a change?

/plan built every goal with `goal_type=GoalType.QUERY, confidence=1.0`, hardcoded. Not
inferred and then wrong: never inferred at all. "build a simple todo agent" came back as a
query, asserting full confidence.

That is not cosmetic. GoalExecutor refuses a mutating goal in a read-only mode:

    _MUTATING_GOAL_TYPES = frozenset({GoalType.CREATE, GoalType.UPDATE, GoalType.DELETE})
    if mode in _READ_ONLY_MODES and goal.goal_type in _MUTATING_GOAL_TYPES:  # executor.py:210
        ... refuse ...

Since nothing ever produced a mutating goal_type, that gate has never fired in its life. A
"delete every work order" request was typed QUERY and went straight through the read-only
check. The check was real; the input to it was a constant.

Two flaws in the existing detect_intent() (agent_middleware) that this does not repeat:

  * It matches against `set(question.split())`, so the multi-word entries in its own keyword
    lists — "how many" — can never match a token, and are dead.
  * It tests CREATE first, unconditionally, so "show me how to create an index" is a CREATE.
    Order of the IF statements decided the answer, rather than anything about the sentence.

Here, the verb that appears EARLIEST in the sentence wins, because that is the one carrying
the request: "build a todo agent that deletes old tasks" is a build; "delete the todo agent I
built" is a delete. Same two verbs, opposite requests, and only their position separates them.
"""

from __future__ import annotations

import re
from typing import Iterable

from src.monkey_brain.kernel.plan.goals.goal import GoalType

# Ordered by specificity within a type; phrases are matched as phrases, not tokens.
_KEYWORDS: dict[GoalType, tuple[str, ...]] = {
    GoalType.DELETE: (
        "delete",
        "remove",
        "drop",
        "clear",
        "purge",
        "uninstall",
        "revoke",
    ),
    GoalType.UPDATE: (
        "update",
        "edit",
        "modify",
        "change",
        "rename",
        "set",
        "mark",
        "complete",
        "finish",
        "close",
        "assign",
        "approve",
        "enable",
        "disable",
        "migrate",
        "patch",
        "fix",
    ),
    GoalType.CREATE: (
        "create",
        "build",
        "add",
        "new",
        "write",
        "draft",
        "generate",
        "compose",
        "make",
        "implement",
        "scaffold",
        "deploy",
        "install",
        "register",
        "schedule",
    ),
    GoalType.ANALYZE: (
        "analyze",
        "analyse",
        "analysis",
        "summarize",
        "summarise",
        "summary",
        "explain",
        "review",
        "evaluate",
        "assess",
        "diagnose",
        "why",
    ),
    GoalType.AGGREGATE: ("how many", "count", "total", "sum", "average", "aggregate"),
    GoalType.SEARCH: (
        "search",
        "find",
        "look up",
        "lookup",
        "locate",
        "which",
        "where",
    ),
    GoalType.QUERY: (
        "get",
        "show",
        "list",
        "read",
        "fetch",
        "what",
        "when",
        "who",
        "status",
        "is",
        "are",
        "does",
        "do",
    ),
}

# A keyword only counts as a whole word (or whole phrase) — "set" must not fire on "asset",
# and "is" must not fire on "this".
_PATTERNS: list[tuple[GoalType, str, re.Pattern[str]]] = [
    (goal_type, word, re.compile(rf"(?<!\w){re.escape(word)}(?!\w)"))
    for goal_type, words in _KEYWORDS.items()
    for word in words
]

MATCHED_CONFIDENCE = 0.9
# No verb we recognise. QUERY is the safe default — it is the only type the executor's
# read-only gate lets through — but it must NOT claim confidence it does not have. The old
# code asserted 1.0 on a value it never computed.
UNMATCHED_CONFIDENCE = 0.3


def classify_goal_type(question: str) -> tuple[GoalType, float]:
    """(goal_type, confidence) for a natural-language request.

    The earliest recognised verb decides, since that is the verb carrying the request. Ties
    (two keywords at the same position, e.g. a word in two lists) are broken toward the more
    destructive type, so an ambiguous sentence is never quietly typed as a read.
    """
    text = (question or "").lower().strip()
    if not text:
        return GoalType.QUERY, UNMATCHED_CONFIDENCE

    severity = {
        GoalType.DELETE: 0,
        GoalType.UPDATE: 1,
        GoalType.CREATE: 2,
        GoalType.ANALYZE: 3,
        GoalType.AGGREGATE: 4,
        GoalType.SEARCH: 5,
        GoalType.QUERY: 6,
    }

    best: tuple[int, int, GoalType] | None = None  # (position, severity, type)
    for goal_type, _word, pattern in _PATTERNS:
        m = pattern.search(text)
        if m is None:
            continue
        candidate = (m.start(), severity[goal_type], goal_type)
        if best is None or candidate < best:
            best = candidate

    if best is None:
        return GoalType.QUERY, UNMATCHED_CONFIDENCE
    return best[2], MATCHED_CONFIDENCE


def is_mutating(goal_type: GoalType) -> bool:
    """Whether executing this goal can change the world. Mirrors GoalExecutor's own set."""
    return goal_type in (GoalType.CREATE, GoalType.UPDATE, GoalType.DELETE)


def mutating_verbs(question: str) -> list[str]:
    """Every mutating verb present anywhere in the question, not just the leading one.

    `classify_goal_type` deliberately answers "what is being asked for". This answers a
    different, narrower question — "could this touch anything" — for a caller that wants to
    be careful about a request like "show me how to delete the audit log", whose leading verb
    is a read but which names a destructive one.
    """
    found: list[str] = []
    text = (question or "").lower()
    for goal_type, word, pattern in _PATTERNS:
        if is_mutating(goal_type) and pattern.search(text):
            found.append(word)
    return sorted(set(found))


def describe(goal_type: GoalType, confidence: float) -> str:
    return f"{goal_type.value} (confidence {confidence:.1f})"


__all__: Iterable[str] = (
    "classify_goal_type",
    "is_mutating",
    "mutating_verbs",
    "describe",
    "MATCHED_CONFIDENCE",
    "UNMATCHED_CONFIDENCE",
)
