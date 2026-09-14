import json
import logging
import re
from datetime import datetime, timezone
from importlib import import_module
from typing import Any
from uuid import uuid4

from services.common.config import settings
from services.common.ontology_registry import ontology_entity_for_collection

logger = logging.getLogger("uvicorn.error")
_driver = None


# Credentials must NEVER be mirrored into the graph store. Neo4j is a SECONDARY store with
# its own (often broader) access controls and is queried for analytics — a mirrored password
# hash is an offline-cracking target and a mirrored refresh token is a session-hijack primitive.
# The users mirror previously copied the whole Mongo document, hashed_password and
# refresh_tokens included. Enforced here so EVERY collection is covered, now and in future.
_SENSITIVE_FIELDS = frozenset(
    {
        "password",
        "hashed_password",
        "password_hash",
        "passwordhash",
        "refresh_token",
        "refresh_tokens",
        "access_token",
        "token",
        "tokens",
        "secret",
        "client_secret",
        "api_key",
        "apikey",
        "private_key",
        "salt",
        "otp_secret",
        "mfa_secret",
        "totp_secret",
        "session_token",
    }
)


def strip_sensitive(document: dict[str, Any]) -> dict[str, Any]:
    """Remove credential-bearing fields before a document leaves Mongo for the graph store."""
    if not isinstance(document, dict):
        return document
    removed = [k for k in document if k.lower() in _SENSITIVE_FIELDS]
    if removed:
        logger.debug("neo4j mirror: stripped sensitive fields %s", sorted(removed))
    return {k: v for k, v in document.items() if k.lower() not in _SENSITIVE_FIELDS}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def _collection_type(collection: str) -> str:
    collection_type = collection
    for prefix in ("industrial_", "pharmaceutical_", "warehouse_"):
        if collection_type.startswith(prefix):
            collection_type = collection_type.removeprefix(prefix)
            break
    return _singular(collection_type)


def _document_key(collection: str, document: dict[str, Any]) -> str:
    collection_id_key = f"{_collection_type(collection)}_id"
    preferred_keys = (
        collection_id_key,
        "id",
        "server_id",
        "edge_server_id",
        "board_id",
        "checklist_id",
        "executed_bmr_record_id",
        "executed_bpr_record_id",
        "executed_instruction_evidence_id",
        "ipc_result_id",
        "batch_execution_record_id",
        "batch_id",
        "work_order_id",
        "plant_id",
        "building_id",
        "floor_id",
        "room_id",
        "bay_id",
        "line_id",
        "stage_id",
        "workstation_id",
        "workflow_id",
        "layout_id",
        "step_id",
        "event_id",
        "pricing_id",
        "product_id",
        "sensor_id",
        "reading_id",
        "machine_id",
        "equipment_id",
        "user_id",
        "role_id",
        "permission_id",
        "team_id",
        "department_id",
        "customer_id",
        "supplier_id",
        "inventory_id",
        "document_id",
        "report_template_id",
        "mbmr_template_id",
        "packing_instruction_id",
        "proposed_change_id",
    )

    for key in preferred_keys:
        value = document.get(key)
        if value is not None:
            return str(value)

    for key, value in document.items():
        if key.endswith("_id") and value is not None:
            return str(value)

    value = document.get("_id")
    if value is not None:
        return str(value)
    return str(uuid4())


def _singular(value: str) -> str:
    singular_overrides = {
        "bays": "bay",
        "classes": "class",
        "customs_declarations": "customs_declaration",
        "matrices": "matrix",
        "changeover_matrices": "changeover_matrix",
        "permissions": "permission",
        "roles": "role",
        "shifts": "shift",
        "warehouses": "warehouse",
    }
    if value in singular_overrides:
        return singular_overrides[value]
    if value.endswith("ies"):
        return f"{value[:-3]}y"
    if value.endswith("ses"):
        return value[:-2]
    if value.endswith("s") and not value.endswith("ss"):
        return value[:-1]
    return value


def _document_type(collection: str, entity_id: str) -> str:
    parts = [part for part in entity_id.split(":") if part]
    if len(parts) >= 2:
        return _singular(parts[-2])
    return _collection_type(collection)


def _document_name(entity_id: str) -> str:
    return entity_id.split(":")[-1] if ":" in entity_id else entity_id


def _display_name(document: dict[str, Any], entity_id: str) -> str:
    for key in (
        "name",
        "title",
        "product_name",
        "customer_name",
        "supplier_name",
        "machine_name",
        "equipment_name",
        "workstation_name",
        "line_name",
        "stage_name",
    ):
        value = document.get(key)
        if value:
            return str(value)
    return _document_name(entity_id)


def _neo4j_label(node_type: str) -> str:
    parts = re.findall(r"[A-Za-z0-9]+", node_type)
    label = "".join(part[:1].upper() + part[1:] for part in parts)
    if not label or not label[0].isalpha():
        return "Record"
    return label


def _as_id_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None and str(item)]
    if isinstance(value, tuple | set):
        return [str(item) for item in value if item is not None and str(item)]
    if str(value):
        return [str(value)]
    return []


def _add_relationship(
    relationships: list[dict[str, str]],
    source: Any,
    rel_type: str,
    target: Any,
    current_entity_id: str,
) -> None:
    for source_id in _as_id_list(source):
        for target_id in _as_id_list(target):
            if source_id == target_id:
                continue
            relationships.append(
                {
                    "source": source_id,
                    "type": rel_type,
                    "target": target_id,
                    "owner": current_entity_id,
                }
            )


def _registry_relationships(
    collection: str, document: dict[str, Any], entity_id: str
) -> list[dict[str, str]]:
    entity = ontology_entity_for_collection(collection)
    if not entity:
        return []
    relationships: list[dict[str, str]] = []
    for rule in entity.get("relationships") or []:
        rel_type = rule.get("type")
        if not rel_type:
            continue
        source = (
            entity_id
            if rule.get("source") == "self"
            else document.get(str(rule.get("source_field") or ""))
        )
        target = (
            entity_id
            if rule.get("target") == "self"
            else document.get(str(rule.get("target_field") or ""))
        )
        _add_relationship(relationships, source, str(rel_type), target, entity_id)
    return relationships


