"""Grounding — did the answer come from evidence, or did the model invent it?

Driving /execute end-to-end produced this:

    success      : False
    llm_answered : True
    semantic_hits: 0    graph_paths: 0    citations: 0
    answer       : "Based on the calibration records for line 3, the following pumps are due
                    for calibration: P-301 (last calibrated 2026-01-04, 180-day interval —
                    OVERDUE by 10 days) and P-305 ..."

There are no calibration records for line 3. There was no retrieval. The pump IDs, the
dates and the intervals were invented wholesale, on a run that had already FAILED — and
`llm_answered: true` reads to a caller like a quality signal, so it certified the
hallucination.

`llm_answered` answers "did an LLM produce this", which is nearly always true and therefore
nearly useless. The question a caller actually needs answered is "is there anything behind
this", and nothing in the pipeline was answering it. That is what this module adds.

Evidence means a source that is not the model:

  * semantic_hits — retrieved from the vector store
  * graph_paths   — traversed in the knowledge graph
  * step sources  — an agent that declares where its output came from (see
                    `extract_step_citations`); an agent that queried Mongo or Influx can say
                    so, and that grounds the answer. An agent that merely returns a dict it
                    made up cannot, and does not.

Modes (env `MB_GROUNDING`):

    off     — assess nothing
    warn    — assess and report; the answer is returned as-is  (default)
    strict  — an ungrounded assertion is WITHHELD and replaced by a refusal

Default is `warn` because flipping straight to `strict` would blank out answers on every
deployment that has no retrieval wired up yet, which is a behaviour change nobody asked for.
It reports the truth; `strict` acts on it.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Iterable, List

logger = logging.getLogger("agentos.grounding")

OFF = "off"
WARN = "warn"
STRICT = "strict"
_VALID_MODES = (OFF, WARN, STRICT)

REFUSAL = (
    "I can't answer this from the available evidence. Nothing was retrieved for this "
    "question — no documents, no graph paths, and no agent reported a source — so any "
    "specifics I gave you would be invented rather than looked up."
)

# Keys through which an agent may declare where its output came from.
_SOURCE_KEYS = ("citations", "sources", "source", "provenance")


def mode() -> str:
    """Current grounding mode. An unrecognised value must not silently disable the check."""
    raw = (os.getenv("MB_GROUNDING") or WARN).strip().lower()
    if raw not in _VALID_MODES:
        logger.warning(
            "MB_GROUNDING=%r is not one of %s — falling back to %r",
            raw,
            _VALID_MODES,
            WARN,
        )
        return WARN
    return raw


def _as_citation(value: Any, agent: str) -> Dict[str, Any] | None:
    """Normalise one agent-declared source into a citation, or None if it declares nothing."""
    if isinstance(value, dict):
        source = value.get("source") or value.get("uri") or value.get("id") or value.get("collection")
        if not source:
            return None
        cite = {"source": str(source), "via": agent}
        if "score" in value or "similarity" in value:
            try:
                cite["score"] = round(float(value.get("score", value.get("similarity", 0.0))), 4)
            except (TypeError, ValueError):
                logger.debug("_as_citation: suppressed exception", exc_info=True)
        return cite
    if isinstance(value, str) and value.strip():
        return {"source": value.strip(), "via": agent}
    return None


def extract_step_citations(step_outputs: Iterable[Any]) -> List[Dict[str, Any]]:
    """Pull agent-declared sources out of step outputs.

    An agent grounds the answer by SAYING where its data came from. This is the hook that
    lets a real data agent (Mongo, Influx, Neo4j) count as evidence without the retrieval
    layer having to be the only path to grounding.
    """
    citations: List[Dict[str, Any]] = []
    for step in step_outputs or []:
        if not isinstance(step, dict):
            continue
        agent = str(step.get("agent", "") or "")
        output = step.get("output")
        if not isinstance(output, dict):
            continue
        # A FAILED step may still have read real data, and its answer may rest on that read.
        # LineResolverAgent fails on "line 3" — but it fails *because* it read the instruments
        # collection and found LINE-TAB-001 and LINE-CAP-001, and that list is exactly what
        # the reply quotes back. Excluding it marked a fully-evidenced answer as ungrounded.
        # `sources` describes what the agent READ, not what it managed to produce; an agent
        # that read nothing declares nothing (see the CollectionAbsent path, which cites []).
        for key in _SOURCE_KEYS:
            declared = output.get(key)
            if declared is None:
                continue
            values = declared if isinstance(declared, (list, tuple)) else [declared]
            for value in values:
                cite = _as_citation(value, agent)
                if cite:
                    citations.append(cite)
    return citations


def assess(
    *,
    answer: str,
    semantic_hits: Any = (),
    graph_paths: Any = (),
    citations: Any = (),
    llm_answered: bool = False,
) -> Dict[str, Any]:
    """Report whether `answer` rests on anything other than the model's own priors."""
    n_hits = len(semantic_hits or [])
    n_paths = len(graph_paths or [])
    n_cites = len(citations or [])
    evidence = n_hits + n_paths + n_cites

    current = mode()
    grounded = evidence > 0

    # An LLM answer with no evidence behind it is an assertion the system cannot support.
    # A status summary (llm_answered=False) makes no factual claim, so it is not at risk —
    # and neither is an empty answer.
    ungrounded_assertion = bool(llm_answered and answer and not grounded and current != OFF)

    return {
        "grounded": grounded,
        "evidence_count": evidence,
        "semantic_hits": n_hits,
        "graph_paths": n_paths,
        "citations": n_cites,
        "ungrounded_assertion": ungrounded_assertion,
        "mode": current,
        "answer_withheld": ungrounded_assertion and current == STRICT,
    }


def enforce(answer: str, report: Dict[str, Any], run_id: str = "") -> str:
    """Apply the grounding policy to the answer.

    In `strict` an ungrounded assertion is withheld — returning it would mean the system
    stating specifics it cannot support. In `warn` the answer stands, but the caller is told
    it is ungrounded and the run is logged loudly.
    """
    if not report.get("ungrounded_assertion"):
        return answer

    if report.get("answer_withheld"):
        logger.error(
            "run=%r WITHHELD an ungrounded answer (%d chars, zero evidence): %.120s",
            run_id,
            len(answer),
            answer,
        )
        return REFUSAL

    logger.warning(
        "run=%r answer is UNGROUNDED — zero semantic hits, zero graph paths, zero cited "
        "sources. It is being returned as-is because MB_GROUNDING=%s; treat it as the "
        "model's prior, not as fact: %.120s",
        run_id,
        report.get("mode"),
        answer,
    )
    return answer
