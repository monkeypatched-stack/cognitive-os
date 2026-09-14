from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


class CppCqaType(str, Enum):
    CPP = "CPP"
    CQA = "CQA"


class CppCqaStatus(str, Enum):
    DRAFT = "Draft"
    APPROVED = "Approved"
    EFFECTIVE = "Effective"
    RETIRED = "Retired"


class CppCqaSeverity(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class CppCqaLimit(BaseModel):
    target: Optional[float | str] = None
    lower_limit: Optional[float] = None
    upper_limit: Optional[float] = None
    unit: Optional[str] = None
    acceptance_criteria: Optional[str] = None


class CppCqaRegistryEntry(BaseModel):
    registry_id: str = Field(..., min_length=1)
    registry_type: CppCqaType
    name: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)

    process_definition_id: str = Field(..., min_length=1)
    process_step_id: Optional[str] = None
    stage_id: Optional[str] = None
    workstation_id: Optional[str] = None
    product_id: Optional[str] = None
    bom_id: Optional[str] = None

    parameter: str = Field(..., min_length=1)
    unit: Optional[str] = None
    method: Optional[str] = None
    method_reference: Optional[str] = None
    limits: CppCqaLimit = Field(default_factory=CppCqaLimit)
    severity: CppCqaSeverity = CppCqaSeverity.MEDIUM
    status: CppCqaStatus = CppCqaStatus.DRAFT
    owner_role: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    effective_from: Optional[datetime] = None
    retired_at: Optional[datetime] = None

    linked_ipc_checkpoint_ids: list[str] = Field(default_factory=list)
    linked_document_ids: list[str] = Field(default_factory=list)
    linked_batch_execution_record_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_and_validate(self) -> "CppCqaRegistryEntry":
        self.created_at = ensure_utc(self.created_at) or utc_now()
        self.updated_at = ensure_utc(self.updated_at) or utc_now()
        self.approved_at = ensure_utc(self.approved_at)
        self.effective_from = ensure_utc(self.effective_from)
        self.retired_at = ensure_utc(self.retired_at)

        if (
            self.status in {CppCqaStatus.APPROVED, CppCqaStatus.EFFECTIVE}
            and not self.approved_by
        ):
            raise ValueError(
                "approved/effective CPP/CQA registry entries must include approved_by."
            )
        if (
            self.retired_at
            and self.effective_from
            and self.retired_at < self.effective_from
        ):
            raise ValueError("retired_at cannot be before effective_from.")
        return self

    model_config = {"use_enum_values": True}


class CppCqaRegistryCreate(CppCqaRegistryEntry):
    pass


class CppCqaRegistryUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    process_step_id: Optional[str] = None
    stage_id: Optional[str] = None
    workstation_id: Optional[str] = None
    product_id: Optional[str] = None
    bom_id: Optional[str] = None
    parameter: Optional[str] = None
    unit: Optional[str] = None
    method: Optional[str] = None
    method_reference: Optional[str] = None
    limits: Optional[CppCqaLimit] = None
    severity: Optional[CppCqaSeverity] = None
    status: Optional[CppCqaStatus] = None
    owner_role: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    effective_from: Optional[datetime] = None
    retired_at: Optional[datetime] = None
    linked_ipc_checkpoint_ids: Optional[list[str]] = None
    linked_document_ids: Optional[list[str]] = None
    linked_batch_execution_record_ids: Optional[list[str]] = None
    metadata: Optional[dict[str, Any]] = None
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "CppCqaRegistryUpdate":
        self.updated_at = ensure_utc(self.updated_at) or utc_now()
        self.approved_at = ensure_utc(self.approved_at)
        self.effective_from = ensure_utc(self.effective_from)
        self.retired_at = ensure_utc(self.retired_at)
        return self


class CppCqaRegistryResponse(CppCqaRegistryEntry):
    class Config:
        from_attributes = True


class PaginatedCppCqaRegistryResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[CppCqaRegistryResponse]
