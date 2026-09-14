"""Equipment inventory — what is actually installed on a line.

Reads pharmaceutical_equipment and instruments. Returns the real rows, and cites them.

Note the distinction this agent has to keep straight: "the line has no pumps" is an ANSWER
(we looked, and there are none — the data has analyzers, balances and mixers, not pumps),
whereas "the line does not exist" is a FAILURE. Collapsing the two is how a question about
line 3 turned into a confident list of pumps.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from broca.agents.ddd._base_ddd import BaseDDDAgent
from broca.registry import auto_register

from domains.manufacturing.agents import _data
from domains.manufacturing.agents._data import DataBackedAgentMixin
from domains.manufacturing.agents.lines import resolve_line

logger = logging.getLogger("manufacturing.agents.equipment")

_EQUIPMENT_FIELDS = {
    "_id": 0,
    "equipment_id": 1,
    "name": 1,
    "category": 1,
    "line_id": 1,
    "workstation_id": 1,
    "status": 1,
}
_INSTRUMENT_FIELDS = {
    "_id": 0,
    "serial_number": 1,
    "name": 1,
    "category": 1,
    "line_id": 1,
    "workstation_id": 1,
    "status": 1,
}


def _asset_kind(question: str) -> str:
    """The kind of asset the question asks about ('pumps' -> 'pump'), or '' for all."""
    m = re.search(
        r"\b(pump|mixer|balance|analyz\w*|scale|sensor|valve|compressor|"
        r"granulator|blender|press|coater|filter)s?\b",
        question.lower(),
    )
    return m.group(1).rstrip("s") if m else ""


async def equipment_on_line(line_id: str, kind: str = "") -> dict[str, Any]:
    """Every asset on a line, optionally narrowed to one kind. Cites what it read."""
    equipment = await _data.find(
        "pharmaceutical_equipment", {"line_id": line_id}, _EQUIPMENT_FIELDS, limit=500
    )
    try:
        instruments = await _data.find(
            "instruments", {"line_id": line_id}, _INSTRUMENT_FIELDS, limit=500
        )
    except _data.CollectionAbsent:
        instruments = []

    assets: list[dict[str, Any]] = []
    for row in equipment:
        assets.append(
            {
                "asset_id": row.get("equipment_id", ""),
                "name": row.get("name", ""),
                "category": row.get("category", ""),
                "kind": "equipment",
                "workstation_id": row.get("workstation_id", ""),
                "status": row.get("status", ""),
            }
        )
    for row in instruments:
        assets.append(
            {
                "asset_id": row.get("serial_number", ""),
                "name": row.get("name", ""),
                "category": row.get("category", ""),
                "kind": "instrument",
                "workstation_id": row.get("workstation_id", ""),
                "status": row.get("status", ""),
            }
        )

    matched = assets
    if kind:
        matched = [
            a
            for a in assets
            if kind in a["name"].lower() or kind in a["category"].lower()
        ]

    return {
        "assets": matched,
        "total_on_line": len(assets),
        "sources": [
            _data.cite("pharmaceutical_equipment", len(equipment)),
            _data.cite("instruments", len(instruments)),
        ],
    }


@auto_register
class EquipmentInventoryAgent(DataBackedAgentMixin, BaseDDDAgent):
    """Lists the equipment and instruments installed on a production line."""

    agent_type = "equipment_inventory"
    description = (
        "Lists the real equipment and instruments installed on a production line, "
        "from the equipment and instrument records, optionally filtered by asset kind"
    )
    ddd_layer = "aggregate"
    readonly = True

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "question": str(context.get("question", "")),
            "line_id": str(context.get("line_id", "")),
        }

    async def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        question = perception["question"]
        line_id = perception["line_id"]
        if not line_id:
            resolved = await resolve_line(question)
            if not resolved.get("success"):
                return {
                    "success": False,
                    "error": resolved.get("error", "line unresolved"),
                    "sources": resolved.get("sources", []),
                }
            line_id = resolved["line_id"]

        kind = _asset_kind(question)
        found = await equipment_on_line(line_id, kind)
        return {"success": True, "line_id": line_id, "kind": kind, **found}

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        if not decision.get("success"):
            return {
                "action": "report",
                "operation": "equipment.inventory",
                "success": False,
                "error": decision["error"],
                "summary": decision["error"],
                "sources": decision.get("sources", []),
            }

        assets = decision["assets"]
        line_id = decision["line_id"]
        kind = decision["kind"]

        if kind and not assets:
            # A real, grounded, negative answer — not a failure.
            summary = (
                f"There are no {kind}s on {line_id}. It has {decision['total_on_line']} "
                f"assets, none of which is a {kind}."
            )
        elif kind:
            summary = f"{len(assets)} {kind}(s) on {line_id}: " + ", ".join(
                f"{a['name']} ({a['asset_id']})" for a in assets[:10]
            )
        else:
            summary = f"{len(assets)} assets on {line_id}: " + ", ".join(
                f"{a['name']} ({a['asset_id']})" for a in assets[:10]
            )

        return {
            "action": "report",
            "operation": "equipment.inventory",
            "success": True,
            "line_id": line_id,
            "asset_kind": kind,
            "count": len(assets),
            "assets": assets,
            "asset_ids": [a["asset_id"] for a in assets],
            "summary": summary,
            "answer": summary,
            "sources": decision["sources"],
        }
