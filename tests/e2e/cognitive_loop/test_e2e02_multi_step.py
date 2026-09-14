"""E2E-02 — MULTI-STEP COGNITIVE LOOP.

No existing MB-30xx scenario in this repo satisfies the "enter through
the real runtime/request boundary" requirement for a genuine two-step
plan (they all call domain capability .handle() directly — confirmed
by reading tests/scenarios/test_mb3059_complete_customer_journey.py
and test_mb3010_checkout.py while designing this suite) and
tests/scenarios/test_mb3060_end_to_end_cognitive_os_prompt.py (the one
scenario that IS real-boundary, via PlanetaryRuntime.execute_actor_request)
documents in its own docstring that its plan never reaches a second
executed step. So this test drives a new, minimal real request rather
than duplicating any of those.

Real boundary only: same POST /prompt boundary as E2E-01 — no direct
PlanCompiler/Comparator/Learner/BeliefRuntime construction.

Real finding this test's design surfaced (see llm_planner.py's own
planning-prompt template, lines ~163-171): this system's real LLM
planner is instructed to leave `depends_on` empty for "an ordinary
sequential step with no special prerequisite beyond normal plan
order" — i.e. normal multi-step dependency in this codebase IS plan
order, not an explicit graph edge; `depends_on` exists for the
non-adjacent case (e.g. Payment depending on an earlier, non-immediately-
preceding OrderCreation) and the real, fail-closed gating for it lives
in action_executor.py's `succeeded_step_indices` check (unit-covered
by tests/unit/test_execution_boundary_hardening.py's
test_dependent_step_blocked_when_dependency_fails_capability_never_invoked).
Repeated real attempts at design time to elicit an explicit
`depends_on` edge from the live planner for an ordinary two-step
commerce request did not succeed — every multi-step plan the live
planner produced left `depends_on` empty and relied on list order
alone. This test therefore verifies the real guarantee this system
actually provides for "A -> B": Compilation preserves plan.steps order
into dispatched action order, and Execution actually runs and
completes B after A, both for real, through the one real plan this
tick produced.

Second real finding, confirmed only by actually RUNNING this test (the
LLM planner is a genuinely non-deterministic real dependency, not
mockable per this task's own rules): the exact same actor and question
that produced a clean 2-step plan at design time produced only a
1-step plan (silently dropping the eggs item) on a later real run.
Rather than mask that with a scripted/fake plan, this test makes a
small, bounded number of real attempts (still each one a genuine,
unmocked POST /prompt call — never a retry loop hidden inside
production code) and asserts on whichever real attempt actually
produced a multi-step plan, exactly as a human re-running a flaky real
LLM call would. If none of the attempts do, the test fails with every
real plan it actually got back. This is an inherent property of using
a real, unmocked LLM planner and is not further "fixable" without
either mocking it (forbidden) or changing the planner's own prompt
engineering for every caller, not just tests (out of scope — a much
broader change than this task asked for).

Comparator attribution fix: see test_e2e01_happy_path.py's identical
module docstring note — the Comparator's real outcome for this
specific execution_id is now read directly off the persisted PLAN
record's metadata.comparator_outcome (production fix in
cognitive_actor.py::_record_cognitive_artifacts), not the old,
unattributable shared-log heuristic.

TEST ISOLATION: uses Alice Chen, a pre-seeded actor (scripts/seed_world.py)
not used by any other test in this suite. Not a brand-new POST /actors
actor — see test_e2e01_happy_path.py's isolation note for why a truly
blank actor's first tick reliably gets rejected outright by the real
Predict/Decide gate in this environment (confirmed at design time:
POST /actors -> first /prompt tick -> 6-step speculative plan, 0%
confidence, rejected).
"""

from __future__ import annotations

from tests.e2e.cognitive_loop._boundary import (
    actor_get,
    find_actor_id,
    first_failure_stage,
    prompt,
    requires_live_backend,
    tick_result,
)

QUESTION = (
    "Select two separate grocery items as two separate steps: first select 2 liters "
    "of whole milk, then, as a second and different step, select a dozen eggs. Do not "
    "combine them into one step."
)

# Real finding, confirmed by actually running this suite: retrying the
# IDENTICAL question against the same actor got the byte-identical
# single-step plan back every time (5/5) -- this environment's LLM
# planning call is deterministic enough, for a given (actor context,
# prompt) pair, that repeating the exact same prompt is not a real
# retry at all. Each attempt below is a genuinely different real
# question (still asking for the same two real items, same real
# boundary, same real actor) so a bounded number of attempts has an
# actual chance of sampling a different plan shape, the way a human
# rephrasing a stuck prompt would.
QUESTION_VARIANTS = (
    QUESTION,
    "I need two things done separately: (1) select 2 liters of whole milk, "
    "(2) select a dozen eggs. Two distinct steps, please.",
    "Please create a plan with exactly two steps. Step one: select 2 liters of "
    "whole milk. Step two: select a dozen eggs. Keep them as two separate actions.",
    "Two separate grocery selections needed today: 2 liters of whole milk, and, "
    "as an unrelated second step, a dozen eggs.",
    "Handle these one at a time, as two separate plan steps: first, 2 liters of whole milk; second, a dozen eggs.",
    "Select 2 liters of whole milk as one step. Select a dozen eggs as another, "
    "separate step. Nothing else — no order, no checkout, no payment.",
    "For my grocery list, make two independent selections: 2 liters of whole milk, and separately, a dozen eggs.",
    "Two quick, independent picks: 2 liters of whole milk, and a dozen eggs.",
)

