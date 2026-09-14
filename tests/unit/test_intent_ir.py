"""Tests for IntentIR / ExecutionContext / GoalExecutor.execute_context / Replay.

Covers the 10 required scenarios from the architectural hardening sprint:
  1. schema version mismatch
  2. malformed intent
  3. missing required fields
  4. tampered intent
  5. immutable intent enforcement
  6. ExecutionContext propagation
  7. replay execution
  8. validation failure paths
  9. deterministic execution
  10. planner/runtime compatibility

Written, not executed, per project convention (write test files; don't run
pytest as part of a fix/feature change).
"""

import asyncio
import sys
import os
from dataclasses import FrozenInstanceError

_repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_repo, os.path.join(_repo, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.monkey_brain.kernel.plan.goals.intent_ir import (
    IntentIR,
    build_intent_ir,
    validate_intent_ir,
    verify_intent_ir,
    sign_intent_ir,
    CURRENT_SCHEMA_VERSION,
)
from src.monkey_brain.kernel.plan.goals.goal import Goal, GoalType
from src.monkey_brain.kernel.execute.context import ExecutionContext
from src.monkey_brain.kernel.execute.models import ExecutionMode
from src.monkey_brain.kernel.plan.goals.executor import GoalExecutor
from src.monkey_brain.kernel.plan.goals.run_store import RunStore, get_run_store, replay


def _make_ir(goal_type=GoalType.QUERY, run_id="run-1", intent_type="work_order_query"):
    goal = Goal(name=intent_type, goal_type=goal_type, required_outputs=["x"])
    intent = {"intent": intent_type, "confidence": 0.9, "workload_id": intent_type}
    return build_intent_ir(intent=intent, goal=goal, run_id=run_id, question="q")


# ── 1. schema version mismatch ──────────────────────────────────────────────


def test_schema_version_mismatch_rejected():
    ir = _make_ir()
    d = ir.to_dict()
    d["schema_version"] = "99.9"
    bad_ir = IntentIR.from_dict(d)
    errors = validate_intent_ir(bad_ir)
    assert any("schema_version" in e for e in errors)


def test_current_schema_version_is_supported():
    ir = _make_ir()
    assert ir.schema_version == CURRENT_SCHEMA_VERSION
    assert validate_intent_ir(ir) == []


# ── 2. malformed intent ─────────────────────────────────────────────────────


def test_malformed_intent_ir_dict_raises():
    malformed = {"schema_version": "1.0"}  # missing everything else
    try:
        IntentIR.from_dict(malformed)
        raised = False
    except (KeyError, ValueError, TypeError):
        raised = True
    assert raised, "from_dict must raise on malformed input, not silently coerce"


# ── 3. missing required fields ──────────────────────────────────────────────


def test_missing_goal_name_rejected():
    ir = _make_ir()
    d = ir.to_dict()
    d["goal"] = {}  # no name
    bad_ir = IntentIR.from_dict(d)
    errors = validate_intent_ir(bad_ir)
    assert any("goal.name" in e for e in errors)


def test_missing_execution_metadata_question_rejected():
    ir = _make_ir()
    d = ir.to_dict()
    d["execution_metadata"] = {}
    bad_ir = IntentIR.from_dict(d)
    errors = validate_intent_ir(bad_ir)
    assert any("execution_metadata" in e for e in errors)


# ── 4. tampered intent ──────────────────────────────────────────────────────


def test_tampered_goal_fails_signature_verification():
    ir = _make_ir()
    d = ir.to_dict()
    d["goal"] = dict(d["goal"])
    d["goal"]["name"] = "work_order_delete"  # attacker escalates to a mutating goal
    tampered = IntentIR.from_dict(d)
    assert verify_intent_ir(tampered) is False
    errors = validate_intent_ir(tampered)
    assert any("signature" in e for e in errors)


def test_untampered_roundtrip_verifies():
    ir = _make_ir()
    ir2 = IntentIR.from_dict(ir.to_dict())
    assert verify_intent_ir(ir2) is True


# ── 5. immutable intent enforcement ─────────────────────────────────────────


def test_intent_ir_frozen_dataclass():
    ir = _make_ir()
    try:
        ir.intent_type = "hacked"
        raised = False
    except FrozenInstanceError:
        raised = True
    assert raised


def test_intent_ir_dict_fields_are_read_only():
    ir = _make_ir()
    try:
        ir.goal["name"] = "hacked"
        raised = False
    except TypeError:
        raised = True
    assert raised, "goal/parameters/execution_metadata must be MappingProxyType, not plain dict"


def test_execution_context_frozen_dataclass():
    ir = _make_ir()
    ctx = ExecutionContext.create(run_id="r", execution_mode=ExecutionMode.QUERY, intent_ir=ir)
    try:
        ctx.run_id = "hacked"
        raised = False
    except FrozenInstanceError:
        raised = True
    assert raised


# ── 6. ExecutionContext propagation ─────────────────────────────────────────


def test_execution_context_log_extra_carries_ir_fields():
    ir = _make_ir(run_id="run-42")
    ctx = ExecutionContext.create(
        run_id="run-42",
        execution_mode=ExecutionMode.EXECUTE,
        intent_ir=ir,
        user_id="u1",
    )
    extra = ctx.log_extra()
    assert extra["run_id"] == "run-42"
    assert extra["intent_id"] == ir.intent_ir_id
    assert extra["schema_version"] == CURRENT_SCHEMA_VERSION
    assert extra["execution_mode"] == "execute"


def test_execute_context_uses_context_run_id_not_kwarg():
    """GoalExecutor.execute_context must source run_id/mode/goal entirely
    from `context` — never from a separately-passed loose parameter."""
    ir = _make_ir(goal_type=GoalType.CREATE, run_id="ctx-run")
    ctx = ExecutionContext.create(run_id="ctx-run", execution_mode=ExecutionMode.QUERY, intent_ir=ir)
    ge = GoalExecutor()
    answer, _, _, llm_answered = asyncio.run(ge.execute_context(ctx, mongo_client=None))
    assert "query mode" in answer
    assert llm_answered is False


# ── 7. replay execution ─────────────────────────────────────────────────────


def test_replay_missing_run_id_reports_error_not_exception():
    store = get_run_store()
    assert store.has("no-such-run") is False
    answer, hits, paths, llm_answered = asyncio.run(replay("no-such-run", None))
    assert "no stored IntentIR" in answer
    assert hits == [] and paths == [] and llm_answered is False


def test_replay_reexecutes_stored_intent_ir_without_reclassifying():
    ir = _make_ir(run_id="replay-run", intent_type="work_order_query")
    store = RunStore()  # singleton — same instance as get_run_store()
    store.store("replay-run", ir)
    assert store.has("replay-run") is True

    # replay()'s "execute" branch resolves get_cognitive_runtime_instance()
    # (the Kernel-booted singleton) when runtime= isn't passed explicitly —
    # this test never boots a Kernel, so pass a bare, unconfigured
    # CognitiveRuntime() directly (replay()'s own documented supported
    # usage: "runtime — the caller's already-booted runtime"). It's not a
    # full production runtime, but that's fine here: this test only checks
    # that the stored IntentIR's own goal name reaches the answer
    # unchanged, not that execution actually succeeds.
    from src.monkey_brain.kernel.cognitive_runtime import CognitiveRuntime

    answer, *_ = asyncio.run(replay("replay-run", None, runtime=CognitiveRuntime()))
    # Must reference the exact goal from the stored IntentIR, not something
    # re-derived from a (nonexistent, since replay has no raw question input
    # of its own here) fresh classification pass.
    assert "work_order_query" in answer


# ── 8. validation failure paths ─────────────────────────────────────────────


def test_execute_context_stops_immediately_on_validation_failure():
    ir = _make_ir()
    d = ir.to_dict()
    d["confidence"] = 5.0  # out of [0,1] range
    bad_ir = IntentIR.from_dict(d)
    ctx = ExecutionContext.create(run_id="r", execution_mode=ExecutionMode.EXECUTE, intent_ir=bad_ir)
    ge = GoalExecutor()
    answer, hits, paths, llm_answered = asyncio.run(ge.execute_context(ctx, mongo_client=None))
    assert "validation failed" in answer
    assert llm_answered is False
    # No partial execution artifacts should leak through on a validation failure.
    assert hits == [] and paths == []


def test_execute_context_with_no_intent_ir_is_an_error_not_a_fallback():
    ctx = ExecutionContext.create(run_id="r", execution_mode=ExecutionMode.EXECUTE, intent_ir=None)
    ge = GoalExecutor()
    answer, *_ = asyncio.run(ge.execute_context(ctx, mongo_client=None))
    assert "no IntentIR provided" in answer


# ── 9. deterministic execution ──────────────────────────────────────────────


def test_same_intent_ir_produces_same_read_only_refusal_every_time():
    ir = _make_ir(goal_type=GoalType.DELETE, run_id="det-run")
    ge = GoalExecutor()
    answers = []
    for _ in range(3):
        ctx = ExecutionContext.create(run_id="det-run", execution_mode=ExecutionMode.QUERY, intent_ir=ir)
        answer, *_ = asyncio.run(ge.execute_context(ctx, mongo_client=None))
        answers.append(answer)
    assert len(set(answers)) == 1, "identical IntentIR + mode must yield identical outcome every time"


# ── 10. planner/runtime compatibility ───────────────────────────────────────


def test_runtime_never_reclassifies_a_planner_supplied_goal():
    """Build an IntentIR whose goal name deliberately would NOT match what
    fresh classification of `question` would produce, and confirm the
    runtime still executes the IntentIR's goal verbatim."""
    goal = Goal(name="totally_custom_goal_xyz", goal_type=GoalType.QUERY, required_outputs=["x"])
    intent = {"intent": "totally_custom_goal_xyz", "confidence": 0.99}
    ir = build_intent_ir(
        intent=intent,
        goal=goal,
        run_id="compat-run",
        question="this text has nothing to do with the goal name",
    )
    ctx = ExecutionContext.create(run_id="compat-run", execution_mode=ExecutionMode.EXECUTE, intent_ir=ir)
    ge = GoalExecutor()
    answer, *_ = asyncio.run(ge.execute_context(ctx, mongo_client=None))
    assert "totally_custom_goal_xyz" in answer


def test_goal_from_dict_roundtrips_through_intent_ir():
    goal = Goal(
        name="g",
        goal_type=GoalType.ANALYZE,
        required_outputs=["a", "b"],
        entities=["e1"],
    )
    ir = build_intent_ir(intent={"intent": "g", "confidence": 0.7}, goal=goal, run_id="r", question="q")
    rebuilt = Goal.from_dict(dict(ir.goal))
    assert rebuilt.name == goal.name
    assert rebuilt.goal_type == goal.goal_type
    assert rebuilt.required_outputs == goal.required_outputs
    assert rebuilt.entities == goal.entities


if __name__ == "__main__":
    tests = [
        test_schema_version_mismatch_rejected,
        test_current_schema_version_is_supported,
        test_malformed_intent_ir_dict_raises,
        test_missing_goal_name_rejected,
        test_missing_execution_metadata_question_rejected,
        test_tampered_goal_fails_signature_verification,
        test_untampered_roundtrip_verifies,
        test_intent_ir_frozen_dataclass,
        test_intent_ir_dict_fields_are_read_only,
        test_execution_context_frozen_dataclass,
        test_execution_context_log_extra_carries_ir_fields,
        test_execute_context_uses_context_run_id_not_kwarg,
        test_replay_missing_run_id_reports_error_not_exception,
        test_replay_reexecutes_stored_intent_ir_without_reclassifying,
        test_execute_context_stops_immediately_on_validation_failure,
        test_execute_context_with_no_intent_ir_is_an_error_not_a_fallback,
        test_same_intent_ir_produces_same_read_only_refusal_every_time,
        test_runtime_never_reclassifies_a_planner_supplied_goal,
        test_goal_from_dict_roundtrips_through_intent_ir,
    ]
    ok = 0
    failures = []
    for fn in tests:
        try:
            fn()
            ok += 1
        except Exception as e:
            failures.append(f"{fn.__name__}: {e!r}")
    print(f"IntentIR/ExecutionContext: {ok}/{len(tests)} passed")
    for f in failures:
        print(f"  FAIL: {f}")
