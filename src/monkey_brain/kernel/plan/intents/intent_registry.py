from fastapi import logger
from src.monkey_brain.kernel.plan.intents.predicates.codegen import (
    codegen_question_answer,
    is_codegen_question,
)
from src.monkey_brain.kernel.plan.intents.predicates.drug_research import (
    drug_research_question_answer,
    is_drug_research_question,
)
from src.monkey_brain.kernel.plan.intents.predicates.batch_record import (
    batch_record_question_answer,
    is_batch_record_question,
)
from src.monkey_brain.kernel.plan.intents.predicates.batch_creation import (
    batch_creation_question_answer,
    is_batch_creation_question,
)
from src.monkey_brain.kernel.plan.intents.predicates.batch_update import (
    batch_update_question_answer,
    is_batch_update_question,
)
from src.monkey_brain.kernel.plan.intents.predicates.batch_delete import (
    batch_delete_question_answer,
    is_batch_delete_question,
)
from src.monkey_brain.kernel.plan.intents.predicates.sittingface_pipeline import (
    sittingface_pipeline_question_answer,
    is_sittingface_pipeline_question,
)
from src.monkey_brain.kernel.plan.intents.predicates.specification_discovery import (
    specification_discovery_question_answer,
    is_specification_discovery_question,
)
from src.monkey_brain.kernel.plan.intents.predicates.self_healing_pipeline import (
    self_healing_pipeline_question_answer,
    is_self_healing_question,
)
from src.monkey_brain.kernel.plan.intents.predicates.batch_hold import (
    batch_hold_question_answer,
    is_batch_hold_question,
)
from src.monkey_brain.kernel.plan.intents.predicates.batch_close import (
    batch_close_question_answer,
    is_batch_close_question,
)
from src.monkey_brain.kernel.plan.intents.predicates.work_order_create import (
    work_order_create_question_answer,
    is_work_order_create_question,
)
from src.monkey_brain.kernel.plan.intents.predicates.work_order_query import (
    work_order_query_question_answer,
    is_work_order_query_question,
)
from src.monkey_brain.kernel.plan.intents.predicates.work_order_update import (
    work_order_update_question_answer,
    is_work_order_update_question,
)
from src.monkey_brain.kernel.plan.intents.predicates.work_order_delete import (
    work_order_delete_question_answer,
    is_work_order_delete_question,
)
from src.monkey_brain.kernel.plan.intents.predicates.work_order_hold import (
    work_order_hold_question_answer,
    is_work_order_hold_question,
)
from src.monkey_brain.kernel.plan.intents.predicates.work_order_status import (
    work_order_status_question_answer,
    is_work_order_status_question,
)
from src.monkey_brain.kernel.plan.intents.predicates.approval_create import (
    approval_create_question_answer,
    is_approval_create_question,
)
from src.monkey_brain.kernel.plan.intents.predicates.approval_query import (
    approval_query_question_answer,
    is_approval_query_question,
)
from src.monkey_brain.kernel.plan.intents.predicates.approval_decision import (
    approval_decision_question_answer,
    is_approval_decision_question,
)
from src.monkey_brain.kernel.plan.intents.predicates.approval_hold import (
    approval_hold_question_answer,
    is_approval_hold_question,
)
from src.monkey_brain.kernel.plan.intents.predicates.change_control import (
    change_control_question_answer,
    is_change_control_question,
    is_change_control_create_question,
    is_change_control_query_question,
    is_change_control_update_question,
    is_change_control_decision_question,
)
from src.monkey_brain.kernel.plan.intents.predicates.compliance_audit import (
    is_compliance_audit_question,
    compliance_audit_approval_question_answer,
)
from src.monkey_brain.kernel.plan.intents.predicates.decision_intelligence import (
    decision_intelligence_question_answer,
    is_decision_intelligence_question,
)
from src.monkey_brain.kernel.plan.intents.predicates.production_kpi import (
    production_kpi_question_answer,
    is_production_kpi_question,
)
from src.monkey_brain.kernel.plan.intents.predicates.warehouse_shipping import (
    warehouse_shipping_question_answer,
    is_warehouse_shipping_question,
)
from src.monkey_brain.kernel.plan.intents.predicates.worker import (
    worker_question_answer,
    is_worker_question,
)
from src.monkey_brain.kernel.plan.intents.predicates.sop_query import (
    sop_query_question_answer,
    is_sop_query_question,
)
from src.monkey_brain.kernel.plan.intents.predicates.facility_query import (
    facility_query_question_answer,
    is_facility_query,
)
from src.monkey_brain.kernel.plan.intents.predicates.plant_hierarchy import (
    plant_hierarchy_question_answer,
    is_plant_hierarchy,
)

