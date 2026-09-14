from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from services.procurement.models.goods_receipts import Attachment, AuditInfo
from services.procurement.models.sampling import DocumentStatus
from services.products.models.product_common import UnitOfMeasure


class InspectionType(str, Enum):
    INCOMING = "Incoming"
    IN_PROCESS = "In-Process"
    FINAL = "Final"
    AUDIT = "Audit"


class TestResultType(str, Enum):
    QUANTITATIVE = "Quantitative"
    QUALITATIVE = "Qualitative"
    VISUAL = "Visual"
    DIMENSIONAL = "Dimensional"


class QCDisposition(str, Enum):
    ACCEPT = "Accept"
    REJECT = "Reject"
    CONDITIONAL_ACCEPT = "Conditional Accept"
    PENDING = "Pending"


class NCRStatus(str, Enum):
    OPEN = "Open"
    IN_REVIEW = "In Review"
    PENDING_SUPPLIER_RESPONSE = "Pending Supplier Response"
    CLOSED = "Closed"


class NCRDisposition(str, Enum):
    RETURN_TO_SUPPLIER = "Return to Supplier"
    REWORK = "Rework"
    SCRAP = "Scrap"
    USE_AS_IS = "Use As Is"
    RE_INSPECT = "Re-inspect"


class DefectSeverity(str, Enum):
    CRITICAL = "Critical"
    MAJOR = "Major"
    MINOR = "Minor"


class SpecificationLimit(BaseModel):
    """Acceptance limits for a single test parameter."""

    parameter_name: str
    unit: Optional[str] = None
    lower_spec_limit: Optional[Decimal] = None
    upper_spec_limit: Optional[Decimal] = None
    target: Optional[Decimal] = None
    acceptable_values: list[str] = Field(
        default_factory=list,
        description="For qualitative/enumerated parameters, e.g. ['Green', 'Yellow']",
    )


class MaterialSpecification(BaseModel):
    """Version-controlled specification for a material / product."""

    spec_id: UUID = Field(default_factory=uuid4)
    spec_code: str
    material_code: str
    version: str
    effective_date: datetime
    approved_by: str
    status: DocumentStatus = DocumentStatus.APPROVED
    parameters: list[SpecificationLimit]
    documents: list[Attachment] = Field(default_factory=list)
    audit: AuditInfo = Field(default_factory=AuditInfo)


class MaterialSpecificationCreate(MaterialSpecification):
    """Schema for creating material specifications."""


class MaterialSpecificationUpdate(BaseModel):
    spec_code: Optional[str] = None
    material_code: Optional[str] = None
    version: Optional[str] = None
    effective_date: Optional[datetime] = None
    approved_by: Optional[str] = None
    status: Optional[DocumentStatus] = None
    parameters: Optional[list[SpecificationLimit]] = None
    documents: Optional[list[Attachment]] = None
    audit: Optional[AuditInfo] = None


class MaterialSpecificationResponse(MaterialSpecification):
    model_config = ConfigDict(from_attributes=True)


class PaginatedMaterialSpecificationResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[MaterialSpecificationResponse]


class InspectionChecklistItem(BaseModel):
    """Single test/check within an inspection checklist template."""

    item_id: UUID = Field(default_factory=uuid4)
    sequence: int
    parameter_name: str
    test_method: Optional[str] = None
    result_type: TestResultType
    spec_limit: Optional[SpecificationLimit] = None
    mandatory: bool = True
    instrument_required: Optional[str] = None


class InspectionChecklist(BaseModel):
    """Template checklist assigned to a material type + inspection type."""

    checklist_id: UUID = Field(default_factory=uuid4)
    name: str
    material_code: Optional[str] = None
    inspection_type: InspectionType
    version: str
    items: list[InspectionChecklistItem]
    approved_by: str
    effective_from: datetime
    status: DocumentStatus = DocumentStatus.APPROVED


class InspectionChecklistCreate(InspectionChecklist):
    """Schema for creating inspection checklists."""


class InspectionChecklistUpdate(BaseModel):
    name: Optional[str] = None
    material_code: Optional[str] = None
    inspection_type: Optional[InspectionType] = None
    version: Optional[str] = None
    items: Optional[list[InspectionChecklistItem]] = None
    approved_by: Optional[str] = None
    effective_from: Optional[datetime] = None
    status: Optional[DocumentStatus] = None


class InspectionChecklistResponse(InspectionChecklist):
    model_config = ConfigDict(from_attributes=True)


class PaginatedInspectionChecklistResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[InspectionChecklistResponse]


