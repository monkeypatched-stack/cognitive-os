"""A Broca agent's payload must survive the trip to the runtime (R-5 root cause).

There are two classes named AgentResult:

    src.monkey_brain.runtime.agent_resolver.AgentResult          (what the runtime consumes)
    src.monkey_brain.kernel.execute.runtime.outcome.AgentResult  (what every agent returns)

BrocaAgent.execute() checked `isinstance(raw, AgentResult)` against the FIRST, while every
BaseETASSAgent returns the SECOND. The check never matched, so execution fell through to:

    return AgentResult(success=True, produced={"value": str(raw)})

Every agent's structured payload was str()-ed into a "value" key and success was hardcoded
True. Hence "LineResolverAgent: [ok]" with an empty action in the live answer — the output
dict had no `action` — and hence zero grounding forever: an agent's `sources` were flattened
into a string before anything could read them.
"""

from __future__ import annotations

import asyncio

from src.monkey_brain.kernel.execute.runtime.outcome import AgentResult as AgentOutcome
from src.monkey_brain.runtime.agent_resolver import AgentResult as ResolverResult
from src.monkey_brain.runtime.agent_resolver import BrocaAgent

PAYLOAD = {
    "action": "report",
    "operation": "calibration.due",
    "success": True,
    "overdue": [{"equipment_id": "SN-INS-TAB-06-02", "days_overdue": 10}],
    "sources": [{"source": "mongo://demo/calibration_records", "count": 78}],
}


class _Agent:
    agent_type = "calibration_due_evaluator"

    def __init__(self, result):
        self._result = result

    async def handle(self, state):
        return self._result


def _execute(result):
    return asyncio.run(BrocaAgent(_Agent(result), "CalibrationDueEvaluatorAgent").execute({}))


def test_the_two_agent_result_classes_are_genuinely_different():
    """If they ever merge, the bug this guards is gone — but until then, this is the trap."""
    assert AgentOutcome is not ResolverResult


def test_the_payload_survives_instead_of_being_stringified():
    out = _execute(AgentOutcome(reward=1.0, payload=PAYLOAD, success=True))
    assert out.produced == PAYLOAD
    assert "value" not in out.produced, "the payload was stringified again"
    assert out.produced["action"] == "report"


def test_the_sources_survive_so_the_answer_can_be_grounded():
    from src.monkey_brain.kernel.execute.grounding import extract_step_citations

    out = _execute(AgentOutcome(reward=1.0, payload=PAYLOAD, success=True))
    cites = extract_step_citations([{"agent": "x", "success": True, "output": out.produced}])
    assert cites and cites[0]["source"] == "mongo://demo/calibration_records"


def test_a_failed_agent_is_not_reported_as_a_success():
    """success was hardcoded True on this path — a failing agent still read [ok]."""
    failed = AgentOutcome(
        reward=0.0,
        success=False,
        payload={"action": "report", "success": False, "error": "no line 3"},
    )
    out = _execute(failed)
    assert out.success is False
    assert out.produced["error"] == "no line 3"


def test_observations_and_reward_are_carried_through():
    out = _execute(AgentOutcome(reward=0.75, payload=PAYLOAD, success=True, observations=["read 78 records"]))
    assert out.events_emitted == ["read 78 records"]
    assert out.feedback == 0.75


def test_a_plain_dict_agent_still_works():
    """Legacy agents that return a bare dict must not regress."""
    out = _execute({"success": False, "payload": {"error": "boom"}})
    assert out.success is False
    assert out.produced == {"error": "boom"}


def test_an_unrecognised_result_is_a_failure_not_a_silent_success():
    """Previously ANY unrecognised type became success=True with a stringified body."""
    out = _execute("just a string")
    assert out.success is False
    assert "unrecognised agent result type" in out.produced["error"]
