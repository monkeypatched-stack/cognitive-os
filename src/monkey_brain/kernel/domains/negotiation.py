"""Negotiation domain — utility evaluation, bounded bilateral bargaining,
and persistent agreements, shared across verticals.

Same split principle as logistics.py/domain_security.py: nothing here
reasons about groceries specifically, and nothing here decides FOR an
actor. `evaluate_candidates` computes real, deterministic numbers from
an actor's own real preferences — it ranks, it doesn't choose; the
actor's own LLM planner still makes the actual decision, using these
numbers as one more real fact (kernel/pipeline/llm_planner.py's own
constitution: "no business decision... may be hardcoded anywhere").
`negotiate_terms` generalizes the existing `negotiate_price` (grocery.py)
bargaining shape to any single bounded numeric term. `record_agreement`
follows the exact CAS-append template `place_bid` already uses.
"""

from __future__ import annotations

import time
from typing import Any


def evaluate_utility(
    preferences: dict[str, float], candidate: dict[str, float]
) -> float:
    """Real, deterministic weighted-sum utility over whatever keys the
    two dicts share — sum(preferences[k] * candidate[k]). Does no
    normalization or interpretation itself (same principle
    llm_planner.py's system prompt states for facts generally: the
    kernel computes and surfaces, it doesn't decide what a number
    means); see evaluate_candidates for the normalized, multi-candidate
    form actually used for a real side-by-side comparison."""
    shared_keys = set(preferences) & set(candidate)
    if not shared_keys:
        return 0.0
    return sum(preferences[k] * candidate[k] for k in shared_keys)


def evaluate_candidates(
    preferences: dict[str, float], candidates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Ranks real candidate options against one actor's own real
    preferences — the concrete, explainable "Utility Evaluation" a
    negotiation/strategy trace exposes, not something an LLM merely
    asserts happened. Each candidate is
    `{"name": str, "attributes": {"cost": 40, "speed": 0.9, ...}}`.

    Attributes are min-max normalized ACROSS the candidate set, per
    key, before the weighted sum — so "cost" (dollars) and "speed"
    (0-1) are comparable regardless of native units. A key present on
    only some candidates is normalized only across the candidates that
    have it. Caller convention: pre-negate lower-is-better attributes
    (e.g. `attributes={"cost": -40}` for a $40 option) so "higher
    normalized value = better" holds uniformly — this function has no
    domain knowledge of which attributes are cost-like.

    Returns candidates sorted best-first, each tagged with its real
    computed `utility`.
    """
    if not candidates:
        return []

    all_keys: set[str] = set()
    for c in candidates:
        all_keys |= set(c.get("attributes", {}).keys())

    bounds: dict[str, tuple[float, float]] = {}
    for key in all_keys:
        values = [
            c["attributes"][key] for c in candidates if key in c.get("attributes", {})
        ]
        bounds[key] = (min(values), max(values))

    results = []
    for c in candidates:
        normalized: dict[str, float] = {}
        for key, value in c.get("attributes", {}).items():
            lo, hi = bounds[key]
            normalized[key] = 1.0 if hi == lo else (value - lo) / (hi - lo)
        utility = evaluate_utility(preferences, normalized)
        results.append(
            {
                "name": c.get("name", ""),
                "utility": round(utility, 4),
                "attributes": c.get("attributes", {}),
            }
        )

    results.sort(key=lambda r: r["utility"], reverse=True)
    return results


def negotiate_terms(
    high_side_opening: float,
    high_side_floor: float,
    low_side_opening: float,
    max_rounds: int = 3,
) -> dict:
    """Generic bounded split-the-difference bargaining over a single
    numeric term — the exact same algorithm `negotiate_price`
    (grocery.py:2704) already uses in production, generalized past
    price so a non-price negotiation (MB-3301's schedule delay,
    MB-3305's route-exchange terms) reuses the identical proven shape
    instead of a new one: one side opens high and has a real floor
    below which it won't go (`high_side_opening`/`high_side_floor` —
    e.g. a driver's opening ask for a 3-hour delay, willing to go as
    low as 1 hour), the other opens low (`low_side_opening` — e.g. a
    warehouse's opening offer of 0 hours) and has no explicit ceiling
    (mirrors `negotiate_price`'s buyer never exceeding the listed
    price — here there is no natural "listed" ceiling, so the low side
    is free to rise as far as the high side's floor). A deal is
    reached the moment the low side's position meets or exceeds the
    high side's position, settling at their midpoint that round — not
    automatically at whichever number moved last.
    """
    if low_side_opening >= high_side_floor:
        term = round((low_side_opening + high_side_floor) / 2, 2)
        return {"agreed": True, "term": term, "rounds": []}

    high_pos, low_pos = high_side_opening, low_side_opening
    rounds = []
    for i in range(1, max_rounds + 1):
        if low_pos >= high_pos:
            term = round((low_pos + high_pos) / 2, 2)
            rounds.append(
                {
                    "round": i,
                    "high_side": round(high_pos, 2),
                    "low_side": round(low_pos, 2),
                    "outcome": "deal",
                }
            )
            return {"agreed": True, "term": term, "rounds": rounds}
        rounds.append(
            {
                "round": i,
                "high_side": round(high_pos, 2),
                "low_side": round(low_pos, 2),
                "outcome": "no deal yet",
            }
        )
        gap = high_pos - low_pos
        high_pos = max(high_side_floor, high_pos - gap / 2)
        low_pos = low_pos + gap / 2

    if low_pos >= high_pos:
        term = round((low_pos + high_pos) / 2, 2)
        return {"agreed": True, "term": term, "rounds": rounds}
    return {
        "agreed": False,
        "term": None,
        "rounds": rounds,
        "reason": f"no agreement within {max_rounds} rounds",
    }


def record_agreement(
    kg: Any, entity_id: str, agreement: dict[str, Any], max_attempts: int = 5
) -> tuple[bool, str]:
    """CAS-appends a negotiated agreement to entity_id's real,
    persistent `agreements` list — same template `place_bid`
    (grocery.py:2741) already uses for CAS-append. This is what makes
    a negotiated outcome real world state future reasoning can read
    back (via the same KG fact-lookup every capability already uses),
    not an in-memory-only result that vanishes after the tick that
    produced it."""
    for _ in range(max_attempts):
        entity = kg.get_entity(entity_id)
        if entity is None:
            return False, "entity not found"
        agreements = list(entity.attributes.get("agreements", []))
        agreements.append({**agreement, "recorded_at": time.time()})
        version = kg.version_of(entity_id)
        ok, _current = kg.compare_and_swap(
            entity_id, version, {"agreements": agreements}
        )
        if ok:
            return True, "agreement recorded"
    return False, "too much contention, gave up"