# ------------------------------------------------------------------
# Semantic Definition
# ------------------------------------------------------------------

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass(slots=True, frozen=True)
class IntentDefinition:
    intent: str

    description: str = ""

    required_inputs: list[str] = field(default_factory=list)

    required_outputs: list[str] = field(default_factory=list)

    default_augmentations: list[str] = field(default_factory=list)

    constraints: dict[str, Any] = field(default_factory=dict)

    min_confidence: float = 0.58

    # GoalType value (goal.py's GoalType enum) this intent resolves to.
    # Kept as a plain string here so intent_registry.py doesn't depend on
    # the kernel's goal module — goal.py converts via GoalType(...).
    goal_type: str = "query"


DRUG_RESEARCH_DEFINITION = IntentDefinition(
    intent="drug_research",
    required_inputs=["compound"],
    required_outputs=["drug_research_result"],
    min_confidence=0.50,
)

BATCH_RECORD_DEFINITION = IntentDefinition(
    intent="batch_record",
    required_inputs=["batch_id"],
    required_outputs=["batch_record"],
    default_augmentations=[
        "approvals",
        "deviations",
        "lineage",
    ],
)

BATCH_CREATION_DEFINITION = IntentDefinition(
    intent="batch_creation_request",
    required_inputs=["product", "size", "route"],
    required_outputs=["workflow"],
    default_augmentations=[
        "sops",
        "process_parameters",
        "quality_controls",
    ],
    goal_type="create",
)

BATCH_UPDATE_DEFINITION = IntentDefinition(
    intent="batch_update",
    required_inputs=["batch_id"],
    required_outputs=["update_workflow"],
    default_augmentations=[
        "change_control",
        "quality_review",
        "audit_trail",
    ],
    goal_type="update",
)

BATCH_DELETE_DEFINITION = IntentDefinition(
    intent="batch_delete",
    required_inputs=["batch_id"],
    required_outputs=["deletion_workflow"],
    default_augmentations=[
        "disposition",
        "regulatory_compliance",
        "audit_trail",
    ],
    goal_type="delete",
)

BATCH_HOLD_DEFINITION = IntentDefinition(
    intent="batch_hold",
    required_inputs=["batch_id"],
    required_outputs=["hold_workflow"],
    default_augmentations=[
        "stabilization",
        "notification",
        "monitoring",
    ],
    goal_type="update",
)

BATCH_CLOSE_DEFINITION = IntentDefinition(
    intent="batch_close",
    required_inputs=["batch_id"],
    required_outputs=["close_workflow"],
    default_augmentations=[
        "quality_review",
        "yield_calculation",
        "release_approval",
    ],
    goal_type="update",
)

WORK_ORDER_CREATE_DEFINITION = IntentDefinition(
    intent="work_order_create",
    required_inputs=["work_order_type", "description"],
    required_outputs=["work_order"],
    default_augmentations=[
        "sops",
        "resources",
        "schedule",
    ],
    goal_type="create",
)

WORK_ORDER_QUERY_DEFINITION = IntentDefinition(
    intent="work_order_query",
    required_inputs=["work_order_id"],
    required_outputs=["work_order_details"],
    default_augmentations=[
        "status_history",
        "time_tracking",
    ],
)

WORK_ORDER_UPDATE_DEFINITION = IntentDefinition(
    intent="work_order_update",
    required_inputs=["work_order_id"],
    required_outputs=["work_order"],
    default_augmentations=[
        "change_control",
        "notification",
        "audit_trail",
    ],
    goal_type="update",
)

