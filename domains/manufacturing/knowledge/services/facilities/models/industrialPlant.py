from datetime import datetime, timezone
from typing import Optional, Literal
from pydantic import BaseModel, Field, model_validator, EmailStr

PlantStatus = Literal[
    "Active", "Reduced Capacity", "Shutdown", "Maintenance", "Decommissioned"
]
Timezone = Literal["UTC", "CET", "IST", "EST", "PST", "JST", "CST", "GST", "AEST"]
PlantType = Literal[
    "Production",
    "Packaging",
    "R&D",
    "Warehouse",
]
CertificationStatus = Literal["Valid", "Expired", "Pending Renewal"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ── Sub-models ────────────────────────────────────────────────────────────────


class GeoCoordinates(BaseModel):
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)


class ContactInfo(BaseModel):
    name: str
    role: str
    email: Optional[str] = None
    phone: Optional[str] = None


class Certification(BaseModel):
    name: str
    issued_by: Optional[str] = None
    issue_date: Optional[str] = None
    expiry_date: Optional[str] = None
    status: CertificationStatus = "Valid"


class ShiftSchedule(BaseModel):
    shifts_per_day: int = Field(..., ge=1, le=3)
    operating_days_per_week: int = Field(..., ge=1, le=7)
    hours_per_shift: float = Field(..., ge=1.0, le=12.0)


class UtilityInfo(BaseModel):
    power_source: Optional[str] = None
    power_capacity_kw: Optional[float] = Field(None, ge=0)
    water_source: Optional[str] = None
    water_consumption_m3_day: Optional[float] = Field(None, ge=0)
    has_backup_generator: bool = False


# ── Core model ────────────────────────────────────────────────────────────────


class IndustrialPlant(BaseModel):
    # Identity
    id: str
    name: str
    plant_code: Optional[str] = Field(
        None, description="Internal short code e.g. PLT-001"
    )
    type: PlantType

    # Location
    location: str
    address: str
    country: str
    geo: Optional[GeoCoordinates] = None
    timezone: Timezone

    # Operations
    status: PlantStatus
    capacity_utilization: int = Field(
        ..., ge=0, le=100, description="Current utilization %"
    )
    total_area_sqm: Optional[float] = Field(
        None, ge=0, description="Total floor area in m²"
    )
    employee_count: Optional[int] = Field(None, ge=0)
    established_year: Optional[int] = Field(None, ge=1800, le=2100)

    # People
    manager: str
    manager_contact: Optional[ContactInfo] = None
    emergency_contact: Optional[ContactInfo] = None

    # Scheduling & utilities
    shift_schedule: Optional[ShiftSchedule] = None
    utilities: Optional[UtilityInfo] = None

    # Compliance
    certifications: Optional[list[Certification]] = None
    regulatory_body: Optional[str] = None

    # Meta
    notes: Optional[str] = None
    description: Optional[str] = None

    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_and_validate(self):
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self

    @model_validator(mode="after")
    def check_utilization_vs_status(self) -> "IndustrialPlant":
        if self.status == "Shutdown" and self.capacity_utilization > 0:
            raise ValueError("capacity_utilization must be 0 when status is 'Shutdown'")
        if self.status == "Reduced Capacity" and self.capacity_utilization >= 100:
            raise ValueError(
                "capacity_utilization must be < 100 when status is 'Reduced Capacity'"
            )
        return self


# ── Request / Response schemas ────────────────────────────────────────────────


class IndustrialPlantCreate(IndustrialPlant):
    pass


class IndustrialPlantUpdate(BaseModel):
    name: Optional[str] = None
    plant_code: Optional[str] = None
    type: Optional[PlantType] = None
    location: Optional[str] = None
    address: Optional[str] = None
    country: Optional[str] = None
    geo: Optional[GeoCoordinates] = None
    timezone: Optional[Timezone] = None
    status: Optional[PlantStatus] = None
    capacity_utilization: Optional[int] = Field(None, ge=0, le=100)
    total_area_sqm: Optional[float] = Field(None, ge=0)
    employee_count: Optional[int] = Field(None, ge=0)
    established_year: Optional[int] = Field(None, ge=1800, le=2100)
    manager: Optional[str] = None
    manager_contact: Optional[ContactInfo] = None
    emergency_contact: Optional[ContactInfo] = None
    shift_schedule: Optional[ShiftSchedule] = None
    utilities: Optional[UtilityInfo] = None
    certifications: Optional[list[Certification]] = None
    regulatory_body: Optional[str] = None
    notes: Optional[str] = None
    description: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class IndustrialPlantResponse(IndustrialPlant):
    class Config:
        from_attributes = True


class PaginatedPlantResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[IndustrialPlantResponse]
