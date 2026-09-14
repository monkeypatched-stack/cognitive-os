"""
CDC hooks that keep process_definitions in sync with SOPs,
and apply approved change-control changes back to SOPs.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger("pm.sop_cdc")

PD_COLLECTION = "process_definitions"
SOP_COLLECTION = "sops"
ISO_COLLECTION = "industrial_sops"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _build_process_step(step_text: str, index: int, pd_id: str) -> dict:
    step_id = f"STEP-{pd_id}-{index + 1:03d}"
    return {
        "id": step_id,
        "sequence": index + 1,
        "name": step_text[:120] if isinstance(step_text, str) else f"Step {index + 1}",
        "description": step_text if isinstance(step_text, str) else "",
        "action_type": "MANUAL",
        "command": None,
        "inputs": [],
        "outputs": [],
        "depends_on": [f"STEP-{pd_id}-{index:03d}"] if index > 0 else [],
        "is_optional": False,
        "timeout_seconds": None,
        "retry_count": 0,
        "status": "pending",
        "prechecks": {
            "id": f"PRE-{step_id}",
            "process_definition_id": pd_id,
            "process_step_id": step_id,
            "conditions": [],
            "all_must_pass": True,
            "timeout_seconds": 300,
            "description": "",
        },
        "postchecks": {
            "id": f"POST-{step_id}",
            "process_definition_id": pd_id,
            "process_step_id": step_id,
            "conditions": [],
            "all_must_pass": True,
            "timeout_seconds": 300,
            "description": "",
        },
        "constraints": {
            "id": f"CONST-{step_id}",
            "process_definition_id": pd_id,
            "process_step_id": step_id,
            "constraints": [],
            "description": "",
        },
        "corrective_actions": {
            "id": f"CA-{step_id}",
            "process_definition_id": pd_id,
            "process_step_id": step_id,
            "actions": [],
            "description": "",
        },
        "notes": None,
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
    }


def _build_steps_from_sop(sop: dict, pd_id: str) -> dict:
    steps_items = []
    pd = sop.get("process_definition", {})
    if isinstance(pd, dict):
        raw_steps = pd.get("steps", {})
        if isinstance(raw_steps, dict):
            steps_items = raw_steps.get("items", [])

    if not steps_items:
        raw_steps_list = pd.get("steps", []) if isinstance(pd, dict) else []
        if isinstance(raw_steps_list, list):
            steps_items = raw_steps_list

    if not steps_items:
        iso = sop.get("_industrial_sop", {})
        if isinstance(iso, dict):
            steps_items = iso.get("steps", [])

    processed = []
    for i, item in enumerate(steps_items):
        if isinstance(item, str):
            processed.append(_build_process_step(item, i, pd_id))
        elif isinstance(item, dict):
            text = (
                item.get("command")
                or item.get("description")
                or item.get("name")
                or f"Step {i + 1}"
            )
            step = _build_process_step(text, i, pd_id)
            step.update({k: v for k, v in item.items() if k in step})
            processed.append(step)

    return {
        "id": f"STEPS-{pd_id}",
        "process_definition_id": pd_id,
        "description": f"Steps for {sop.get('title', pd_id)}",
        "steps": processed,
        "allow_parallel_execution": False,
        "rollback_on_failure": False,
    }


def _build_pd_from_sop(sop: dict) -> dict:
    sop_id = sop.get("id", "")
    pd = sop.get("process_definition", {})
    pd_id = pd.get("process_definition_id") if isinstance(pd, dict) else None
    if not pd_id:
        pd_id = f"PROC-{sop_id}"

    now = _utc_now()
    title = sop.get("title", "")
    description = ""
    if isinstance(pd, dict):
        description = pd.get("description", "")
    if not description:
        description = sop.get("scope", "") or sop.get("purpose", "") or title

    tags = list(sop.get("tags", [])) if isinstance(sop.get("tags"), list) else []

    return {
        "process_definition_id": pd_id,
        "name": title,
        "description": description,
        "version": sop.get("version", "1.0.0"),
        "route_version": "1.0.0",
        "revision": "A",
        "process_type": "manufacturing",
        "product_id": None,
        "product_family": None,
        "plant_id": None,
        "line_id": None,
        "bom_id": None,
        "recipe_id": None,
        "owner": sop.get("entity_name"),
        "tags": tags,
        "status": "pending",
        "approval_status": "draft",
        "change_control_id": None,
        "supersedes_process_definition_id": None,
        "effective_from": None,
        "effective_to": None,
        "approved_by": None,
        "approved_at": None,
        "retired_at": None,
        "route_stage_ids": [],
        "material_flow_edge_ids": [],
        "ipc_checkpoints": [],
        "cleaning_requirements": [],
        "instruction_templates": [],
        "metadata": {
            "source": "sop_cdc",
            "sop_id": sop_id,
        },
        "steps": _build_steps_from_sop(sop, pd_id),
        "created_at": now,
        "updated_at": now,
    }


# ─── CDC: SOP → Process Definition ───────────────────────────────────────────


async def sync_sop_to_process_definition(
    db: AsyncIOMotorDatabase, sop: dict
) -> dict | None:
    """
    After a SOP is created or updated, upsert the corresponding
    process_definition record so the process-definition pages stay current.
    Returns the upserted process_definition dict.
    """
    pd_doc = _build_pd_from_sop(sop)
    pd_id = pd_doc["process_definition_id"]

    existing = await db[PD_COLLECTION].find_one(
        {"process_definition_id": pd_id}, {"_id": 0}
    )

    if existing:
        now = _utc_now()
        pd_doc.pop("created_at", None)
        pd_doc["updated_at"] = now
        await db[PD_COLLECTION].update_one(
            {"process_definition_id": pd_id},
            {"$set": pd_doc},
        )
        logger.info(
            "CDC: updated process_definition %s from SOP %s", pd_id, sop.get("id")
        )
    else:
        await db[PD_COLLECTION].insert_one(pd_doc)
        logger.info(
            "CDC: created process_definition %s from SOP %s", pd_id, sop.get("id")
        )

    return pd_doc


async def sync_all_sops_to_process_definitions(db: AsyncIOMotorDatabase) -> int:
    """Bulk sync all SOPs to process_definitions. Returns count of upserted docs."""
    count = 0
    async for sop in db[SOP_COLLECTION].find():
        await sync_sop_to_process_definition(db, sop)
        count += 1
    return count


# ─── CDC: Change Control Approval → SOP ──────────────────────────────────────


async def apply_approved_change_control_to_sop(
    db: AsyncIOMotorDatabase,
    change_control: dict,
) -> list[dict]:
    """
    When a change control reaches 'Approved' status (all approvers approved),
    apply the proposed changes to the affected SOPs.

    Reads proposed changes from gxp_proposed_changes, finds matching SOPs,
    updates the SOP's process_definition, steps, prechecks, postchecks,
    constraints, and corrective_actions.  Returns list of promotion results.
    """
    cc_id = change_control.get("change_control_id", "")
    now = _utc_now()
    results: list[dict] = []

    proposed_nodes = (
        await db["gxp_proposed_changes"]
        .find(
            {
                "change_control_id": cc_id,
                "status": {"$in": ["Pending Approval", "Approved"]},
                "archived": {"$ne": True},
            },
            {"_id": 0},
        )
        .to_list(length=500)
    )

    affected_sops = change_control.get("affected_sops", [])
    affected_entities = change_control.get("affected_entities", [])

    for node in proposed_nodes:
        source_collection = node.get("source_collection", "")
        source_id = node.get("source_id", "")
        pending = node.get("pending_properties") or {}

        target_sop_id = None
        if source_collection == SOP_COLLECTION:
            target_sop_id = source_id
        elif source_collection == ISO_COLLECTION:
            iso = await db[ISO_COLLECTION].find_one({"sop_id": source_id}, {"_id": 1})
            if iso:
                target_sop_id = str(iso["_id"])

        if not target_sop_id and affected_sops:
            target_sop_id = affected_sops[0]

        if not target_sop_id:
            continue

        sop = await db[SOP_COLLECTION].find_one({"id": target_sop_id}, {"_id": 0})
        if not sop:
            continue

        set_fields: dict[str, Any] = {"updated_at": now}

        for field in ("title", "description", "scope", "purpose", "tags", "version"):
            if field in pending and pending[field] is not None:
                set_fields[field] = pending[field]

        for sub_doc_key in (
            "process_definition",
            "prechecks",
            "postchecks",
            "constraints",
            "corrective_actions",
        ):
            if sub_doc_key in pending and isinstance(pending[sub_doc_key], dict):
                merged = {**(sop.get(sub_doc_key) or {}), **pending[sub_doc_key]}
                set_fields[sub_doc_key] = merged

        if "status" in pending:
            set_fields["status"] = pending["status"]

        await db[SOP_COLLECTION].update_one(
            {"id": target_sop_id},
            {"$set": set_fields},
        )

        await db["gxp_proposed_changes"].update_one(
            {"proposed_change_id": node.get("proposed_change_id")},
            {
                "$set": {
                    "status": "Archived",
                    "lifecycle_state": "archived",
                    "archived": True,
                    "archived_at": now,
                    "effective_at": now,
                    "live_node_touched": True,
                    "promotion_result": {
                        "collection": SOP_COLLECTION,
                        "source_id": target_sop_id,
                        "applied_values": pending,
                    },
                    "updated_at": now,
                }
            },
        )

        updated_sop = await db[SOP_COLLECTION].find_one(
            {"id": target_sop_id}, {"_id": 0}
        )
        if updated_sop:
            await sync_sop_to_process_definition(db, updated_sop)

        results.append(
            {
                "proposed_change_id": node.get("proposed_change_id"),
                "sop_id": target_sop_id,
                "applied_values": list(pending.keys()),
                "effective_at": now.isoformat(),
            }
        )

        logger.info(
            "CDC: applied change_control %s to SOP %s (proposed_change %s)",
            cc_id,
            target_sop_id,
            node.get("proposed_change_id"),
        )

    return results
