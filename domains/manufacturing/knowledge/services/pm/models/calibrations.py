from datetime import date, datetime, timedelta
from typing import Optional, Literal, List
from pydantic import BaseModel, Field, model_validator

from services.pm.models.calibration_point import CalibrationPoint
from services.auth.models.users import UserEntry

# ─────────────────────────────────────────────
# LITERALS
# ─────────────────────────────────────────────

CalibrationStatus = Literal[
    "Scheduled",
    "In Progress",
    "Completed",
    "Passed",
    "Failed",
    "Overdue",
    "Cancelled",
]

CalibrationType = Literal[
    "Routine",
    "Post-Repair",
    "Validation",
    "Verification",
    "Emergency",
]

CalibrationStandard = Literal[
    "NIST",
    "ISO 17025",
    "USP",
    "EP",
    "In-House",
    "OEM",
    "Other",
]

# ─────────────────────────────────────────────
# BASE
# ─────────────────────────────────────────────


class CalibrationRecord(BaseModel):
    record_id: str = Field(..., min_length=1)
    equipment_id: str = Field(..., min_length=1)
    work_order_id: Optional[str] = None
    instrument_tag: Optional[str] = None
    instrument_range: Optional[str] = None
    instrument_resolution: Optional[str] = None
    calibration_type: CalibrationType
    status: CalibrationStatus = "Scheduled"
    standard_used: CalibrationStandard = "In-House"
    certificate_no: Optional[str] = None
    scheduled_date: Optional[date] = None
    performed_date: Optional[date] = None
    next_due_date: Optional[date] = None
    calibration_interval_days: Optional[int] = Field(None, gt=0)
    performed_by: Optional[str] = None
    reviewed_by: Optional[str] = None
    approved_by: Optional[str] = None
    reference_instrument: Optional[str] = None
    reference_cert_no: Optional[str] = None
    reference_expiry: Optional[date] = None
    data_points: List[CalibrationPoint] = Field(default_factory=list)
    tolerance: Optional[float] = Field(None, ge=0)
    max_deviation_found: Optional[float] = None
    unit: Optional[str] = None
    room_temp_celsius: Optional[float] = None
    room_humidity_pct: Optional[float] = Field(None, ge=0, le=100)
    out_of_tolerance: bool = False
    corrective_action: Optional[str] = None
    as_found_condition: Optional[str] = None
    as_left_condition: Optional[str] = None
    remarks: Optional[str] = None
    attachments: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @model_validator(mode="after")
    def compute_max_deviation(self):
        if self.data_points:
            deviations = [
                abs(dp.deviation) for dp in self.data_points if dp.deviation is not None
            ]
            if deviations:
                self.max_deviation_found = round(max(deviations), 6)
        return self

    @model_validator(mode="after")
    def flag_out_of_tolerance(self):
        if self.max_deviation_found is not None and self.tolerance is not None:
            if self.max_deviation_found > self.tolerance:
                self.out_of_tolerance = True
        return self

    @model_validator(mode="after")
    def next_due_after_performed(self):
        if self.performed_date and self.next_due_date:
            if self.next_due_date <= self.performed_date:
                raise ValueError("'next_due_date' must be after 'performed_date'.")
        return self

    @model_validator(mode="after")
    def auto_next_due_date(self):
        if (
            self.performed_date
            and self.calibration_interval_days
            and not self.next_due_date
        ):
            self.next_due_date = self.performed_date + timedelta(
                days=self.calibration_interval_days
            )
        return self

    @model_validator(mode="after")
    def passed_requires_data_points(self):
        if self.status == "Passed" and not self.data_points:
            raise ValueError(
                "Status 'Passed' requires at least one calibration data point."
            )
        return self


# ─────────────────────────────────────────────
# CREATE
# ─────────────────────────────────────────────


class CalibrationRecordCreate(CalibrationRecord):
    pass


# ─────────────────────────────────────────────
# UPDATE
# ─────────────────────────────────────────────


class CalibrationRecordUpdate(BaseModel):
    calibration_type: Optional[CalibrationType] = None
    status: Optional[CalibrationStatus] = None
    standard_used: Optional[CalibrationStandard] = None
    certificate_no: Optional[str] = None
    instrument_tag: Optional[str] = None
    instrument_range: Optional[str] = None
    instrument_resolution: Optional[str] = None
    scheduled_date: Optional[date] = None
    performed_date: Optional[date] = None
    next_due_date: Optional[date] = None
    calibration_interval_days: Optional[int] = Field(None, gt=0)
    performed_by: Optional[UserEntry] = None
    reviewed_by: Optional[UserEntry] = None
    approved_by: Optional[UserEntry] = None
    reference_instrument: Optional[str] = None
    reference_cert_no: Optional[str] = None
    reference_expiry: Optional[date] = None
    data_points: Optional[List[CalibrationPoint]] = None
    tolerance: Optional[float] = Field(None, ge=0)
    unit: Optional[str] = None
    room_temp_celsius: Optional[float] = None
    room_humidity_pct: Optional[float] = Field(None, ge=0, le=100)
    corrective_action: Optional[str] = None
    as_found_condition: Optional[str] = None
    as_left_condition: Optional[str] = None
    remarks: Optional[str] = None
    attachments: Optional[List[str]] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ─────────────────────────────────────────────
# RESPONSE & PAGINATION
# ─────────────────────────────────────────────


class CalibrationRecordResponse(CalibrationRecord):
    class Config:
        from_attributes = True


class PaginatedCalibrationResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: List[CalibrationRecordResponse]
