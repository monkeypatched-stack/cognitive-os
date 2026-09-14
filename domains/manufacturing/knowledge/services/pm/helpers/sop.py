"""
helpers/sop_helper.py
---------------------
MongoDB CRUD helper for the SOP (Standard Operating Procedure) aggregate model.
Mirrors the pattern established in the devices helper.
"""

import re
from typing import Any, Optional
from uuid import uuid4
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.pm.models.sop import SOPCreate, SOPResponse, SOPUpdate
from services.common.compliance import CHANGE_CONTROL_COLLECTION, utc_now

COLLECTION = "sops"
MATERIAL_FLOW_COLLECTION = "material_flows"


def _serialize(doc: dict) -> dict:
    """Strip MongoDB's internal _id before returning a document."""
    doc.pop("_id", None)
    return doc


def _sop_process_definition_id(record: dict) -> str:
    process_definition = record.get("process_definition")
    if isinstance(process_definition, dict) and process_definition.get(
        "process_definition_id"
    ):
        return str(process_definition["process_definition_id"])
    for section_name in (
        "prechecks",
        "postchecks",
        "constraints",
        "corrective_actions",
    ):
        section = record.get(section_name)
        if isinstance(section, dict):
            value = section.get("process_definition_id") or section.get("workflow_id")
            if value:
                return str(value)
    return f"PROC-{record.get('id') or 'SOP'}"


def _normalize_section(
    section: object, process_definition_id: str, section_id: str
) -> dict:
    normalized = dict(section) if isinstance(section, dict) else {}
    normalized.setdefault("id", section_id)
    normalized["process_definition_id"] = str(
        normalized.get("process_definition_id")
        or normalized.get("workflow_id")
        or process_definition_id
    )
    return normalized


def _normalize_sop_record(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None

    record = _serialize(doc)
    sop_id = str(
        record.get("id")
        or record.get("sop_id")
        or record.get("document_id")
        or record.get("name")
        or "SOP"
    )
    record["id"] = sop_id
    process_definition_id = _sop_process_definition_id(record)
    process_step_id = str(
        record.get("process_step_id")
        or (record.get("corrective_actions") or {}).get("process_step_id")
        or (record.get("corrective_actions") or {}).get("workflow_step_id")
        or f"{process_definition_id}-step-001"
    )

    record.setdefault("title", sop_id)
    record.setdefault("purpose", record.get("title") or sop_id)
    record.setdefault("scope", record.get("purpose") or record.get("title") or sop_id)
    record.setdefault("version", "1.0.0")
    record.setdefault("references", [])
    record.setdefault("tags", [])
    record.setdefault("document_ids", [])

    process_definition = record.get("process_definition")
    if not isinstance(process_definition, dict):
        process_definition = (
            record.get("processDefinition")
            if isinstance(record.get("processDefinition"), dict)
            else {}
        )
    process_definition = dict(process_definition)
    process_definition.setdefault("process_definition_id", process_definition_id)
    process_definition.setdefault("name", record.get("title") or process_definition_id)
    process_definition.setdefault(
        "description", record.get("purpose") or record.get("scope") or ""
    )
    process_definition.setdefault("version", record.get("version") or "1.0.0")
    process_definition.setdefault("owner", record.get("authored_by"))
    process_definition.setdefault("tags", record.get("tags") or [])
    process_definition.setdefault("status", "pending")
    if process_definition.get("status") == "active":
        process_definition["status"] = "completed"
    elif process_definition.get("status") not in {
        "pending",
        "in_progress",
        "completed",
        "failed",
        "skipped",
        "blocked",
    }:
        process_definition["status"] = "pending"
    process_definition.setdefault("approval_status", "draft")
    process_definition.setdefault("ipc_checkpoints", [])
    process_definition.setdefault("cleaning_requirements", [])
    process_definition.setdefault("instruction_templates", [])
    process_definition.setdefault(
        "steps",
        {
            "id": f"{process_definition_id}-steps",
            "process_definition_id": process_definition_id,
            "description": record.get("scope") or record.get("purpose"),
            "steps": [],
        },
    )
    record["process_definition"] = process_definition

    record["prechecks"] = _normalize_section(
        record.get("prechecks"), process_definition_id, f"PRE-{sop_id}"
    )
    record["postchecks"] = _normalize_section(
        record.get("postchecks"), process_definition_id, f"POST-{sop_id}"
    )
    record["constraints"] = _normalize_section(
        record.get("constraints"), process_definition_id, f"CON-{sop_id}"
    )
    constraints = record["constraints"].get("constraints")
    if isinstance(constraints, list):
        record["constraints"]["constraints"] = [
            (
                {
                    **item,
                    "process_definition_id": str(
                        item.get("process_definition_id")
                        or item.get("workflow_id")
                        or process_definition_id
                    ),
                }
                if isinstance(item, dict)
                else item
            )
            for item in constraints
        ]

    record["corrective_actions"] = _normalize_section(
        record.get("corrective_actions"), process_definition_id, f"CA-{sop_id}"
    )
    record["corrective_actions"]["process_step_id"] = str(
        record["corrective_actions"].get("process_step_id")
        or record["corrective_actions"].get("workflow_step_id")
        or process_step_id
    )

    return SOPResponse(**record).model_dump()


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    """Return a paginated list of all SOPs and the total count."""
    query: dict = {}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_normalize_sop_record(d) async for d in cursor], total


