from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.procurement.models.goods_receipts import Attachment, AuditInfo
from services.products.models.product_common import UnitOfMeasure


class DocumentStatus(str, Enum):
    DRAFT = "Draft"
    APPROVED = "Approved"
    OBSOLETE = "Obsolete"
    ARCHIVED = "Archived"


class SamplingMethod(str, Enum):
    AQL_STANDARD = "AQL Standard"
    FIXED_QUANTITY = "Fixed Quantity"
    PERCENTAGE_BASED = "Percentage Based"
    SKIP_LOT = "Skip Lot"
    ZERO_DEFECT = "Zero Defect"


class RiskClassification(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class SampleType(str, Enum):
    RETAIN = "Retain Sample"
    LAB = "Send-to-Lab Sample"
    IN_HOUSE = "In-House Test Sample"


class SampleStatus(str, Enum):
    PENDING = "Pending Collection"
    COLLECTED = "Collected"
    IN_TRANSIT = "In Transit to Lab"
    IN_TESTING = "In Testing"
    RESULTS_IN = "Results Received"
    DISPOSED = "Disposed"


class AQLLevel(str, Enum):
    """Standard AQL inspection levels."""

    LEVEL_I = "I"
    LEVEL_II = "II"
    LEVEL_III = "III"
    SPECIAL_S1 = "S-1"
    SPECIAL_S2 = "S-2"
    SPECIAL_S3 = "S-3"
    SPECIAL_S4 = "S-4"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SamplingPlanVersion(BaseModel):
    version_number: str
    effective_date: datetime
    change_summary: Optional[str] = None
    approved_by: str
    status: DocumentStatus = DocumentStatus.APPROVED


class SamplingPlan(BaseModel):
    """
    Master sampling plan for a material type / supplier combination.
    Version-controlled; QA managers own this record.
    """

    plan_id: str = Field(default_factory=lambda: str(uuid4()))
    plan_code: str
    name: str
    material_code: Optional[str] = None
    supplier_id: Optional[str] = None
    risk_classification: RiskClassification = RiskClassification.MEDIUM
    sampling_method: SamplingMethod
    aql_level: Optional[AQLLevel] = None
    fixed_sample_qty: Optional[int] = None
    percentage: Optional[float] = Field(default=None, ge=0, le=100)
    skip_lot_frequency: Optional[int] = Field(
        default=None,
        ge=1,
        description="Inspect every Nth lot; only valid when method is SKIP_LOT",
    )
    min_sample_qty: Optional[int] = None
    max_sample_qty: Optional[int] = None
    current_version: SamplingPlanVersion
    history: list[SamplingPlanVersion] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_method_fields(self) -> "SamplingPlan":
        if (
            self.sampling_method == SamplingMethod.FIXED_QUANTITY
            and self.fixed_sample_qty is None
        ):
            raise ValueError("fixed_sample_qty is required for FIXED_QUANTITY method.")
        if (
            self.sampling_method == SamplingMethod.PERCENTAGE_BASED
            and self.percentage is None
        ):
            raise ValueError("percentage is required for PERCENTAGE_BASED method.")
        if (
            self.sampling_method == SamplingMethod.SKIP_LOT
            and self.skip_lot_frequency is None
        ):
            raise ValueError("skip_lot_frequency is required for SKIP_LOT method.")
        return self


class SamplingPlanCreate(SamplingPlan):
    """Schema for creating sampling plans."""


class SamplingPlanUpdate(BaseModel):
    plan_code: Optional[str] = None
    name: Optional[str] = None
    material_code: Optional[str] = None
    supplier_id: Optional[str] = None
    risk_classification: Optional[RiskClassification] = None
    sampling_method: Optional[SamplingMethod] = None
    aql_level: Optional[AQLLevel] = None
    fixed_sample_qty: Optional[int] = None
    percentage: Optional[float] = Field(default=None, ge=0, le=100)
    skip_lot_frequency: Optional[int] = Field(default=None, ge=1)
    min_sample_qty: Optional[int] = None
    max_sample_qty: Optional[int] = None
    current_version: Optional[SamplingPlanVersion] = None
    history: Optional[list[SamplingPlanVersion]] = None


class SamplingPlanResponse(SamplingPlan):
    model_config = ConfigDict(from_attributes=True)


class PaginatedSamplingPlanResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[SamplingPlanResponse]


class ChainOfCustodyEntry(BaseModel):
    """Single entry in a sample's chain of custody log."""

    entry_id: UUID = Field(default_factory=uuid4)
    timestamp: datetime = Field(default_factory=utc_now)
    transferred_by: str
    transferred_to: str
    location: str
    action: str
    notes: Optional[str] = None


class Sample(BaseModel):
    """
    Individual sample record: from collection through testing to disposition.
    """

    sample_id: str = Field(default_factory=lambda: str(uuid4()))
    sample_code: str = Field(description="Human-readable unique Sample ID")
    gr_id: Optional[str] = None
    gr_line_id: Optional[str] = None
    sampling_plan_id: Optional[str] = None
    sample_type: SampleType = SampleType.IN_HOUSE
    status: SampleStatus = SampleStatus.PENDING
    collected_by: Optional[str] = None
    collected_at: Optional[datetime] = None
    collection_location: Optional[str] = None
    container_details: Optional[str] = None
    sample_qty: Optional[Decimal] = None
    uom: Optional[UnitOfMeasure] = None
    lims_request_id: Optional[str] = None
    lims_result_id: Optional[str] = None
    lab_received_at: Optional[datetime] = None
    results_received_at: Optional[datetime] = None
    disposed_at: Optional[datetime] = None
    disposed_by: Optional[str] = None
    disposal_method: Optional[str] = None
    chain_of_custody: list[ChainOfCustodyEntry] = Field(default_factory=list)
    attachments: list[Attachment] = Field(default_factory=list)
    notes: Optional[str] = None
    audit: AuditInfo = Field(default_factory=AuditInfo)

    def add_custody_entry(self, entry: ChainOfCustodyEntry) -> None:
        self.chain_of_custody.append(entry)


class SampleCreate(Sample):
    """Schema for creating samples."""


class SampleUpdate(BaseModel):
    sample_code: Optional[str] = None
    sample_type: Optional[SampleType] = None
    status: Optional[SampleStatus] = None
    collected_by: Optional[str] = None
    collected_at: Optional[datetime] = None
    collection_location: Optional[str] = None
    container_details: Optional[str] = None
    sample_qty: Optional[Decimal] = None
    uom: Optional[UnitOfMeasure] = None
    lims_request_id: Optional[str] = None
    lims_result_id: Optional[str] = None
    lab_received_at: Optional[datetime] = None
    results_received_at: Optional[datetime] = None
    disposed_at: Optional[datetime] = None
    disposed_by: Optional[str] = None
    disposal_method: Optional[str] = None
    chain_of_custody: Optional[list[ChainOfCustodyEntry]] = None
    attachments: Optional[list[Attachment]] = None
    notes: Optional[str] = None
    audit: Optional[AuditInfo] = None


class SampleResponse(Sample):
    model_config = ConfigDict(from_attributes=True)


class PaginatedSampleResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[SampleResponse]


class ResamplingRecord(BaseModel):
    """Documents a re-sampling event with reason and approval."""

    resample_id: str = Field(default_factory=lambda: str(uuid4()))
    original_sample_id: Optional[str] = None
    new_sample_id: Optional[str] = None
    reason_code: str = "Resampling"
    reason_detail: str = "Resampling requested"
    requested_by: str = "System"
    approved_by: str = "System"
    approved_at: datetime = Field(default_factory=utc_now)
    notes: Optional[str] = None


class ResamplingRecordCreate(ResamplingRecord):
    """Schema for creating resampling records."""


class ResamplingRecordUpdate(BaseModel):
    new_sample_id: Optional[str] = None
    reason_code: Optional[str] = None
    reason_detail: Optional[str] = None
    requested_by: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    notes: Optional[str] = None


class ResamplingRecordResponse(ResamplingRecord):
    model_config = ConfigDict(from_attributes=True)


class PaginatedResamplingRecordResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[ResamplingRecordResponse]
