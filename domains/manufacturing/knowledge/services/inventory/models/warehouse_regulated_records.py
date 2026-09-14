from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator

from services.products.models.product_common import ensure_utc, utc_now


class WarehouseRecordStatus(str, Enum):
    DRAFT = "Draft"
    ACTIVE = "Active"
    RECEIVED = "Received"
    QUARANTINED = "Quarantined"
    RELEASED = "Released"
    REJECTED = "Rejected"
    CLOSED = "Closed"


class WarehouseItemType(str, Enum):
    PRODUCT = "product"
    COMPONENT = "component"
    RAW_MATERIAL = "raw_material"
    PACKAGING_MATERIAL = "packaging_material"
    EQUIPMENT = "equipment"


class WarehouseDisposition(str, Enum):
    QUARANTINE = "Quarantine"
    RELEASE = "Release"
    REJECTION = "Rejection"


class SpecialStorageRequirement(BaseModel):
    requirement_id: str = Field(..., min_length=1)
    item_type: WarehouseItemType = WarehouseItemType.RAW_MATERIAL
    product_id: Optional[str] = None
    component_id: Optional[str] = None
    material_code: Optional[str] = None
    storage_type: str = Field(default="Ambient", min_length=1)
    temperature_zone: str = Field(default="Ambient", min_length=1)
    min_temperature_c: Optional[float] = None
    max_temperature_c: Optional[float] = None
    min_humidity_pct: Optional[float] = Field(default=None, ge=0, le=100)
    max_humidity_pct: Optional[float] = Field(default=None, ge=0, le=100)
    light_sensitive: bool = False
    hazardous: bool = False
    segregation_required: bool = False
    allowed_location_ids: list[str] = Field(default_factory=list)
    required_status_label: Optional[str] = None
    document_ids: list[str] = Field(default_factory=list)
    status: WarehouseRecordStatus = WarehouseRecordStatus.ACTIVE
    effective_from: datetime = Field(default_factory=utc_now)
    effective_to: Optional[datetime] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_by: Optional[str] = None
    updated_by: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_temperature_range(self) -> "SpecialStorageRequirement":
        self.effective_from = ensure_utc(self.effective_from)
        self.effective_to = ensure_utc(self.effective_to)
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        if (
            self.min_temperature_c is not None
            and self.max_temperature_c is not None
            and self.min_temperature_c > self.max_temperature_c
        ):
            raise ValueError("min_temperature_c cannot exceed max_temperature_c.")
        if (
            self.min_humidity_pct is not None
            and self.max_humidity_pct is not None
            and self.min_humidity_pct > self.max_humidity_pct
        ):
            raise ValueError("min_humidity_pct cannot exceed max_humidity_pct.")
        if not any([self.product_id, self.component_id, self.material_code]):
            raise ValueError(
                "storage requirements must reference product_id, component_id, or material_code."
            )
        return self


class SpecialStorageRequirementCreate(SpecialStorageRequirement):
    pass


class SpecialStorageRequirementUpdate(BaseModel):
    product_id: Optional[str] = None
    component_id: Optional[str] = None
    material_code: Optional[str] = None
    storage_type: Optional[str] = None
    temperature_zone: Optional[str] = None
    min_temperature_c: Optional[float] = None
    max_temperature_c: Optional[float] = None
    min_humidity_pct: Optional[float] = Field(default=None, ge=0, le=100)
    max_humidity_pct: Optional[float] = Field(default=None, ge=0, le=100)
    light_sensitive: Optional[bool] = None
    hazardous: Optional[bool] = None
    segregation_required: Optional[bool] = None
    allowed_location_ids: Optional[list[str]] = None
    required_status_label: Optional[str] = None
    document_ids: Optional[list[str]] = None
    status: Optional[WarehouseRecordStatus] = None
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
    metadata: Optional[dict[str, Any]] = None
    updated_by: Optional[str] = None


class MaterialInwardLog(BaseModel):
    inward_log_id: str = Field(..., min_length=1)
    gr_id: Optional[str] = None
    gr_number: Optional[str] = None
    po_number: Optional[str] = None
    material_code: str = Field(..., min_length=1)
    product_id: Optional[str] = None
    component_id: Optional[str] = None
    lot_id: Optional[str] = None
    batch_lot_number: Optional[str] = None
    received_qty: float = Field(..., gt=0)
    unit: str = Field(..., min_length=1)
    received_by: str = Field(..., min_length=1)
    received_at: datetime = Field(default_factory=utc_now)
    dock_location: Optional[str] = None
    warehouse_location_id: Optional[str] = None
    grn_record_id: Optional[str] = None
    status: WarehouseRecordStatus = WarehouseRecordStatus.RECEIVED
    attachment_ids: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_dates(self) -> "MaterialInwardLog":
        self.received_at = ensure_utc(self.received_at)
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self


class MaterialInwardLogCreate(MaterialInwardLog):
    pass


class MaterialInwardLogUpdate(BaseModel):
    gr_id: Optional[str] = None
    gr_number: Optional[str] = None
    po_number: Optional[str] = None
    material_code: Optional[str] = None
    product_id: Optional[str] = None
    component_id: Optional[str] = None
    lot_id: Optional[str] = None
    batch_lot_number: Optional[str] = None
    received_qty: Optional[float] = Field(default=None, gt=0)
    unit: Optional[str] = None
    received_by: Optional[str] = None
    received_at: Optional[datetime] = None
    dock_location: Optional[str] = None
    warehouse_location_id: Optional[str] = None
    grn_record_id: Optional[str] = None
    status: Optional[WarehouseRecordStatus] = None
    attachment_ids: Optional[list[str]] = None
    document_ids: Optional[list[str]] = None
    metadata: Optional[dict[str, Any]] = None