def _document_relationships(
    collection: str, document: dict[str, Any], entity_id: str
) -> list[dict[str, str]]:
    relationships: list[dict[str, str]] = _registry_relationships(
        collection, document, entity_id
    )

    if collection == "industrial_lines":
        _add_relationship(
            relationships, document.get("plant_id"), "HAS_LINE", entity_id, entity_id
        )
    elif collection == "industrial_stages":
        _add_relationship(
            relationships, document.get("line_id"), "HAS_STAGE", entity_id, entity_id
        )
    elif collection == "workstations":
        _add_relationship(
            relationships,
            document.get("line_id"),
            "HAS_WORKSTATION",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("stage_id"),
            "HAS_WORKSTATION",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "OPERATED_BY",
            document.get("operator_id"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "SUPERVISED_BY",
            document.get("supervisor_id"),
            entity_id,
        )
    elif collection == "devices":
        _add_relationship(
            relationships,
            document.get("assigned_to"),
            "ASSIGNED_DEVICE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships, document.get("user_id"), "USES_DEVICE", entity_id, entity_id
        )
    elif collection == "sensors":
        _add_relationship(
            relationships,
            document.get("machine_id"),
            "HAS_SENSOR",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("equipment_id"),
            "HAS_SENSOR",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("edge_server_id"),
            "COLLECTS_SENSOR",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("edge_server_id"),
            "ROUTES_SENSOR_TELEMETRY",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships, document.get("asset_tag"), "HAS_SENSOR", entity_id, entity_id
        )
        _add_relationship(
            relationships,
            entity_id,
            "PUBLISHES_TO_TOPIC",
            document.get("topic"),
            entity_id,
        )
        _add_relationship(
            relationships, entity_id, "MEASURES", document.get("sensor_type"), entity_id
        )
    elif collection in {"sensor_readings", "iot_sensor_readings"}:
        _add_relationship(
            relationships,
            document.get("sensor_id"),
            "HAS_SENSOR_READING",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("machine_id"),
            "HAS_SENSOR_READING",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("equipment_id"),
            "HAS_SENSOR_READING",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("edge_server_id"),
            "FORWARDED_SENSOR_READING",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "READING_OF_SENSOR",
            document.get("sensor_id"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "READING_FOR_MACHINE",
            document.get("machine_id"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "READING_FOR_EQUIPMENT",
            document.get("equipment_id"),
            entity_id,
        )
    elif collection in {"ble_devices", "uwb_devices"}:
        _add_relationship(
            relationships,
            document.get("assigned_to") or document.get("user_id"),
            "USES_IOT_DEVICE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("plant_id"),
            "HAS_IOT_DEVICE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("line_id"),
            "HAS_IOT_DEVICE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("stage_id"),
            "HAS_IOT_DEVICE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("workstation_id"),
            "HAS_IOT_DEVICE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("machine_id"),
            "HAS_IOT_DEVICE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("equipment_id"),
            "HAS_IOT_DEVICE",
            entity_id,
            entity_id,
        )
        for tag in document.get("tags") or []:
            _add_relationship(relationships, entity_id, "HAS_IOT_TAG", tag, entity_id)
    elif collection == "pharmaceutical_machines":
        _add_relationship(
            relationships, document.get("plant_id"), "HAS_MACHINE", entity_id, entity_id
        )
        _add_relationship(
            relationships, document.get("line_id"), "HAS_MACHINE", entity_id, entity_id
        )
        _add_relationship(
            relationships, document.get("stage_id"), "HAS_MACHINE", entity_id, entity_id
        )
        _add_relationship(
            relationships,
            document.get("workstation_id"),
            "HAS_MACHINE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("family_id"),
            "HAS_MACHINE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships, document.get("class_id"), "HAS_MACHINE", entity_id, entity_id
        )
        _add_relationship(
            relationships,
            document.get("subclass_id"),
            "HAS_MACHINE",
            entity_id,
            entity_id,
        )
    elif collection == "pharmaceutical_equipment":
        _add_relationship(
            relationships,
            document.get("plant_id"),
            "HAS_EQUIPMENT",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("line_id"),
            "HAS_EQUIPMENT",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("stage_id"),
            "HAS_EQUIPMENT",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("workstation_id"),
            "HAS_EQUIPMENT",
            entity_id,
            entity_id,
        )
    elif collection == "constraints":
        _add_relationship(
            relationships,
            document.get("plant_id"),
            "HAS_CONSTRAINT",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("line_id"),
            "HAS_CONSTRAINT",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("stage_id"),
            "HAS_CONSTRAINT",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("workstation_id"),
            "HAS_CONSTRAINT",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("machine_id"),
            "HAS_CONSTRAINT",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("equipment_id"),
            "HAS_CONSTRAINT",
            entity_id,
            entity_id,
        )
    elif collection in {"process_constraints", "process_step_constraints"}:
        _add_relationship(
            relationships,
            document.get("process_definition_id"),
            "HAS_CONSTRAINT_DEFINITION",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("process_step_id"),
            "HAS_STEP_CONSTRAINT_DEFINITION",
            entity_id,
            entity_id,
        )
        for constraint in document.get("constraints") or []:
            if not isinstance(constraint, dict):
                continue
            constraint_id = constraint.get("id")
            _add_relationship(
                relationships, entity_id, "DEFINES_CONSTRAINT", constraint_id, entity_id
            )
            _add_relationship(
                relationships,
                document.get("process_definition_id"),
                "HAS_CONSTRAINT",
                constraint_id,
                entity_id,
            )
            _add_relationship(
                relationships,
                document.get("process_step_id"),
                "HAS_STEP_CONSTRAINT",
                constraint_id,
                entity_id,
            )
    elif collection in {"process_definition_prechecks", "process_step_prechecks"}:
        _add_relationship(
            relationships,
            document.get("process_definition_id"),
            "HAS_PRECHECK_DEFINITION",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("process_step_id"),
            "HAS_STEP_PRECHECK_DEFINITION",
            entity_id,
            entity_id,
        )
        for condition in document.get("conditions") or []:
            if not isinstance(condition, dict):
                continue
            condition_id = condition.get("id")
            _add_relationship(
                relationships, entity_id, "DEFINES_PRECHECK", condition_id, entity_id
            )
            _add_relationship(
                relationships,
                document.get("process_definition_id"),
                "HAS_PRECHECK",
                condition_id,
                entity_id,
            )
            _add_relationship(
                relationships,
                document.get("process_step_id"),
                "HAS_STEP_PRECHECK",
                condition_id,
                entity_id,
            )
    elif collection in {"process_definition_postchecks", "process_step_postchecks"}:
        _add_relationship(
            relationships,
            document.get("process_definition_id"),
            "HAS_POSTCHECK_DEFINITION",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("process_step_id"),
            "HAS_STEP_POSTCHECK_DEFINITION",
            entity_id,
            entity_id,
        )
        for condition in document.get("conditions") or []:
            if not isinstance(condition, dict):
                continue
            condition_id = condition.get("id")
            _add_relationship(
                relationships, entity_id, "DEFINES_POSTCHECK", condition_id, entity_id
            )
            _add_relationship(
                relationships,
                document.get("process_definition_id"),
                "HAS_POSTCHECK",
                condition_id,
                entity_id,
            )
            _add_relationship(
                relationships,
                document.get("process_step_id"),
                "HAS_STEP_POSTCHECK",
                condition_id,
                entity_id,
            )
            for corrective_action_id in (
                condition.get("links_to_corrective_action_ids") or []
            ):
                _add_relationship(
                    relationships,
                    condition_id,
                    "REMEDIATED_BY",
                    corrective_action_id,
                    entity_id,
                )
    elif collection == "corrective_actions":
        _add_relationship(
            relationships,
            document.get("process_definition_id"),
            "HAS_CORRECTIVE_ACTION_DEFINITION",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("process_step_id"),
            "HAS_STEP_CORRECTIVE_ACTION_DEFINITION",
            entity_id,
            entity_id,
        )
        for action in document.get("actions") or []:
            if not isinstance(action, dict):
                continue
            action_id = action.get("id")
            _add_relationship(
                relationships,
                entity_id,
                "DEFINES_CORRECTIVE_ACTION",
                action_id,
                entity_id,
            )
            for postcheck_id in action.get("applies_to_postchecks") or []:
                _add_relationship(
                    relationships, postcheck_id, "REMEDIATED_BY", action_id, entity_id
                )
    elif collection == "work_orders":
        _add_relationship(
            relationships,
            document.get("machine_id"),
            "HAS_WORK_ORDER",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("equipment_id"),
            "HAS_WORK_ORDER",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("workstation_id"),
            "HAS_WORK_ORDER",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "ASSIGNED_TO",
            document.get("assigned_to_id"),
            entity_id,
        )
    elif collection in {"process_definitions", "process-definitions"}:
        _add_relationship(
            relationships,
            document.get("bom_id"),
            "USES_PROCESS_DEFINITION",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("plant_id"),
            "HAS_PROCESS_DEFINITION",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("line_id"),
            "HAS_PROCESS_DEFINITION",
            entity_id,
            entity_id,
        )
        for requirement in document.get("cleaning_requirements") or []:
            if not isinstance(requirement, dict):
                continue
            requirement_id = requirement.get("cleaning_requirement_id")
            _add_relationship(
                relationships, entity_id, "REQUIRES_CLEANING", requirement_id, entity_id
            )
            _add_relationship(
                relationships,
                requirement.get("process_step_id"),
                "REQUIRES_CLEANING",
                requirement_id,
                entity_id,
            )
            _add_relationship(
                relationships,
                requirement.get("stage_id"),
                "REQUIRES_CLEANING",
                requirement_id,
                entity_id,
            )
            _add_relationship(
                relationships,
                requirement.get("workstation_id"),
                "REQUIRES_CLEANING",
                requirement_id,
                entity_id,
            )
            _add_relationship(
                relationships,
                requirement.get("equipment_id"),
                "REQUIRES_CLEANING",
                requirement_id,
                entity_id,
            )
            for equipment_id in requirement.get("equipment_ids") or []:
                _add_relationship(
                    relationships,
                    equipment_id,
                    "REQUIRES_CLEANING",
                    requirement_id,
                    entity_id,
                )
            _add_relationship(
                relationships,
                requirement_id,
                "CONTROLLED_BY_DOCUMENT",
                requirement.get("sop_document_id"),
                entity_id,
            )
            for document_id in requirement.get("derived_from_sop_document_ids") or []:
                _add_relationship(
                    relationships,
                    requirement_id,
                    "CONTROLLED_BY_DOCUMENT",
                    document_id,
                    entity_id,
                )
        for template in document.get("instruction_templates") or []:
            if not isinstance(template, dict):
                continue
            template_id = template.get("instruction_template_id")
            _add_relationship(
                relationships,
                entity_id,
                "HAS_INSTRUCTION_TEMPLATE",
                template_id,
                entity_id,
            )
            for step in template.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                step_id = step.get("instruction_step_id")
                _add_relationship(
                    relationships,
                    template_id,
                    "HAS_INSTRUCTION_STEP",
                    step_id,
                    entity_id,
                )
                for requirement_id in step.get("cleaning_requirement_ids") or []:
                    _add_relationship(
                        relationships,
                        step_id,
                        "REQUIRES_CLEANING",
                        requirement_id,
                        entity_id,
                    )
    elif collection == "production_batches":
        _add_relationship(
            relationships,
            document.get("work_order_id"),
            "HAS_PRODUCTION_BATCH",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("product_id"),
            "HAS_PRODUCTION_BATCH",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships, document.get("bom_id"), "USED_BY_BATCH", entity_id, entity_id
        )
        _add_relationship(
            relationships,
            document.get("process_definition_id"),
            "GOVERNS_BATCH",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("process_route_id"),
            "GOVERNS_BATCH",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("plant_id"),
            "HAS_PRODUCTION_BATCH",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("line_id"),
            "HAS_PRODUCTION_BATCH",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("current_stage_id"),
            "HAS_PRODUCTION_BATCH",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("current_workstation_id"),
            "HAS_PRODUCTION_BATCH",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "QA_REVIEWED_BY",
            document.get("qa_reviewer_id"),
            entity_id,
        )
        for execution_id in document.get("batch_execution_record_ids") or []:
            _add_relationship(
                relationships,
                entity_id,
                "HAS_BATCH_EXECUTION_RECORD",
                execution_id,
                entity_id,
            )
    elif collection == "batch_production_execution_records":
        _add_relationship(
            relationships,
            document.get("batch_id"),
            "HAS_BATCH_EXECUTION_RECORD",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("work_order_id"),
            "HAS_BATCH_EXECUTION_RECORD",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("process_definition_id"),
            "EXECUTES_PROCESS_DEFINITION",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("recipe_id"),
            "USES_RECIPE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships, document.get("bom_id"), "USES_BOM", entity_id, entity_id
        )
        _add_relationship(
            relationships,
            document.get("product_id"),
            "PRODUCES_PRODUCT",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("plant_id"),
            "HAS_BATCH_EXECUTION_RECORD",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("line_id"),
            "HAS_BATCH_EXECUTION_RECORD",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("stage_id"),
            "HAS_BATCH_EXECUTION_RECORD",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("workstation_id"),
            "HAS_BATCH_EXECUTION_RECORD",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "OPERATED_BY",
            document.get("operator_id"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "SUPERVISED_BY",
            document.get("supervisor_id"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "QA_REVIEWED_BY",
            document.get("qa_reviewer_id"),
            entity_id,
        )
        for machine_id in document.get("machine_ids") or []:
            _add_relationship(
                relationships,
                machine_id,
                "HAS_BATCH_EXECUTION_RECORD",
                entity_id,
                entity_id,
            )
        for equipment_id in document.get("equipment_ids") or []:
            _add_relationship(
                relationships,
                equipment_id,
                "HAS_BATCH_EXECUTION_RECORD",
                entity_id,
                entity_id,
            )
        metadata = document.get("metadata") or {}
        _add_relationship(
            relationships,
            entity_id,
            "HAS_EXECUTED_BMR",
            metadata.get("executed_bmr_record_id"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "HAS_EXECUTED_BMR_DOCUMENT",
            metadata.get("executed_bmr_document_id"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "HAS_EXECUTED_BPR",
            metadata.get("executed_bpr_record_id"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "HAS_EXECUTED_BPR_DOCUMENT",
            metadata.get("executed_bpr_document_id"),
            entity_id,
        )
        for requirement_id in metadata.get("required_cleaning_requirement_ids") or []:
            _add_relationship(
                relationships, entity_id, "REQUIRES_CLEANING", requirement_id, entity_id
            )
        cleaning_map = metadata.get("cleaning_requirement_record_map")
        if isinstance(cleaning_map, dict):
            for requirement_id, cleaning_record_id in cleaning_map.items():
                _add_relationship(
                    relationships,
                    requirement_id,
                    "SATISFIED_BY_CLEANING_RECORD",
                    cleaning_record_id,
                    entity_id,
                )
                _add_relationship(
                    relationships,
                    entity_id,
                    "HAS_REQUIRED_CLEANING_RECORD",
                    cleaning_record_id,
                    entity_id,
                )
        for sop_id in document.get("sop_ids") or []:
            _add_relationship(relationships, entity_id, "USES_SOP", sop_id, entity_id)
        for document_id in [
            *(document.get("sop_document_ids") or []),
            *(document.get("evidence_document_ids") or []),
        ]:
            _add_relationship(
                relationships,
                entity_id,
                "HAS_DOCUMENT_EVIDENCE",
                document_id,
                entity_id,
            )
        for weighing in document.get("dispense_weighing_records") or []:
            if not isinstance(weighing, dict):
                continue
            _add_relationship(
                relationships,
                entity_id,
                "HAS_DISPENSE_WEIGHING_RECORD",
                weighing.get("dispense_weighing_id"),
                entity_id,
            )
            _add_relationship(
                relationships,
                entity_id,
                "HAS_BMR_EVIDENCE",
                weighing.get("bmr_document_id"),
                entity_id,
            )
            _add_relationship(
                relationships,
                entity_id,
                "DISPENSES_MATERIAL",
                weighing.get("material_id"),
                entity_id,
            )
            _add_relationship(
                relationships,
                entity_id,
                "USES_MATERIAL_LOT",
                weighing.get("material_lot_id"),
                entity_id,
            )
            _add_relationship(
                relationships,
                entity_id,
                "USES_SCALE_EQUIPMENT",
                weighing.get("scale_equipment_id"),
                entity_id,
            )
    elif collection == "executed_bmr_records":
        _add_relationship(
            relationships,
            document.get("batch_execution_record_id"),
            "HAS_EXECUTED_BMR",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("batch_id"),
            "HAS_EXECUTED_BMR",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "HAS_EXECUTED_BMR_DOCUMENT",
            document.get("executed_bmr_document_id"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "GENERATED_FROM_MBMR_TEMPLATE",
            document.get("mbmr_template_id"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "GENERATED_FROM_MBMR_DOCUMENT",
            document.get("mbmr_document_id"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "GENERATED_FROM_BMR_TEMPLATE",
            document.get("bmr_template_id"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "GENERATED_FROM_BMR_TEMPLATE_DOCUMENT",
            document.get("bmr_template_document_id"),
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("process_definition_id"),
            "HAS_EXECUTED_BMR",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("process_route_id"),
            "HAS_EXECUTED_BMR",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("bom_id"),
            "HAS_EXECUTED_BMR",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("product_id"),
            "HAS_EXECUTED_BMR",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("plant_id"),
            "HAS_EXECUTED_BMR",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("line_id"),
            "HAS_EXECUTED_BMR",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "EXECUTED_BY",
            document.get("executed_by"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "REVIEWED_BY",
            document.get("reviewed_by"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "APPROVED_BY",
            document.get("approved_by"),
            entity_id,
        )
        for step_execution_id in document.get("batch_step_execution_ids") or []:
            _add_relationship(
                relationships,
                entity_id,
                "INCLUDES_BATCH_STEP_EXECUTION",
                step_execution_id,
                entity_id,
            )
        for cleaning_record_id in document.get("cleaning_record_ids") or []:
            _add_relationship(
                relationships,
                entity_id,
                "INCLUDES_CLEANING_RECORD",
                cleaning_record_id,
                entity_id,
            )
        metadata = document.get("metadata") or {}
        for requirement_id in metadata.get("required_cleaning_requirement_ids") or []:
            _add_relationship(
                relationships, entity_id, "REQUIRES_CLEANING", requirement_id, entity_id
            )
        cleaning_map = metadata.get("cleaning_requirement_record_map")
        if isinstance(cleaning_map, dict):
            for requirement_id, cleaning_record_id in cleaning_map.items():
                _add_relationship(
                    relationships,
                    requirement_id,
                    "SATISFIED_BY_CLEANING_RECORD",
                    cleaning_record_id,
                    entity_id,
                )
                _add_relationship(
                    relationships,
                    entity_id,
                    "HAS_REQUIRED_CLEANING_RECORD",
                    cleaning_record_id,
                    entity_id,
                )
        for usage_id in document.get("equipment_usage_ids") or []:
            _add_relationship(
                relationships,
                entity_id,
                "INCLUDES_EQUIPMENT_USAGE",
                usage_id,
                entity_id,
            )
        for usage_id in document.get("area_room_usage_ids") or []:
            _add_relationship(
                relationships,
                entity_id,
                "INCLUDES_AREA_ROOM_USAGE",
                usage_id,
                entity_id,
            )
        for reconciliation_id in document.get("yield_reconciliation_record_ids") or []:
            _add_relationship(
                relationships,
                entity_id,
                "INCLUDES_YIELD_RECONCILIATION",
                reconciliation_id,
                entity_id,
            )
        for registry_id in document.get("cpp_cqa_registry_ids") or []:
            _add_relationship(
                relationships,
                entity_id,
                "INCLUDES_CPP_CQA_RECORD",
                registry_id,
                entity_id,
            )
        for document_id in document.get("evidence_document_ids") or []:
            _add_relationship(
                relationships,
                entity_id,
                "HAS_DOCUMENT_EVIDENCE",
                document_id,
                entity_id,
            )
        for evidence_id in document.get("executed_instruction_evidence_ids") or []:
            _add_relationship(
                relationships,
                entity_id,
                "HAS_EXECUTED_INSTRUCTION_EVIDENCE",
                evidence_id,
                entity_id,
            )
        for report_template_id in metadata.get("report_template_ids") or []:
            _add_relationship(
                relationships,
                entity_id,
                "USES_REPORT_TEMPLATE",
                report_template_id,
                entity_id,
            )
        for section in document.get("sections") or []:
            if not isinstance(section, dict):
                continue
            _add_relationship(
                relationships,
                entity_id,
                "HAS_EXECUTED_BMR_SECTION",
                section.get("section_execution_id"),
                entity_id,
            )
            _add_relationship(
                relationships,
                section.get("template_section_id"),
                "EXECUTED_AS_BMR_SECTION",
                section.get("section_execution_id"),
                entity_id,
            )
            _add_relationship(
                relationships,
                section.get("stage_id"),
                "HAS_EXECUTED_BMR_SECTION",
                section.get("section_execution_id"),
                entity_id,
            )
            _add_relationship(
                relationships,
                section.get("workstation_id"),
                "EXECUTED_BMR_SECTION_AT",
                section.get("section_execution_id"),
                entity_id,
            )
            _add_relationship(
                relationships,
                section.get("batch_step_execution_id"),
                "RECORDED_IN_BMR_SECTION",
                section.get("section_execution_id"),
                entity_id,
            )
            for report_template_id in section.get("report_template_ids") or []:
                _add_relationship(
                    relationships,
                    section.get("section_execution_id"),
                    "USES_REPORT_TEMPLATE",
                    report_template_id,
                    entity_id,
                )
            for report_section_id in section.get("report_template_section_ids") or []:
                _add_relationship(
                    relationships,
                    section.get("section_execution_id"),
                    "BINDS_REPORT_SECTION",
                    report_section_id,
                    entity_id,
                )
            for evidence_id in section.get("executed_instruction_evidence_ids") or []:
                _add_relationship(
                    relationships,
                    section.get("section_execution_id"),
                    "HAS_EXECUTED_INSTRUCTION_EVIDENCE",
                    evidence_id,
                    entity_id,
                )
    elif collection == "executed_bpr_records":
        _add_relationship(
            relationships,
            document.get("batch_execution_record_id"),
            "HAS_EXECUTED_BPR",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("batch_id"),
            "HAS_EXECUTED_BPR",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "HAS_EXECUTED_BPR_DOCUMENT",
            document.get("executed_bpr_document_id"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "GENERATED_FROM_PACKING_INSTRUCTION",
            document.get("packing_instruction_id"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "GENERATED_FROM_BPR_TEMPLATE",
            document.get("instruction_template_id"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "GENERATED_FROM_BPR_TEMPLATE_DOCUMENT",
            document.get("bpr_template_document_id"),
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("process_definition_id"),
            "HAS_EXECUTED_BPR",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("bpr_process_definition_id"),
            "HAS_EXECUTED_BPR",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("bom_id"),
            "HAS_EXECUTED_BPR",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("product_id"),
            "HAS_EXECUTED_BPR",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("plant_id"),
            "HAS_EXECUTED_BPR",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("line_id"),
            "HAS_EXECUTED_BPR",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "EXECUTED_BY",
            document.get("executed_by"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "REVIEWED_BY",
            document.get("reviewed_by"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "APPROVED_BY",
            document.get("approved_by"),
            entity_id,
        )
        for cleaning_record_id in document.get("cleaning_record_ids") or []:
            _add_relationship(
                relationships,
                entity_id,
                "INCLUDES_CLEANING_RECORD",
                cleaning_record_id,
                entity_id,
            )
        metadata = document.get("metadata") or {}
        for requirement_id in metadata.get("required_cleaning_requirement_ids") or []:
            _add_relationship(
                relationships, entity_id, "REQUIRES_CLEANING", requirement_id, entity_id
            )
        cleaning_map = metadata.get("cleaning_requirement_record_map")
        if isinstance(cleaning_map, dict):
            for requirement_id, cleaning_record_id in cleaning_map.items():
                _add_relationship(
                    relationships,
                    requirement_id,
                    "SATISFIED_BY_CLEANING_RECORD",
                    cleaning_record_id,
                    entity_id,
                )
                _add_relationship(
                    relationships,
                    entity_id,
                    "HAS_REQUIRED_CLEANING_RECORD",
                    cleaning_record_id,
                    entity_id,
                )
        for usage_id in document.get("equipment_usage_ids") or []:
            _add_relationship(
                relationships,
                entity_id,
                "INCLUDES_EQUIPMENT_USAGE",
                usage_id,
                entity_id,
            )
        for usage_id in document.get("area_room_usage_ids") or []:
            _add_relationship(
                relationships,
                entity_id,
                "INCLUDES_AREA_ROOM_USAGE",
                usage_id,
                entity_id,
            )
        for reconciliation_id in document.get("yield_reconciliation_record_ids") or []:
            _add_relationship(
                relationships,
                entity_id,
                "INCLUDES_YIELD_RECONCILIATION",
                reconciliation_id,
                entity_id,
            )
        for registry_id in document.get("cpp_cqa_registry_ids") or []:
            _add_relationship(
                relationships,
                entity_id,
                "INCLUDES_CPP_CQA_RECORD",
                registry_id,
                entity_id,
            )
        for document_id in document.get("evidence_document_ids") or []:
            _add_relationship(
                relationships,
                entity_id,
                "HAS_DOCUMENT_EVIDENCE",
                document_id,
                entity_id,
            )
        for report_template_id in metadata.get("report_template_ids") or []:
            _add_relationship(
                relationships,
                entity_id,
                "USES_REPORT_TEMPLATE",
                report_template_id,
                entity_id,
            )
        for step in document.get("steps") or []:
            if not isinstance(step, dict):
                continue
            _add_relationship(
                relationships,
                entity_id,
                "HAS_EXECUTED_BPR_STEP",
                step.get("step_execution_id"),
                entity_id,
            )
            _add_relationship(
                relationships,
                step.get("template_step_id"),
                "EXECUTED_AS_BPR_STEP",
                step.get("step_execution_id"),
                entity_id,
            )
            _add_relationship(
                relationships,
                step.get("stage_id"),
                "HAS_EXECUTED_BPR_STEP",
                step.get("step_execution_id"),
                entity_id,
            )
            _add_relationship(
                relationships,
                step.get("workstation_id"),
                "EXECUTED_BPR_STEP_AT",
                step.get("step_execution_id"),
                entity_id,
            )
            for report_template_id in step.get("report_template_ids") or []:
                _add_relationship(
                    relationships,
                    step.get("step_execution_id"),
                    "USES_REPORT_TEMPLATE",
                    report_template_id,
                    entity_id,
                )
            for report_section_id in step.get("report_template_section_ids") or []:
                _add_relationship(
                    relationships,
                    step.get("step_execution_id"),
                    "BINDS_REPORT_SECTION",
                    report_section_id,
                    entity_id,
                )
            for evidence_id in step.get("executed_instruction_evidence_ids") or []:
                _add_relationship(
                    relationships,
                    step.get("step_execution_id"),
                    "HAS_EXECUTED_INSTRUCTION_EVIDENCE",
                    evidence_id,
                    entity_id,
                )
    elif collection == "batch_step_executions":
        _add_relationship(
            relationships,
            document.get("batch_execution_record_id"),
            "HAS_BATCH_STEP_EXECUTION",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("batch_id"),
            "HAS_BATCH_STEP_EXECUTION",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("process_definition_id"),
            "HAS_BATCH_STEP_EXECUTION",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("process_step_id"),
            "HAS_BATCH_STEP_EXECUTION",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("stage_id"),
            "HAS_BATCH_STEP_EXECUTION",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("workstation_id"),
            "HAS_BATCH_STEP_EXECUTION",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "OPERATED_BY",
            document.get("operator_id"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "VERIFIED_BY",
            document.get("verifier_id"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "QA_REVIEWED_BY",
            document.get("qa_reviewer_id"),
            entity_id,
        )
        for equipment_id in document.get("equipment_ids") or []:
            _add_relationship(
                relationships, equipment_id, "USED_IN_BATCH_STEP", entity_id, entity_id
            )
        for machine_id in document.get("machine_ids") or []:
            _add_relationship(
                relationships, machine_id, "USED_IN_BATCH_STEP", entity_id, entity_id
            )
        for document_id in document.get("evidence_document_ids") or []:
            _add_relationship(
                relationships,
                entity_id,
                "HAS_DOCUMENT_EVIDENCE",
                document_id,
                entity_id,
            )
        for evidence_id in document.get("executed_instruction_evidence_ids") or []:
            _add_relationship(
                relationships,
                entity_id,
                "HAS_EXECUTED_INSTRUCTION_EVIDENCE",
                evidence_id,
                entity_id,
            )
    elif collection == "executed_instruction_evidence":
        _add_relationship(
            relationships,
            document.get("batch_step_execution_id"),
            "HAS_EXECUTED_INSTRUCTION_EVIDENCE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("executed_bpr_step_id"),
            "HAS_EXECUTED_INSTRUCTION_EVIDENCE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("batch_execution_record_id"),
            "HAS_EXECUTED_INSTRUCTION_EVIDENCE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("batch_id"),
            "HAS_EXECUTED_INSTRUCTION_EVIDENCE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "EVIDENCES_INSTRUCTION_TEMPLATE",
            document.get("instruction_template_id"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "EVIDENCES_INSTRUCTION_STEP",
            document.get("instruction_step_id"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "EVIDENCES_SOURCE_RECORD",
            document.get("source_record_id"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "EVIDENCES_SOURCE_DOCUMENT",
            document.get("source_document_id"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "EVIDENCES_SOURCE_SECTION",
            document.get("source_section_id"),
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("process_definition_id"),
            "HAS_EXECUTED_INSTRUCTION_EVIDENCE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("process_step_id"),
            "HAS_EXECUTED_INSTRUCTION_EVIDENCE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("stage_id"),
            "HAS_EXECUTED_INSTRUCTION_EVIDENCE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("workstation_id"),
            "HAS_EXECUTED_INSTRUCTION_EVIDENCE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "PERFORMED_BY",
            document.get("performed_by"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "VERIFIED_BY",
            document.get("verified_by"),
            entity_id,
        )
        for equipment_id in document.get("equipment_ids") or []:
            _add_relationship(
                relationships,
                equipment_id,
                "USED_FOR_EXECUTED_INSTRUCTION",
                entity_id,
                entity_id,
            )
        for document_id in document.get("evidence_document_ids") or []:
            _add_relationship(
                relationships,
                entity_id,
                "HAS_DOCUMENT_EVIDENCE",
                document_id,
                entity_id,
            )
    elif collection == "yield_reconciliation_records":
        _add_relationship(
            relationships,
            document.get("batch_execution_record_id"),
            "HAS_YIELD_RECONCILIATION",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("batch_id"),
            "HAS_YIELD_RECONCILIATION",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("process_definition_id"),
            "HAS_YIELD_RECONCILIATION",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("product_id"),
            "HAS_YIELD_RECONCILIATION",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("bom_id"),
            "HAS_YIELD_RECONCILIATION",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "REVIEWED_BY",
            document.get("reviewed_by"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "APPROVED_BY",
            document.get("approved_by"),
            entity_id,
        )
        for document_id in document.get("evidence_document_ids") or []:
            _add_relationship(
                relationships,
                entity_id,
                "HAS_DOCUMENT_EVIDENCE",
                document_id,
                entity_id,
            )
    elif collection == "ipc_result_records":
        _add_relationship(
            relationships,
            document.get("batch_execution_record_id"),
            "HAS_IPC_RESULT",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("batch_step_execution_id"),
            "HAS_IPC_RESULT",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("batch_id"),
            "HAS_IPC_RESULT",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("ipc_checkpoint_id"),
            "HAS_IPC_RESULT",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("process_definition_id"),
            "HAS_IPC_RESULT",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("process_step_id"),
            "HAS_IPC_RESULT",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("stage_id"),
            "HAS_IPC_RESULT",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("workstation_id"),
            "HAS_IPC_RESULT",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("product_id"),
            "HAS_IPC_RESULT",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("bom_id"),
            "HAS_IPC_RESULT",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "METHOD_REFERENCE",
            document.get("method_reference"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "SAMPLED_BY",
            document.get("sampled_by"),
            entity_id,
        )
        _add_relationship(
            relationships, entity_id, "TESTED_BY", document.get("tested_by"), entity_id
        )
        _add_relationship(
            relationships,
            entity_id,
            "REVIEWED_BY",
            document.get("reviewed_by"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "APPROVED_BY",
            document.get("approved_by"),
            entity_id,
        )
        for limit in document.get("limits_snapshot") or []:
            if not isinstance(limit, dict):
                continue
            for cqa_id in limit.get("cqa_ids") or []:
                _add_relationship(
                    relationships, entity_id, "VERIFIES_CQA", cqa_id, entity_id
                )
            for cpp_id in limit.get("cpp_ids") or []:
                _add_relationship(
                    relationships, entity_id, "VERIFIES_CPP", cpp_id, entity_id
                )
        for document_id in document.get("evidence_document_ids") or []:
            _add_relationship(
                relationships,
                entity_id,
                "HAS_DOCUMENT_EVIDENCE",
                document_id,
                entity_id,
            )
    elif collection == "batch_release_workflows":
        _add_relationship(
            relationships,
            document.get("batch_execution_record_id"),
            "HAS_BATCH_RELEASE_WORKFLOW",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("batch_id"),
            "HAS_BATCH_RELEASE_WORKFLOW",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("process_definition_id"),
            "HAS_BATCH_RELEASE_WORKFLOW",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("product_id"),
            "HAS_BATCH_RELEASE_WORKFLOW",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("bom_id"),
            "HAS_BATCH_RELEASE_WORKFLOW",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("plant_id"),
            "HAS_BATCH_RELEASE_WORKFLOW",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("line_id"),
            "HAS_BATCH_RELEASE_WORKFLOW",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "QA_REVIEWED_BY",
            document.get("qa_reviewer_id"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "RELEASED_BY",
            document.get("released_by"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "REJECTED_BY",
            document.get("rejected_by"),
            entity_id,
        )
        for document_id in [
            *(document.get("bmr_document_ids") or []),
            *(document.get("bpr_document_ids") or []),
            *(document.get("evidence_document_ids") or []),
        ]:
            _add_relationship(
                relationships,
                entity_id,
                "HAS_DOCUMENT_EVIDENCE",
                document_id,
                entity_id,
            )
        for step_execution_id in document.get("batch_step_execution_ids") or []:
            _add_relationship(
                relationships,
                entity_id,
                "RELEASES_BATCH_STEP_EXECUTION",
                step_execution_id,
                entity_id,
            )
        for reconciliation_id in document.get("yield_reconciliation_record_ids") or []:
            _add_relationship(
                relationships,
                entity_id,
                "RELEASES_YIELD_RECONCILIATION",
                reconciliation_id,
                entity_id,
            )
        for report_template_id in document.get("report_template_ids") or []:
            _add_relationship(
                relationships,
                entity_id,
                "USES_REPORT_TEMPLATE",
                report_template_id,
                entity_id,
            )
        for package_key in ("executed_bmr_package", "executed_bpr_package"):
            package = (
                document.get(package_key)
                if isinstance(document.get(package_key), dict)
                else {}
            )
            for report_template_id in package.get("report_template_ids") or []:
                _add_relationship(
                    relationships,
                    entity_id,
                    "RELEASE_PACKAGE_USES_REPORT_TEMPLATE",
                    report_template_id,
                    entity_id,
                )
    elif collection == "equipment_usage_ledger":
        _add_relationship(
            relationships,
            document.get("batch_execution_record_id"),
            "HAS_EQUIPMENT_USAGE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("batch_id"),
            "HAS_EQUIPMENT_USAGE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("process_definition_id"),
            "HAS_EQUIPMENT_USAGE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("process_step_id"),
            "HAS_EQUIPMENT_USAGE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("workstation_id"),
            "HAS_EQUIPMENT_USAGE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("machine_id"),
            "HAS_EQUIPMENT_USAGE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("equipment_id"),
            "HAS_EQUIPMENT_USAGE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "OPERATED_BY",
            document.get("operator_id"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "SUPERVISED_BY",
            document.get("supervisor_id"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "QA_REVIEWED_BY",
            document.get("qa_reviewer_id"),
            entity_id,
        )
        for document_id in document.get("evidence_document_ids") or []:
            _add_relationship(
                relationships,
                entity_id,
                "HAS_DOCUMENT_EVIDENCE",
                document_id,
                entity_id,
            )
    elif collection == "area_room_usage_ledger":
        _add_relationship(
            relationships,
            document.get("batch_execution_record_id"),
            "HAS_AREA_ROOM_USAGE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("batch_id"),
            "HAS_AREA_ROOM_USAGE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("process_definition_id"),
            "HAS_AREA_ROOM_USAGE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("process_step_id"),
            "HAS_AREA_ROOM_USAGE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("plant_id"),
            "HAS_AREA_ROOM_USAGE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("line_id"),
            "HAS_AREA_ROOM_USAGE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("stage_id"),
            "HAS_AREA_ROOM_USAGE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("workstation_id"),
            "HAS_AREA_ROOM_USAGE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("building_id"),
            "HAS_AREA_ROOM_USAGE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("floor_id"),
            "HAS_AREA_ROOM_USAGE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("room_id"),
            "HAS_AREA_ROOM_USAGE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("bay_id"),
            "HAS_AREA_ROOM_USAGE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("area_id"),
            "HAS_AREA_ROOM_USAGE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "OPERATED_BY",
            document.get("operator_id"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "SUPERVISED_BY",
            document.get("supervisor_id"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "QA_REVIEWED_BY",
            document.get("qa_reviewer_id"),
            entity_id,
        )
        for document_id in document.get("evidence_document_ids") or []:
            _add_relationship(
                relationships,
                entity_id,
                "HAS_DOCUMENT_EVIDENCE",
                document_id,
                entity_id,
            )
    elif collection == "cpp_cqa_registry":
        relationship_type = (
            "HAS_CPP"
            if str(document.get("registry_type") or "").upper() == "CPP"
            else "HAS_CQA"
        )
        _add_relationship(
            relationships,
            document.get("process_definition_id"),
            relationship_type,
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("process_step_id"),
            relationship_type,
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("stage_id"),
            relationship_type,
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("workstation_id"),
            relationship_type,
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("product_id"),
            relationship_type,
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("bom_id"),
            relationship_type,
            entity_id,
            entity_id,
        )
        for checkpoint_id in document.get("linked_ipc_checkpoint_ids") or []:
            _add_relationship(
                relationships, checkpoint_id, "GOVERNS_CPP_CQA", entity_id, entity_id
            )
        for document_id in document.get("linked_document_ids") or []:
            _add_relationship(
                relationships,
                entity_id,
                "CONTROLLED_BY_DOCUMENT",
                document_id,
                entity_id,
            )
        for batch_execution_record_id in (
            document.get("linked_batch_execution_record_ids") or []
        ):
            _add_relationship(
                relationships,
                batch_execution_record_id,
                "HAS_CPP_CQA_RECORD",
                entity_id,
                entity_id,
            )
    elif collection == "packing_instructions":
        _add_relationship(
            relationships,
            document.get("process_definition_id"),
            "HAS_PACKING_INSTRUCTION",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("bpr_process_definition_id"),
            "HAS_BPR_PACKING_INSTRUCTION",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("bpr_document_id"),
            "CONTROLS_PACKING_INSTRUCTION",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("product_id"),
            "HAS_PACKING_INSTRUCTION",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("bom_id"),
            "HAS_PACKING_INSTRUCTION",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("plant_id"),
            "HAS_PACKING_INSTRUCTION",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("line_id"),
            "HAS_PACKING_INSTRUCTION",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "APPROVED_BY",
            document.get("approved_by"),
            entity_id,
        )
        for document_id in document.get("evidence_document_ids") or []:
            _add_relationship(
                relationships,
                entity_id,
                "HAS_DOCUMENT_EVIDENCE",
                document_id,
                entity_id,
            )
        for step in document.get("steps") or []:
            if not isinstance(step, dict):
                continue
            _add_relationship(
                relationships,
                entity_id,
                "HAS_PACKING_STEP",
                step.get("process_step_id"),
                entity_id,
            )
            _add_relationship(
                relationships,
                step.get("stage_id"),
                "HAS_PACKING_STEP",
                step.get("process_step_id"),
                entity_id,
            )
            _add_relationship(
                relationships,
                step.get("workstation_id"),
                "EXECUTES_PACKING_STEP",
                step.get("process_step_id"),
                entity_id,
            )
    elif collection == "mbmr_templates":
        _add_relationship(
            relationships,
            document.get("process_definition_id"),
            "HAS_MBMR_TEMPLATE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("process_route_id"),
            "HAS_MBMR_TEMPLATE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("document_id"),
            "DESCRIBES_MBMR_TEMPLATE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("product_id"),
            "HAS_MBMR_TEMPLATE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("bom_id"),
            "HAS_MBMR_TEMPLATE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("plant_id"),
            "HAS_MBMR_TEMPLATE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("line_id"),
            "HAS_MBMR_TEMPLATE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "APPROVED_BY",
            document.get("approved_by"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "GENERATES_BMR_TEMPLATE",
            document.get("bmr_template_id"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "GENERATES_BMR_DOCUMENT",
            document.get("bmr_document_id"),
            entity_id,
        )
        for document_id in document.get("evidence_document_ids") or []:
            _add_relationship(
                relationships,
                entity_id,
                "HAS_DOCUMENT_EVIDENCE",
                document_id,
                entity_id,
            )
        for section in document.get("sections") or []:
            if not isinstance(section, dict):
                continue
            _add_relationship(
                relationships,
                entity_id,
                "HAS_MBMR_SECTION",
                section.get("section_id"),
                entity_id,
            )
            _add_relationship(
                relationships,
                section.get("stage_id"),
                "HAS_MBMR_SECTION",
                section.get("section_id"),
                entity_id,
            )
            _add_relationship(
                relationships,
                section.get("workstation_id"),
                "EXECUTES_MBMR_SECTION",
                section.get("section_id"),
                entity_id,
            )
    elif collection == "cleaning_records":
        _add_relationship(
            relationships,
            document.get("equipment_id"),
            "HAS_CLEANING_RECORD",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("work_order_id"),
            "HAS_CLEANING_RECORD",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("batch_execution_record_id"),
            "HAS_CLEANING_RECORD",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("process_definition_id"),
            "HAS_CLEANING_RECORD",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("process_step_id"),
            "HAS_CLEANING_RECORD",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("instruction_template_id"),
            "HAS_CLEANING_RECORD",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("cleaning_requirement_id"),
            "EXECUTED_BY_CLEANING_RECORD",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "PERFORMED_BY",
            document.get("performed_by"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "VERIFIED_BY",
            document.get("verified_by"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "APPROVED_BY",
            document.get("approved_by"),
            entity_id,
        )
    elif collection == "report_templates":
        _add_relationship(
            relationships,
            document.get("process_definition_id"),
            "HAS_REPORT_TEMPLATE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("batch_execution_record_id"),
            "USES_REPORT_TEMPLATE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("document_template_id"),
            "HAS_REPORT_TEMPLATE",
            entity_id,
            entity_id,
        )
        for collection_name in document.get("source_collections") or []:
            _add_relationship(
                relationships,
                entity_id,
                "READS_SOURCE_COLLECTION",
                collection_name,
                entity_id,
            )
        for record_id in document.get("required_record_ids") or []:
            _add_relationship(
                relationships, entity_id, "REQUIRES_REPORT_RECORD", record_id, entity_id
            )
    elif collection == "checklists":
        _add_relationship(
            relationships,
            document.get("work_order_id"),
            "HAS_CHECKLIST",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("owner_id"),
            "OWNS_CHECKLIST",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "ASSIGNED_TO",
            document.get("assigned_to"),
            entity_id,
        )
    elif collection == "sops":
        _add_relationship(
            relationships, document.get("plant_id"), "HAS_SOP", entity_id, entity_id
        )
        _add_relationship(
            relationships, document.get("line_id"), "HAS_SOP", entity_id, entity_id
        )
        _add_relationship(
            relationships, document.get("stage_id"), "HAS_SOP", entity_id, entity_id
        )
        _add_relationship(
            relationships,
            document.get("workstation_id"),
            "HAS_SOP",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships, document.get("machine_id"), "HAS_SOP", entity_id, entity_id
        )
        _add_relationship(
            relationships, document.get("equipment_id"), "HAS_SOP", entity_id, entity_id
        )
    elif collection == "calendar_bookings":
        _add_relationship(
            relationships,
            document.get("work_order_id"),
            "HAS_CALENDAR_BOOKING",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("booked_by"),
            "HAS_CALENDAR_BOOKING",
            entity_id,
            entity_id,
        )
    elif collection == "kanban_boards":
        _add_relationship(
            relationships,
            document.get("assigned_to"),
            "HAS_KANBAN",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships, document.get("owner_id"), "OWNS_KANBAN", entity_id, entity_id
        )
    elif collection == "server_groups":
        _add_relationship(
            relationships,
            document.get("line_id"),
            "HAS_SERVER_GROUP",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("plant_id"),
            "HAS_SERVER_GROUP",
            entity_id,
            entity_id,
        )
    elif collection == "edge_servers":
        _add_relationship(
            relationships,
            document.get("plant_id"),
            "HAS_EDGE_SERVER",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("building_id"),
            "HAS_EDGE_SERVER",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("floor_id"),
            "HAS_EDGE_SERVER",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("room_id"),
            "HAS_EDGE_SERVER",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("bay_id"),
            "HAS_EDGE_SERVER",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("workstation_id"),
            "HAS_EDGE_SERVER",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("server_group_id"),
            "HAS_EDGE_SERVER",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("group_id"),
            "HAS_EDGE_SERVER",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("line_id"),
            "HAS_EDGE_SERVER",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "ROUTES_TO_SERVER",
            document.get("parent_server_id"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "UPSTREAM_BROKER",
            document.get("upstream_broker_url"),
            entity_id,
        )
    elif collection == "external_queues":
        _add_relationship(
            relationships,
            document.get("parent_server_id"),
            "SENDS_TO_EXTERNAL_QUEUE",
            entity_id,
            entity_id,
        )
    elif collection == "notifications":
        _add_relationship(
            relationships,
            document.get("work_order_id"),
            "HAS_NOTIFICATION",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("calendar_booking_id"),
            "HAS_NOTIFICATION",
            entity_id,
            entity_id,
        )
    elif collection == "boms":
        _add_relationship(
            relationships, document.get("product_id"), "HAS_BOM", entity_id, entity_id
        )
        _add_relationship(
            relationships,
            entity_id,
            "SUPPLIED_BY",
            document.get("supplier_id"),
            entity_id,
        )
    elif collection == "products":
        _add_relationship(
            relationships,
            entity_id,
            "SUPPLIED_BY",
            document.get("supplier_id"),
            entity_id,
        )
    elif collection == "teams":
        _add_relationship(
            relationships,
            document.get("department_id") or document.get("department"),
            "HAS_TEAM",
            entity_id,
            entity_id,
        )
    elif collection == "users":
        _add_relationship(
            relationships, document.get("department"), "HAS_USER", entity_id, entity_id
        )
        _add_relationship(
            relationships, document.get("team"), "HAS_USER", entity_id, entity_id
        )
        _add_relationship(
            relationships, entity_id, "HAS_ROLE", document.get("role"), entity_id
        )
        _add_relationship(
            relationships,
            entity_id,
            "REPORTS_TO",
            document.get("reports_to_id")
            or document.get("manager_id")
            or document.get("supervisor_id"),
            entity_id,
        )
    elif collection == "classes":
        _add_relationship(
            relationships, document.get("family_id"), "HAS_CLASS", entity_id, entity_id
        )
    elif collection == "subclasses":
        _add_relationship(
            relationships,
            document.get("class_id"),
            "HAS_SUBCLASS",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            document.get("family_id"),
            "HAS_SUBCLASS",
            entity_id,
            entity_id,
        )
    elif collection == "roles":
        _add_relationship(
            relationships,
            entity_id,
            "HAS_PERMISSION",
            document.get("permissions"),
            entity_id,
        )
    elif collection == "gxp_change_controls":
        for relationship in document.get("graph_relationships") or []:
            if not isinstance(relationship, dict):
                continue
            rel_type = relationship.get("type")
            target_id = relationship.get("target_id")
            if rel_type and target_id:
                _add_relationship(
                    relationships, entity_id, str(rel_type), target_id, entity_id
                )
        for step in document.get("approval_workflow") or []:
            if not isinstance(step, dict):
                continue
            assigned_user = (
                step.get("assigned_user")
                if isinstance(step.get("assigned_user"), dict)
                else {}
            )
            approver_id = step.get("assigned_user_id") or assigned_user.get("user_id")
            _add_relationship(
                relationships,
                entity_id,
                "REQUIRES_APPROVAL_FROM",
                approver_id,
                entity_id,
            )
        basis = (
            document.get("approval_assignment_basis")
            if isinstance(document.get("approval_assignment_basis"), dict)
            else {}
        )
        _add_relationship(
            relationships,
            entity_id,
            "CONTROLS_DOCUMENT",
            basis.get("document_node_id") or basis.get("sop_id"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "CONTROLS_PROCESS",
            basis.get("workstation_id"),
            entity_id,
        )
    elif collection == "gxp_proposed_changes":
        _add_relationship(
            relationships,
            document.get("change_control_id"),
            "HAS_PROPOSED_CHANGE",
            entity_id,
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "PROPOSES_CHANGE_TO",
            document.get("source_id"),
            entity_id,
        )

    if collection != "gxp_change_controls":
        for step in document.get("approval_chain") or []:
            if not isinstance(step, dict):
                continue
            assigned_user = (
                step.get("assigned_user")
                if isinstance(step.get("assigned_user"), dict)
                else {}
            )
            approver_id = step.get("assigned_user_id") or assigned_user.get("user_id")
            _add_relationship(
                relationships,
                entity_id,
                "REQUIRES_APPROVAL_FROM",
                approver_id,
                entity_id,
            )
        basis = (
            document.get("approval_assignment_basis")
            if isinstance(document.get("approval_assignment_basis"), dict)
            else {}
        )
        _add_relationship(
            relationships,
            entity_id,
            "CONTROLS_DOCUMENT",
            basis.get("document_node_id") or basis.get("sop_id"),
            entity_id,
        )
        _add_relationship(
            relationships,
            entity_id,
            "CONTROLS_PROCESS",
            basis.get("workstation_id"),
            entity_id,
        )

    return relationships


def _get_driver():
    global _driver
    if _driver is None:
        neo4j = import_module("neo4j")
        _driver = neo4j.AsyncGraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
        )
    return _driver


async def close_neo4j_mirror_driver() -> None:
    global _driver
    if _driver is not None:
        await _driver.close()
        _driver = None


async def reset_neo4j_mirror(collections: list[str] | None = None) -> None:
    if not settings.NEO4J_MIRROR_ENABLED:
        return
    driver = _get_driver()
    async with driver.session() as session:
        await session.execute_write(
            _reset_neo4j_mirror_tx, collections or [], collections is None
        )


async def cleanup_neo4j_bloat() -> None:
    if not settings.NEO4J_MIRROR_ENABLED:
        return
    driver = _get_driver()
    async with driver.session() as session:
        await session.execute_write(_cleanup_neo4j_bloat_tx)


async def mirror_document(
    collection: str, document: dict[str, Any], operation: str
) -> None:
    if not settings.NEO4J_MIRROR_ENABLED:
        return
    # Strip credentials BEFORE anything derives from the document — the users mirror was
    # copying hashed_password and refresh_tokens straight into Neo4j.
    document = strip_sensitive(document)
    entity_id = _document_key(collection, document)
    node_name = _display_name(document, entity_id)
    node_type = _document_type(collection, entity_id)
    payload = _jsonable(document)
    relationships = _document_relationships(collection, document, entity_id)
    updated_at = _now()
    driver = _get_driver()

    async with driver.session() as session:
        await session.execute_write(
            _mirror_document_tx,
            collection,
            entity_id,
            node_name,
            node_type,
            operation,
            _json(payload),
            relationships,
            updated_at,
        )


async def mirror_deleted_document(
    collection: str, document: dict[str, Any], operation: str = "delete"
) -> None:
    if not settings.NEO4J_MIRROR_ENABLED:
        return
    entity_id = _document_key(collection, document)
    node_name = _document_name(entity_id)
    node_type = _document_type(collection, entity_id)
    driver = _get_driver()

    async with driver.session() as session:
        await session.execute_write(
            _mirror_deleted_document_tx,
            collection,
            entity_id,
            node_name,
            node_type,
        )


async def mirror_write_operation(
    collection: str,
    operation: str,
    query: Any = None,
    update: Any = None,
    result: Any = None,
) -> None:
    if not settings.NEO4J_MIRROR_ENABLED:
        return
    driver = _get_driver()
    written_at = _now()
    result_payload = {
        key: getattr(result, key)
        for key in (
            "matched_count",
            "modified_count",
            "deleted_count",
            "upserted_id",
            "inserted_id",
        )
        if hasattr(result, key)
    }

    async with driver.session() as session:
        await session.execute_write(
            _mirror_write_operation_tx,
            str(uuid4()),
            collection,
            operation,
            _json(_jsonable(query)),
            _json(_jsonable(update)),
            _json(_jsonable(result_payload)),
            written_at,
        )


async def safe_mirror(coro) -> None:
    try:
        await coro
    except Exception:
        try:
            await close_neo4j_mirror_driver()
        except Exception:
            logger.exception("Neo4j mirror driver cleanup failed")
        logger.exception("Neo4j mirror write failed")
        if settings.NEO4J_MIRROR_STRICT:
            raise


async def _mirror_document_tx(
    tx,
    collection: str,
    entity_id: str,
    node_name: str,
    node_type: str,
    operation: str,
    payload_json: str,
    relationships: list[dict[str, str]],
    updated_at: str,
) -> None:
    node_label = _neo4j_label(node_type)
    await tx.run(
        f"""
        MERGE (d:{node_label} {{collection: $collection, entity_id: $entity_id}})
        SET d.payload_json = $payload_json,
            d.name = $node_name,
            d.type = $node_type,
            d.node_type = $node_type,
            d.deleted = false,
            d.last_operation = $operation,
            d.updated_at = $updated_at
        """,
        collection=collection,
        entity_id=entity_id,
        node_name=node_name,
        node_type=node_type,
        operation=operation,
        payload_json=payload_json,
        updated_at=updated_at,
    )
    await tx.run(
        """
        MATCH ()-[r]->()
        WHERE r.mirror_managed = true
          AND r.mirror_owner_entity_id = $entity_id
        DELETE r
        """,
        entity_id=entity_id,
    )
    for relationship in relationships:
        rel_type = relationship["type"]
        await tx.run(
            f"""
            MATCH (source {{entity_id: $source}})
            MATCH (target {{entity_id: $target}})
            MERGE (source)-[r:{rel_type}]->(target)
            SET r.mirror_managed = true,
                r.mirror_owner_entity_id = $owner,
                r.updated_at = $updated_at
            """,
            source=relationship["source"],
            target=relationship["target"],
            owner=relationship["owner"],
            updated_at=updated_at,
        )


async def _reset_neo4j_mirror_tx(tx, collections: list[str], reset_all: bool) -> None:
    await tx.run(
        """
        MATCH (d)
        WHERE $reset_all OR d.collection IN $collections
        DETACH DELETE d
        """,
        collections=collections,
        reset_all=reset_all,
    )
    await tx.run(
        """
        MATCH (op:MongoWriteOperation)
        WHERE $reset_all OR op.collection IN $collections
        DELETE op
        """,
        collections=collections,
        reset_all=reset_all,
    )


async def _cleanup_neo4j_bloat_tx(tx) -> None:
    await tx.run("""
        MATCH (n)
        WHERE any(label IN labels(n) WHERE label IN ['MongoWriteOperation'])
           OR coalesce(n.graph_id, '') ENDS WITH '-canvas-snapshot'
           OR coalesce(n.graph_id, '') ENDS WITH '-canvas-imported-nodes'
           OR coalesce(n.key, '') ENDS WITH '-canvas-snapshot'
           OR coalesce(n.key, '') ENDS WITH '-canvas-imported-nodes'
           OR (
                'GraphNode' IN labels(n)
                AND n.canvas_node_id IS NULL
                AND coalesce(n.graph_id, '') ENDS WITH '-canvas'
           )
        DETACH DELETE n
        """)


async def _mirror_deleted_document_tx(
    tx,
    collection: str,
    entity_id: str,
    node_name: str,
    node_type: str,
) -> None:
    await tx.run(
        """
        MATCH (d)
        WHERE d.collection = $collection
          AND d.entity_id = $entity_id
          AND (d.type = $node_type OR d.node_type = $node_type OR d.name = $node_name)
        DETACH DELETE d
        """,
        collection=collection,
        entity_id=entity_id,
        node_name=node_name,
        node_type=node_type,
    )


async def _mirror_write_operation_tx(
    tx,
    operation_id: str,
    collection: str,
    operation: str,
    query_json: str,
    update_json: str,
    result_json: str,
    written_at: str,
) -> None:
    await tx.run(
        """
        CREATE (op:MongoWriteOperation {
            operation_id: $operation_id,
            collection: $collection,
            operation: $operation,
            query_json: $query_json,
            update_json: $update_json,
            result_json: $result_json,
            written_at: $written_at
        })
        """,
        operation_id=operation_id,
        collection=collection,
        operation=operation,
        query_json=query_json,
        update_json=update_json,
        result_json=result_json,
        written_at=written_at,
    )
