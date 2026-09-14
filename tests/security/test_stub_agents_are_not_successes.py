"""An agent that did nothing must not report success (T-3).

233 of the registered agents are structural stubs:

    class LineAgent(BaseDDDAgent):
        def act(self, decision):
            return {"action": f"line.{decision['operation']}", "success": True}

No I/O anywhere in any of them. They report success unconditionally, so every step backed by
one reads `complete`. Executing "build a simple todo agent" therefore returned HTTP 200,
success=true, four of four nodes complete — and built nothing:

    TaskDefinitionAgent -> task_allocation   (a ROBOTICS stub, handed the word "task")
    TaskExecutionAgent  -> task_allocation   (the same stub again)
    UserInterfaceAgent  -> n8n: return [{json: {result: 'Todo received!'}}]
    PersistenceAgent    -> n8n: return [{json: {result: body.todo || 'No todo provided'}}]

The verdict is taken from the RESULT, not from a flag on the class: a flag gets set once and
then rots, whereas a stub that starts returning real data stops being treated as a stub the
moment it does.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

sys.path.insert(0, "packages/broca")

from broca.agents.ddd._base_ddd import BaseDDDAgent, is_status_echo


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- the echo test itself


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "line.status", "success": True},  # LineAgent
        {
            "action": "scheduling.optimize",
            "success": True,
            "schedule": {},
        },  # SchedulingAgent
        {"action": "plant.status", "success": True, "layer": "domain"},
        {"action": "x", "success": True, "items": [], "detail": "", "meta": None},
    ],
)
def test_a_status_with_no_result_is_an_echo(payload):
    assert is_status_echo(payload) is True


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "report", "success": True, "summary": "78 assets on LINE-TAB-001"},
        {
            "action": "report",
            "success": True,
            "sources": [{"source": "mongo://demo/instruments"}],
        },
        {
            "action": "report",
            "success": True,
            "count": 0,
            "assets": [],
        },  # a real zero IS a result
        {
            "action": "report",
            "success": True,
            "out_of_tolerance": False,
        },  # so is a real False
    ],
)
def test_a_payload_with_real_content_is_not_an_echo(payload):
    assert is_status_echo(payload) is False


def test_zero_and_false_are_results_not_emptiness():
    """ "no pumps on this line" is an answer. Treating count=0 as 'nothing produced' would
    throw away exactly the grounded negatives this whole effort exists to protect."""
    assert is_status_echo({"action": "report", "count": 0}) is False
    assert is_status_echo({"action": "report", "overdue": False}) is False


# ---------------------------------------------------------------- the agents


def test_the_manufacturing_stub_now_reports_unimplemented():
    from broca.agents.domains.manufacturing import LineAgent

    result = _run(LineAgent().handle({"question": "status", "operation": "status"}))
    assert result.success is False, "a stub that touched nothing reported success"
    assert result.payload["unimplemented"] is True
    assert "not implemented" in result.payload["error"]


def test_the_robotics_stub_that_answered_for_a_todo_now_reports_unimplemented():
    """task_allocation is what TaskDefinitionAgent and TaskExecutionAgent were generalized to."""
    from broca.agents.domains.robotics import TaskAllocationAgent

    result = _run(TaskAllocationAgent().handle({"question": "build a simple todo agent"}))
    assert result.success is False
    assert result.payload["unimplemented"] is True


def test_a_real_data_agent_is_untouched():
    from domains.manufacturing.agents.lines import LineResolverAgent

    result = _run(LineResolverAgent().handle({"question": "the tablet line"}))
    assert result.success is True
    assert "unimplemented" not in result.payload
    assert result.payload["answer"] == "Resolved to LINE-TAB-001."


def test_an_agent_that_starts_producing_results_stops_being_a_stub():
    """The check is on the result, so no flag has to be flipped by hand."""

    class Waking(BaseDDDAgent):
        agent_type = "waking"
        description = "returns a result once it has one"
        produce = False

        def act(self, decision):
            if self.produce:
                return {"action": "report", "success": True, "rows": [1, 2, 3]}
            return {"action": "report", "success": True}

    agent = Waking()
    assert _run(agent.handle({})).success is False  # stub

    agent.produce = True
    result = _run(agent.handle({}))
    assert result.success is True  # real, with no code change
    assert "unimplemented" not in result.payload


def test_a_stub_backed_step_fails_the_run():
    """End of the chain: BrocaAgent takes the agent's own verdict, so the step fails, so
    /execute reports success=false instead of a green build that built nothing."""
    from src.monkey_brain.runtime.agent_resolver import BrocaAgent
    from broca.agents.domains.manufacturing import LineAgent

    result = _run(BrocaAgent(LineAgent(), "LineAgent").execute({"question": "status"}))
    assert result.success is False
    assert result.produced["unimplemented"] is True