class WarehouseStatusLabel(BaseModel):
    label_id: str = Field(..., min_length=1)
    entity_type: str = Field(..., min_length=1)
    entity_id: str = Field(..., min_length=1)
    label_type: WarehouseDisposition | WarehouseRecordStatus
    label_text: str = Field(..., min_length=1)
    barcode: Optional[str] = None
    printed: bool = False
    printed_at: Optional[datetime] = None
    applied_by: Optional[str] = None
    applied_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    document_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_dates(self) -> "WarehouseStatusLabel":
        self.printed_at = ensure_utc(self.printed_at)
        self.applied_at = ensure_utc(self.applied_at)
        self.expires_at = ensure_utc(self.expires_at)
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self


class WarehouseStatusLabelCreate(WarehouseStatusLabel):
    pass


class WarehouseStatusLabelUpdate(BaseModel):
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    label_type: Optional[WarehouseDisposition | WarehouseRecordStatus] = None
    label_text: Optional[str] = None
    barcode: Optional[str] = None
    printed: Optional[bool] = None
    printed_at: Optional[datetime] = None
    applied_by: Optional[str] = None
    applied_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    document_ids: Optional[list[str]] = None
    metadata: Optional[dict[str, Any]] = None


class WarehouseDispensingRecord(BaseModel):
    dispensing_record_id: str = Field(..., min_length=1)
    work_order_id: Optional[str] = None
    batch_id: Optional[str] = None
    bom_id: Optional[str] = None
    component_id: Optional[str] = None
    material_code: str = Field(..., min_length=1)
    source_location_id: str = Field(..., min_length=1)
    destination_location_id: Optional[str] = None
    requested_qty: float = Field(..., gt=0)
    dispensed_qty: float = Field(..., gt=0)
    unit: str = Field(..., min_length=1)
    dispensed_by: str = Field(..., min_length=1)
    dispensed_at: datetime = Field(default_factory=utc_now)
    scale_equipment_id: Optional[str] = None
    machine_id: Optional[str] = None
    status: WarehouseRecordStatus = WarehouseRecordStatus.RELEASED
    verified_by: Optional[str] = None
    verified_at: Optional[datetime] = None
    document_ids: list[str] = Field(default_factory=list)
    signature_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_dates(self) -> "WarehouseDispensingRecord":
        self.dispensed_at = ensure_utc(self.dispensed_at)
        self.verified_at = ensure_utc(self.verified_at)
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self


class WarehouseDispensingRecordCreate(WarehouseDispensingRecord):
    pass


class WarehouseDispensingRecordUpdate(BaseModel):
    work_order_id: Optional[str] = None
    batch_id: Optional[str] = None
    bom_id: Optional[str] = None
    component_id: Optional[str] = None
    material_code: Optional[str] = None
    source_location_id: Optional[str] = None
    destination_location_id: Optional[str] = None
    requested_qty: Optional[float] = Field(default=None, gt=0)
    dispensed_qty: Optional[float] = Field(default=None, gt=0)
    unit: Optional[str] = None
    dispensed_by: Optional[str] = None
    dispensed_at: Optional[datetime] = None
    scale_equipment_id: Optional[str] = None
    machine_id: Optional[str] = None
    status: Optional[WarehouseRecordStatus] = None
    verified_by: Optional[str] = None
    verified_at: Optional[datetime] = None
    document_ids: Optional[list[str]] = None
    signature_ids: Optional[list[str]] = None
    metadata: Optional[dict[str, Any]] = None


class InventoryDispositionRecord(BaseModel):
    disposition_id: str = Field(..., min_length=1)
    entity_type: str = Field(..., min_length=1)
    entity_id: str = Field(..., min_length=1)
    disposition: WarehouseDisposition
    gr_id: Optional[str] = None
    inventory_id: Optional[str] = None
    product_id: Optional[str] = None
    material_code: Optional[str] = None
    lot_id: Optional[str] = None
    reason: str = Field(..., min_length=1)
    decided_by: str = Field(..., min_length=1)
    decided_at: datetime = Field(default_factory=utc_now)
    qa_reviewer_id: Optional[str] = None
    target_location_id: Optional[str] = None
    status: WarehouseRecordStatus = WarehouseRecordStatus.CLOSED
    label_id: Optional[str] = None
    document_ids: list[str] = Field(default_factory=list)
    signature_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_dates(self) -> "InventoryDispositionRecord":
        self.decided_at = ensure_utc(self.decided_at)
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self


class InventoryDispositionRecordCreate(InventoryDispositionRecord):
    pass


class InventoryDispositionRecordUpdate(BaseModel):
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    disposition: Optional[WarehouseDisposition] = None
    gr_id: Optional[str] = None
    inventory_id: Optional[str] = None
    product_id: Optional[str] = None
    material_code: Optional[str] = None
    lot_id: Optional[str] = None
    reason: Optional[str] = None
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None
    qa_reviewer_id: Optional[str] = None
    target_location_id: Optional[str] = None
    status: Optional[WarehouseRecordStatus] = None
    label_id: Optional[str] = None
    document_ids: Optional[list[str]] = None
    signature_ids: Optional[list[str]] = None
    metadata: Optional[dict[str, Any]] = None


class PaginatedSpecialStorageRequirementResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[SpecialStorageRequirement]


class PaginatedMaterialInwardLogResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[MaterialInwardLog]


class PaginatedWarehouseStatusLabelResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[WarehouseStatusLabel]


class PaginatedWarehouseDispensingRecordResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[WarehouseDispensingRecord]


class PaginatedInventoryDispositionRecordResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[InventoryDispositionRecord]
