"""
helpers/process_definition_helper.py
--------------------------
MongoDB CRUD helper for the ProcessDefinition model.
Mirrors the pattern established in the devices helper.
"""

import logging
from datetime import datetime, timezone
import re
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.auth.helpers.graph_store import save_graph_state
from services.process_definitions.models.process_definition import (
    ProcessApprovalStatus,
    ProcessDefinitionCanvasAttachment,
    ProcessDefinitionCanvasHyperlink,
    ProcessDefinitionCanvasLayoutCreate,
    ProcessDefinitionCanvasLayoutUpdate,
    ProcessDefinitionCanvasNodePosition,
    ProcessDefinitionCanvasNodeProperties,
    ProcessDefinitionCanvasNodePropertiesUpdate,
    ProcessDefinitionCanvasWebhookEvent,
    ProcessDefinitionCreate,
    ProcessDefinitionUpdate,
    ProcessType,
    normalize_canvas_page_id,
)
from services.process_definitions.models.process_constraints import (
    ProcessStepConstraints,
)
from services.process_definitions.models.process_corrections import CorrectiveActions
from services.process_definitions.models.process_post_checks import (
    ProcessStepPostchecks,
)
from services.process_definitions.models.process_pre_checks import ProcessStepPrechecks
from services.process_definitions.models.process_steps import ProcessStep, ProcessSteps
from services.process_definitions.helpers.process_node_catalog import (
    catalog_defaults_for,
    catalog_metadata_for,
)

COLLECTION = "process_definitions"
CANVAS_COLLECTION = "process_definition_canvas_layouts"
CANVAS_GRAPH_USER_ID = "process_definition_canvas_layouts"
IMMUTABLE_APPROVAL_STATUSES = {
    ProcessApprovalStatus.APPROVED.value,
    ProcessApprovalStatus.EFFECTIVE.value,
    ProcessApprovalStatus.RETIRED.value,
}
logger = logging.getLogger(__name__)


def _serialize(doc: dict) -> dict:
    """Strip MongoDB's internal _id before returning a document."""
    doc.pop("_id", None)
    return doc


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _node_identity_query(canvas_node_id: str) -> dict:
    return {
        "$or": [
            {"nodes.node_id": canvas_node_id},
            {"nodes.node_id": {"$regex": f":{canvas_node_id}$"}},
        ]
    }


def _node_matches(node: dict, canvas_node_id: str) -> bool:
    node_id = str(node.get("node_id") or "")
    return node_id == canvas_node_id or node_id.endswith(f":{canvas_node_id}")


def _mirror_canvas_edge_aliases(doc: dict) -> None:
    if "edges" not in doc and "links" in doc:
        doc["edges"] = doc.get("links") or []
    if "edges" not in doc and "connections" in doc:
        doc["edges"] = doc.get("connections") or []
    if "edges" not in doc:
        return

    edges = doc.get("edges") or []
    doc["edges"] = edges
    doc["links"] = edges
    doc["connections"] = edges

    data = doc.get("data")
    if not isinstance(data, dict):
        data = {}
    data["edges"] = edges
    data["links"] = edges
    data["connections"] = edges
    doc["data"] = data


def _node_properties_from_record(node: dict) -> dict:
    return {
        "node_id": str(node.get("node_id") or ""),
        "name": node.get("name"),
        "title": node.get("title"),
        "node_type": node.get("node_type"),
        "node_category": node.get("node_category"),
        "backend": node.get("backend"),
        "config": node.get("config") if isinstance(node.get("config"), dict) else {},
        "metadata": (
            node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
        ),
        "hyperlinks": (
            node.get("hyperlinks") if isinstance(node.get("hyperlinks"), list) else []
        ),
        "attachments": (
            node.get("attachments") if isinstance(node.get("attachments"), list) else []
        ),
        "webhook_events": (
            node.get("webhook_events")
            if isinstance(node.get("webhook_events"), list)
            else []
        ),
        "last_webhook_event": node.get("last_webhook_event"),
    }


async def _sync_canvas_layout_to_graph(layout: Optional[dict]) -> None:
    if not layout:
        return
    layout_id = str(layout.get("layout_id") or "").strip()
    if not layout_id:
        return
    payload = {
        "layout_id": layout_id,
        "page_id": layout.get("page_id"),
        "process_definition_id": layout.get("process_definition_id"),
        "owner_id": layout.get("owner_id"),
        "nodes": layout.get("nodes") if isinstance(layout.get("nodes"), list) else [],
        "edges": layout.get("edges") if isinstance(layout.get("edges"), list) else [],
    }
    try:
        await save_graph_state(CANVAS_GRAPH_USER_ID, f"{layout_id}-canvas", payload)
    except Exception:
        logger.exception(
            "Failed to mirror process_definition canvas layout %s to Neo4j", layout_id
        )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    """Return a paginated list of all process definitions and the total count."""
    query: dict = {}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(d) async for d in cursor], total


