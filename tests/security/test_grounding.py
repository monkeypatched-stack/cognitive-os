"""An ungrounded answer must not be presented as fact (E-3).

Live /execute run, verbatim:

    success      : False
    llm_answered : True
    semantic_hits: 0    graph_paths: 0    citations: 0
    answer       : "Based on the calibration records for line 3, the following pumps are due
                    for calibration: P-301 (last calibrated 2026-01-04, 180-day interval —
                    OVERDUE by 10 days) ..."

There are no calibration records for line 3. Nothing was retrieved. Every specific in that
answer was invented — on a run that had already failed.
"""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.execute.grounding import (
    OFF,
    REFUSAL,
    STRICT,
    WARN,
    assess,
    enforce,
    extract_step_citations,
    mode,
)

HALLUCINATION = "Based on the calibration records for line 3, pumps P-301 and P-305 are due."


def _assess(**kw):
    return assess(
        answer=kw.pop("answer", HALLUCINATION),
        llm_answered=kw.pop("llm_answered", True),
        **kw,
    )


# ---------------------------------------------------------------- detection


def test_the_observed_hallucination_is_flagged(monkeypatch):
    monkeypatch.setenv("MB_GROUNDING", WARN)
    r = _assess(semantic_hits=[], graph_paths=[], citations=[])
    assert r["grounded"] is False
    assert r["ungrounded_assertion"] is True
    assert r["evidence_count"] == 0


@pytest.mark.parametrize(
    "evidence",
    [
        {"semantic_hits": [{"source": "sop.pdf", "score": 0.9}]},
        {"graph_paths": [["Line3", "HAS_PUMP", "P-301"]]},
        {"citations": [{"source": "mongo://calibration", "via": "CalibrationScheduleAgent"}]},
    ],
)
def test_any_real_evidence_grounds_the_answer(monkeypatch, evidence):
    monkeypatch.setenv("MB_GROUNDING", WARN)
    r = _assess(**evidence)
    assert r["grounded"] is True
    assert r["ungrounded_assertion"] is False


def test_a_status_summary_makes_no_claim_so_is_not_an_ungrounded_assertion(monkeypatch):
    """llm_answered=False means nothing factual was asserted — there is nothing to ground."""
    monkeypatch.setenv("MB_GROUNDING", STRICT)
    r = assess(answer="AgentA: query [ok] | AgentB: eval [failed]", llm_answered=False)
    assert r["grounded"] is False  # still no evidence, and we say so
    assert r["ungrounded_assertion"] is False
    assert enforce("AgentA: query [ok]", r) == "AgentA: query [ok]"


def test_an_empty_answer_is_not_an_ungrounded_assertion(monkeypatch):
    monkeypatch.setenv("MB_GROUNDING", STRICT)
    assert assess(answer="", llm_answered=True)["ungrounded_assertion"] is False


# ---------------------------------------------------------------- enforcement


def test_strict_withholds_the_invented_answer(monkeypatch):
    monkeypatch.setenv("MB_GROUNDING", STRICT)
    r = _assess()
    assert r["answer_withheld"] is True
    out = enforce(HALLUCINATION, r, run_id="e545eeaa")
    assert out == REFUSAL
    assert "P-301" not in out, "the fabricated specifics escaped into the response"


def test_warn_returns_the_answer_but_marks_it_ungrounded(monkeypatch):
    monkeypatch.setenv("MB_GROUNDING", WARN)
    r = _assess()
    assert r["answer_withheld"] is False
    assert enforce(HALLUCINATION, r) == HALLUCINATION  # behaviour preserved by default
    assert r["grounded"] is False  # but the caller is told the truth


def test_off_disables_assessment(monkeypatch):
    monkeypatch.setenv("MB_GROUNDING", OFF)
    r = _assess()
    assert r["ungrounded_assertion"] is False
    assert enforce(HALLUCINATION, r) == HALLUCINATION


def test_a_grounded_answer_is_never_withheld(monkeypatch):
    monkeypatch.setenv("MB_GROUNDING", STRICT)
    r = _assess(semantic_hits=[{"source": "sop.pdf"}])
    assert enforce(HALLUCINATION, r) == HALLUCINATION


def test_default_mode_is_warn_and_a_typo_does_not_silently_disable_the_check(
    monkeypatch,
):
    monkeypatch.delenv("MB_GROUNDING", raising=False)
    assert mode() == WARN
    monkeypatch.setenv("MB_GROUNDING", "strict!!")  # fat-fingered
    assert mode() == WARN, "an unparseable mode must not turn the check off"


# ---------------------------------------------------------------- agent-declared sources


def test_an_agent_that_declares_its_source_grounds_the_answer():
    steps = [
        {
            "agent": "CalibrationScheduleAgent",
            "success": True,
            "output": {
                "success": True,
                "sources": [{"source": "mongo://calibration", "score": 1.0}],
            },
        }
    ]
    cites = extract_step_citations(steps)
    assert cites == [
        {
            "source": "mongo://calibration",
            "via": "CalibrationScheduleAgent",
            "score": 1.0,
        }
    ]


def test_an_agent_that_declares_nothing_grounds_nothing():
    """A codegen agent returning a dict it invented is not evidence."""
    steps = [
        {
            "agent": "CalibrationDueEvaluatorAgent",
            "success": True,
            "output": {"success": True, "action": "evaluated", "due": ["P-301"]},
        }
    ]
    assert extract_step_citations(steps) == []


def test_a_failed_agent_that_did_read_still_grounds_what_it_read():
    """LineResolverAgent fails on "line 3" — but it fails BECAUSE it read the instruments
    collection and found LINE-TAB-001 and LINE-CAP-001, and the reply quotes that list back.
    `sources` is what the agent read, not what it managed to produce."""
    steps = [
        {
            "agent": "LineResolverAgent",
            "success": False,
            "output": {
                "success": False,
                "error": "no line matching line 3 exists",
                "sources": [{"source": "mongo://demo/instruments", "count": 2}],
            },
        }
    ]
    cites = extract_step_citations(steps)
    assert cites == [{"source": "mongo://demo/instruments", "via": "LineResolverAgent"}]


def test_an_agent_that_read_nothing_cites_nothing_even_when_it_fails():
    """The other half of the rule: the CollectionAbsent path declares sources: []."""
    steps = [
        {
            "agent": "CalibrationScheduleAgent",
            "success": False,
            "output": {
                "success": False,
                "error": "no calibration records exist",
                "sources": [],
            },
        }
    ]
    assert extract_step_citations(steps) == []


def test_step_citations_survive_malformed_outputs():
    for junk in (
        [],
        [None],
        ["nope"],
        [{"agent": "A", "output": None}],
        [{"output": {"sources": [{}]}}],
    ):
        assert extract_step_citations(junk) == []


# ---------------------------------------------------------------- the wire contract


def test_execute_response_carries_the_grounding_verdict():
    from src.monkey_brain.kernel.models.execute import ExecuteResponse

    fields = ExecuteResponse.model_fields
    assert "grounded" in fields, "a caller cannot tell a retrieved fact from an invention"
    assert "grounding" in fields


def test_the_streaming_client_gets_the_same_verdict():
    """The SSE path is a second consumer — it must not be the one that still gets a
    hallucination with no marking."""
    src = __import__("pathlib").Path("src/monkey_brain/api/routes/execute.py").read_text()
    done = src[src.index('_sse_event("done"') :]
    assert '"grounded"' in done[:600]
    assert "enforce_grounding" in src[: src.index('_sse_event("done"')]