WORK_ORDER_DELETE_DEFINITION = IntentDefinition(
    intent="work_order_delete",
    required_inputs=["work_order_id"],
    required_outputs=["deletion_status"],
    default_augmentations=[
        "impact_assessment",
        "approval",
        "audit_trail",
    ],
    goal_type="delete",
)

WORK_ORDER_STATUS_DEFINITION = IntentDefinition(
    intent="work_order_status",
    required_inputs=["work_order_id", "target_status"],
    required_outputs=["work_order"],
    default_augmentations=[
        "state_machine",
        "approval",
        "notification",
    ],
    goal_type="update",
)

WORK_ORDER_HOLD_DEFINITION = IntentDefinition(
    intent="work_order_hold",
    required_inputs=["work_order_id"],
    required_outputs=["work_order"],
    default_augmentations=[
        "justification",
        "notification",
        "monitoring",
    ],
    goal_type="update",
)

DECISION_INTELLIGENCE_DEFINITION = IntentDefinition(
    intent="decision_intelligence",
    required_inputs=["question"],
    required_outputs=["decision_package"],
    default_augmentations=[
        "current_state",
        "historical_outcomes",
        "simulations",
        "sops",
        "policies",
        "constraints",
    ],
    min_confidence=0.55,
    goal_type="analyze",
)

APPROVAL_CREATE_DEFINITION = IntentDefinition(
    intent="approval_create",
    required_inputs=["approval_type"],
    required_outputs=["approval"],
    default_augmentations=[
        "documentation",
        "approver_routing",
        "electronic_signature",
    ],
    goal_type="create",
)

APPROVAL_QUERY_DEFINITION = IntentDefinition(
    intent="approval_query",
    required_inputs=["approval_id"],
    required_outputs=["approval_details"],
    default_augmentations=[
        "status_history",
        "decision_history",
    ],
)

APPROVAL_DECISION_DEFINITION = IntentDefinition(
    intent="approval_decision",
    required_inputs=["approval_id", "decision"],
    required_outputs=["approval"],
    default_augmentations=[
        "review_checklist",
        "electronic_signature",
        "notification",
    ],
    goal_type="update",
)

APPROVAL_HOLD_DEFINITION = IntentDefinition(
    intent="approval_hold",
    required_inputs=["approval_id"],
    required_outputs=["approval"],
    default_augmentations=[
        "justification",
        "notification",
        "monitoring",
    ],
    goal_type="update",
)

CHANGE_CONTROL_DEFINITION = IntentDefinition(
    intent="change_control",
    required_inputs=["change_type"],
    required_outputs=["change_control"],
    default_augmentations=[
        "impact_assessment",
        "risk_evaluation",
        "approver_routing",
    ],
)

CHANGE_CONTROL_CREATE_DEFINITION = IntentDefinition(
    intent="change_control_create",
    required_inputs=["change_type", "description"],
    required_outputs=["change_control"],
    default_augmentations=[
        "impact_assessment",
        "risk_evaluation",
        "affected_entities",
    ],
    goal_type="create",
)

CHANGE_CONTROL_QUERY_DEFINITION = IntentDefinition(
    intent="change_control_query",
    required_inputs=["change_control_id"],
    required_outputs=["change_control_details"],
    default_augmentations=[
        "status_history",
        "approval_history",
        "audit_trail",
    ],
)

CHANGE_CONTROL_UPDATE_DEFINITION = IntentDefinition(
    intent="change_control_update",
    required_inputs=["change_control_id"],
    required_outputs=["change_control"],
    default_augmentations=[
        "change_control",
        "notification",
        "audit_trail",
    ],
    goal_type="update",
)

CHANGE_CONTROL_DECISION_DEFINITION = IntentDefinition(
    intent="change_control_decision",
    required_inputs=["change_control_id", "decision"],
    required_outputs=["change_control"],
    default_augmentations=[
        "review_checklist",
        "electronic_signature",
        "notification",
    ],
    goal_type="update",
)

PRODUCTION_KPI_DEFINITION = IntentDefinition(
    intent="production_kpi",
    required_inputs=[
        "line",
        "time_range",
    ],
    required_outputs=[
        "kpi_report",
    ],
    goal_type="analyze",
)

WAREHOUSE_SHIPPING_DEFINITION = IntentDefinition(
    intent="warehouse_shipping",
    required_inputs=["shipment"],
    required_outputs=["shipping_status"],
    min_confidence=0.56,
)