async def get_by_id(
    db: AsyncIOMotorDatabase,
    process_definition_id: str,
) -> Optional[dict]:
    """Fetch a single process_definition by its process_definition_id."""
    doc = await db[COLLECTION].find_one(
        {"process_definition_id": process_definition_id}
    )
    return _serialize(doc) if doc else None


async def get_by_owner(
    db: AsyncIOMotorDatabase,
    owner: str,
) -> list[dict]:
    """Return all process definitions belonging to a given owner."""
    if not owner:
        return []
    cursor = db[COLLECTION].find({"owner": owner})
    return [_serialize(d) async for d in cursor]


async def get_by_status(
    db: AsyncIOMotorDatabase,
    status: str,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    """Return paginated process definitions filtered by lifecycle status."""
    query = {"status": status}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(d) async for d in cursor], total


async def get_by_tag(
    db: AsyncIOMotorDatabase,
    tag: str,
) -> list[dict]:
    """Return all process definitions that carry the given tag."""
    if not tag:
        return []
    cursor = db[COLLECTION].find({"tags": tag})
    return [_serialize(d) async for d in cursor]


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


async def create(
    db: AsyncIOMotorDatabase,
    data: ProcessDefinitionCreate,
) -> dict:
    """Insert a new process_definition document and return it."""
    doc = data.model_dump()
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    process_definition_id: str,
    data: ProcessDefinitionUpdate,
) -> Optional[dict]:
    """
    Partially update a process_definition.
    Only fields with non-None values are written; returns the updated doc
    or None if no process_definition matched.
    """
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        return await get_by_id(db, process_definition_id)
    existing = await get_by_id(db, process_definition_id)
    if (
        existing
        and str(existing.get("approval_status") or "").lower()
        in IMMUTABLE_APPROVAL_STATUSES
    ):
        raise ValueError(
            "Approved, effective, and retired process definitions are immutable. Create a new revision instead."
        )
    result = await db[COLLECTION].find_one_and_update(
        {"process_definition_id": process_definition_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result) if result else None


async def submit_for_review(
    db: AsyncIOMotorDatabase,
    process_definition_id: str,
) -> Optional[dict]:
    result = await db[COLLECTION].find_one_and_update(
        {
            "process_definition_id": process_definition_id,
            "approval_status": {
                "$in": [
                    ProcessApprovalStatus.DRAFT.value,
                    ProcessApprovalStatus.REJECTED.value,
                ]
            },
        },
        {
            "$set": {
                "approval_status": ProcessApprovalStatus.IN_REVIEW.value,
                "updated_at": _utc_now(),
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result) if result else None


async def approve(
    db: AsyncIOMotorDatabase,
    process_definition_id: str,
    approved_by: str | None = None,
    make_effective: bool = True,
) -> Optional[dict]:
    now = _utc_now()
    approval_status = (
        ProcessApprovalStatus.EFFECTIVE.value
        if make_effective
        else ProcessApprovalStatus.APPROVED.value
    )
    result = await db[COLLECTION].find_one_and_update(
        {
            "process_definition_id": process_definition_id,
            "approval_status": {
                "$in": [
                    ProcessApprovalStatus.IN_REVIEW.value,
                    ProcessApprovalStatus.DRAFT.value,
                ]
            },
        },
        {
            "$set": {
                "approval_status": approval_status,
                "approved_by": approved_by,
                "approved_at": now,
                "effective_from": now if make_effective else None,
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result) if result else None


async def retire(
    db: AsyncIOMotorDatabase,
    process_definition_id: str,
) -> Optional[dict]:
    now = _utc_now()
    result = await db[COLLECTION].find_one_and_update(
        {"process_definition_id": process_definition_id},
        {
            "$set": {
                "approval_status": ProcessApprovalStatus.RETIRED.value,
                "effective_to": now,
                "retired_at": now,
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result) if result else None


async def create_new_revision(
    db: AsyncIOMotorDatabase,
    process_definition_id: str,
    new_process_definition_id: str,
    revision: str,
) -> Optional[dict]:
    existing = await get_by_id(db, process_definition_id)
    if not existing:
        return None
    existing.pop("_id", None)
    existing["process_definition_id"] = new_process_definition_id
    existing["supersedes_process_definition_id"] = process_definition_id
    existing["revision"] = revision
    existing["approval_status"] = ProcessApprovalStatus.DRAFT.value
    existing["approved_by"] = None
    existing["approved_at"] = None
    existing["effective_from"] = None
    existing["effective_to"] = None
    existing["retired_at"] = None
    existing["created_at"] = _utc_now()
    existing["updated_at"] = _utc_now()
    existing["steps"]["process_definition_id"] = new_process_definition_id
    for step in existing["steps"].get("steps", []):
        step["process_definition_id"] = new_process_definition_id
        for key in ("prechecks", "postchecks", "constraints", "corrective_actions"):
            if isinstance(step.get(key), dict):
                step[key]["process_definition_id"] = new_process_definition_id
    await db[COLLECTION].insert_one(existing)
    return _serialize(existing)


def _empty_step_prechecks(
    process_definition_id: str, step_id: str
) -> ProcessStepPrechecks:
    return ProcessStepPrechecks(
        id=f"{step_id}-prechecks",
        process_definition_id=process_definition_id,
        process_step_id=step_id,
        description="No route pre-checks defined yet.",
    )


def _empty_step_postchecks(
    process_definition_id: str, step_id: str
) -> ProcessStepPostchecks:
    return ProcessStepPostchecks(
        id=f"{step_id}-postchecks",
        process_definition_id=process_definition_id,
        process_step_id=step_id,
        description="No route post-checks defined yet.",
    )


def _empty_step_constraints(
    process_definition_id: str, step_id: str
) -> ProcessStepConstraints:
    return ProcessStepConstraints(
        id=f"{step_id}-constraints",
        process_definition_id=process_definition_id,
        process_step_id=step_id,
    )


def _empty_step_corrections(
    process_definition_id: str, step_id: str
) -> CorrectiveActions:
    return CorrectiveActions(
        id=f"{step_id}-corrections",
        process_definition_id=process_definition_id,
        process_step_id=step_id,
        description="No corrective actions defined yet.",
    )


async def backfill_recipe_routes(db: AsyncIOMotorDatabase) -> dict:
    """Convert current recipe route seed records into governed process definitions."""
    routes = await db.recipe_routes.find({}).to_list(length=1000)
    created = 0
    updated = 0
    skipped = 0
    for route in routes:
        process_definition_id = str(
            route.get("route_id") or route.get("id") or ""
        ).strip()
        if not process_definition_id:
            skipped += 1
            continue

        recipe = await db.batch_recipes.find_one({"route_id": process_definition_id})
        route_steps = route.get("steps") if isinstance(route.get("steps"), list) else []
        route_stage_ids = [
            str(step.get("stage_id"))
            for step in route_steps
            if isinstance(step, dict) and step.get("stage_id")
        ]
        flow_ids = []
        for source_stage_id, target_stage_id in zip(
            route_stage_ids, route_stage_ids[1:]
        ):
            flow = await db.material_flows.find_one(
                {
                    "source_stage_id": source_stage_id,
                    "target_stage_id": target_stage_id,
                    "plant_id": route.get("plant_id"),
                },
                {"flow_id": 1, "id": 1},
            )
            if flow:
                flow_ids.append(str(flow.get("flow_id") or flow.get("id")))

        process_steps = []
        ipc_checkpoints = []
        instruction_steps = []
        for fallback_sequence, step in enumerate(route_steps, start=1):
            if not isinstance(step, dict):
                continue
            sequence = int(step.get("sequence") or fallback_sequence)
            stage_id = str(step.get("stage_id") or "")
            operation = str(
                step.get("operation")
                or step.get("stage_name")
                or f"Route step {sequence}"
            )
            instruction_text = str(
                step.get("instruction")
                or step.get("instructions")
                or step.get("description")
                or f"Execute {operation} according to the approved process definition, SOP, and batch record."
            )
            step_id = f"{process_definition_id}-step-{sequence:03d}"
            step_ipc_checkpoint_ids = []
            raw_ipc_checkpoints = (
                step.get("ipc_checkpoints")
                if isinstance(step.get("ipc_checkpoints"), list)
                else []
            )
            for checkpoint_index, checkpoint in enumerate(raw_ipc_checkpoints, start=1):
                if not isinstance(checkpoint, dict):
                    continue
                checkpoint_id = str(
                    checkpoint.get("checkpoint_id")
                    or checkpoint.get("id")
                    or f"{step_id}-ipc-{checkpoint_index:02d}"
                )
                step_ipc_checkpoint_ids.append(checkpoint_id)
                sampling_plan = (
                    checkpoint.get("sampling_plan")
                    if isinstance(checkpoint.get("sampling_plan"), dict)
                    else {}
                )
                ipc_checkpoints.append(
                    {
                        "checkpoint_id": checkpoint_id,
                        "name": str(
                            checkpoint.get("name")
                            or checkpoint.get("parameter")
                            or f"IPC checkpoint {sequence}.{checkpoint_index}"
                        ),
                        "checkpoint_type": str(
                            checkpoint.get("checkpoint_type")
                            or checkpoint.get("type")
                            or "in_process_control"
                        ),
                        "process_step_id": step_id,
                        "stage_id": stage_id or checkpoint.get("stage_id"),
                        "workstation_id": checkpoint.get("workstation_id")
                        or step.get("workstation_id"),
                        "method": str(
                            checkpoint.get("method")
                            or checkpoint.get("test_method")
                            or checkpoint.get("sampling_method")
                            or "operator_verification"
                        ),
                        "method_reference": checkpoint.get("method_reference")
                        or checkpoint.get("sop_id"),
                        "cqa_ids": (
                            [
                                str(value)
                                for value in checkpoint.get("cqa_ids", [])
                                if value
                            ]
                            if isinstance(checkpoint.get("cqa_ids"), list)
                            else []
                        ),
                        "cpp_ids": (
                            [
                                str(value)
                                for value in checkpoint.get("cpp_ids", [])
                                if value
                            ]
                            if isinstance(checkpoint.get("cpp_ids"), list)
                            else []
                        ),
                        "limits": (
                            checkpoint.get("limits")
                            if isinstance(checkpoint.get("limits"), list)
                            else []
                        ),
                        "sampling_plan": {
                            "sampling_method": str(
                                sampling_plan.get("sampling_method")
                                or checkpoint.get("sampling_method")
                                or "defined_by_protocol"
                            ),
                            "sample_size": sampling_plan.get("sample_size")
                            or checkpoint.get("sample_size"),
                            "frequency": sampling_plan.get("frequency")
                            or checkpoint.get("frequency"),
                            "sample_location": sampling_plan.get("sample_location")
                            or checkpoint.get("sample_location"),
                            "aql_level": sampling_plan.get("aql_level")
                            or checkpoint.get("aql_level"),
                            "retain_sample_required": bool(
                                sampling_plan.get("retain_sample_required")
                                or checkpoint.get("retain_sample_required")
                                or False
                            ),
                            "instructions": sampling_plan.get("instructions")
                            or checkpoint.get("sampling_instructions"),
                        },
                        "required": bool(checkpoint.get("required", True)),
                        "evidence_required": bool(
                            checkpoint.get("evidence_required", True)
                        ),
                        "signature_required": bool(
                            checkpoint.get("signature_required", False)
                        ),
                        "alarm_on_failure": bool(
                            checkpoint.get("alarm_on_failure", True)
                        ),
                        "metadata": {
                            "source_route_step": {
                                key: value
                                for key, value in step.items()
                                if key not in {"embedding"}
                            }
                        },
                    }
                )
            process_steps.append(
                ProcessStep(
                    id=step_id,
                    sequence=sequence,
                    name=operation,
                    description=f"{operation} at {step.get('stage_name') or stage_id}.",
                    inputs=[],
                    outputs=[],
                    depends_on=[process_steps[-1].id] if process_steps else [],
                    prechecks=_empty_step_prechecks(process_definition_id, step_id),
                    postchecks=_empty_step_postchecks(process_definition_id, step_id),
                    constraints=_empty_step_constraints(process_definition_id, step_id),
                    corrective_actions=_empty_step_corrections(
                        process_definition_id, step_id
                    ),
                    notes=f"Backfilled from recipe route stage {stage_id}.",
                )
            )
            instruction_steps.append(
                {
                    "sequence": sequence,
                    "title": operation,
                    "instruction": instruction_text,
                    "process_step_id": step_id,
                    "stage_id": stage_id or None,
                    "workstation_id": step.get("workstation_id"),
                    "equipment_ids": (
                        [str(value) for value in step.get("equipment_ids", []) if value]
                        if isinstance(step.get("equipment_ids"), list)
                        else []
                    ),
                    "material_ids": (
                        [str(value) for value in step.get("material_ids", []) if value]
                        if isinstance(step.get("material_ids"), list)
                        else []
                    ),
                    "acceptance_criteria": step.get("acceptance_criteria"),
                    "ipc_checkpoint_ids": step_ipc_checkpoint_ids,
                    "evidence_required": True,
                    "signature_required": False,
                    "metadata": {
                        "source_route_step": {
                            key: value
                            for key, value in step.items()
                            if key not in {"embedding"}
                        }
                    },
                }
            )

        route_process_type = str(
            route.get("process_type")
            or route.get("type")
            or ProcessType.MANUFACTURING.value
        ).lower()
        instruction_template_type = (
            "packing"
            if route_process_type == ProcessType.PACKAGING.value
            else "manufacturing"
        )
        instruction_template_title = (
            "Packing Instructions"
            if instruction_template_type == "packing"
            else "Manufacturing Instructions"
        )
        instruction_templates = [
            {
                "instruction_template_id": f"{process_definition_id}-{instruction_template_type}-instructions",
                "template_type": instruction_template_type,
                "title": f"{route.get('name') or process_definition_id} {instruction_template_title}",
                "version": str(route.get("version") or "1.0.0"),
                "revision": str(route.get("revision") or "A"),
                "approval_status": ProcessApprovalStatus.DRAFT.value,
                "change_control_id": route.get("change_control_id"),
                "effective_from": route.get("effective_from"),
                "effective_to": route.get("effective_to"),
                "approved_by": None,
                "approved_at": None,
                "derived_from_sop_ids": (
                    [str(value) for value in route.get("sop_ids", []) if value]
                    if isinstance(route.get("sop_ids"), list)
                    else []
                ),
                "derived_from_sop_document_ids": (
                    [str(value) for value in route.get("sop_document_ids", []) if value]
                    if isinstance(route.get("sop_document_ids"), list)
                    else []
                ),
                "derived_from_process_step_ids": [
                    item["process_step_id"]
                    for item in instruction_steps
                    if item.get("process_step_id")
                ],
                "steps": instruction_steps,
                "metadata": {"source": "recipe_route_backfill"},
            }
        ]

        process = {
            "process_definition_id": process_definition_id,
            "name": str(route.get("name") or process_definition_id),
            "description": str(route.get("description") or ""),
            "version": str(route.get("version") or "1.0.0"),
            "route_version": str(
                route.get("route_version") or route.get("version") or "1.0.0"
            ),
            "revision": str(route.get("revision") or "A"),
            "process_type": (
                ProcessType.PACKAGING.value
                if instruction_template_type == "packing"
                else ProcessType.MANUFACTURING.value
            ),
            "product_family": route.get("product_family")
            or (recipe or {}).get("product_family"),
            "plant_id": route.get("plant_id") or (recipe or {}).get("plant_id"),
            "recipe_id": route.get("recipe_id") or (recipe or {}).get("recipe_id"),
            "owner": route.get("owner"),
            "tags": sorted(
                set(
                    ["route", "recipe-route", "backfilled-process-definition"]
                    + [str(tag) for tag in route.get("tags", []) if tag]
                )
            ),
            "status": "pending",
            "approval_status": ProcessApprovalStatus.DRAFT.value,
            "route_stage_ids": route_stage_ids,
            "material_flow_edge_ids": flow_ids,
            "ipc_checkpoints": ipc_checkpoints,
            "instruction_templates": instruction_templates,
            "metadata": {
                "source_collection": "recipe_routes",
                "source_route_id": process_definition_id,
                "source_recipe_id": route.get("recipe_id"),
                "route_selection_rule": (recipe or {}).get("route_selection_rule"),
                "legacy_route": {
                    key: value
                    for key, value in route.items()
                    if key not in {"_id", "embedding"}
                },
            },
            "steps": ProcessSteps(
                id=f"{process_definition_id}-steps",
                process_definition_id=process_definition_id,
                description=f"Route steps for {route.get('name') or process_definition_id}.",
                steps=process_steps,
            ).model_dump(),
            "created_at": route.get("created_at") or _utc_now(),
            "updated_at": _utc_now(),
        }
        existing = await db[COLLECTION].find_one(
            {"process_definition_id": process_definition_id}
        )
        await db[COLLECTION].update_one(
            {"process_definition_id": process_definition_id},
            {"$set": process},
            upsert=True,
        )
        if existing:
            updated += 1
        else:
            created += 1
    return {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "total_routes": len(routes),
    }


async def update_status(
    db: AsyncIOMotorDatabase,
    process_definition_id: str,
    status: str,
) -> Optional[dict]:
    """Convenience helper: update only the status field of a process_definition."""
    result = await db[COLLECTION].find_one_and_update(
        {"process_definition_id": process_definition_id},
        {"$set": {"status": status}},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result) if result else None


async def delete(
    db: AsyncIOMotorDatabase,
    process_definition_id: str,
) -> bool:
    """Delete a process_definition by ID. Returns True if a document was removed."""
    result = await db[COLLECTION].delete_one(
        {"process_definition_id": process_definition_id}
    )
    return result.deleted_count == 1


# ---------------------------------------------------------------------------
# ProcessDefinition canvas layouts
# ---------------------------------------------------------------------------


async def get_all_canvas_layouts(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    query: dict = {}
    total = await db[CANVAS_COLLECTION].count_documents(query)
    cursor = (
        db[CANVAS_COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    )
    return [_serialize(d) async for d in cursor], total


async def get_canvas_layout_by_id(
    db: AsyncIOMotorDatabase,
    layout_id: str,
) -> Optional[dict]:
    doc = await db[CANVAS_COLLECTION].find_one({"layout_id": layout_id})
    if not doc:
        page_id = normalize_canvas_page_id(layout_id, layout_id)
        if page_id and page_id != layout_id:
            doc = await db[CANVAS_COLLECTION].find_one({"page_id": page_id})
    if not doc:
        aliases = {
            "indus-dashboard-process-steps": "process-steps",
            "indus-dashboard-process_definition": "process_definition",
        }
        alias = aliases.get(layout_id)
        if alias:
            doc = await db[CANVAS_COLLECTION].find_one(
                {"layout_id": alias}
            ) or await db[CANVAS_COLLECTION].find_one({"page_id": alias})
    return _serialize(doc) if doc else None


async def get_canvas_layouts_by_process_definition(
    db: AsyncIOMotorDatabase,
    process_definition_id: str,
) -> list[dict]:
    cursor = db[CANVAS_COLLECTION].find(
        {"process_definition_id": process_definition_id}
    )
    return [_serialize(d) async for d in cursor]


async def get_canvas_layouts_by_page(
    db: AsyncIOMotorDatabase,
    page_id: str,
) -> list[dict]:
    cursor = db[CANVAS_COLLECTION].find({"page_id": page_id})
    return [_serialize(d) async for d in cursor]


async def create_canvas_layout(
    db: AsyncIOMotorDatabase,
    data: ProcessDefinitionCanvasLayoutCreate,
) -> dict:
    doc = data.model_dump()
    _mirror_canvas_edge_aliases(doc)
    await db[CANVAS_COLLECTION].insert_one(doc)
    serialized = _serialize(doc)
    await _sync_canvas_layout_to_graph(serialized)
    return serialized


async def update_canvas_layout(
    db: AsyncIOMotorDatabase,
    layout_id: str,
    data: ProcessDefinitionCanvasLayoutUpdate,
) -> Optional[dict]:
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        return await get_canvas_layout_by_id(db, layout_id)
    _mirror_canvas_edge_aliases(fields)
    result = await db[CANVAS_COLLECTION].find_one_and_update(
        {"layout_id": layout_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    serialized = _serialize(result) if result else None
    await _sync_canvas_layout_to_graph(serialized)
    return serialized


async def get_canvas_node_properties(
    db: AsyncIOMotorDatabase,
    layout_id: str,
    canvas_node_id: str,
) -> Optional[dict]:
    layout = await get_canvas_layout_by_id(db, layout_id)
    if not layout:
        return None
    for node in layout.get("nodes", []):
        if _node_matches(node, canvas_node_id):
            return _node_properties_from_record(node)
    return None


async def update_canvas_node_properties(
    db: AsyncIOMotorDatabase,
    layout_id: str,
    canvas_node_id: str,
    data: ProcessDefinitionCanvasNodePropertiesUpdate,
) -> Optional[dict]:
    fields = data.model_dump(exclude_unset=True)
    fields.pop("updated_at", None)
    if not fields:
        return await get_canvas_node_properties(db, layout_id, canvas_node_id)

    set_fields = {"updated_at": _utc_now()}
    for key, value in fields.items():
        set_fields[f"nodes.$.{key}"] = value

    result = await db[CANVAS_COLLECTION].find_one_and_update(
        {"layout_id": layout_id, **_node_identity_query(canvas_node_id)},
        {"$set": set_fields},
        return_document=ReturnDocument.AFTER,
    )
    serialized = _serialize(result) if result else None
    await _sync_canvas_layout_to_graph(serialized)
    if not serialized:
        return None
    for node in serialized.get("nodes", []):
        if _node_matches(node, canvas_node_id):
            return _node_properties_from_record(node)
    return None


async def replace_canvas_node_metadata(
    db: AsyncIOMotorDatabase,
    layout_id: str,
    canvas_node_id: str,
    metadata: dict,
) -> Optional[dict]:
    return await update_canvas_node_properties(
        db,
        layout_id,
        canvas_node_id,
        ProcessDefinitionCanvasNodePropertiesUpdate(metadata=metadata),
    )


async def add_canvas_node_hyperlink(
    db: AsyncIOMotorDatabase,
    layout_id: str,
    canvas_node_id: str,
    hyperlink: ProcessDefinitionCanvasHyperlink,
) -> Optional[dict]:
    result = await db[CANVAS_COLLECTION].find_one_and_update(
        {"layout_id": layout_id, **_node_identity_query(canvas_node_id)},
        {
            "$push": {"nodes.$.hyperlinks": hyperlink.model_dump()},
            "$set": {"updated_at": _utc_now()},
        },
        return_document=ReturnDocument.AFTER,
    )
    serialized = _serialize(result) if result else None
    await _sync_canvas_layout_to_graph(serialized)
    if not serialized:
        return None
    for node in serialized.get("nodes", []):
        if _node_matches(node, canvas_node_id):
            return _node_properties_from_record(node)
    return None


async def add_canvas_node_attachment(
    db: AsyncIOMotorDatabase,
    layout_id: str,
    canvas_node_id: str,
    attachment: ProcessDefinitionCanvasAttachment,
) -> Optional[dict]:
    result = await db[CANVAS_COLLECTION].find_one_and_update(
        {"layout_id": layout_id, **_node_identity_query(canvas_node_id)},
        {
            "$push": {"nodes.$.attachments": attachment.model_dump()},
            "$set": {"updated_at": _utc_now()},
        },
        return_document=ReturnDocument.AFTER,
    )
    serialized = _serialize(result) if result else None
    await _sync_canvas_layout_to_graph(serialized)
    if not serialized:
        return None
    for node in serialized.get("nodes", []):
        if _node_matches(node, canvas_node_id):
            return _node_properties_from_record(node)
    return None


async def remove_canvas_node_list_item(
    db: AsyncIOMotorDatabase,
    layout_id: str,
    canvas_node_id: str,
    field: str,
    index: int,
) -> Optional[dict]:
    current = await get_canvas_node_properties(db, layout_id, canvas_node_id)
    if current is None:
        return None
    items = list(current.get(field) or [])
    if index < 0 or index >= len(items):
        return current
    items.pop(index)
    return await update_canvas_node_properties(
        db,
        layout_id,
        canvas_node_id,
        ProcessDefinitionCanvasNodePropertiesUpdate(**{field: items}),
    )


async def record_canvas_node_webhook_event(
    db: AsyncIOMotorDatabase,
    layout_id: str,
    canvas_node_id: str,
    event: ProcessDefinitionCanvasWebhookEvent,
) -> Optional[dict]:
    event_doc = event.model_dump()
    result = await db[CANVAS_COLLECTION].find_one_and_update(
        {"layout_id": layout_id, **_node_identity_query(canvas_node_id)},
        {
            "$push": {
                "nodes.$.webhook_events": {
                    "$each": [event_doc],
                    "$slice": -100,
                }
            },
            "$set": {
                "nodes.$.last_webhook_event": event_doc,
                "updated_at": _utc_now(),
            },
        },
        return_document=ReturnDocument.AFTER,
    )
    serialized = _serialize(result) if result else None
    await _sync_canvas_layout_to_graph(serialized)
    if not serialized:
        return None
    for node in serialized.get("nodes", []):
        if _node_matches(node, canvas_node_id):
            return _node_properties_from_record(node)
    return None


async def get_canvas_node_record(
    db: AsyncIOMotorDatabase,
    layout_id: str,
    canvas_node_id: str,
) -> Optional[dict]:
    layout = await get_canvas_layout_by_id(db, layout_id)
    if not layout:
        return None
    for node in layout.get("nodes", []):
        if _node_matches(node, canvas_node_id):
            return node
    return None


async def get_connected_canvas_nodes(
    db: AsyncIOMotorDatabase,
    layout_id: str,
    canvas_node_id: str,
) -> dict:
    layout = await get_canvas_layout_by_id(db, layout_id)
    if not layout:
        return {"incoming": [], "outgoing": []}
    nodes = {str(node.get("node_id")): node for node in layout.get("nodes", [])}
    incoming = []
    outgoing = []
    for edge in layout.get("edges", []):
        source = str(edge.get("source_node_id") or edge.get("source") or "")
        target = str(edge.get("target_node_id") or edge.get("target") or "")
        if target == canvas_node_id and source in nodes:
            incoming.append(
                {"edge": edge, "node": _node_properties_from_record(nodes[source])}
            )
        if source == canvas_node_id and target in nodes:
            outgoing.append(
                {"edge": edge, "node": _node_properties_from_record(nodes[target])}
            )
    return {"incoming": incoming, "outgoing": outgoing}


async def add_canvas_node(
    db: AsyncIOMotorDatabase,
    layout_id: str,
    data: ProcessDefinitionCanvasNodePosition,
) -> Optional[dict]:
    """
    Add a node after it has been dropped onto the canvas.
    Existing node IDs are treated as idempotent and return the current layout.
    """
    existing = await get_canvas_layout_by_id(db, layout_id)
    if not existing:
        return None
    if any(node.get("node_id") == data.node_id for node in existing.get("nodes", [])):
        return existing

    node_data = data.model_dump()
    metadata = (
        node_data.get("metadata") if isinstance(node_data.get("metadata"), dict) else {}
    )
    backend = (
        node_data.get("backend") if isinstance(node_data.get("backend"), dict) else {}
    )
    catalog_identifier = (
        node_data.get("node_type")
        or node_data.get("node_category")
        or metadata.get("nodeType")
        or metadata.get("node_type")
        or metadata.get("type")
        or metadata.get("nodeCatalogKey")
        or backend.get("action")
    )
    catalog_identifier = (
        catalog_identifier
        or node_data.get("title")
        or node_data.get("name")
        or data.node_id
    )
    catalog_metadata = catalog_metadata_for(
        str(catalog_identifier) if catalog_identifier is not None else None
    )
    if catalog_metadata:
        catalog_defaults = catalog_defaults_for(str(catalog_identifier))
        default_config = (
            catalog_defaults.get("config")
            if isinstance(catalog_defaults.get("config"), dict)
            else {}
        )
        current_config = (
            node_data.get("config") if isinstance(node_data.get("config"), dict) else {}
        )
        node_data["metadata"] = {**metadata, **catalog_metadata}
        node_data["node_type"] = (
            node_data.get("node_type") or catalog_metadata["nodeType"]
        )
        node_data["node_category"] = (
            node_data.get("node_category") or catalog_metadata["nodeCategory"]
        )
        node_data["backend"] = node_data.get("backend") or catalog_metadata.get(
            "backend"
        )
        node_data["config"] = {**default_config, **current_config}
        node_data["title"] = node_data.get("title") or catalog_metadata["nodeLabel"]
        node_data["name"] = node_data.get("name") or catalog_metadata["nodeLabel"]

    result = await db[CANVAS_COLLECTION].find_one_and_update(
        {"layout_id": layout_id},
        {
            "$push": {"nodes": node_data},
            "$set": {"updated_at": _utc_now()},
        },
        return_document=ReturnDocument.AFTER,
    )
    serialized = _serialize(result) if result else None
    await _sync_canvas_layout_to_graph(serialized)
    return serialized


async def clear_canvas_nodes(
    db: AsyncIOMotorDatabase,
    layout_id: str,
) -> Optional[dict]:
    result = await db[CANVAS_COLLECTION].find_one_and_update(
        {"layout_id": layout_id},
        {"$set": {"nodes": [], "edges": [], "updated_at": _utc_now()}},
        return_document=ReturnDocument.AFTER,
    )
    serialized = _serialize(result) if result else None
    await _sync_canvas_layout_to_graph(serialized)
    return serialized


async def delete_canvas_node(
    db: AsyncIOMotorDatabase,
    canvas_node_id: str,
) -> bool:
    escaped_node_id = re.escape(canvas_node_id)
    suffixed_node_pattern = f":{escaped_node_id}$"
    node_match = {
        "$or": [
            {"id": canvas_node_id},
            {"node_id": canvas_node_id},
            {"canvas_node_id": canvas_node_id},
            {"id": {"$regex": suffixed_node_pattern}},
            {"node_id": {"$regex": suffixed_node_pattern}},
            {"canvas_node_id": {"$regex": suffixed_node_pattern}},
        ]
    }
    edge_match = {
        "$or": [
            {"source": canvas_node_id},
            {"target": canvas_node_id},
            {"source_node_id": canvas_node_id},
            {"target_node_id": canvas_node_id},
            {"sourceNodeId": canvas_node_id},
            {"targetNodeId": canvas_node_id},
            {"source_canvas_node_id": canvas_node_id},
            {"target_canvas_node_id": canvas_node_id},
            {"source": {"$regex": suffixed_node_pattern}},
            {"target": {"$regex": suffixed_node_pattern}},
            {"source_node_id": {"$regex": suffixed_node_pattern}},
            {"target_node_id": {"$regex": suffixed_node_pattern}},
            {"sourceNodeId": {"$regex": suffixed_node_pattern}},
            {"targetNodeId": {"$regex": suffixed_node_pattern}},
            {"source_canvas_node_id": {"$regex": suffixed_node_pattern}},
            {"target_canvas_node_id": {"$regex": suffixed_node_pattern}},
        ]
    }
    result = await db[CANVAS_COLLECTION].update_many(
        {
            "$or": [
                {"nodes.id": canvas_node_id},
                {"nodes.node_id": canvas_node_id},
                {"nodes.canvas_node_id": canvas_node_id},
                {"nodes.id": {"$regex": suffixed_node_pattern}},
                {"nodes.node_id": {"$regex": suffixed_node_pattern}},
                {"nodes.canvas_node_id": {"$regex": suffixed_node_pattern}},
            ]
        },
        {
            "$pull": {
                "nodes": node_match,
                "edges": edge_match,
            },
            "$set": {"updated_at": _utc_now()},
        },
    )
    return result.modified_count > 0


async def delete_canvas_layout(
    db: AsyncIOMotorDatabase,
    layout_id: str,
) -> bool:
    result = await db[CANVAS_COLLECTION].delete_one({"layout_id": layout_id})
    return result.deleted_count == 1