async def get_by_id(
    db: AsyncIOMotorDatabase,
    sop_id: str,
) -> Optional[dict]:
    """Fetch a single SOP by its id."""
    doc = await db[COLLECTION].find_one({"id": sop_id})
    return _normalize_sop_record(doc) if doc else None


async def get_by_process_definition_id(
    db: AsyncIOMotorDatabase,
    process_definition_id: str,
) -> Optional[dict]:
    """Fetch the SOP that wraps a given process_definition."""
    if not process_definition_id:
        return None
    doc = await db[COLLECTION].find_one(
        {"process_definition.process_definition_id": process_definition_id}
    )
    return _normalize_sop_record(doc) if doc else None


async def get_by_author(
    db: AsyncIOMotorDatabase,
    authored_by: str,
) -> list[dict]:
    """Return all SOPs created by a given author."""
    if not authored_by:
        return []
    cursor = db[COLLECTION].find({"authored_by": authored_by})
    return [_normalize_sop_record(d) async for d in cursor]


async def get_by_approver(
    db: AsyncIOMotorDatabase,
    approved_by: str,
) -> list[dict]:
    """Return all SOPs approved by a given approver."""
    if not approved_by:
        return []
    cursor = db[COLLECTION].find({"approved_by": approved_by})
    return [_normalize_sop_record(d) async for d in cursor]


async def get_by_tag(
    db: AsyncIOMotorDatabase,
    tag: str,
) -> list[dict]:
    """Return all SOPs that carry the given tag."""
    if not tag:
        return []
    cursor = db[COLLECTION].find({"tags": tag})
    return [_normalize_sop_record(d) async for d in cursor]


async def get_by_version(
    db: AsyncIOMotorDatabase,
    version: str,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    """Return paginated SOPs that match a specific version string."""
    query = {"version": version}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_normalize_sop_record(d) async for d in cursor], total


def _search_text(record: dict) -> str:
    values: list[str] = []

    def collect(value):
        if value is None:
            return
        if isinstance(value, (str, int, float, bool)):
            values.append(str(value))
            return
        if isinstance(value, dict):
            for item in value.values():
                collect(item)
            return
        if isinstance(value, list):
            for item in value:
                collect(item)

    for key in (
        "id",
        "sop_id",
        "document_id",
        "title",
        "name",
        "purpose",
        "scope",
        "version",
        "entity_name",
        "entity_type",
        "plant_id",
        "line_id",
        "stage_id",
        "workstation_id",
        "machine_id",
        "equipment_id",
        "tags",
        "process_definition",
        "prechecks",
        "postchecks",
        "constraints",
        "corrective_actions",
        "notes",
        "remarks",
        "references",
    ):
        collect(record.get(key))
    return " ".join(values).lower()


def _search_score(query: str, record: dict) -> float:
    terms = [term for term in re.findall(r"[a-z0-9]+", query.lower()) if len(term) >= 3]
    if not terms:
        return 0.0
    haystack = _search_text(record)
    return sum(1 for term in terms if term in haystack) / len(terms)


async def search(
    db: AsyncIOMotorDatabase,
    query: str,
    limit: int = 20,
) -> list[dict]:
    """Search SOP records by free text across SOP and embedded process sections."""
    terms = [
        term for term in re.findall(r"[A-Za-z0-9_-]+", query or "") if len(term) >= 3
    ][:10]
    if not terms:
        return []
    regex = "|".join(re.escape(term) for term in terms)
    text_match = {"$regex": regex, "$options": "i"}
    mongo_query = {
        "$or": [
            {"id": text_match},
            {"sop_id": text_match},
            {"document_id": text_match},
            {"title": text_match},
            {"name": text_match},
            {"purpose": text_match},
            {"scope": text_match},
            {"entity_name": text_match},
            {"entity_type": text_match},
            {"workstation_id": text_match},
            {"machine_id": text_match},
            {"equipment_id": text_match},
            {"tags": text_match},
            {"process_definition.name": text_match},
            {"process_definition.description": text_match},
            {"prechecks.description": text_match},
            {"postchecks.description": text_match},
            {"constraints.description": text_match},
            {"corrective_actions.description": text_match},
            {"notes": text_match},
            {"remarks": text_match},
        ]
    }
    cursor = db[COLLECTION].find(mongo_query).limit(max(limit * 4, limit))
    scored: list[tuple[float, dict]] = []
    async for document in cursor:
        normalized = _normalize_sop_record(document)
        if normalized:
            scored.append((_search_score(query, normalized), normalized))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [record for score, record in scored if score > 0][:limit]