WORKER_DEFINITION = IntentDefinition(
    intent="worker",
    required_inputs=["worker_id"],
    required_outputs=["worker"],
    default_augmentations=[
        "training",
        "certifications",
    ],
)

APPROVAL_DEFINITION = IntentDefinition(
    intent="approval",
    required_inputs=["approval_id"],
    required_outputs=["approval"],
    min_confidence=0.52,
)

AUDIT_LOG_DEFINITION = IntentDefinition(
    intent="audit_log",
    required_inputs=["audit_id"],
    required_outputs=["audit_log"],
    min_confidence=0.52,
)
COUNT_DEFINITION = IntentDefinition(
    intent="count",
    required_inputs=["entity_type"],
    required_outputs=["count_result"],
    min_confidence=0.50,
)

PLANT_TOPOLOGY_DEFINITION = IntentDefinition(
    intent="plant_topology",
    required_inputs=["entity_name"],
    required_outputs=["topology_info"],
    min_confidence=0.50,
)

ASSET_MANAGEMENT_DEFINITION = IntentDefinition(
    intent="asset_management",
    required_inputs=["asset_id"],
    required_outputs=["asset_info"],
    min_confidence=0.50,
)

SOP_COMPLIANCE_DEFINITION = IntentDefinition(
    intent="sop_compliance",
    required_inputs=["sop_id"],
    required_outputs=["compliance_status"],
    min_confidence=0.50,
)

QUALITY_APPROVAL_DEFINITION = IntentDefinition(
    intent="quality_approval",
    required_inputs=["approval_id"],
    required_outputs=["approval_status"],
    min_confidence=0.50,
)

SIMULATION_DEFINITION = IntentDefinition(
    intent="simulation",
    required_inputs=["scenario"],
    required_outputs=["simulation_result"],
    min_confidence=0.50,
)

SELF_HEALING_PIPELINE_DEFINITION = IntentDefinition(
    intent="self_healing_pipeline",
    required_inputs=["question", "errors"],
    required_outputs=["fixed_files", "heal_status"],
    min_confidence=0.10,
    goal_type="other",
)

SITTINGFACE_PIPELINE_DEFINITION = IntentDefinition(
    intent="sittingface_pipeline",
    required_inputs=["question"],
    required_outputs=["answer", "steps", "status"],
    min_confidence=0.40,
    goal_type="other",
)

MAINTENANCE_DEFINITION = IntentDefinition(
    intent="maintenance",
    required_inputs=["equipment_id"],
    required_outputs=["maintenance_status"],
    min_confidence=0.50,
)

KNOWLEDGE_BASE_DEFINITION = IntentDefinition(
    intent="knowledge_base",
    required_inputs=["topic"],
    required_outputs=["knowledge_result"],
    min_confidence=0.50,
)

INTENT_DEFINITIONS: dict[str, IntentDefinition] = {
    definition.intent: definition
    for definition in (
        DRUG_RESEARCH_DEFINITION,
        BATCH_RECORD_DEFINITION,
        BATCH_CREATION_DEFINITION,
        BATCH_UPDATE_DEFINITION,
        BATCH_DELETE_DEFINITION,
        BATCH_HOLD_DEFINITION,
        BATCH_CLOSE_DEFINITION,
        WORK_ORDER_CREATE_DEFINITION,
        WORK_ORDER_QUERY_DEFINITION,
        WORK_ORDER_UPDATE_DEFINITION,
        WORK_ORDER_DELETE_DEFINITION,
        WORK_ORDER_STATUS_DEFINITION,
        WORK_ORDER_HOLD_DEFINITION,
        DECISION_INTELLIGENCE_DEFINITION,
        APPROVAL_CREATE_DEFINITION,
        APPROVAL_QUERY_DEFINITION,
        APPROVAL_DECISION_DEFINITION,
        APPROVAL_HOLD_DEFINITION,
        CHANGE_CONTROL_DEFINITION,
        CHANGE_CONTROL_CREATE_DEFINITION,
        CHANGE_CONTROL_QUERY_DEFINITION,
        CHANGE_CONTROL_UPDATE_DEFINITION,
        CHANGE_CONTROL_DECISION_DEFINITION,
        PRODUCTION_KPI_DEFINITION,
        WAREHOUSE_SHIPPING_DEFINITION,
        WORKER_DEFINITION,
        AUDIT_LOG_DEFINITION,
        COUNT_DEFINITION,
        PLANT_TOPOLOGY_DEFINITION,
        ASSET_MANAGEMENT_DEFINITION,
        SOP_COMPLIANCE_DEFINITION,
        QUALITY_APPROVAL_DEFINITION,
        SIMULATION_DEFINITION,
        MAINTENANCE_DEFINITION,
        KNOWLEDGE_BASE_DEFINITION,
        SITTINGFACE_PIPELINE_DEFINITION,
    )
}