class TestResult(BaseModel):
    """Result recorded for one checklist item during a QC inspection."""

    result_id: UUID = Field(default_factory=uuid4)
    checklist_item_id: UUID
    parameter_name: str
    result_type: TestResultType
    numeric_value: Optional[Decimal] = None
    unit: Optional[str] = None
    lower_spec_limit: Optional[Decimal] = None
    upper_spec_limit: Optional[Decimal] = None
    text_value: Optional[str] = None
    is_oos: bool = False
    instrument_id: Optional[str] = None
    calibration_due: Optional[datetime] = None
    notes: Optional[str] = None

    @model_validator(mode="after")
    def flag_oos(self) -> "TestResult":
        if self.numeric_value is not None:
            lsl_breach = (
                self.lower_spec_limit is not None
                and self.numeric_value < self.lower_spec_limit
            )
            usl_breach = (
                self.upper_spec_limit is not None
                and self.numeric_value > self.upper_spec_limit
            )
            self.is_oos = lsl_breach or usl_breach
        return self


class InspectionStatus(str, Enum):
    SCHEDULED = "Scheduled"
    IN_PROGRESS = "In Progress"
    COMPLETED = "Completed"
    RE_INSPECT = "Re-Inspection Required"


class QCInspection(BaseModel):
    """A complete QC inspection event for a received lot / sample."""

    inspection_id: UUID = Field(default_factory=uuid4)
    inspection_code: str
    inspection_type: InspectionType
    gr_id: UUID
    sample_id: Optional[UUID] = None
    material_code: str
    batch_lot_number: Optional[str] = None
    spec_id: UUID
    checklist_id: UUID
    inspector_id: str
    inspector_name: str
    inspected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    status: InspectionStatus = InspectionStatus.IN_PROGRESS
    results: list[TestResult] = Field(default_factory=list)
    oos_count: int = 0
    disposition: QCDisposition = QCDisposition.PENDING
    disposition_reason: Optional[str] = None
    disposition_by: Optional[str] = None
    disposition_at: Optional[datetime] = None
    digital_signature: Optional[str] = Field(
        default=None,
        description="21 CFR Part 11 compliant e-signature token",
    )
    supporting_docs: list[Attachment] = Field(default_factory=list)
    notes: Optional[str] = None
    audit: AuditInfo = Field(default_factory=AuditInfo)

    @model_validator(mode="after")
    def compute_oos_count(self) -> "QCInspection":
        self.oos_count = sum(1 for result in self.results if result.is_oos)
        return self

    def finalize_disposition(
        self,
        disposition: QCDisposition,
        by: str,
        reason: Optional[str] = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        self.disposition = disposition
        self.disposition_by = by
        self.disposition_at = now
        self.disposition_reason = reason
        self.status = InspectionStatus.COMPLETED
        self.completed_at = now


class QCInspectionCreate(QCInspection):
    """Schema for creating QC inspections."""


class QCInspectionUpdate(BaseModel):
    inspection_code: Optional[str] = None
    inspection_type: Optional[InspectionType] = None
    sample_id: Optional[UUID] = None
    material_code: Optional[str] = None
    batch_lot_number: Optional[str] = None
    spec_id: Optional[UUID] = None
    checklist_id: Optional[UUID] = None
    inspector_id: Optional[str] = None
    inspector_name: Optional[str] = None
    inspected_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    status: Optional[InspectionStatus] = None
    results: Optional[list[TestResult]] = None
    disposition: Optional[QCDisposition] = None
    disposition_reason: Optional[str] = None
    disposition_by: Optional[str] = None
    disposition_at: Optional[datetime] = None
    digital_signature: Optional[str] = None
    supporting_docs: Optional[list[Attachment]] = None
    notes: Optional[str] = None
    audit: Optional[AuditInfo] = None


class QCInspectionResponse(QCInspection):
    model_config = ConfigDict(from_attributes=True)


class PaginatedQCInspectionResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[QCInspectionResponse]


class ReInspectionRecord(BaseModel):
    """Links a follow-up inspection to the original."""

    reinspection_id: UUID = Field(default_factory=uuid4)
    original_inspection_id: UUID
    new_inspection_id: UUID
    reason: str
    authorised_by: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ReInspectionRecordCreate(ReInspectionRecord):
    """Schema for creating re-inspection links."""


class ReInspectionRecordUpdate(BaseModel):
    new_inspection_id: Optional[UUID] = None
    reason: Optional[str] = None
    authorised_by: Optional[str] = None


class ReInspectionRecordResponse(ReInspectionRecord):
    model_config = ConfigDict(from_attributes=True)


class PaginatedReInspectionRecordResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[ReInspectionRecordResponse]


class DefectClassification(str, Enum):
    CRITICAL = "Critical"
    MAJOR = "Major"
    MINOR = "Minor"


class RootCauseCategory(str, Enum):
    SUPPLIER_PROCESS = "Supplier Process"
    TRANSPORT_DAMAGE = "Transport / Damage"
    STORAGE = "Storage Condition"
    DOCUMENTATION = "Documentation Error"
    MEASUREMENT = "Measurement / Instrument"
    SPECIFICATION = "Specification Error"
    UNKNOWN = "Unknown"


class CAPARecord(BaseModel):
    """Corrective Action / Preventive Action record linked to an NCR."""

    capa_id: UUID = Field(default_factory=uuid4)
    ncr_id: UUID
    capa_type: str
    description: str
    assigned_to: str
    due_date: datetime
    completed_at: Optional[datetime] = None
    effectiveness_verified: bool = False
    verified_by: Optional[str] = None
    notes: Optional[str] = None
    audit: AuditInfo = Field(default_factory=AuditInfo)


class SupplierDebitNote(BaseModel):
    """Formal debit note issued to supplier for attributable non-conformance."""

    debit_note_id: UUID = Field(default_factory=uuid4)
    ncr_id: UUID
    supplier_id: str
    amount: Decimal
    currency: str = Field(max_length=3)
    reason: str
    issued_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    issued_by: str
    acknowledged_by_supplier: bool = False
    acknowledged_at: Optional[datetime] = None
    notes: Optional[str] = None


class NonConformanceReport(BaseModel):
    """
    NCR: raised automatically on OOS / Reject disposition,
    or manually by authorised QA personnel.
    """

    ncr_id: UUID = Field(default_factory=uuid4)
    ncr_number: str
    inspection_id: Optional[UUID] = None
    gr_id: UUID
    material_code: str
    batch_lot_number: Optional[str] = None
    supplier_id: str
    defect_description: str
    defect_classification: DefectClassification
    root_cause_category: RootCauseCategory = RootCauseCategory.UNKNOWN
    qty_affected: Decimal
    uom: UnitOfMeasure
    financial_value: Optional[Decimal] = None
    currency: Optional[str] = Field(default=None, max_length=3)
    status: NCRStatus = NCRStatus.OPEN
    disposition: Optional[NCRDisposition] = None
    disposition_approver: Optional[str] = None
    disposition_approved_at: Optional[datetime] = None
    opened_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    assigned_to: Optional[str] = None
    escalated_to: Optional[str] = None
    escalated_at: Optional[datetime] = None
    supplier_notified_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    closed_by: Optional[str] = None
    capas: list[CAPARecord] = Field(default_factory=list)
    debit_notes: list[SupplierDebitNote] = Field(default_factory=list)
    attachments: list[Attachment] = Field(default_factory=list)
    notes: Optional[str] = None
    audit: AuditInfo = Field(default_factory=AuditInfo)

    @computed_field
    @property
    def age_days(self) -> Optional[float]:
        """Days the NCR has been open."""
        end = self.closed_at or datetime.now(timezone.utc)
        opened = self.opened_at
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        return (end - opened).total_seconds() / 86400

    def close(self, closed_by: str, notes: Optional[str] = None) -> None:
        self.status = NCRStatus.CLOSED
        self.closed_by = closed_by
        self.closed_at = datetime.now(timezone.utc)
        if notes:
            self.notes = (self.notes or "") + f"\n[CLOSE] {notes}"


class NonConformanceReportCreate(NonConformanceReport):
    """Schema for creating NCRs."""


class NonConformanceReportUpdate(BaseModel):
    inspection_id: Optional[UUID] = None
    material_code: Optional[str] = None
    batch_lot_number: Optional[str] = None
    supplier_id: Optional[str] = None
    defect_description: Optional[str] = None
    defect_classification: Optional[DefectClassification] = None
    root_cause_category: Optional[RootCauseCategory] = None
    qty_affected: Optional[Decimal] = None
    uom: Optional[UnitOfMeasure] = None
    financial_value: Optional[Decimal] = None
    currency: Optional[str] = Field(default=None, max_length=3)
    status: Optional[NCRStatus] = None
    disposition: Optional[NCRDisposition] = None
    disposition_approver: Optional[str] = None
    disposition_approved_at: Optional[datetime] = None
    assigned_to: Optional[str] = None
    escalated_to: Optional[str] = None
    escalated_at: Optional[datetime] = None
    supplier_notified_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    closed_by: Optional[str] = None
    capas: Optional[list[CAPARecord]] = None
    debit_notes: Optional[list[SupplierDebitNote]] = None
    attachments: Optional[list[Attachment]] = None
    notes: Optional[str] = None
    audit: Optional[AuditInfo] = None


class NonConformanceReportResponse(NonConformanceReport):
    model_config = ConfigDict(from_attributes=True)


class PaginatedNonConformanceReportResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[NonConformanceReportResponse]