def _text_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _dedupe(values: list[str | None]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _record_id(record: dict) -> str | None:
    for key in (
        "id",
        "sop_id",
        "document_id",
        "entity_id",
        "stage_id",
        "workstation_id",
        "machine_id",
        "equipment_id",
    ):
        value = _text_value(record.get(key))
        if value:
            return value
    return None


async def _lookup_stage_ids_for_sop(db: AsyncIOMotorDatabase, sop: dict) -> list[str]:
    process_definition = (
        sop.get("process_definition")
        if isinstance(sop.get("process_definition"), dict)
        else {}
    )
    stage_ids = [
        _text_value(sop.get("stage_id")),
        _text_value(process_definition.get("stage_id")),
    ]

    workstation_id = _text_value(
        sop.get("workstation_id") or process_definition.get("workstation_id")
    )
    if workstation_id:
        workstation = await db["workstations"].find_one(
            {
                "$or": [
                    {"id": workstation_id},
                    {"workstation_id": workstation_id},
                    {"entity_id": workstation_id},
                ]
            },
            {"_id": 0, "stage_id": 1},
        )
        if workstation:
            stage_ids.append(_text_value(workstation.get("stage_id")))

    for collection, key in (
        ("pharmaceutical_machines", "machine_id"),
        ("machines", "machine_id"),
        ("pharmaceutical_equipment", "equipment_id"),
        ("equipment", "equipment_id"),
    ):
        entity_id = _text_value(sop.get(key) or process_definition.get(key))
        if not entity_id:
            continue
        entity = await db[collection].find_one(
            {
                "$or": [
                    {"id": entity_id},
                    {key: entity_id},
                    {"entity_id": entity_id},
                ]
            },
            {"_id": 0, "stage_id": 1},
        )
        if entity:
            stage_ids.append(_text_value(entity.get("stage_id")))

    return _dedupe(stage_ids)


async def _material_flow_graph(
    db: AsyncIOMotorDatabase,
    stage_ids: list[str],
    *,
    include_upstream: bool,
    include_downstream: bool,
    max_depth: int,
) -> tuple[list[dict], list[str]]:
    if not stage_ids:
        return [], []
    visited_stages = set(stage_ids)
    frontier = set(stage_ids)
    flows: list[dict] = []
    flow_keys: set[tuple[str, str, str]] = set()

    for depth in range(1, max_depth + 1):
        clauses = []
        if include_downstream:
            clauses.append({"source_stage_id": {"$in": list(frontier)}})
        if include_upstream:
            clauses.append({"target_stage_id": {"$in": list(frontier)}})
        if not clauses:
            break

        cursor = db[MATERIAL_FLOW_COLLECTION].find({"$or": clauses}, {"_id": 0})
        next_frontier: set[str] = set()
        async for flow in cursor:
            source_stage_id = _text_value(flow.get("source_stage_id"))
            target_stage_id = _text_value(flow.get("target_stage_id"))
            if not source_stage_id or not target_stage_id:
                continue
            direction = "downstream" if source_stage_id in frontier else "upstream"
            connected_stage_id = (
                target_stage_id if direction == "downstream" else source_stage_id
            )
            if direction == "downstream" and not include_downstream:
                continue
            if direction == "upstream" and not include_upstream:
                continue

            key = (
                _text_value(flow.get("flow_id") or flow.get("id"))
                or f"{source_stage_id}->{target_stage_id}",
                source_stage_id,
                target_stage_id,
            )
            if key not in flow_keys:
                flow_keys.add(key)
                flows.append(
                    {
                        **flow,
                        "flow_id": key[0],
                        "source_stage_id": source_stage_id,
                        "target_stage_id": target_stage_id,
                        "direction_from_source": direction,
                        "depth": depth,
                    }
                )
            if connected_stage_id not in visited_stages:
                visited_stages.add(connected_stage_id)
                next_frontier.add(connected_stage_id)
        if not next_frontier:
            break
        frontier = next_frontier

    return flows, sorted(visited_stages - set(stage_ids))


async def _linked_sops_for_stages(
    db: AsyncIOMotorDatabase,
    stage_ids: list[str],
    *,
    source_sop_id: str,
) -> list[dict]:
    if not stage_ids:
        return []

    workstations = (
        await db["workstations"]
        .find(
            {"stage_id": {"$in": stage_ids}},
            {"_id": 0, "id": 1, "workstation_id": 1},
        )
        .to_list(length=500)
    )
    workstation_ids = _dedupe(
        [
            _text_value(item.get("id") or item.get("workstation_id"))
            for item in workstations
        ]
    )

    machines = (
        await db["pharmaceutical_machines"]
        .find(
            {"stage_id": {"$in": stage_ids}},
            {"_id": 0, "id": 1, "machine_id": 1},
        )
        .to_list(length=500)
    )
    machine_ids = _dedupe(
        [_text_value(item.get("id") or item.get("machine_id")) for item in machines]
    )

    equipment = (
        await db["pharmaceutical_equipment"]
        .find(
            {"stage_id": {"$in": stage_ids}},
            {"_id": 0, "id": 1, "equipment_id": 1},
        )
        .to_list(length=500)
    )
    equipment_ids = _dedupe(
        [_text_value(item.get("id") or item.get("equipment_id")) for item in equipment]
    )

    clauses: list[dict] = [
        {"stage_id": {"$in": stage_ids}},
        {"process_definition.stage_id": {"$in": stage_ids}},
    ]
    if workstation_ids:
        clauses.extend(
            [
                {"workstation_id": {"$in": workstation_ids}},
                {"process_definition.workstation_id": {"$in": workstation_ids}},
            ]
        )
    if machine_ids:
        clauses.extend(
            [
                {"machine_id": {"$in": machine_ids}},
                {"process_definition.machine_id": {"$in": machine_ids}},
            ]
        )
    if equipment_ids:
        clauses.extend(
            [
                {"equipment_id": {"$in": equipment_ids}},
                {"process_definition.equipment_id": {"$in": equipment_ids}},
            ]
        )

    cursor = db[COLLECTION].find({"$or": clauses}, {"_id": 0, "embedding": 0})
    linked: list[dict] = []
    seen: set[str] = set()
    async for document in cursor:
        normalized = _normalize_sop_record(document)
        if not normalized:
            continue
        sop_id = _record_id(normalized)
        if not sop_id or sop_id == source_sop_id or sop_id in seen:
            continue
        seen.add(sop_id)
        linked.append(normalized)
    return linked


def _changed_fields(proposed_values: dict) -> list[str]:
    return sorted(str(key) for key in proposed_values.keys())


def _relationship_path(sop: dict, flows: list[dict]) -> list[dict]:
    sop_stage_id = _text_value(
        sop.get("stage_id") or (sop.get("process_definition") or {}).get("stage_id")
    )
    if not sop_stage_id:
        return []
    return [
        {
            "flow_id": flow.get("flow_id"),
            "source_stage_id": flow.get("source_stage_id"),
            "target_stage_id": flow.get("target_stage_id"),
            "direction": flow.get("direction_from_source"),
            "depth": flow.get("depth"),
        }
        for flow in flows
        if flow.get("source_stage_id") == sop_stage_id
        or flow.get("target_stage_id") == sop_stage_id
    ]


def _impact_review_values(
    source_sop: dict, impacted_sop: dict, proposed_values: dict, flows: list[dict]
) -> dict:
    now = utc_now()
    source_sop_id = _record_id(source_sop)
    impacted_sop_id = _record_id(impacted_sop)
    review = {
        "required": True,
        "status": "Pending Approval",
        "source_sop_id": source_sop_id,
        "source_sop_title": source_sop.get("title") or source_sop_id,
        "impacted_sop_id": impacted_sop_id,
        "changed_fields": _changed_fields(proposed_values),
        "relationship_path": _relationship_path(impacted_sop, flows),
        "analyzed_at": now,
    }
    return {
        "linked_sop_change_required": True,
        "requires_review": True,
        "change_impact_review": review,
        "updated_at": now,
    }


def _effect_stage_names(flows: list[dict], direction: str) -> list[str]:
    names: list[str] = []
    for flow in flows:
        if flow.get("direction_from_source") != direction:
            continue
        key = "target_stage_name" if direction == "downstream" else "source_stage_name"
        value = _text_value(flow.get(key))
        if value and value not in names:
            names.append(value)
    return names


def _stage_ids_for_direction(flows: list[dict], direction: str) -> list[str]:
    ids: list[str] = []
    for flow in flows:
        if flow.get("direction_from_source") != direction:
            continue
        key = "target_stage_id" if direction == "downstream" else "source_stage_id"
        value = _text_value(flow.get(key))
        if value and value not in ids:
            ids.append(value)
    return ids


def _proposed_change_text(proposed_values: dict) -> str:
    fragments: list[str] = []
    for section, value in dict(proposed_values or {}).items():
        if isinstance(value, dict):
            for key, nested_value in value.items():
                if isinstance(nested_value, (str, int, float, bool)):
                    fragments.append(f"{section}.{key}={nested_value}")
        elif isinstance(value, (str, int, float, bool)):
            fragments.append(f"{section}={value}")
    return "; ".join(fragments[:8])


def _change_effects(
    source_sop: dict,
    proposed_values: dict,
    flows: list[dict],
    linked_sops: list[dict],
    required_changes: list[dict],
) -> dict:
    changed_fields = _changed_fields(proposed_values)
    downstream_stage_names = _effect_stage_names(flows, "downstream")
    upstream_stage_names = _effect_stage_names(flows, "upstream")
    downstream_stage_ids = _stage_ids_for_direction(flows, "downstream")
    upstream_stage_ids = _stage_ids_for_direction(flows, "upstream")
    proposed_text = _proposed_change_text(proposed_values).lower()
    proposed = dict(proposed_values or {})
    constraints = (
        proposed.get("constraints")
        if isinstance(proposed.get("constraints"), dict)
        else {}
    )
    postchecks = (
        proposed.get("postchecks")
        if isinstance(proposed.get("postchecks"), dict)
        else {}
    )

    release_gate = bool(
        constraints.get("quality_hold_required")
        or constraints.get("downstream_release_requires_qa_disposition")
        or "qa" in proposed_text
        or "hold" in proposed_text
        or "release" in proposed_text
    )
    deviation_required = bool("deviation" in proposed_text or "capa" in proposed_text)
    postcheck_tightened = bool(postchecks or "postchecks" in changed_fields)
    downstream_start_blocked = release_gate and bool(downstream_stage_names)

    operational_effects: list[dict[str, Any]] = []
    if release_gate:
        operational_effects.append(
            {
                "effect": "Adds a QA release gate at the source SOP stage.",
                "what_changes": "Material cannot be released from the changed SOP stage until the new postcheck/constraint is satisfied.",
                "scope": source_sop.get("stage_id"),
            }
        )
    if downstream_start_blocked:
        operational_effects.append(
            {
                "effect": "Blocks downstream stage start when the gate is not satisfied.",
                "what_changes": "Downstream execution must wait for QA disposition before consuming the released material.",
                "affected_stages": downstream_stage_names,
            }
        )
    if deviation_required:
        operational_effects.append(
            {
                "effect": "Turns exceptions into formal quality events.",
                "what_changes": "Alarms, quantity mismatches, or identity exceptions require deviation/CAPA review instead of simple operator acknowledgement.",
                "affected_records": [
                    "deviation_records",
                    "capa_records",
                    "quality_refs",
                ],
            }
        )
    if postcheck_tightened:
        operational_effects.append(
            {
                "effect": "Increases closeout evidence requirements.",
                "what_changes": "Operators and supervisors must capture stronger postcheck evidence before handoff.",
                "affected_records": [
                    "document_metadata",
                    "work_orders",
                    "part11_audit_trail",
                ],
            }
        )

    if not operational_effects:
        operational_effects.append(
            {
                "effect": "No material-flow effect detected.",
                "what_changes": "The proposed change stays local to the source SOP unless manually linked to additional stages.",
                "scope": source_sop.get("stage_id"),
            }
        )

    risk_effects = [
        {
            "risk": "Quality escape risk",
            "direction": (
                "decreases" if release_gate or deviation_required else "unchanged"
            ),
            "reason": (
                "The changed SOP requires QA disposition before downstream material movement."
                if release_gate
                else "No new QA gate was detected."
            ),
        },
        {
            "risk": "Cycle time / queue time",
            "direction": "increases" if release_gate else "unchanged",
            "reason": "Downstream work may wait for QA review and deviation/CAPA closure.",
        },
        {
            "risk": "Documentation burden",
            "direction": (
                "increases"
                if postcheck_tightened or deviation_required
                else "unchanged"
            ),
            "reason": "More evidence, signatures, and quality-event links are required.",
        },
    ]

    changes_needed: list[dict[str, Any]] = [
        {
            "area": "Source SOP",
            "change": "Revise the source SOP text, postchecks, constraints, and corrective-action trigger.",
            "why": "The desired result has to be encoded in the controlled instruction, not only described in the impact analysis.",
            "target_records": [_record_id(source_sop)],
            "approval_required": True,
        },
        {
            "area": "ProcessDefinition",
            "change": "Add or update the workflow gate that evaluates the new postcheck before material release.",
            "why": "The simulator and live workflow need an executable gate, otherwise downstream stages can still start from the old workflow.",
            "target_records": [
                "process-definitions",
                "process_definition_postchecks",
                "process_constraints",
            ],
            "approval_required": True,
        },
        {
            "area": "Audit and evidence",
            "change": "Require evidence capture, e-signature, and audit entries for the new decision point.",
            "why": "The desired result depends on proving who reviewed the exception, when, and on what evidence.",
            "target_records": [
                "document_metadata",
                "node_reference_links",
                "part11_audit_trail",
            ],
            "approval_required": False,
        },
    ]
    if downstream_stage_names:
        changes_needed.append(
            {
                "area": "Downstream SOPs",
                "change": "Update downstream start prechecks to require QA disposition/release from the source stage.",
                "why": "This is what prevents Tablet Compression, Capsule Filling, or later stages from consuming unreleased material.",
                "target_stages": downstream_stage_names,
                "target_stage_ids": downstream_stage_ids,
                "target_sop_count": len(
                    {
                        _record_id(sop)
                        for sop in linked_sops
                        if _text_value(
                            sop.get("stage_id")
                            or (sop.get("process_definition") or {}).get("stage_id")
                        )
                        in downstream_stage_ids
                    }
                ),
                "approval_required": True,
            }
        )
    if upstream_stage_names:
        changes_needed.append(
            {
                "area": "Upstream SOPs",
                "change": "Review upstream handoff and material identity checks for compatibility with the stricter downstream release gate.",
                "why": "Upstream stages may need clearer lot/quantity/identity evidence so the new gate can be satisfied without rework.",
                "target_stages": upstream_stage_names,
                "target_stage_ids": upstream_stage_ids,
                "target_sop_count": len(
                    {
                        _record_id(sop)
                        for sop in linked_sops
                        if _text_value(
                            sop.get("stage_id")
                            or (sop.get("process_definition") or {}).get("stage_id")
                        )
                        in upstream_stage_ids
                    }
                ),
                "approval_required": True,
            }
        )
    if deviation_required:
        changes_needed.append(
            {
                "area": "Quality workflows",
                "change": "Link deviation/CAPA creation, QA disposition, and effectiveness checks to the SOP exception path.",
                "why": "This turns the desired quality behavior into enforceable records and follow-up actions.",
                "target_records": [
                    "deviation_records",
                    "capa_records",
                    "quality_refs",
                    "corrective_actions",
                ],
                "approval_required": True,
            }
        )
    if release_gate:
        changes_needed.append(
            {
                "area": "Execution scheduling",
                "change": "Make downstream work orders/bookings wait for the QA release signal when the source gate is open.",
                "why": "Without this scheduling change, operators may still see downstream work as executable.",
                "target_records": ["work_orders", "calendar_bookings", "timesheets"],
                "approval_required": False,
            }
        )
    changes_needed.append(
        {
            "area": "Simulation",
            "change": "Rerun positive and negative simulations after the SOP/process changes are staged.",
            "why": "Positive run proves compliant material can still flow; negative run proves exceptions stop downstream release.",
            "target_records": [
                "world_model_simulation_runs",
                "world_model_simulation_audits",
            ],
            "approval_required": False,
        }
    )

    return {
        "summary": (
            "This change adds a controlled release gate and can delay downstream execution until QA disposition is complete."
            if release_gate
            else "This change is traceable, but no downstream release gate was detected from the proposed values."
        ),
        "changed_fields": changed_fields,
        "detected_controls": {
            "release_gate": release_gate,
            "deviation_or_capa_required": deviation_required,
            "postcheck_tightened": postcheck_tightened,
            "downstream_start_blocked_until_disposition": downstream_start_blocked,
        },
        "material_flow_effect": {
            "upstream_context_stages": upstream_stage_names,
            "upstream_context_stage_ids": upstream_stage_ids,
            "downstream_affected_stages": downstream_stage_names,
            "downstream_affected_stage_ids": downstream_stage_ids,
            "connections_analyzed": len(flows),
        },
        "operational_effects": operational_effects,
        "risk_effects": risk_effects,
        "changes_needed_to_get_desired_result": changes_needed,
        "records_and_workflows_affected": {
            "linked_sops_requiring_review": len(linked_sops),
            "required_change_actions": len(required_changes),
            "approval_required": bool(
                required_changes or release_gate or deviation_required
            ),
            "audit_required": True,
            "collections": [
                "sops",
                "process-definitions",
                "process_definition_postchecks",
                "process_constraints",
                "corrective_actions",
                "deviation_records",
                "capa_records",
                "node_reference_links",
            ],
        },
        "expected_operator_experience": (
            "The operator can complete the SOP only after recording the stricter postcheck evidence. "
            "If an alarm, mismatch, or identity exception exists, the downstream handoff pauses until QA disposition is recorded."
            if release_gate
            else "The operator sees the revised SOP text/checks, but downstream execution is not automatically paused by material-flow logic."
        ),
    }


def _sop_graph(
    source_sop: dict,
    source_stage_ids: list[str],
    flows: list[dict],
    linked_sops: list[dict],
) -> dict:
    nodes: list[dict] = []
    edges: list[dict] = []
    seen_nodes: set[tuple[str, str]] = set()

    def add_node(node_type: str, node_id: str | None, **properties: Any) -> None:
        if not node_id:
            return
        key = (node_type, node_id)
        if key in seen_nodes:
            return
        seen_nodes.add(key)
        nodes.append({"node_id": node_id, "node_type": node_type, **properties})

    source_sop_id = _record_id(source_sop)
    add_node("sop", source_sop_id, title=source_sop.get("title"), role="source")
    for stage_id in source_stage_ids:
        add_node("stage", stage_id, role="source_stage")
        edges.append(
            {
                "type": "HAS_SOP",
                "source_type": "stage",
                "source_id": stage_id,
                "target_type": "sop",
                "target_id": source_sop_id,
            }
        )

    for flow in flows:
        source_stage_id = flow.get("source_stage_id")
        target_stage_id = flow.get("target_stage_id")
        add_node("stage", source_stage_id, name=flow.get("source_stage_name"))
        add_node("stage", target_stage_id, name=flow.get("target_stage_name"))
        edges.append(
            {
                "type": "MATERIAL_FLOWS_TO",
                "source_type": "stage",
                "source_id": source_stage_id,
                "target_type": "stage",
                "target_id": target_stage_id,
                "flow_id": flow.get("flow_id"),
                "depth": flow.get("depth"),
            }
        )

    for sop in linked_sops:
        sop_id = _record_id(sop)
        stage_id = _text_value(
            sop.get("stage_id") or (sop.get("process_definition") or {}).get("stage_id")
        )
        add_node("sop", sop_id, title=sop.get("title"), role="impacted")
        if stage_id:
            add_node("stage", stage_id)
            edges.append(
                {
                    "type": "HAS_SOP",
                    "source_type": "stage",
                    "source_id": stage_id,
                    "target_type": "sop",
                    "target_id": sop_id,
                }
            )
        edges.append(
            {
                "type": "SOP_CHANGE_IMPACTS",
                "source_type": "sop",
                "source_id": source_sop_id,
                "target_type": "sop",
                "target_id": sop_id,
            }
        )
        edges.append(
            {
                "type": "REQUIRES_REVIEW",
                "source_type": "sop",
                "source_id": sop_id,
                "target_type": "change",
                "target_id": source_sop_id,
            }
        )

    return {"nodes": nodes, "edges": edges}


async def analyze_sop_change_impact(
    db: AsyncIOMotorDatabase,
    sop_id: str,
    proposed_values: dict,
    *,
    change_reason: str = "SOP revision impact analysis",
    include_upstream: bool = True,
    include_downstream: bool = True,
    max_depth: int = 2,
) -> dict:
    source_sop = await get_by_id(db, sop_id)
    if not source_sop:
        return {}

    source_sop_id = _record_id(source_sop) or sop_id
    source_stage_ids = await _lookup_stage_ids_for_sop(db, source_sop)
    production_connections, connected_stage_ids = await _material_flow_graph(
        db,
        source_stage_ids,
        include_upstream=include_upstream,
        include_downstream=include_downstream,
        max_depth=max_depth,
    )
    linked_sops = await _linked_sops_for_stages(
        db, connected_stage_ids, source_sop_id=source_sop_id
    )
    required_changes = [
        {
            "collection": COLLECTION,
            "entity_id": _record_id(sop),
            "entity_name": sop.get("title") or _record_id(sop),
            "reason": change_reason,
            "impact_type": "material_flow_linked_sop_review",
            "proposed_values": _impact_review_values(
                source_sop, sop, proposed_values, production_connections
            ),
        }
        for sop in linked_sops
        if _record_id(sop)
    ]
    effects = _change_effects(
        source_sop,
        proposed_values,
        production_connections,
        linked_sops,
        required_changes,
    )
    return {
        "source_sop": source_sop,
        "source_change": {
            "collection": COLLECTION,
            "entity_id": source_sop_id,
            "entity_name": source_sop.get("title") or source_sop_id,
            "reason": change_reason,
            "proposed_values": {**dict(proposed_values or {}), "updated_at": utc_now()},
        },
        "effects": effects,
        "production_connections": production_connections,
        "linked_sops": [
            {
                "id": _record_id(sop),
                "title": sop.get("title"),
                "stage_id": sop.get("stage_id")
                or (sop.get("process_definition") or {}).get("stage_id"),
                "workstation_id": sop.get("workstation_id")
                or (sop.get("process_definition") or {}).get("workstation_id"),
                "relationship_path": _relationship_path(sop, production_connections),
            }
            for sop in linked_sops
        ],
        "required_changes": required_changes,
        "sop_graph": _sop_graph(
            source_sop, source_stage_ids, production_connections, linked_sops
        ),
    }


async def create_sop_change_control(
    db: AsyncIOMotorDatabase,
    impact: dict,
    *,
    current_user: dict,
    change_type: str = "Major",
    risk_score: int | None = 45,
    approval_chain: list[dict] | None = None,
) -> dict:
    from services.auth.routers.auth import (
        _build_change_control_record,
        _create_proposed_change_nodes,
    )
    from services.common.approval_chains import (
        create_approval_tracking_and_notifications,
    )

    source_change = impact.get("source_change") or {}
    required_changes = impact.get("required_changes") or []
    proposed_changes = [source_change, *required_changes]
    affected_entities = [
        {
            "type": "sop",
            "collection": COLLECTION,
            "entity_id": change.get("entity_id"),
            "name": change.get("entity_name"),
        }
        for change in proposed_changes
        if change.get("entity_id")
    ]
    payload = {
        "title": f"SOP material-flow impact change: {source_change.get('entity_name') or source_change.get('entity_id')}",
        "description": "Source SOP revision with graph-derived downstream/upstream SOP review requirements.",
        "change_type": change_type,
        "reason": source_change.get("reason") or "SOP revision impact analysis",
        "justification": "Production connection and material-flow graph identified linked SOPs requiring review.",
        "affected_entity_class": "sops",
        "affected_entities": affected_entities,
        "affected_collections": [COLLECTION],
        "graph_impact_summary": {
            "source_sop": impact.get("source_sop"),
            "sop_graph": impact.get("sop_graph"),
            "production_connections": impact.get("production_connections"),
            "linked_sops": impact.get("linked_sops"),
        },
        "impact_assessment": "Linked SOP impact generated from production connections and material flow.",
        "proposed_changes": proposed_changes,
        "risk_score": risk_score,
        "validation_required": True,
        "sop_revision_required": True,
        "retraining_required": True,
        "status": "Open",
        "approval_chain": approval_chain or [],
    }
    record = await _build_change_control_record(db, payload, current_user)
    graph_relationships = list(record.get("graph_relationships") or [])
    graph_relationships.extend(impact.get("sop_graph", {}).get("edges") or [])
    record["graph_relationships"] = graph_relationships
    record["sop_change_impact_graph_id"] = f"sop-impact-graph-{uuid4().hex}"
    from services.common.compliance import sha256_hex

    record["record_hash"] = sha256_hex(record)
    await db[CHANGE_CONTROL_COLLECTION].insert_one(record)

    proposed_nodes = await _create_proposed_change_nodes(db, record, current_user)
    if proposed_nodes:
        await db[CHANGE_CONTROL_COLLECTION].update_one(
            {"change_control_id": record["change_control_id"]},
            {
                "$set": {
                    "proposed_change_nodes": proposed_nodes,
                    "proposed_change_node_ids": [
                        node.get("proposed_change_id") for node in proposed_nodes
                    ],
                    "live_node_touched": False,
                    "updated_at": utc_now(),
                }
            },
        )
        record["proposed_change_nodes"] = proposed_nodes
        record["proposed_change_node_ids"] = [
            node.get("proposed_change_id") for node in proposed_nodes
        ]
        record["live_node_touched"] = False

    approval_tokens = await create_approval_tracking_and_notifications(
        db,
        source_collection=CHANGE_CONTROL_COLLECTION,
        source_id=record["change_control_id"],
        source_title=record.get("title")
        or record.get("change_control_number")
        or record["change_control_id"],
        chain=record["approval_chain"],
        current_user_id=current_user.get("user_id") or current_user.get("sub"),
    )
    record["approval_token_records"] = approval_tokens
    return record


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


async def create(
    db: AsyncIOMotorDatabase,
    data: SOPCreate,
) -> dict:
    """Insert a new SOP document and return it."""
    doc = data.model_dump()
    await db[COLLECTION].insert_one(doc)
    return _normalize_sop_record(doc)


async def update(
    db: AsyncIOMotorDatabase,
    sop_id: str,
    data: SOPUpdate,
) -> Optional[dict]:
    """
    Partially update a SOP.
    Only non-None fields are written; returns the updated doc
    or None if no SOP matched.
    """
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        return await get_by_id(db, sop_id)
    result = await db[COLLECTION].find_one_and_update(
        {"id": sop_id},
        {"$set": fields},
        return_document=True,
    )
    return _normalize_sop_record(result) if result else None


async def update_process_definition(
    db: AsyncIOMotorDatabase,
    sop_id: str,
    process_definition: dict,
) -> Optional[dict]:
    """Replace the embedded process_definition sub-document."""
    result = await db[COLLECTION].find_one_and_update(
        {"id": sop_id},
        {"$set": {"process_definition": process_definition}},
        return_document=True,
    )
    return _normalize_sop_record(result) if result else None


async def update_prechecks(
    db: AsyncIOMotorDatabase,
    sop_id: str,
    prechecks: dict,
) -> Optional[dict]:
    """Replace the embedded prechecks sub-document."""
    result = await db[COLLECTION].find_one_and_update(
        {"id": sop_id},
        {"$set": {"prechecks": prechecks}},
        return_document=True,
    )
    return _normalize_sop_record(result) if result else None


async def update_postchecks(
    db: AsyncIOMotorDatabase,
    sop_id: str,
    postchecks: dict,
) -> Optional[dict]:
    """Replace the embedded postchecks sub-document."""
    result = await db[COLLECTION].find_one_and_update(
        {"id": sop_id},
        {"$set": {"postchecks": postchecks}},
        return_document=True,
    )
    return _normalize_sop_record(result) if result else None


async def update_constraints(
    db: AsyncIOMotorDatabase,
    sop_id: str,
    constraints: dict,
) -> Optional[dict]:
    """Replace the embedded constraints sub-document."""
    result = await db[COLLECTION].find_one_and_update(
        {"id": sop_id},
        {"$set": {"constraints": constraints}},
        return_document=True,
    )
    return _normalize_sop_record(result) if result else None


async def update_corrective_actions(
    db: AsyncIOMotorDatabase,
    sop_id: str,
    corrective_actions: dict,
) -> Optional[dict]:
    """Replace the embedded corrective_actions sub-document."""
    result = await db[COLLECTION].find_one_and_update(
        {"id": sop_id},
        {"$set": {"corrective_actions": corrective_actions}},
        return_document=True,
    )
    return _normalize_sop_record(result) if result else None


async def add_reference(
    db: AsyncIOMotorDatabase,
    sop_id: str,
    reference: str,
) -> Optional[dict]:
    """Append a new URL/document reference to the references array."""
    result = await db[COLLECTION].find_one_and_update(
        {"id": sop_id},
        {"$addToSet": {"references": reference}},
        return_document=True,
    )
    return _normalize_sop_record(result) if result else None


async def remove_reference(
    db: AsyncIOMotorDatabase,
    sop_id: str,
    reference: str,
) -> Optional[dict]:
    """Remove a specific reference from the references array."""
    result = await db[COLLECTION].find_one_and_update(
        {"id": sop_id},
        {"$pull": {"references": reference}},
        return_document=True,
    )
    return _normalize_sop_record(result) if result else None


async def delete(
    db: AsyncIOMotorDatabase,
    sop_id: str,
) -> bool:
    """Delete a SOP by ID. Returns True if a document was removed."""
    result = await db[COLLECTION].delete_one({"id": sop_id})
    return result.deleted_count == 1
