from datetime import date, datetime
from typing import Optional, Literal, List
from pydantic import BaseModel, Field, model_validator

from services.assets.models.material import MaterialUsed
from services.auth.models.users import UserEntry

# ─────────────────────────────────────────────
# LITERALS
# ─────────────────────────────────────────────

CleaningStatus = Literal[
    "Scheduled",
    "In Progress",
    "Completed",
    "Verified",
    "Failed",
    "Cancelled",
    "Pre-Production",
    "Post-Production",
]

CleaningMethod = Literal[
    "CIP",
    "COP",
    "Manual",
    "Automated",
    "Spray Wash",
    "Ultrasonic",
    "Other",
]

CleaningType = Literal[
    "Routine",
    "Deep Clean",
    "Post-Production",
    "Pre-Production",
    "Changeover",
    "Validation",
    "Sanitization",
]

VerificationMethod = Literal[
    "Visual Inspection",
    "Swab Test",
    "Rinse Sample",
    "TOC Analysis",
    "HPLC",
    "pH Test",
    "Conductivity",
    "None",
]

# ─────────────────────────────────────────────
# BASE
# ─────────────────────────────────────────────


class CleaningRecord(BaseModel):
    record_id: str = Field(..., min_length=1)
    equipment_id: str = Field(..., min_length=1)
    work_order_id: Optional[str] = None
    batch_execution_record_id: Optional[str] = None
    batch_id: Optional[str] = None
    process_definition_id: Optional[str] = None
    process_step_id: Optional[str] = None
    instruction_template_id: Optional[str] = None
    instruction_step_sequence: Optional[int] = Field(default=None, ge=1)
    cleaning_requirement_id: Optional[str] = None
    cleaning_type: CleaningType
    cleaning_method: CleaningMethod
    status: CleaningStatus = "Scheduled"
    scheduled_start: Optional[datetime] = None
    scheduled_end: Optional[datetime] = None
    actual_start: Optional[datetime] = None
    actual_end: Optional[datetime] = None
    performed_by: Optional[str] = None
    verified_by: Optional[str] = None
    approved_by: Optional[str] = None
    chemicals_used: List[MaterialUsed] = Field(default_factory=list)
    water_type: Optional[str] = None
    water_volume_liters: Optional[float] = Field(None, gt=0)
    verification_method: VerificationMethod = "Visual Inspection"
    verification_result: Optional[str] = None
    residue_limit_ppm: Optional[float] = Field(None, ge=0)
    residue_found_ppm: Optional[float] = Field(None, ge=0)
    previous_product: Optional[str] = None
    current_product: Optional[str] = None
    batch_no: Optional[str] = None
    room_temp_celsius: Optional[float] = None
    room_humidity_pct: Optional[float] = Field(None, ge=0, le=100)
    remarks: Optional[str] = None
    attachments: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @model_validator(mode="after")
    def actual_end_after_start(self):
        if self.actual_start and self.actual_end:
            if self.actual_end < self.actual_start:
                raise ValueError("'actual_end' cannot be before 'actual_start'.")
        return self

    @model_validator(mode="after")
    def scheduled_end_after_start(self):
        if self.scheduled_start and self.scheduled_end:
            if self.scheduled_end < self.scheduled_start:
                raise ValueError("'scheduled_end' cannot be before 'scheduled_start'.")
        return self

    @model_validator(mode="after")
    def residue_within_limit(self):
        if (
            self.residue_found_ppm is not None
            and self.residue_limit_ppm is not None
            and self.status == "Verified"
        ):
            if self.residue_found_ppm > self.residue_limit_ppm:
                raise ValueError(
                    f"Residue found ({self.residue_found_ppm} ppm) exceeds limit "
                    f"({self.residue_limit_ppm} ppm) — cannot mark as Verified."
                )
        return self

    @model_validator(mode="after")
    def verified_required_cleaning_has_gmp_evidence(self):
        if self.status == "Verified" and self.cleaning_requirement_id:
            missing = []
            if not self.actual_start or not self.actual_end:
                missing.append("actual_start/actual_end")
            if not self.performed_by:
                missing.append("performed_by")
            if not self.verified_by:
                missing.append("verified_by")
            if not self.approved_by:
                missing.append("approved_by")
            if not self.attachments:
                missing.append("attachments")
            if self.verification_result != "Pass":
                missing.append("verification_result=Pass")
            if missing:
                raise ValueError(
                    f"verified required cleaning records must include {', '.join(missing)}."
                )
        return self


# ─────────────────────────────────────────────
# CREATE
# ─────────────────────────────────────────────


class CleaningRecordCreate(CleaningRecord):
    pass


# ─────────────────────────────────────────────
# UPDATE
# ─────────────────────────────────────────────


class CleaningRecordUpdate(BaseModel):
    equipment_id: Optional[str] = None
    work_order_id: Optional[str] = None
    batch_execution_record_id: Optional[str] = None
    batch_id: Optional[str] = None
    process_definition_id: Optional[str] = None
    process_step_id: Optional[str] = None
    instruction_template_id: Optional[str] = None
    instruction_step_sequence: Optional[int] = Field(default=None, ge=1)
    cleaning_requirement_id: Optional[str] = None
    cleaning_type: Optional[CleaningType] = None
    cleaning_method: Optional[CleaningMethod] = None
    status: Optional[CleaningStatus] = None
    scheduled_start: Optional[datetime] = None
    scheduled_end: Optional[datetime] = None
    actual_start: Optional[datetime] = None
    actual_end: Optional[datetime] = None
    performed_by: Optional[List[UserEntry]] = None
    verified_by: Optional[UserEntry] = None
    approved_by: Optional[UserEntry] = None
    chemicals_used: Optional[List[MaterialUsed]] = None
    water_type: Optional[str] = None
    water_volume_liters: Optional[float] = Field(None, gt=0)
    verification_method: Optional[VerificationMethod] = None
    verification_result: Optional[str] = None
    residue_limit_ppm: Optional[float] = Field(None, ge=0)
    residue_found_ppm: Optional[float] = Field(None, ge=0)
    previous_product: Optional[str] = None
    next_product: Optional[str] = None
    batch_no: Optional[str] = None
    room_temp_celsius: Optional[float] = None
    room_humidity_pct: Optional[float] = Field(None, ge=0, le=100)
    remarks: Optional[str] = None
    attachments: Optional[List[str]] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ─────────────────────────────────────────────
# RESPONSE & PAGINATION
# ─────────────────────────────────────────────


class CleaningRecordResponse(CleaningRecord):
    class Config:
        from_attributes = True


class PaginatedCleaningRecordResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: List[CleaningRecordResponse]
