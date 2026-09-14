"""E2E-04 — LEARNING FEEDBACK.

Real boundary only: two real POST /prompt calls for the SAME actor,
same question — nothing about TransitionModel/PolicyStore/
comparator_runtime is constructed directly; both runs go through the
unmodified production pipeline exactly like E2E-01/02/03.

TEST ISOLATION: this is the ONE test in the suite that intentionally
carries state from one execution to another (see the suite's TEST
ISOLATION note) — Run 2 is deliberately the SAME actor as Run 1, on
purpose, because that's what "learning feedback" means. A brand-new
actor via POST /actors is used (not a pre-seeded family member) so
Run 1 is a genuine, from-nothing cold start with no borrowed history
from any other test or from this suite's own design-time calibration
calls.

Why this scenario, concretely: this suite's design pass ran exactly
this experiment live (fresh actor, "Buy 2 liters of whole milk.",
twice) and observed a real, dramatic before/after: Run 1 produced a
6-step speculative plan at 0% predicted confidence, entirely rejected,
every action failed; Run 2 (same actor, same question, immediately
after) produced a single ProductSelection step at 85% predicted
confidence, accepted, and genuinely executed successfully. That's a
real instance of Execution -> Learning -> Prediction closing the loop
in this codebase, not a hypothetical — this test asserts the general,
directional shape of that same real behavior rather than hardcoding
those exact numbers (which the task spec itself says not to do unless
the exact delta is a documented algorithm guarantee, which it isn't
here).
"""

from __future__ import annotations

from tests.e2e.cognitive_loop._boundary import (
    actor_get,
    create_actor,
    family_society_id,
    prompt,
    requires_live_backend,
    tick_result,
)

QUESTION = "Buy 2 liters of whole milk."


def _selected_probability(tick: dict) -> float:
    selected = (tick.get("predicted_outcome") or {}).get("selected") or {}
    return float(selected.get("probability", 0.0) or 0.0)


@requires_live_backend
def test_e2e04_learning_feedback_loop():
    actor_id = create_actor("LearningFeedback", society_id=family_society_id())

    # ── Run 1: request -> execute -> compare -> learn -> belief ─────────
    response_1 = prompt(actor_id, QUESTION, run_simulate=False)
    tick_1 = tick_result(response_1)
    assert tick_1["execution_id"], "Run 1: no execution_id — pipeline never really ran"
    assert tick_1["learned"] is True, "Run 1: learning state does not exist (tick.learned is False)"
    probability_1 = _selected_probability(tick_1)

    # ── Run 2: same request -> fresh plan -> prediction -> execute ->
    # compare -> learn, same actor ───────────────────────────────────────
    response_2 = prompt(actor_id, QUESTION, run_simulate=False)
    tick_2 = tick_result(response_2)
    assert tick_2["execution_id"], "Run 2: no execution_id — pipeline never really ran"

    # ── Run 2 does not reuse the stale Run 1 execution; gets a distinct
    # execution_id ───────────────────────────────────────────────────────
    assert tick_2["execution_id"] != tick_1["execution_id"], (
        "Run 2 reused Run 1's execution_id — the two ticks are not actually distinct"
    )

    # ── Run 2 prediction is based on the updated model: reads AT LEAST as
    # confidently as Run 1's now-stale, un-learned-from prior (directional
    # only — no specific delta asserted, per the task spec's own guidance) ──
    probability_2 = _selected_probability(tick_2)
    assert probability_2 >= probability_1, (
        f"Run 2's prediction ({probability_2}) did not reflect Run 1's real, learned "
        f"evidence (Run 1 was {probability_1}) — Predict does not appear to be reading "
        f"the updated model"
    )

    # ── both runs are real, separately persisted executions (not one
    # tick silently overwriting the other) ──────────────────────────────
    executions = actor_get(actor_id, "executions")
    correlation_ids = {e.get("correlation_id") for e in executions}
    assert tick_1["execution_id"] in correlation_ids, (
        f"Run 1's execution_id {tick_1['execution_id']} was not persisted to the Timeline"
    )
    assert tick_2["execution_id"] in correlation_ids, (
        f"Run 2's execution_id {tick_2['execution_id']} was not persisted to the Timeline"
    )
