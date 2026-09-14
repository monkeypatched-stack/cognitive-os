from datetime import date, datetime, timezone
from enum import Enum
from typing import Annotated, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class VehicleType(str, Enum):
    VAN = "van"
    BOX_TRUCK = "box_truck"
    FLATBED = "flatbed"
    REFRIGERATED_TRUCK = "refrigerated_truck"
    TANKER = "tanker"
    TRAILER = "trailer"
    CONTAINER = "container"
    OTHER = "other"


class VehicleDimensions(BaseModel):
    """Internal cargo dimensions (metres)."""

    length_m: Annotated[float, Field(gt=0, le=25)]
    width_m: Annotated[float, Field(gt=0, le=5)]
    height_m: Annotated[float, Field(gt=0, le=5)]

    @property
    def volume_m3(self) -> float:
        return round(self.length_m * self.width_m * self.height_m, 4)


class Vehicle(BaseModel):
    """Transport vehicle belonging to a carrier or manufacturer fleet."""

    id: UUID = Field(default_factory=uuid4)
    carrier_id: Optional[str] = Field(
        None, description="Owning carrier; None = own fleet"
    )
    vehicle_type: VehicleType
    registration_plate: str = Field(..., min_length=1, max_length=20)
    vin: Optional[str] = Field(
        None, min_length=17, max_length=17, description="Vehicle Identification Number"
    )
    make: Optional[str] = Field(None, max_length=50)
    model: Optional[str] = Field(None, max_length=50)
    year: Optional[int] = Field(None, ge=1900, le=2100)
    max_payload_kg: Annotated[float, Field(gt=0)]
    max_volume_m3: Optional[Annotated[float, Field(gt=0)]] = None
    cargo_dimensions: Optional[VehicleDimensions] = None
    has_tail_lift: bool = False
    is_refrigerated: bool = False
    temperature_range_celsius: Optional[tuple[float, float]] = Field(
        None, description="Min and max temp if refrigerated"
    )
    driver_name: Optional[str] = Field(None, max_length=150)
    driver_license_number: Optional[str] = Field(None, max_length=50)
    driver_phone: Optional[str] = Field(None, max_length=30)
    mot_expiry: Optional[date] = Field(
        None, description="MOT / roadworthiness certificate expiry"
    )
    insurance_expiry: Optional[date] = None
    gps_tracker_id: Optional[str] = Field(None, max_length=100)
    active: bool = True
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: Optional[datetime] = None

    model_config = {"str_strip_whitespace": True}

    @model_validator(mode="after")
    def validate_temperature_range(self) -> "Vehicle":
        if self.is_refrigerated and self.temperature_range_celsius is None:
            raise ValueError(
                "temperature_range_celsius is required for refrigerated vehicles"
            )
        if self.temperature_range_celsius is not None:
            low, high = self.temperature_range_celsius
            if low >= high:
                raise ValueError("temperature_range_celsius[0] must be less than [1]")
        return self


class VehicleCreate(Vehicle):
    pass


class VehicleUpdate(BaseModel):
    carrier_id: Optional[UUID] = None
    vehicle_type: Optional[VehicleType] = None
    registration_plate: Optional[str] = Field(None, min_length=1, max_length=20)
    vin: Optional[str] = Field(None, min_length=17, max_length=17)
    make: Optional[str] = Field(None, max_length=50)
    model: Optional[str] = Field(None, max_length=50)
    year: Optional[int] = Field(None, ge=1900, le=2100)
    max_payload_kg: Optional[Annotated[float, Field(gt=0)]] = None
    max_volume_m3: Optional[Annotated[float, Field(gt=0)]] = None
    cargo_dimensions: Optional[VehicleDimensions] = None
    has_tail_lift: Optional[bool] = None
    is_refrigerated: Optional[bool] = None
    temperature_range_celsius: Optional[tuple[float, float]] = None
    driver_name: Optional[str] = Field(None, max_length=150)
    driver_license_number: Optional[str] = Field(None, max_length=50)
    driver_phone: Optional[str] = Field(None, max_length=30)
    mot_expiry: Optional[date] = None
    insurance_expiry: Optional[date] = None
    gps_tracker_id: Optional[str] = Field(None, max_length=100)
    active: Optional[bool] = None
    notes: Optional[str] = None
    updated_at: Optional[datetime] = None

    model_config = {"str_strip_whitespace": True}

    @model_validator(mode="after")
    def validate_temperature_range(self) -> "VehicleUpdate":
        if self.is_refrigerated is True and self.temperature_range_celsius is None:
            raise ValueError(
                "temperature_range_celsius is required for refrigerated vehicles"
            )
        if self.temperature_range_celsius is not None:
            low, high = self.temperature_range_celsius
            if low >= high:
                raise ValueError("temperature_range_celsius[0] must be less than [1]")
        return self


class VehicleResponse(Vehicle):
    class Config:
        from_attributes = True


class PaginatedVehicleResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[VehicleResponse]