# ------------------------------------------------------------------
# Legacy Execution Route
# ------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class IntentRoute:
    intent: str
    handler: Callable[..., Awaitable[Any]]
    predicate: Callable[[str], bool]
    min_confidence: float = 0.58
    question_source: str = "payload"
    top_k: bool = False


DRUG_RESEARCH_INTENT = IntentRoute(
    intent="drug_research",
    handler=drug_research_question_answer,
    predicate=is_drug_research_question,
    min_confidence=0.50,
    question_source="visible",
    top_k=True,
)

BATCH_RECORD_INTENT = IntentRoute(
    intent="batch_record",
    handler=batch_record_question_answer,
    predicate=is_batch_record_question,
)

BATCH_CREATION_INTENT = IntentRoute(
    intent="batch_creation_request",
    handler=batch_creation_question_answer,
    predicate=is_batch_creation_question,
    question_source="visible",
)

BATCH_UPDATE_INTENT = IntentRoute(
    intent="batch_update",
    handler=batch_update_question_answer,
    predicate=is_batch_update_question,
    question_source="visible",
)

BATCH_DELETE_INTENT = IntentRoute(
    intent="batch_delete",
    handler=batch_delete_question_answer,
    predicate=is_batch_delete_question,
    question_source="visible",
)

BATCH_HOLD_INTENT = IntentRoute(
    intent="batch_hold",
    handler=batch_hold_question_answer,
    predicate=is_batch_hold_question,
    question_source="visible",
)

BATCH_CLOSE_INTENT = IntentRoute(
    intent="batch_close",
    handler=batch_close_question_answer,
    predicate=is_batch_close_question,
    question_source="visible",
)

WORK_ORDER_CREATE_INTENT = IntentRoute(
    intent="work_order_create",
    handler=work_order_create_question_answer,
    predicate=is_work_order_create_question,
    question_source="visible",
)

WORK_ORDER_QUERY_INTENT = IntentRoute(
    intent="work_order_query",
    handler=work_order_query_question_answer,
    predicate=is_work_order_query_question,
    question_source="visible",
)

WORK_ORDER_UPDATE_INTENT = IntentRoute(
    intent="work_order_update",
    handler=work_order_update_question_answer,
    predicate=is_work_order_update_question,
    question_source="visible",
)

WORK_ORDER_DELETE_INTENT = IntentRoute(
    intent="work_order_delete",
    handler=work_order_delete_question_answer,
    predicate=is_work_order_delete_question,
    question_source="visible",
)

WORK_ORDER_STATUS_INTENT = IntentRoute(
    intent="work_order_status",
    handler=work_order_status_question_answer,
    predicate=is_work_order_status_question,
    question_source="visible",
)

WORK_ORDER_HOLD_INTENT = IntentRoute(
    intent="work_order_hold",
    handler=work_order_hold_question_answer,
    predicate=is_work_order_hold_question,
    question_source="visible",
)

DECISION_INTELLIGENCE_INTENT = IntentRoute(
    intent="decision_intelligence",
    handler=decision_intelligence_question_answer,
    predicate=is_decision_intelligence_question,
    question_source="visible",
    min_confidence=0.55,
)

APPROVAL_CREATE_INTENT = IntentRoute(
    intent="approval_create",
    handler=approval_create_question_answer,
    predicate=is_approval_create_question,
    question_source="visible",
)

