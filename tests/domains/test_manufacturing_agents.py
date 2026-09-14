"""The manufacturing agents must answer from the data, or not answer (R-5).

Asked "which pumps on line 3 are due for calibration?", the old pipeline replied:

    "P-301 (last calibrated 2026-01-04, 180-day interval — OVERDUE by 10 days) and P-305..."

There is no line 3. There are no pumps. There were no calibration records. Every step
nonetheless reported [ok], because the 300-odd domain agents are structural stubs whose act()
returns {"success": True} and reads nothing.

These tests stub the data layer, so they assert the agents' judgement rather than the contents
of anyone's database.
"""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

from domains.manufacturing.agents import _data
from domains.manufacturing.agents.calibration import (
    CalibrationDueEvaluatorAgent,
    CalibrationScheduleAgent,
    evaluate_due,
)
from domains.manufacturing.agents.equipment import EquipmentInventoryAgent, _asset_kind
from domains.manufacturing.agents.lines import LineResolverAgent, resolve_line

# the real shape of the demo data: two lines, neither of them "3", and not a pump in sight
INSTRUMENTS = [
    {
        "serial_number": "SN-INS-TAB-06-02",
        "name": "Timer",
        "category": "Measurement",
        "line_id": "LINE-TAB-001",
        "workstation_id": "WS-TAB-06",
        "status": "Active",
    },
    {
        "serial_number": "SN-INS-TAB-08-02",
        "name": "Viscometer",
        "category": "Analytical",
        "line_id": "LINE-TAB-001",
        "workstation_id": "WS-TAB-08",
        "status": "Active",
    },
]
EQUIPMENT = [
    {
        "equipment_id": "EQP-TAB-01-01",
        "name": "LAF Sampling Booth",
        "category": "Dispensing",
        "line_id": "LINE-TAB-001",
        "workstation_id": "WS-TAB-01",
        "status": "active",
    },
    {
        "equipment_id": "EQP-CAP-02-02",
        "name": "Conical Mill",
        "category": "Milling",
        "line_id": "LINE-CAP-001",
        "workstation_id": "WS-CAP-02",
        "status": "active",
    },
]
RECORDS = [
    {
        "record_id": "CAL-SN-INS-TAB-06-02",
        "equipment_id": "SN-INS-TAB-06-02",
        "next_due_date": "2026-07-04",
        "status": "Overdue",
        "calibration_interval_days": 90,
    },
    {
        "record_id": "CAL-EQP-TAB-01-01",
        "equipment_id": "EQP-TAB-01-01",
        "next_due_date": "2027-01-01",
        "status": "Completed",
        "calibration_interval_days": 365,
    },
]


@pytest.fixture
def data(monkeypatch):
    """Stub the data layer. `absent` lists collections that do not exist at all."""
    state = {"absent": set()}
    rows = {
        "instruments": INSTRUMENTS,
        "pharmaceutical_equipment": EQUIPMENT,
        "calibration_records": RECORDS,
        "plant_locations": [],
    }

    async def fake_find(collection, query=None, projection=None, limit=200):
        if collection in state["absent"]:
            raise _data.CollectionAbsent(f"mongo://demo/{collection} does not exist")
        result = rows.get(collection, [])
        query = query or {}
        if "line_id" in query:
            result = [r for r in result if r.get("line_id") == query["line_id"]]
        if "equipment_id" in query:
            wanted = set(query["equipment_id"].get("$in", []))
            result = [r for r in result if r.get("equipment_id") in wanted]
        return list(result)

    async def fake_distinct(collection, field):
        if collection in state["absent"]:
            raise _data.CollectionAbsent(f"mongo://demo/{collection} does not exist")
        return sorted({r[field] for r in rows.get(collection, []) if r.get(field)})

    monkeypatch.setattr(_data, "find", fake_find)
    monkeypatch.setattr(_data, "distinct", fake_distinct)
    return state


async def _payload(agent, question):
    return (await agent.handle({"question": question})).payload


def _run(coro):
    """The suite has no pytest-asyncio mode configured; drive coroutines directly."""
    return asyncio.run(coro)


# ---------------------------------------------------------------- the line that never existed


def test_line_3_is_refused_and_the_real_lines_are_named(data):
    """A truthful negative is an ANSWER, not a malfunction.

    success stays True — the agent looked and reported correctly; it is the *goal* that is
    unsatisfiable, not the agent that broke. This distinction is load-bearing: the composition
    layer discards a FAILED capability and substitutes an LLM guess for it, which is exactly
    how "line 3" became five fabricated pumps. `resolved` carries the real verdict.
    """
    out = _run(
        _payload(LineResolverAgent(), "which pumps on line 3 are due for calibration?")
    )
    assert out["success"] is True
    assert out["resolved"] is False
    assert "no line matching line 3 exists" in out["answer"]
    assert "LINE-TAB-001" in out["answer"] and "LINE-CAP-001" in out["answer"]


@pytest.mark.parametrize(
    "question,expected",
    [
        (
            "what is due on the tablet line?",
            "LINE-TAB-001",
        ),  # 'tablet' -> TAB, by prefix
        ("what is due on the capsule line?", "LINE-CAP-001"),  # 'capsule' -> CAP
        ("show me LINE-CAP-001", "LINE-CAP-001"),  # the id itself
    ],
)
def test_a_real_line_reference_resolves(data, question, expected):
    out = _run(_payload(LineResolverAgent(), question))
    assert out["success"] is True
    assert out["line_id"] == expected