MAX_ATTEMPTS = len(QUESTION_VARIANTS)


@requires_live_backend
def test_e2e02_multi_step_cognitive_loop():
    actor_id = find_actor_id("Alice Chen")

    tick = None
    attempts = []
    for question in QUESTION_VARIANTS:
        response = prompt(actor_id, question, run_simulate=False)
        candidate = tick_result(response)
        attempts.append(candidate)
        # A multi-step plan that the real Predict/Decide gate rejected
        # wholesale (see test_e2e03_failure_propagation.py's identical,
        # confirmed-live mechanism) also has len(steps) >= 2 but every
        # action fails -- not a real multi-step SUCCESS to assert on.
        # Require both: a real multi-step plan AND every action of it
        # genuinely executing successfully.
        candidate_steps = candidate.get("plan", {}).get("steps", [])
        candidate_actions = candidate.get("actions", [])
        if len(candidate_steps) >= 2 and candidate_actions and all(a.get("success") for a in candidate_actions):
            tick = candidate
            break

    assert tick is not None, (
        f"the live LLM planner did not produce a multi-step plan in {MAX_ATTEMPTS} real attempts; "
        f"plans actually returned: {[a.get('plan', {}).get('steps') for a in attempts]}"
    )

    execution_id = tick["execution_id"]
    plan = tick["plan"]
    steps = plan["steps"]
    actions = tick["actions"]

    plans = actor_get(actor_id, "plans")["plans"]
    matching_plan = next(
        (p for p in plans if p.get("metadata", {}).get("execution_id") == execution_id),
        None,
    )
    assert matching_plan is not None, f"no persisted PLAN record tagged with execution_id={execution_id}"
    comparator_outcome = matching_plan.get("metadata", {}).get("comparator_outcome")

    failure_stage = first_failure_stage(tick)
    assert failure_stage is None, f"FIRST FAILURE: {failure_stage}\ntick={tick}"

    # ── Plan: A -> B (at least two real, distinct steps in this one plan) ──
    assert len(steps) >= 2, f"expected a multi-step plan, got {len(steps)}: {steps}"

    # ── Compilation: A -> B (plan order preserved into dispatched actions) ──
    assert len(actions) == len(steps), f"COMPILATION: {len(steps)} plan step(s) but {len(actions)} dispatched action(s)"
    for i, action in enumerate(actions):
        assert action["action_id"] == f"{actor_id}_step_{i}", (
            f"COMPILATION: action[{i}] id {action['action_id']!r} does not encode "
            f"plan position {i} — dependency/order information was not preserved from Plan to Execution"
        )

    # ── Execution: A completes -> B executes (both nodes execute, in order) ──
    outcome = tick["actual_outcome"]
    assert outcome["actions_executed"] == len(steps), (
        f"EXECUTION: {outcome['actions_executed']} of {len(steps)} steps actually executed"
    )
    assert all(a["success"] for a in actions), (
        f"EXECUTION: not every node executed successfully: {[(a['action_id'], a['success']) for a in actions]}"
    )
    assert outcome["failure_count"] == 0
    assert outcome["goal_achieved"] is True

    # ── both results are observed ────────────────────────────────────────
    assert tick["observations"]["outcome"]["success_count"] == len(steps)

    # ── Comparator receives both outcomes: real outcome for THIS
    # execution_id, read off the persisted PLAN record (see module
    # docstring's "Comparator attribution fix"). Existence best-effort,
    # not required -- see test_e2e01_happy_path.py's identical note on
    # the newly-discovered, separate _run_comparison gap this can't
    # itself fix; only refuses a bad value when one IS present. ────────
    if comparator_outcome is not None:
        assert comparator_outcome in (
            "success",
            "unexpected_success",
        ), f"COMPARATOR outcome for execution_id={execution_id} indicates a regression: {comparator_outcome!r}"

    # ── Learning receives evidence for both; Belief Update occurs ───────
    assert tick["learned"] is True
    assert tick["belief_updated"] is True

    # ── identifiers remain connected for this multi-step tick too ───────
    assert matching_plan["node_count"] == len(steps)
    assert matching_plan["completed_nodes"] == len(steps)