APPROVAL_QUERY_INTENT = IntentRoute(
    intent="approval_query",
    handler=approval_query_question_answer,
    predicate=is_approval_query_question,
    question_source="visible",
)

APPROVAL_DECISION_INTENT = IntentRoute(
    intent="approval_decision",
    handler=approval_decision_question_answer,
    predicate=is_approval_decision_question,
    question_source="visible",
)

APPROVAL_HOLD_INTENT = IntentRoute(
    intent="approval_hold",
    handler=approval_hold_question_answer,
    predicate=is_approval_hold_question,
    question_source="visible",
)

CHANGE_CONTROL_INTENT = IntentRoute(
    intent="change_control",
    handler=change_control_question_answer,
    predicate=is_change_control_question,
    question_source="visible",
)

CHANGE_CONTROL_CREATE_INTENT = IntentRoute(
    intent="change_control_create",
    handler=change_control_question_answer,
    predicate=is_change_control_create_question,
    question_source="visible",
)

CHANGE_CONTROL_QUERY_INTENT = IntentRoute(
    intent="change_control_query",
    handler=change_control_question_answer,
    predicate=is_change_control_query_question,
    question_source="visible",
)

CHANGE_CONTROL_UPDATE_INTENT = IntentRoute(
    intent="change_control_update",
    handler=change_control_question_answer,
    predicate=is_change_control_update_question,
    question_source="visible",
)

CHANGE_CONTROL_DECISION_INTENT = IntentRoute(
    intent="change_control_decision",
    handler=change_control_question_answer,
    predicate=is_change_control_decision_question,
    question_source="visible",
)

PRODUCTION_KPI_INTENT = IntentRoute(
    intent="production_kpi",
    handler=production_kpi_question_answer,
    predicate=is_production_kpi_question,
)

WAREHOUSE_SHIPPING_INTENT = IntentRoute(
    intent="warehouse_shipping",
    handler=warehouse_shipping_question_answer,
    predicate=is_warehouse_shipping_question,
    min_confidence=0.56,
)

WORKER_INTENT = IntentRoute(
    intent="worker",
    handler=worker_question_answer,
    predicate=is_worker_question,
    question_source="visible",
)

APPROVAL_INTENT = IntentRoute(
    intent="approval",
    handler=compliance_audit_approval_question_answer,
    predicate=is_compliance_audit_question,
    min_confidence=0.52,
)

AUDIT_LOG_INTENT = IntentRoute(
    intent="audit_log",
    handler=compliance_audit_approval_question_answer,
    predicate=is_compliance_audit_question,
    min_confidence=0.52,
)

SOP_COMPLIANCE_INTENT = IntentRoute(
    intent="sop_compliance",
    handler=sop_query_question_answer,
    predicate=is_sop_query_question,
    question_source="visible",
)

SITTINGFACE_PIPELINE_INTENT = IntentRoute(
    intent="sittingface_pipeline",
    handler=sittingface_pipeline_question_answer,
    predicate=is_sittingface_pipeline_question,
    min_confidence=0.40,
    question_source="visible",
)

SPECIFICATION_DISCOVERY_INTENT = IntentRoute(
    intent="specification_discovery",
    handler=specification_discovery_question_answer,
    predicate=is_specification_discovery_question,
    min_confidence=0.30,
    question_source="visible",
)

SELF_HEALING_PIPELINE_INTENT = IntentRoute(
    intent="self_healing_pipeline",
    handler=self_healing_pipeline_question_answer,
    predicate=is_self_healing_question,
    min_confidence=0.90,  # predicate-only intent; classifier should never match it
    question_source="visible",
)

CODEGEN_INTENT = IntentRoute(
    intent="codegen",
    handler=codegen_question_answer,
    predicate=is_codegen_question,
    min_confidence=0.30,
    question_source="visible",
)

FACILITY_QUERY_INTENT = IntentRoute(
    intent="facility_query",
    handler=facility_query_question_answer,
    predicate=is_facility_query,
    min_confidence=0.30,
    question_source="visible",
)

PLANT_HIERARCHY_INTENT = IntentRoute(
    intent="plant_hierarchy",
    handler=plant_hierarchy_question_answer,
    predicate=is_plant_hierarchy,
    min_confidence=0.30,
    question_source="visible",
)