def test_line_1_is_ambiguous_because_both_lines_are_001(data):
    """Every line here is -001, so the number identifies nothing. Say so; do not pick one."""
    out = _run(resolve_line("what is on line 1?"))
    assert out["success"] is False
    assert "ambiguous" in out["error"]


def test_the_resolver_cites_what_it_read(data):
    out = _run(_payload(LineResolverAgent(), "the tablet line"))
    assert out["sources"], "a resolution with no source cannot ground anything"
    assert out["sources"][0]["source"].startswith("mongo://")


# ---------------------------------------------------------------- the pumps that never existed


def test_asset_kind_is_read_from_the_question():
    assert _asset_kind("which pumps are due?") == "pump"
    assert _asset_kind("which mixers are due?") == "mixer"
    assert _asset_kind("what is due for calibration?") == ""


def test_no_pumps_is_an_answer_not_an_invention(data):
    """The line exists and has assets; none is a pump. That is a grounded negative — the
    single most important behaviour in this file, because it is where P-301 came from.
    """
    out = _run(
        _payload(EquipmentInventoryAgent(), "which pumps are on the tablet line?")
    )
    assert out["success"] is True  # the query ran; "none" is the true answer
    assert out["count"] == 0
    assert "no pumps on LINE-TAB-001" in out["summary"]
    assert out["sources"]


def test_inventory_returns_the_real_assets(data):
    out = _run(
        _payload(EquipmentInventoryAgent(), "what equipment is on the tablet line?")
    )
    assert out["success"] is True
    assert "EQP-TAB-01-01" in out["asset_ids"]
    assert (
        "EQP-CAP-02-02" not in out["asset_ids"]
    ), "an asset from the other line leaked in"


def test_inventory_on_a_line_that_does_not_exist_fails(data):
    out = _run(_payload(EquipmentInventoryAgent(), "what is on line 3?"))
    assert out["success"] is False
    assert "line 3" in out["error"]


# ---------------------------------------------------------------- calibration


def test_evaluate_due_splits_by_the_date_and_invents_nothing():
    today = date(2026, 7, 14)
    ev = evaluate_due(RECORDS, today)
    assert [e["equipment_id"] for e in ev["overdue"]] == ["SN-INS-TAB-06-02"]
    assert ev["overdue"][0]["days_overdue"] == 10
    assert [e["equipment_id"] for e in ev["ok"]] == ["EQP-TAB-01-01"]


def test_a_record_with_no_due_date_is_undated_not_ok():
    ev = evaluate_due([{"equipment_id": "X", "next_due_date": None}], date(2026, 7, 14))
    assert [e["equipment_id"] for e in ev["undated"]] == ["X"]
    assert not ev["ok"] and not ev["overdue"]


def test_calibration_reports_what_is_actually_overdue(data):
    out = _run(
        _payload(
            CalibrationDueEvaluatorAgent(),
            "what is due for calibration on the tablet line?",
        )
    )
    assert out["success"] is True
    assert any(e["equipment_id"] == "SN-INS-TAB-06-02" for e in out["overdue"])
    assert any(s["source"].endswith("/calibration_records") for s in out["sources"])
    assert "P-301" not in out["summary"], "the fabricated pump is back"


def test_missing_calibration_collection_fails_rather_than_guesses(data):
    """The collection genuinely did not exist. 'I have nothing to read' is the only honest
    answer — a plausible schedule is exactly what got invented before."""
    data["absent"].add("calibration_records")
    for agent in (CalibrationScheduleAgent(), CalibrationDueEvaluatorAgent()):
        out = _run(_payload(agent, "what is due on the tablet line?"))
        assert out["success"] is False, "a missing collection IS a genuine failure"
        assert "no calibration records exist" in out["error"]
        assert out["sources"] == [], "nothing was read, so nothing may be cited"


def test_calibration_on_a_nonexistent_line_answers_rather_than_fails(data):
    """Same rule: no line 3 is a grounded answer. Only a missing collection is a failure."""
    out = _run(
        _payload(
            CalibrationDueEvaluatorAgent(),
            "which pumps on line 3 are due for calibration?",
        )
    )
    assert out["success"] is True
    assert out["resolved"] is False
    assert "line 3" in out["answer"]
    assert "error" not in out, "an unsatisfiable goal is not an agent error"


# ---------------------------------------------------------------- the grounding contract


def test_every_successful_agent_declares_a_source(data):
    """kernel.execute.grounding.extract_step_citations reads `sources` off the payload. An
    agent that reports data without one is, to the grounding check, indistinguishable from an
    agent that made the data up."""
    from src.monkey_brain.kernel.execute.grounding import extract_step_citations

    for agent, question in (
        (LineResolverAgent(), "the tablet line"),
        (EquipmentInventoryAgent(), "what is on the tablet line?"),
        (CalibrationScheduleAgent(), "calibration schedule for the tablet line"),
        (CalibrationDueEvaluatorAgent(), "what is due on the tablet line?"),
    ):
        out = _run(_payload(agent, question))
        assert out["success"] is True
        cites = extract_step_citations(
            [{"agent": agent.agent_type, "success": True, "output": out}]
        )
        assert cites, f"{agent.agent_type} produced data the grounding check cannot see"


def test_agents_are_readonly(data):
    """These read the plant's records. BaseDDDAgent's readonly guard rejects any act() that
    returns a mutating action, so a future edit cannot quietly turn one into a writer.
    """
    for agent in (
        LineResolverAgent(),
        EquipmentInventoryAgent(),
        CalibrationScheduleAgent(),
        CalibrationDueEvaluatorAgent(),
    ):
        assert agent.readonly is True
        out = _run(_payload(agent, "what is on the tablet line?"))
        assert out["action"] == "report"
