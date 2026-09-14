from typing import Literal, Optional, List
from pydantic import BaseModel, Field, field_validator
from datetime import date

from services.pm.models.calibrations import CalibrationRecord
from services.pm.models.maintainanceLog import MaintenanceLog

EquipmentStatus = Literal["active", "inactive", "maintenance", "retired", "calibration"]
EquipmentCategory = Literal[
    "Glassware",
    "Instrument",
    "Storage",
    "Safety",
    "Filtration",
    "Dispensing",
    "Labware",
    "Documentation",
    "Machine",
]


def _normalize_equipment_status(value):
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {
            "active",
            "inactive",
            "maintenance",
            "retired",
            "calibration",
        }:
            return normalized
    return value


class RegulatoryInfo(BaseModel):
    gmp_compliant: bool = False
    fda_registered: bool = False
    ce_marked: bool = False
    iso_certified: bool = False
    certification_numbers: Optional[List[str]] = None
    compliance_standards: Optional[List[str]] = Field(
        default=None, description="e.g., USP, BP, ISO 9001"
    )


# ── Shared location / classification mixin ────────────────────────────────────


class _LocationMixin(BaseModel):
    plant_id: Optional[str] = None
    building_id: Optional[str] = None
    floor_id: Optional[str] = None
    room_id: Optional[str] = None
    bay_id: Optional[str] = None


class _ClassificationMixin(BaseModel):
    family_id: Optional[str] = None
    class_id: Optional[str] = None
    subclass_id: Optional[str] = None


# ── Models ────────────────────────────────────────────────────────────────────


class PharmaceuticalEquipment(_LocationMixin, _ClassificationMixin):
    workstation_id: str = Field(..., description="Unique equipment identifier")
    equipment_id: str = Field(..., description="Unique equipment identifier")
    name: str
    category: EquipmentCategory
    manufacturer: str
    model_number: Optional[str] = None
    serial_number: Optional[str] = None
    asset_tag: Optional[str] = None
    purchase_date: Optional[date] = None
    expiry_date: Optional[date] = None
    status: EquipmentStatus = "active"
    material: Optional[str] = Field(
        default=None,
        description="e.g., Borosilicate glass, stainless steel 316L, polypropylene",
    )
    capacity_or_range: Optional[str] = Field(
        default=None, description="e.g., 500 mL, 0-200°C, 0.001-200 g"
    )
    accuracy: Optional[str] = Field(default=None, description="e.g., ±0.01 g, ±0.1°C")
    sterility_grade: Optional[str] = Field(
        default=None, description="e.g., Sterile, Non-sterile, Grade A/B/C/D"
    )
    requires_calibration: bool = False
    # calibration
    calibration_frequency_days: Optional[int] = None
    last_calibration_date: Optional[date] = None
    calibration_next_due_date: Optional[date] = None
    calibration_records: Optional[List[CalibrationRecord]] = []
    # maintenance
    maintenance_frequency_days: Optional[int] = None
    last_maintenance_date: Optional[date] = None
    maintenace_next_due_date: Optional[date] = None
    maintenance_records: Optional[List[MaintenanceLog]] = []
    # cleaning
    cleaning_frequency_days: Optional[int] = None
    last_cleaning_date: Optional[date] = None
    cleaning_next_due_date: Optional[date] = None
    regulatory_info: Optional[RegulatoryInfo] = None
    supplier: Optional[str] = None
    purchase_cost: Optional[float] = Field(default=None, ge=0)
    notes: Optional[str] = None
    tags: Optional[List[str]] = Field(
        default=None,
        description="e.g., ['sterile', 'single-use', 'critical', 'controlled']",
    )

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value):
        return _normalize_equipment_status(value)


