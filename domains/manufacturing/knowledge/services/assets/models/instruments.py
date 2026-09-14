from datetime import datetime, timezone
from typing import Literal, Optional
from pydantic import BaseModel, Field

InstrumentCategory = Literal[
    "Analytical",
    "Measurement",
    "Monitoring",
    "Calibration",
    "Testing",
    "Inspection",
    "Other",
]
InstrumentStatus = Literal[
    "Active", "Inactive", "Calibration Due", "Under Maintenance", "Retired"
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Instrument(BaseModel):
    instrument_id: str
    name: str
    category: InstrumentCategory = "Measurement"
    manufacturer: Optional[str] = None
    model_number: Optional[str] = None
    serial_number: Optional[str] = None
    status: InstrumentStatus = "Active"
    description: Optional[str] = None

    plant_id: Optional[str] = None
    building_id: Optional[str] = None
    floor_id: Optional[str] = None
    room_id: Optional[str] = None
    bay_id: Optional[str] = None
    line_id: Optional[str] = None
    stage_id: Optional[str] = None
    workstation_id: Optional[str] = None

    measurement_range: Optional[str] = None
    unit: Optional[str] = None
    accuracy_class: Optional[str] = None
    calibration_required: bool = True

    last_calibration_date: Optional[str] = None
    calibration_frequency_days: Optional[int] = None
    calibration_next_due_date: Optional[str] = None

    last_maintenance_date: Optional[str] = None
    maintenance_frequency_days: Optional[int] = None
    maintenance_next_due_date: Optional[str] = None

    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class InstrumentCreate(Instrument):
    pass


class InstrumentUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[InstrumentCategory] = None
    manufacturer: Optional[str] = None
    model_number: Optional[str] = None
    serial_number: Optional[str] = None
    status: Optional[InstrumentStatus] = None
    description: Optional[str] = None
    measurement_range: Optional[str] = None
    unit: Optional[str] = None
    accuracy_class: Optional[str] = None
    calibration_required: Optional[bool] = None
    last_calibration_date: Optional[str] = None
    calibration_frequency_days: Optional[int] = None
    calibration_next_due_date: Optional[str] = None
    last_maintenance_date: Optional[str] = None
    maintenance_frequency_days: Optional[int] = None
    maintenance_next_due_date: Optional[str] = None


class InstrumentResponse(Instrument):
    class Config:
        from_attributes = True


class PaginatedInstrumentResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[InstrumentResponse]