# ── FALLBACK: World Graph Traversal ──────────────────────────────────────────
# This intent matches ANY question that doesn't match other predicates.
# It ensures intent is never None in the UNDERSTAND phase.
# MUST be defined BEFORE INTENT_REGISTRY and placed LAST in the registry.


async def world_graph_traversal_handler(question: str, **kwargs) -> dict:
    """Handle questions by traversing the world graph.

    This is the fallback handler for questions that don't match
    any specific intent. It searches the world graph for matching
    states and executes a cognitive cycle.
    """
    return {
        "question": question,
        "intent": "world_graph_traversal",
        "answer": f"Traversing world graph for: {question}",
        "handler": "world_graph_traversal",
    }


def is_world_graph_traversal_question(question: str) -> bool:
    """Match any question - this is the fallback predicate.

    This predicate always returns True, so it must be LAST in INTENT_REGISTRY.
    It catches all questions that don't match other specific predicates.
    """
    return True


WORLD_GRAPH_TRAVERSAL_INTENT = IntentRoute(
    intent="world_graph_traversal",
    handler=world_graph_traversal_handler,
    predicate=is_world_graph_traversal_question,
    min_confidence=0.0,  # Always matches
    question_source="payload",
)


INTENT_REGISTRY: dict[str, IntentRoute] = {
    route.intent: route
    for route in (
        SELF_HEALING_PIPELINE_INTENT,  # must be first — healing sentinel takes priority
        CODEGEN_INTENT,  # before drug_research — "generate service" must not fall to LLM
        FACILITY_QUERY_INTENT,
        PLANT_HIERARCHY_INTENT,
        DRUG_RESEARCH_INTENT,
        BATCH_RECORD_INTENT,
        BATCH_CREATION_INTENT,
        BATCH_UPDATE_INTENT,
        BATCH_DELETE_INTENT,
        BATCH_HOLD_INTENT,
        BATCH_CLOSE_INTENT,
        WORK_ORDER_CREATE_INTENT,
        WORK_ORDER_QUERY_INTENT,
        WORK_ORDER_UPDATE_INTENT,
        WORK_ORDER_DELETE_INTENT,
        WORK_ORDER_STATUS_INTENT,
        WORK_ORDER_HOLD_INTENT,
        DECISION_INTELLIGENCE_INTENT,
        APPROVAL_CREATE_INTENT,
        APPROVAL_QUERY_INTENT,
        APPROVAL_DECISION_INTENT,
        APPROVAL_HOLD_INTENT,
        CHANGE_CONTROL_INTENT,
        CHANGE_CONTROL_CREATE_INTENT,
        CHANGE_CONTROL_QUERY_INTENT,
        CHANGE_CONTROL_UPDATE_INTENT,
        CHANGE_CONTROL_DECISION_INTENT,
        PRODUCTION_KPI_INTENT,
        WAREHOUSE_SHIPPING_INTENT,
        WORKER_INTENT,
        AUDIT_LOG_INTENT,
        SOP_COMPLIANCE_INTENT,
        SITTINGFACE_PIPELINE_INTENT,  # ETASS-specific keywords
        SPECIFICATION_DISCOVERY_INTENT,
        WORLD_GRAPH_TRAVERSAL_INTENT,  # MUST BE LAST — fallback for unmatched questions
    )
}


def get_intent_definition(
    intent_name: str | None,
) -> IntentDefinition:
    return INTENT_DEFINITIONS.get(
        intent_name or "",
        IntentDefinition(intent="generic"),
    )


# required_outputs / goal_type for intents that are only in INTENT_REGISTRY
# (routable) but have no IntentDefinition entry — facility-hierarchy
# sub-lookups, free-text search, and a handful of registry-only intents.
_EXTRA_REQUIRED_OUTPUTS: dict[str, list[str]] = {
    "plant": ["lines"],
    "line": ["stages", "workstations"],
    "line_stage": ["stages"],
    "line_workstation": ["workstations"],
    "stage_workstation": ["workstations"],
    "document_search": ["documents"],
    "web_search": ["search_results"],
}

