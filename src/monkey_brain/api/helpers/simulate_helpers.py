"""simulate_helpers.py — Business logic and shared helpers for simulation endpoints."""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("agentos.simulate")

_KEYWORD_MAP: dict[str, list[str]] = {
    "machine": ["pharmaceutical_machines"],
    "equipment": ["pharmaceutical_equipment"],
    "instrument": ["pharmaceutical_equipment", "instruments"],
    "line": ["industrial_lines"],
    "stage": ["industrial_stages"],
    "workstation": ["industrial_workstations"],
    "batch": [
        "production_batches",
        "batch_production_execution_records",
        "batch_step_executions",
    ],
    "work order": ["work_orders"],
    "sop": ["sops"],
    "change control": ["gxp_change_controls"],
    "oee": ["industrial_lines", "industrial_stages"],
    "mtbf": ["pharmaceutical_machines"],
    "plant": ["industrial_plants"],
    "worker": ["users"],
    "operator": ["industrial_workstations"],
    "status": [
        "pharmaceutical_machines",
        "industrial_workstations",
        "industrial_lines",
    ],
    "capa": ["gxp_change_controls"],
    "maintenance": ["work_orders"],
}


def _extract_query_facts(answer: str) -> dict[str, Any]:
    """Extract factual claims from a query answer string.

    Returns numbers, names, statuses, and collection-like facts found in text.
    """
    numbers = set(re.findall(r"\b\d+\.?\d*\b", answer))
    words = set(re.findall(r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*", answer))
    statuses: set[str] = set()
    for s in (
        "Active",
        "Operational",
        "Completed",
        "In Progress",
        "Planned",
        "Released",
        "Approved",
        "On Hold",
        "Cancelled",
        "Todo",
        "Under QA Review",
        "Dispensing",
        "In Production",
        "Failed",
    ):
        if s.lower() in answer.lower():
            statuses.add(s)
    return {"numbers": numbers, "names": words, "statuses": statuses}


def compare_predicted_vs_actual(
    predicted_state: dict[str, Any],
    query_answer: str,
    question: str,
) -> dict[str, Any]:
    """Compare simulator's entity snapshot against query's actual answer.

    Focuses on RELEVANT collections only — what the question actually asks about.
    Two independent reads of the same DB, measuring if they agree.
    """
    entities = predicted_state.get("entities", {})
    collections = predicted_state.get("collections", {})
    query_facts = _extract_query_facts(query_answer)
    q_lower = question.lower()

    relevant_cols: set[str] = set()
    for kw, cols in _KEYWORD_MAP.items():
        if kw in q_lower:
            relevant_cols.update(cols)
    if not relevant_cols:
        relevant_cols = set(entities.keys()) | set(collections.keys())

    is_count_question = any(kw in q_lower for kw in ("how many", "count", "number of"))
    is_status_question = any(kw in q_lower for kw in ("status", "state", "condition"))
    is_detail_question = any(kw in q_lower for kw in ("detail", "show", "tell me about", "describe"))

    total_facts = 0
    total_matches = 0

    # Count matching
    count_matches = 0
    count_total = 0
    count_details: list[dict] = []
    for col in sorted(relevant_cols):
        info = collections.get(col, {})
        count = info.get("count", 0)
        if count > 0:
            count_total += 1
            matched = str(count) in query_facts["numbers"]
            if matched:
                count_matches += 1
            count_details.append({"collection": col, "count": count, "found_in_answer": matched})
    total_facts += count_total
    total_matches += count_matches

    # Name matching (skip for pure count questions)
    name_matches = 0
    name_total = 0
    name_details: list[dict] = []
    if not is_count_question:
        for col in sorted(relevant_cols):
            info = entities.get(col, {})
            sim_names = set(info.get("names", []))
            if not sim_names:
                continue
            name_total += len(sim_names)
            matched_names = [n for n in sim_names if n.lower() in query_answer.lower()]
            name_matches += len(matched_names)
            name_details.append(
                {
                    "collection": col,
                    "sim_names": sorted(sim_names),
                    "names_in_answer": sorted(matched_names),
                }
            )
        total_facts += name_total
        total_matches += name_matches

    # Status matching (only for status/detail questions)
    status_matches = 0
    status_total = 0
    status_details: list[dict] = []
    if is_status_question or is_detail_question:
        for col in sorted(relevant_cols):
            info = entities.get(col, {})
            sim_statuses = set(info.get("statuses", {}).keys())
            if not sim_statuses:
                continue
            for s in sim_statuses:
                status_total += 1
                if s.lower() in query_answer.lower():
                    status_matches += 1
            status_details.append(
                {
                    "collection": col,
                    "sim_statuses": sorted(sim_statuses),
                    "statuses_in_answer": sorted(s for s in sim_statuses if s.lower() in query_answer.lower()),
                }
            )
        total_facts += status_total
        total_matches += status_matches

    loss = round(1.0 - (total_matches / total_facts), 4) if total_facts else 1.0

    return {
        "loss": loss,
        "total_facts": total_facts,
        "matching_facts": total_matches,
        "count_accuracy": round(count_matches / count_total, 4) if count_total else 0.0,
        "name_accuracy": round(name_matches / name_total, 4) if name_total else 0.0,
        "status_accuracy": (round(status_matches / status_total, 4) if status_total else 0.0),
        "count_matches": count_matches,
        "name_matches": name_matches,
        "status_matches": status_matches,
        "relevant_collections": sorted(relevant_cols),
        "count_details": count_details,
        "name_details": name_details,
        "status_details": status_details,
    }


def resolve_workload_name(sim_result: dict[str, Any], goal_obj: Any) -> str:
    """Derive workload name from sim_result intent or goal_obj attributes."""
    name = sim_result.get("intent", "")
    if not name and goal_obj is not None:
        name = getattr(goal_obj, "expert", "") or getattr(goal_obj, "workload_id", "") or ""
    return name


async def classify_and_setup_ctx(
    question: str,
    context: dict[str, Any] | None,
    db: Any,
) -> tuple[str, Any, dict[str, Any]]:
    """Route question through create_goal and inject intent/goal into context.

    Returns (normalized_question, goal_obj, ctx).
    """
    from src.monkey_brain.kernel.execute.orchestration.routing import create_goal

    normalized, classified_intent, goal_obj = create_goal(question)
    ctx = dict(context or {})
    if "db" not in ctx and db is not None:
        ctx["db"] = db
    ctx.setdefault("_intent", (classified_intent or {}).get("intent", ""))
    ctx.setdefault("_goal", getattr(goal_obj, "name", "") if goal_obj is not None else "")
    return normalized, goal_obj, ctx


async def fetch_query_answer(question: str, mongo_client: Any = None) -> str:
    """Run the query workload in-process and return the answer string, or '' on failure."""
    from src.monkey_brain.kernel.execute.runtime.executor import get_executor
    from src.monkey_brain.kernel.execute.models import ExecutionMode

    try:
        result = await get_executor().execute(question, mongo_client, question_source=ExecutionMode.QUERY)
        answer, _, _, _ = result
        return answer or ""
    except Exception as exc:
        logger.warning("[simulate] query fetch failed: %s", exc)
    return ""


async def store_bellman_transition(
    db: Any,
    sim_result: dict[str, Any],
    question: str,
    predicted_state: dict[str, Any],
    query_answer: str,
    loss_result: dict[str, Any],
    workload_name: str,
) -> bool:
    """Store a Bellman transition. Returns True on success."""
    if db is None:
        return False
    from src.cortex.world_model_simulation import _store_transition

    try:
        await _store_transition(
            db,
            simulation_id=sim_result.get("simulation_id", ""),
            prompt=question,
            action=question[:200],
            # _store_transition's parameter is `pipeline_name`. Passing `workload_name`
            # raised TypeError: unexpected keyword argument — swallowed by the except
            # below, which returned False. NO Bellman transition was ever persisted,
            # so the planner's Q-values had nothing to learn from.
            pipeline_name=workload_name,
            state_before=predicted_state,
            state_after={"query_answer": query_answer[:2000]},
            loss_result=loss_result,
            grounding_score=sim_result.get("grounding_score", 0.0),
            feasibility_verdict=sim_result.get("feasibility_verdict", "unknown"),
        )
        return True
    except Exception as exc:
        logger.warning("[simulate] transition store failed: %s", exc)
        return False