class PharmaceuticalEquipmentCreate(_LocationMixin, _ClassificationMixin):
    workstation_id: str = Field(..., description="Unique equipment identifier")
    equipment_id: str = Field(..., description="Unique equipment identifier")
    name: str
    category: EquipmentCategory
    manufacturer: str
    model_number: Optional[str] = None
    serial_number: Optional[str] = None
    asset_tag: Optional[str] = None
    purchase_date: Optional[date] = None
    expiry_date: Optional[date] = None
    status: EquipmentStatus = "active"
    material: Optional[str] = None
    capacity_or_range: Optional[str] = None
    accuracy: Optional[str] = None
    sterility_grade: Optional[str] = None
    requires_calibration: bool = False
    # calibration
    calibration_frequency_days: Optional[int] = None
    last_calibration_date: Optional[date] = None
    calibration_next_due_date: Optional[date] = None
    # maintenance
    maintenance_frequency_days: Optional[int] = None
    last_maintenance_date: Optional[date] = None
    maintenace_next_due_date: Optional[date] = None
    # cleaning
    cleaning_frequency_days: Optional[int] = None
    last_cleaning_date: Optional[date] = None
    cleaning_next_due_date: Optional[date] = None
    regulatory_info: Optional[RegulatoryInfo] = None
    supplier: Optional[str] = None
    purchase_cost: Optional[float] = Field(default=None, ge=0)
    notes: Optional[str] = None
    tags: Optional[List[str]] = None

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value):
        return _normalize_equipment_status(value)


class PharmaceuticalEquipmentUpdate(_LocationMixin, _ClassificationMixin):
    workstation_id: Optional[str] = None
    name: Optional[str] = None
    category: Optional[EquipmentCategory] = None
    manufacturer: Optional[str] = None
    model_number: Optional[str] = None
    serial_number: Optional[str] = None
    asset_tag: Optional[str] = None
    purchase_date: Optional[date] = None
    expiry_date: Optional[date] = None
    status: Optional[EquipmentStatus] = None
    material: Optional[str] = None
    capacity_or_range: Optional[str] = None
    accuracy: Optional[str] = None
    sterility_grade: Optional[str] = None
    requires_calibration: Optional[bool] = None
    # calibration
    calibration_frequency_days: Optional[int] = None
    last_calibration_date: Optional[date] = None
    calibration_next_due_date: Optional[date] = None
    # maintenance
    maintenance_frequency_days: Optional[int] = None
    last_maintenance_date: Optional[date] = None
    maintenace_next_due_date: Optional[date] = None
    # cleaning
    cleaning_frequency_days: Optional[int] = None
    last_cleaning_date: Optional[date] = None
    cleaning_next_due_date: Optional[date] = None
    regulatory_info: Optional[RegulatoryInfo] = None
    supplier: Optional[str] = None
    purchase_cost: Optional[float] = Field(default=None, ge=0)
    notes: Optional[str] = None
    tags: Optional[List[str]] = None

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value):
        return _normalize_equipment_status(value)


class PharmaceuticalEquipmentResponse(_LocationMixin, _ClassificationMixin):
    workstation_id: Optional[str] = None
    equipment_id: str
    name: str
    category: EquipmentCategory
    manufacturer: str
    model_number: Optional[str] = None
    serial_number: Optional[str] = None
    asset_tag: Optional[str] = None
    purchase_date: Optional[date] = None
    expiry_date: Optional[date] = None
    status: EquipmentStatus
    material: Optional[str] = None
    capacity_or_range: Optional[str] = None
    accuracy: Optional[str] = None
    sterility_grade: Optional[str] = None
    requires_calibration: bool
    # calibration
    calibration_frequency_days: Optional[int] = None
    last_calibration_date: Optional[date] = None
    calibration_next_due_date: Optional[date] = None
    calibration_records: Optional[List[CalibrationRecord]] = []
    # maintenance
    maintenance_frequency_days: Optional[int] = None
    last_maintenance_date: Optional[date] = None
    maintenace_next_due_date: Optional[date] = None
    maintenance_records: Optional[List[MaintenanceLog]] = []
    # cleaning
    cleaning_frequency_days: Optional[int] = None
    last_cleaning_date: Optional[date] = None
    cleaning_next_due_date: Optional[date] = None
    regulatory_info: Optional[RegulatoryInfo] = None
    assigned_to: Optional[str] = None
    supplier: Optional[str] = None
    purchase_cost: Optional[float] = None
    notes: Optional[str] = None
    tags: Optional[List[str]] = None

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value):
        return _normalize_equipment_status(value)

    class Config:
        from_attributes = True


class PaginatedEquipmentResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: List[PharmaceuticalEquipmentResponse]
