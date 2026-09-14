"""Calibration schedule and due evaluation — from the calibration records, or not at all.

These two agents read `calibration_records`, whose schema is the PM service's own
CalibrationRecord model (next_due_date, calibration_interval_days, status, ...).

If that collection does not exist, they say so and fail. They do not fall back to a plausible
guess, because a plausible guess is exactly what the system produced last time: "P-301 (last
calibrated 2026-01-04, 180-day interval — OVERDUE by 10 days)", for a pump that does not
exist, on a line that does not exist, from a collection that did not exist.

Run scripts/seed_calibration_records.py to populate the collection from the real instruments.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from broca.agents.ddd._base_ddd import BaseDDDAgent
from broca.registry import auto_register

from domains.manufacturing.agents import _data
from domains.manufacturing.agents._data import DataBackedAgentMixin
from domains.manufacturing.agents.equipment import equipment_on_line
from domains.manufacturing.agents.lines import resolve_line

logger = logging.getLogger("manufacturing.agents.calibration")

COLLECTION = "calibration_records"

_ABSENT = (
    f"no calibration records exist — {{uri}} is not present in the database. "
    f"Nothing can be said about what is due until it is populated."
)


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.strip()[:10]).date()
        except ValueError:
            return None
    return None


async def records_for_line(line_id: str) -> dict[str, Any]:
    """Calibration records for every asset on a line. Raises CollectionAbsent if there are none."""
    inventory = await equipment_on_line(line_id)
    asset_ids = [a["asset_id"] for a in inventory["assets"] if a["asset_id"]]
    if not asset_ids:
        return {"records": [], "asset_ids": [], "sources": inventory["sources"]}

    records = await _data.find(
        COLLECTION, {"equipment_id": {"$in": asset_ids}}, limit=1000
    )
    return {
        "records": records,
        "asset_ids": asset_ids,
        "sources": inventory["sources"] + [_data.cite(COLLECTION, len(records))],
    }


def evaluate_due(records: list[dict], today: date) -> dict[str, list[dict]]:
    """Split records into overdue / due-soon / ok by next_due_date. Pure; no invention."""
    overdue: list[dict] = []
    due_soon: list[dict] = []
    ok: list[dict] = []
    undated: list[dict] = []

    for record in records:
        due = _as_date(record.get("next_due_date"))
        entry = {
            "equipment_id": record.get("equipment_id", ""),
            "record_id": record.get("record_id", ""),
            "next_due_date": due.isoformat() if due else None,
            "status": record.get("status", ""),
            "interval_days": record.get("calibration_interval_days"),
        }
        if due is None:
            undated.append(entry)
            continue
        days = (due - today).days
        entry["days_until_due"] = days
        if days < 0:
            entry["days_overdue"] = -days
            overdue.append(entry)
        elif days <= 30:
            due_soon.append(entry)
        else:
            ok.append(entry)

    overdue.sort(key=lambda e: e["days_overdue"], reverse=True)
    due_soon.sort(key=lambda e: e["days_until_due"])
    return {"overdue": overdue, "due_soon": due_soon, "ok": ok, "undated": undated}


class _CalibrationAgent(DataBackedAgentMixin, BaseDDDAgent):
    """Shared plumbing: resolve the line, read the records, fail honestly if there are none."""

    ddd_layer = "aggregate"
    readonly = True

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "question": str(context.get("question", "")),
            "line_id": str(context.get("line_id", "")),
        }

    async def _load(self, perception: dict[str, Any]) -> dict[str, Any]:
        line_id = perception["line_id"]
        if not line_id:
            resolved = await resolve_line(perception["question"])
            if not resolved.get("success"):
                return {
                    "success": False,
                    "error": resolved.get("error", "line unresolved"),
                    "sources": resolved.get("sources", []),
                }
            line_id = resolved["line_id"]

        try:
            found = await records_for_line(line_id)
        except _data.CollectionAbsent:
            # Genuine inability — there is nothing to read, so nothing can be said.
            return {
                "success": False,
                "unable": True,
                "error": _ABSENT.format(uri=_data.source_uri(COLLECTION)),
                "sources": [],
            }

        return {"success": True, "line_id": line_id, **found}


@auto_register
class CalibrationScheduleAgent(_CalibrationAgent):
    """Reads the calibration schedule for the assets on a line."""

    agent_type = "calibration_schedule"
    description = (
        "Reads the calibration records (schedule, interval, next due date) for the "
        "assets on a production line"
    )

    async def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        return await self._load(perception)

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        if not decision.get("success"):
            # Two very different things arrive here. "There is no line 3" is an ANSWER — the
            # agent read the data and reported the truth, so success stays True and the
            # composition layer will not discard the step and replace it with an LLM guess.
            # "The calibration_records collection does not exist" is a genuine inability:
            # nothing can be said, and the run must be able to see that it failed.
            unable = bool(decision.get("unable"))
            payload = {
                "action": "report",
                "operation": "calibration.schedule",
                "success": not unable,
                "resolved": False,
                "summary": decision["error"],
                "answer": decision["error"],
                "sources": decision.get("sources", []),
            }
            if unable:
                payload["error"] = decision["error"]
            return payload

        records = decision["records"]
        line_id = decision["line_id"]
        if not records:
            summary = (
                f"{line_id} has {len(decision['asset_ids'])} assets, but none of them "
                f"has a calibration record."
            )
        else:
            summary = f"{len(records)} calibration records for the assets on {line_id}."

        return {
            "action": "report",
            "operation": "calibration.schedule",
            "success": True,
            "line_id": line_id,
            "count": len(records),
            "records": records,
            "summary": summary,
            "answer": summary,
            "sources": decision["sources"],
        }


@auto_register
class CalibrationDueEvaluatorAgent(_CalibrationAgent):
    """Decides which assets are overdue or due soon, from their records and today's date."""

    agent_type = "calibration_due_evaluator"
    description = (
        "Evaluates which assets on a line are overdue or due soon for calibration, "
        "by comparing their next due date against today"
    )

    async def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        loaded = await self._load(perception)
        if not loaded.get("success"):
            return loaded
        loaded["evaluation"] = evaluate_due(loaded["records"], date.today())
        return loaded

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        if not decision.get("success"):
            # Two very different things arrive here. "There is no line 3" is an ANSWER — the
            # agent read the data and reported the truth, so success stays True and the
            # composition layer will not discard the step and replace it with an LLM guess.
            # "The calibration_records collection does not exist" is a genuine inability:
            # nothing can be said, and the run must be able to see that it failed.
            unable = bool(decision.get("unable"))
            payload = {
                "action": "report",
                "operation": "calibration.due",
                "success": not unable,
                "resolved": False,
                "summary": decision["error"],
                "answer": decision["error"],
                "sources": decision.get("sources", []),
            }
            if unable:
                payload["error"] = decision["error"]
            return payload

        line_id = decision["line_id"]
        ev = decision["evaluation"]
        overdue, due_soon = ev["overdue"], ev["due_soon"]

        if not decision["records"]:
            summary = f"No calibration records exist for the assets on {line_id}."
        elif not overdue and not due_soon:
            summary = f"Nothing on {line_id} is overdue or due within 30 days."
        else:
            parts = []
            if overdue:
                parts.append(
                    "overdue: "
                    + ", ".join(
                        f"{e['equipment_id']} ({e['days_overdue']}d, due {e['next_due_date']})"
                        for e in overdue[:10]
                    )
                )
            if due_soon:
                parts.append(
                    "due within 30 days: "
                    + ", ".join(
                        f"{e['equipment_id']} (in {e['days_until_due']}d)"
                        for e in due_soon[:10]
                    )
                )
            summary = f"On {line_id} — " + "; ".join(parts) + "."

        return {
            "action": "report",
            "operation": "calibration.due",
            "success": True,
            "line_id": line_id,
            "evaluated_on": date.today().isoformat(),
            "overdue": overdue,
            "due_soon": due_soon,
            "ok_count": len(ev["ok"]),
            "undated": ev["undated"],
            "summary": summary,
            "answer": summary,
            "sources": decision["sources"],
        }
