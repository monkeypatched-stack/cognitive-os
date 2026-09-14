"""
models/process_definition.py
------------------
ProcessDefinition descriptor model.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, List, Optional

from pydantic import BaseModel, Field, model_validator

from services.process_definitions.models.process_steps import ProcessSteps


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class Status(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


class ProcessType(str, Enum):
    MANUFACTURING = "manufacturing"
    PACKAGING = "packaging"
    CLEANING = "cleaning"
    MAINTENANCE = "maintenance"
    APPROVAL = "approval"
    AUDIT = "audit"
    AUTOMATION = "automation"


class ProcessApprovalStatus(str, Enum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    EFFECTIVE = "effective"
    RETIRED = "retired"
    REJECTED = "rejected"


class InstructionTemplateType(str, Enum):
    MANUFACTURING = "manufacturing"
    PACKING = "packing"
    CLEANING = "cleaning"


class IpcCheckpointType(str, Enum):
    IN_PROCESS_CONTROL = "in_process_control"
    CQA = "cqa"
    CPP = "cpp"
    YIELD = "yield"
    WEIGHT = "weight"
    TEMPERATURE = "temperature"
    HUMIDITY = "humidity"
    PH = "ph"
    ASSAY = "assay"
    VISUAL = "visual"
    CUSTOM = "custom"


class IpcLimit(BaseModel):
    parameter: str = Field(..., min_length=1)
    target: Optional[float | str] = None
    lower_limit: Optional[float] = None
    upper_limit: Optional[float] = None
    unit: Optional[str] = None
    operator: Optional[str] = None
    acceptance_criteria: Optional[str] = None


class IpcSamplingPlan(BaseModel):
    sampling_method: str = Field(..., min_length=1)
    sample_size: Optional[int] = Field(default=None, ge=1)
    frequency: Optional[str] = None
    sample_location: Optional[str] = None
    aql_level: Optional[str] = None
    retain_sample_required: bool = False
    instructions: Optional[str] = None


class IpcCheckpoint(BaseModel):
    checkpoint_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    checkpoint_type: IpcCheckpointType = Field(
        default=IpcCheckpointType.IN_PROCESS_CONTROL
    )
    process_step_id: Optional[str] = None
    stage_id: Optional[str] = None
    workstation_id: Optional[str] = None
    method: str = Field(..., min_length=1)
    method_reference: Optional[str] = None
    cqa_ids: List[str] = Field(default_factory=list)
    cpp_ids: List[str] = Field(default_factory=list)
    limits: List[IpcLimit] = Field(default_factory=list)
    sampling_plan: Optional[IpcSamplingPlan] = None
    required: bool = True
    evidence_required: bool = True
    signature_required: bool = False
    alarm_on_failure: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"use_enum_values": True}


class CleaningRequirement(BaseModel):
    cleaning_requirement_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    process_step_id: Optional[str] = None
    instruction_template_id: Optional[str] = None
    instruction_step_sequence: Optional[int] = Field(default=None, ge=1)
    stage_id: Optional[str] = None
    workstation_id: Optional[str] = None
    equipment_ids: List[str] = Field(default_factory=list)
    cleaning_type: str = Field(..., min_length=1)
    cleaning_method: str = Field(..., min_length=1)
    timing: str = Field(default="post-production")
    verification_method: str = Field(default="Visual Inspection")
    residue_limit_ppm: Optional[float] = Field(default=None, ge=0)
    required_before_next_step: bool = True
    evidence_required: bool = True
    signature_required: bool = False
    derived_from_sop_ids: List[str] = Field(default_factory=list)
    derived_from_sop_document_ids: List[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProcessInstructionStep(BaseModel):
    sequence: int = Field(..., ge=1)
    title: str = Field(..., min_length=1)
    instruction: str = Field(..., min_length=1)
    process_step_id: Optional[str] = None
    stage_id: Optional[str] = None
    workstation_id: Optional[str] = None
    equipment_ids: List[str] = Field(default_factory=list)
    material_ids: List[str] = Field(default_factory=list)
    acceptance_criteria: Optional[str] = None
    ipc_checkpoint_ids: List[str] = Field(default_factory=list)
    cleaning_requirement_ids: List[str] = Field(default_factory=list)
    evidence_required: bool = True
    signature_required: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProcessInstructionTemplate(BaseModel):
    instruction_template_id: str = Field(..., min_length=1)
    template_type: InstructionTemplateType = Field(
        default=InstructionTemplateType.MANUFACTURING
    )
    title: str = Field(..., min_length=1)
    version: str = Field(default="1.0.0")
    revision: str = Field(default="A")
    approval_status: ProcessApprovalStatus = Field(default=ProcessApprovalStatus.DRAFT)
    change_control_id: Optional[str] = None
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    derived_from_sop_ids: List[str] = Field(default_factory=list)
    derived_from_sop_document_ids: List[str] = Field(default_factory=list)
    derived_from_process_step_ids: List[str] = Field(default_factory=list)
    steps: List[ProcessInstructionStep] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"use_enum_values": True}

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "ProcessInstructionTemplate":
        self.effective_from = ensure_utc(self.effective_from)
        self.effective_to = ensure_utc(self.effective_to)
        self.approved_at = ensure_utc(self.approved_at)
        return self


ProcessDefinitionCanvasPageId = str


def normalize_canvas_page_id(page_id: str, layout_id: Optional[str] = None) -> str:
    aliases = {
        "indus-dashboard-process_definition": "process_definition",
        "indus-dashboard-process-steps": "process-steps",
        "indus-dashboard-process-prechecks": "process-prechecks",
        "indus-dashboard-process-postchecks": "process-postchecks",
        "indus-dashboard-process-constraints": "process-constraints",
        "indus-dashboard-process-corrections": "process-corrections",
    }
    valid = {
        "process_definition",
        "process-steps",
        "process-prechecks",
        "process-postchecks",
        "process-constraints",
        "process-corrections",
    }
    candidate = page_id or ""
    if candidate in valid:
        return candidate
    if candidate in aliases:
        return aliases[candidate]
    if layout_id in aliases:
        return aliases[layout_id]
    return candidate


class ProcessDefinitionCanvasHyperlink(BaseModel):
    label: Optional[str] = None
    url: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProcessDefinitionCanvasAttachment(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    type: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProcessDefinitionCanvasWebhookEvent(BaseModel):
    event_id: Optional[str] = None
    provider: Optional[str] = None
    direction: str = "incoming"
    payload: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, Any] = Field(default_factory=dict)
    status: Optional[str] = None
    status_code: Optional[int] = None
    response_body: Optional[Any] = None
    error: Optional[str] = None
    received_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "ProcessDefinitionCanvasWebhookEvent":
        self.received_at = ensure_utc(self.received_at)
        return self


class ProcessDefinitionCanvasWebhookDispatchRequest(BaseModel):
    url: Optional[str] = None
    method: str = "POST"
    payload: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)


class ProcessDefinitionCanvasWebhookDispatchResponse(BaseModel):
    ok: bool
    status_code: Optional[int] = None
    response_body: Optional[Any] = None
    error: Optional[str] = None
    event: ProcessDefinitionCanvasWebhookEvent


class ProcessDefinitionCanvasBackendCall(BaseModel):
    service: Optional[str] = None
    action: Optional[str] = None
    method: str = "POST"
    path: Optional[str] = None
    url: Optional[str] = None
    headers: dict[str, str] = Field(default_factory=dict)


class ProcessDefinitionCanvasNodeExecutionRequest(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)
    backend: Optional[ProcessDefinitionCanvasBackendCall] = None
    dry_run: bool = False


class ProcessDefinitionCanvasNodeExecutionResponse(BaseModel):
    ok: bool
    layout_id: str
    node_id: str
    node_type: Optional[str] = None
    backend: Optional[ProcessDefinitionCanvasBackendCall] = None
    status_code: Optional[int] = None
    response_body: Optional[Any] = None
    error: Optional[str] = None
    event: ProcessDefinitionCanvasWebhookEvent


class ProcessDefinitionCanvasFlowSimulationRequest(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)
    start_node_id: Optional[str] = None
    max_steps: int = Field(default=100, ge=1, le=1000)
    persist_trace: bool = True


class ProcessDefinitionCanvasFlowSimulationResponse(BaseModel):
    flow_run_id: str
    layout_id: str
    mode: str = "simulate"
    status: str
    compiled_from: Optional[str] = None
    source_sop_id: Optional[str] = None
    source_sop_title: Optional[str] = None
    source_process_definition_id: Optional[str] = None
    compiled_layout: dict[str, Any] = Field(default_factory=dict)
    input_message: dict[str, Any] = Field(default_factory=dict)
    start_node_ids: list[str] = Field(default_factory=list)
    node_trace: list[dict[str, Any]] = Field(default_factory=list)
    edges_taken: list[dict[str, Any]] = Field(default_factory=list)
    events_generated: list[dict[str, Any]] = Field(default_factory=list)
    records_would_create: list[dict[str, Any]] = Field(default_factory=list)
    final_message: dict[str, Any] = Field(default_factory=dict)
    final_messages: list[dict[str, Any]] = Field(default_factory=list)
    blocked_constraints: list[dict[str, Any]] = Field(default_factory=list)
    risk_summary: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class ProcessDefinitionCanvasNodeProperties(BaseModel):
    node_id: str = Field(..., min_length=1)
    name: Optional[str] = None
    title: Optional[str] = None
    node_type: Optional[str] = None
    node_category: Optional[str] = None
    backend: Optional[ProcessDefinitionCanvasBackendCall] = None
    config: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    hyperlinks: list[ProcessDefinitionCanvasHyperlink] = Field(default_factory=list)
    attachments: list[ProcessDefinitionCanvasAttachment] = Field(default_factory=list)
    webhook_events: list[ProcessDefinitionCanvasWebhookEvent] = Field(
        default_factory=list
    )
    last_webhook_event: Optional[ProcessDefinitionCanvasWebhookEvent] = None


class ProcessDefinitionCanvasNodePropertiesUpdate(BaseModel):
    name: Optional[str] = None
    title: Optional[str] = None
    node_type: Optional[str] = None
    node_category: Optional[str] = None
    backend: Optional[ProcessDefinitionCanvasBackendCall] = None
    config: Optional[dict[str, Any]] = None
    metadata: Optional[dict[str, Any]] = None
    hyperlinks: Optional[list[ProcessDefinitionCanvasHyperlink]] = None
    attachments: Optional[list[ProcessDefinitionCanvasAttachment]] = None
    webhook_events: Optional[list[ProcessDefinitionCanvasWebhookEvent]] = None
    last_webhook_event: Optional[ProcessDefinitionCanvasWebhookEvent] = None
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="before")
    @classmethod
    def accept_canvas_node_property_aliases(cls, value: Any) -> Any:
        if isinstance(value, dict):
            updates = dict(value)
            if not updates.get("node_type"):
                node_type = updates.get("type") or updates.get("nodeType")
                if node_type:
                    updates["node_type"] = node_type
            if not updates.get("node_category"):
                node_category = updates.get("category") or updates.get("nodeCategory")
                if node_category:
                    updates["node_category"] = node_category
            return updates
        return value

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "ProcessDefinitionCanvasNodePropertiesUpdate":
        self.updated_at = ensure_utc(self.updated_at)
        return self


class ProcessDefinitionCanvasNodePosition(BaseModel):
    node_id: str = Field(..., min_length=1)
    x: float
    y: float
    name: Optional[str] = None
    title: Optional[str] = None
    node_type: Optional[str] = None
    node_category: Optional[str] = None
    backend: Optional[ProcessDefinitionCanvasBackendCall] = None
    config: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    hyperlinks: list[ProcessDefinitionCanvasHyperlink] = Field(default_factory=list)
    attachments: list[ProcessDefinitionCanvasAttachment] = Field(default_factory=list)
    webhook_events: list[ProcessDefinitionCanvasWebhookEvent] = Field(
        default_factory=list
    )
    last_webhook_event: Optional[ProcessDefinitionCanvasWebhookEvent] = None

    @model_validator(mode="before")
    @classmethod
    def accept_canvas_node_aliases(cls, value: Any) -> Any:
        if isinstance(value, dict):
            updates = dict(value)
            if not updates.get("node_id"):
                alias = (
                    updates.get("id")
                    or updates.get("nodeId")
                    or updates.get("canvas_node_id")
                )
                if alias:
                    updates["node_id"] = alias
            if not updates.get("node_type"):
                node_type = updates.get("type") or updates.get("nodeType")
                if node_type:
                    updates["node_type"] = node_type
            if not updates.get("node_category"):
                node_category = updates.get("category") or updates.get("nodeCategory")
                if node_category:
                    updates["node_category"] = node_category
            position = updates.get("position")
            if isinstance(position, dict):
                if updates.get("x") is None and position.get("x") is not None:
                    updates["x"] = position["x"]
                if updates.get("y") is None and position.get("y") is not None:
                    updates["y"] = position["y"]
            return updates
        return value


class ProcessDefinitionCanvasEdge(BaseModel):
    edge_id: str = Field(..., min_length=1)
    source_node_id: str = Field(..., min_length=1)
    target_node_id: str = Field(..., min_length=1)
    source_port: int = Field(default=0, ge=0)
    target_port: Optional[int] = Field(default=None, ge=0)
    source: Optional[str] = None
    target: Optional[str] = None
    sourceNodeId: Optional[str] = None
    targetNodeId: Optional[str] = None
    label: Optional[str] = None
    sourceHandle: Optional[str] = None
    targetHandle: Optional[str] = None
    flowColor: Optional[str] = None
    flowEnabled: Optional[bool] = None
    arrowHead: Optional[str] = None
    routeMode: Optional[str] = None
    points: list[dict[str, float]] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def accept_canvas_edge_aliases(cls, value: Any) -> Any:
        if isinstance(value, dict):
            updates = dict(value)
            if not updates.get("edge_id"):
                edge_id = updates.get("id") or updates.get("edgeId")
                if edge_id:
                    updates["edge_id"] = edge_id
            if not updates.get("source_node_id"):
                source = (
                    updates.get("source")
                    or updates.get("sourceNodeId")
                    or updates.get("sourceId")
                )
                if source:
                    updates["source_node_id"] = source
            if not updates.get("target_node_id"):
                target = (
                    updates.get("target")
                    or updates.get("targetNodeId")
                    or updates.get("targetId")
                )
                if target:
                    updates["target_node_id"] = target
            if (
                updates.get("source_port") is None
                and updates.get("sourcePort") is not None
            ):
                updates["source_port"] = updates["sourcePort"]
            if (
                updates.get("target_port") is None
                and updates.get("targetPort") is not None
            ):
                updates["target_port"] = updates["targetPort"]
            return updates
        return value


class ProcessDefinitionCanvasLayout(BaseModel):
    layout_id: str = Field(..., min_length=1)
    page_id: ProcessDefinitionCanvasPageId
    owner_id: Optional[str] = None
    process_definition_id: Optional[str] = None

    nodes: list[ProcessDefinitionCanvasNodePosition] = Field(default_factory=list)
    edges: list[ProcessDefinitionCanvasEdge] = Field(default_factory=list)
    links: list[ProcessDefinitionCanvasEdge] = Field(default_factory=list)
    connections: list[ProcessDefinitionCanvasEdge] = Field(default_factory=list)
    visual_nodes: list[Any] = Field(default_factory=list)
    data: Any = None

    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="before")
    @classmethod
    def accept_canvas_layout_aliases(cls, value: Any) -> Any:
        if isinstance(value, dict):
            updates = dict(value)
            if not updates.get("layout_id"):
                layout_id = updates.get("id") or updates.get("layoutId")
                if layout_id:
                    updates["layout_id"] = layout_id
            if not updates.get("page_id"):
                page_id = updates.get("page") or updates.get("pageId")
                if page_id:
                    updates["page_id"] = page_id
            if not updates.get("nodes"):
                nodes = (
                    updates.get("positions")
                    or updates.get("node_positions")
                    or updates.get("nodePositions")
                )
                if nodes:
                    updates["nodes"] = nodes
            if not updates.get("edges"):
                edges = updates.get("links") or updates.get("connections")
                if edges:
                    updates["edges"] = edges
            if "edges" in updates:
                updates["links"] = updates.get("edges") or []
                updates["connections"] = updates.get("edges") or []

            page_id = updates.get("page_id")
            layout_id = updates.get("layout_id")
            if isinstance(page_id, str):
                updates["page_id"] = normalize_canvas_page_id(page_id, layout_id)
            elif isinstance(layout_id, str):
                inferred = normalize_canvas_page_id("", layout_id)
                if inferred:
                    updates["page_id"] = inferred
            return updates
        return value

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "ProcessDefinitionCanvasLayout":
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self


class ProcessDefinitionCanvasLayoutCreate(ProcessDefinitionCanvasLayout):
    pass


class ProcessDefinitionCanvasLayoutUpdate(BaseModel):
    page_id: Optional[ProcessDefinitionCanvasPageId] = None
    owner_id: Optional[str] = None
    process_definition_id: Optional[str] = None
    nodes: Optional[list[ProcessDefinitionCanvasNodePosition]] = None
    edges: Optional[list[ProcessDefinitionCanvasEdge]] = None
    links: Optional[list[ProcessDefinitionCanvasEdge]] = None
    connections: Optional[list[ProcessDefinitionCanvasEdge]] = None
    visual_nodes: Optional[list[Any]] = None
    data: Any = None
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="before")
    @classmethod
    def accept_canvas_layout_update_aliases(cls, value: Any) -> Any:
        if isinstance(value, dict):
            updates = dict(value)
            if not updates.get("page_id"):
                page_id = updates.get("page") or updates.get("pageId")
                if page_id:
                    updates["page_id"] = page_id
            if not updates.get("nodes"):
                nodes = (
                    updates.get("positions")
                    or updates.get("node_positions")
                    or updates.get("nodePositions")
                )
                if nodes:
                    updates["nodes"] = nodes
            if not updates.get("edges"):
                edges = updates.get("links") or updates.get("connections")
                if edges:
                    updates["edges"] = edges
            if "edges" in updates:
                updates["links"] = updates.get("edges") or []
                updates["connections"] = updates.get("edges") or []
            if isinstance(updates.get("page_id"), str):
                updates["page_id"] = normalize_canvas_page_id(updates["page_id"], None)
            return updates
        return value

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "ProcessDefinitionCanvasLayoutUpdate":
        self.updated_at = ensure_utc(self.updated_at)
        return self


class ProcessDefinitionCanvasLayoutResponse(ProcessDefinitionCanvasLayout):
    class Config:
        from_attributes = True


class ProcessDefinitionEndpointInfo(BaseModel):
    method: str
    path: str
    description: str


class PaginatedProcessDefinitionCanvasLayoutResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[ProcessDefinitionCanvasLayoutResponse]


class ProcessDefinition(BaseModel):
    process_definition_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    description: str = Field(...)
    version: str = Field(default="1.0.0")
    route_version: str = Field(default="1.0.0")
    revision: str = Field(default="A")
    process_type: ProcessType = Field(default=ProcessType.MANUFACTURING)
    product_id: Optional[str] = None
    product_family: Optional[str] = None
    plant_id: Optional[str] = None
    line_id: Optional[str] = None
    bom_id: Optional[str] = None
    recipe_id: Optional[str] = None
    owner: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    status: Status = Field(default=Status.PENDING)
    approval_status: ProcessApprovalStatus = Field(default=ProcessApprovalStatus.DRAFT)
    change_control_id: Optional[str] = None
    supersedes_process_definition_id: Optional[str] = None
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    retired_at: Optional[datetime] = None
    route_stage_ids: List[str] = Field(default_factory=list)
    material_flow_edge_ids: List[str] = Field(default_factory=list)
    ipc_checkpoints: List[IpcCheckpoint] = Field(default_factory=list)
    cleaning_requirements: List[CleaningRequirement] = Field(default_factory=list)
    instruction_templates: List[ProcessInstructionTemplate] = Field(
        default_factory=list
    )
    metadata: dict[str, Any] = Field(default_factory=dict)
    steps: ProcessSteps
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    model_config = {"use_enum_values": True}

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "ProcessDefinition":
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        self.effective_from = ensure_utc(self.effective_from)
        self.effective_to = ensure_utc(self.effective_to)
        self.approved_at = ensure_utc(self.approved_at)
        self.retired_at = ensure_utc(self.retired_at)
        return self


class ProcessDefinitionCreate(ProcessDefinition):
    pass


class ProcessDefinitionUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    version: Optional[str] = None
    route_version: Optional[str] = None
    revision: Optional[str] = None
    process_type: Optional[ProcessType] = None
    product_id: Optional[str] = None
    product_family: Optional[str] = None
    plant_id: Optional[str] = None
    line_id: Optional[str] = None
    bom_id: Optional[str] = None
    recipe_id: Optional[str] = None
    owner: Optional[str] = None
    tags: Optional[List[str]] = None
    status: Optional[Status] = None
    approval_status: Optional[ProcessApprovalStatus] = None
    change_control_id: Optional[str] = None
    supersedes_process_definition_id: Optional[str] = None
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    retired_at: Optional[datetime] = None
    route_stage_ids: Optional[List[str]] = None
    material_flow_edge_ids: Optional[List[str]] = None
    ipc_checkpoints: Optional[List[IpcCheckpoint]] = None
    cleaning_requirements: Optional[List[CleaningRequirement]] = None
    instruction_templates: Optional[List[ProcessInstructionTemplate]] = None
    metadata: Optional[dict[str, Any]] = None
    steps: Optional[ProcessSteps] = None
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "ProcessDefinitionUpdate":
        self.updated_at = ensure_utc(self.updated_at)
        self.effective_from = ensure_utc(self.effective_from)
        self.effective_to = ensure_utc(self.effective_to)
        self.approved_at = ensure_utc(self.approved_at)
        self.retired_at = ensure_utc(self.retired_at)
        return self


class ProcessDefinitionResponse(ProcessDefinition):
    class Config:
        from_attributes = True


class PaginatedProcessDefinitionResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[ProcessDefinitionResponse]
