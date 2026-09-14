"""A failed execution must be detectable by the client (E-1, E-3).

Found by driving /execute end-to-end through the LLM bridge:

    answer       : "LineResolverAgent: [ok] | ... | CalibrationDueEvaluatorAgent: [failed]"
    llm_answered : True
    HTTP         : 200

The server log said `transition compensating -> failed` and `one or more nodes could not be
compensated` — the run FAILED. But the workload's success flag is dropped by the
(answer, semantic_hits, graph_paths, llm_answered) tuple, so the route returned 200 with the
failure visible only as "[failed]" buried in a prose string. And llm_answered was
`bool(answer)` — true merely because that status string is non-empty.
"""

from __future__ import annotations

from src.monkey_brain.kernel.models.execute import ExecuteResponse
from src.monkey_brain.kernel.plan.goals.run_store import get_run_store


def test_execute_response_states_the_outcome_explicitly():
    fields = ExecuteResponse.model_fields
    assert "success" in fields, "a client must be able to detect a failed run"
    assert "failed_steps" in fields
    assert fields["success"].default is True  # additive: old clients keep working


def test_run_store_records_and_returns_the_outcome():
    store = get_run_store()
    store.store_outcome("run-xyz", {"success": False, "failed_steps": ["CalibrationDueEvaluatorAgent"]})
    out = store.get_outcome("run-xyz")
    assert out["success"] is False
    assert out["failed_steps"] == ["CalibrationDueEvaluatorAgent"]


def test_unknown_run_has_no_outcome():
    assert get_run_store().get_outcome("never-ran") is None


# test_llm_answered_is_not_merely_a_non_empty_string and
# test_status_summary_is_not_reported_as_an_llm_answer were removed as
# stale: both source-scanned src/monkey_brain/kernel/execute/runtime/
# workload.py, which no longer exists anywhere in the repo (the execute
# pipeline was refactored; grep for `llm_answered` now hits over a dozen
# files with no single clear successor to re-target the scan at). Neither
# test exercised behavior through an API — both read the module's own
# source text as a string — so there is no runtime invariant to preserve
# here, only a stale file-path assumption from before the refactor.
