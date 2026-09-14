"""Shared goal canonicalization — the (actor_id, goal_key) composite key
every goal-scoped planning state (Current Plan, plan-skip counters,
TransitionModel) uses to avoid cross-goal contamination (a standing plan/
score-floor/failure-history belonging to one goal must never suppress a
freshly generated plan for an unrelated goal).

Originally private to kernel/pipeline/belief_state.py (BeliefState.update_goal's
own idempotency check) — extracted here so every goal-scoped lookup across
the planning/prediction pipeline shares exactly one normalization, not a
second implementation that could drift.
"""

from __future__ import annotations

import re

_GOAL_CONNECTOR_WORDS = frozenset({"a", "an", "the", "of", "to", "for", "and"})


def canonicalize_goal(name: str) -> str:
    """Surface-form normalization ONLY (case, trailing punctuation,
    whitespace, common connector words) — NOT semantic dedup (it won't
    merge "buy milk" and "purchase dairy"), and deliberately doesn't try to
    be; that would need real similarity scoring this isn't attempting."""
    lowered = name.strip().lower().rstrip(".!?")
    words = [w for w in re.findall(r"[a-z0-9]+", lowered) if w not in _GOAL_CONNECTOR_WORDS]
    return " ".join(sorted(words))