_EXTRA_GOAL_TYPES: dict[str, str] = {
    "plant": "query",
    "line": "query",
    "line_stage": "query",
    "line_workstation": "query",
    "stage_workstation": "query",
    "document_search": "search",
    "web_search": "search",
    "self_healing_pipeline": "other",
    "codegen": "create",
    "specification_discovery": "create",
    "facility_query": "query",
    "plant_hierarchy": "query",
}


def resolve_required_outputs(intent_name: str) -> list[str]:
    """required_outputs for `intent_name`: its IntentDefinition if registered,
    else the extra table above, else `[intent_name]` as a last resort."""
    intent_def = INTENT_DEFINITIONS.get(intent_name)
    if intent_def:
        return list(intent_def.required_outputs)
    return _EXTRA_REQUIRED_OUTPUTS.get(intent_name, [intent_name])


def resolve_goal_type(intent_name: str) -> str:
    """goal_type string for `intent_name`: its IntentDefinition if registered,
    else the extra table above, defaulting to "query"."""
    intent_def = INTENT_DEFINITIONS.get(intent_name)
    if intent_def:
        return intent_def.goal_type
    return _EXTRA_GOAL_TYPES.get(intent_name, "query")


# ---------------------------------------------------------------------------
# Dynamic intent registration from soma charts
# ---------------------------------------------------------------------------

_somatic_compiler = None


def set_somatic_compiler(compiler) -> None:
    """Wire the SomaticCompiler so the executor can register intents from charts."""
    global _somatic_compiler
    _somatic_compiler = compiler


def get_somatic_compiler():
    """The SomaticCompiler singleton — SittingFace's knowledge-base repo.

    Used by CognitiveRuntime.explore_knowledge_base() to query SittingFace
    as an external repo it reads from, rather than an owned subsystem.
    """
    return _somatic_compiler


def lookup_or_register_from_soma(intent_name: str) -> IntentRoute | None:
    """If intent_name matches a soma chart, register it as a sittingface route.

    Called by the executor when a classified intent has no static handler.
    Returns the newly registered route, or None if no chart matches.
    """
    if _somatic_compiler is None or not intent_name:
        return None

    chart_names = {c.name for c in getattr(_somatic_compiler, "charts", [])}
    if intent_name not in chart_names:
        return None

    from src.monkey_brain.kernel.plan.intents.predicates.sittingface_workload import (
        sittingface_workload_question_answer,
    )

    route = IntentRoute(
        intent=intent_name,
        handler=sittingface_workload_question_answer,
        predicate=lambda q: True,
        min_confidence=0.40,
        question_source="visible",
    )
    INTENT_REGISTRY[intent_name] = route
    return route


def register_domain_intent(domain: str, entity_name: str = "") -> IntentRoute | None:
    """Auto-register a new intent for an unseen domain.

    When a new domain is encountered (e.g., 'legal', 'entertainment'),
    this function registers a new intent in INTENT_REGISTRY so that
    future questions about this domain can be handled properly.

    Args:
        domain: The domain name (e.g., 'legal', 'entertainment')
        entity_name: Optional entity name (e.g., 'lawyer', 'music')

    Returns:
        The newly registered IntentRoute, or None if registration failed.
    """
    intent_name = f"{domain}_query"

    # Don't register if already exists
    if intent_name in INTENT_REGISTRY:
        return INTENT_REGISTRY[intent_name]

    # Create predicate that matches questions about this domain
    def domain_predicate(question: str) -> bool:
        question_lower = question.lower()
        # Match if domain name or entity name is in the question
        if domain.lower() in question_lower:
            return True
        if entity_name and entity_name.lower() in question_lower:
            return True
        return False

    # Create handler for this domain
    async def domain_handler(question: str, **kwargs) -> dict:
        return {
            "question": question,
            "intent": intent_name,
            "domain": domain,
            "entity": entity_name,
            "answer": f"Processing {domain} request: {question}",
            "handler": "domain_handler",
        }

    # Register the intent
    route = IntentRoute(
        intent=intent_name,
        handler=domain_handler,
        predicate=domain_predicate,
        min_confidence=0.5,
        question_source="payload",
    )

    INTENT_REGISTRY[intent_name] = route
    logger.info(f"Registered new domain intent: {intent_name}")

    return route
