from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Annotated, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, computed_field, model_validator

from services.shipping.models.delivery_note import DeliveryStatus


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PackageType(str, Enum):
    BOX = "box"
    CARTON = "carton"
    DRUM = "drum"
    BAG = "bag"
    CRATE = "crate"
    TOTE = "tote"
    ENVELOPE = "envelope"
    OTHER = "other"


class PackageItem(BaseModel):
    """Product line packed inside a package."""

    line_number: int = Field(..., ge=1)
    product_code: str = Field(..., max_length=50)
    description: str = Field(..., max_length=255)
    batch_number: Optional[str] = Field(None, max_length=50)
    serial_number: Optional[str] = Field(None, max_length=100)
    expiry_date: Optional[date] = None
    quantity: Annotated[Decimal, Field(gt=0)]
    uom: str = Field(..., max_length=20)
    unit_weight_kg: Optional[float] = Field(None, ge=0)
    hazardous: bool = False
    un_number: Optional[str] = Field(None, max_length=4)

    model_config = {"str_strip_whitespace": True}


class Package(BaseModel):
    """Individual physical parcel, box, drum, or other container."""

    id: UUID = Field(default_factory=uuid4)
    package_code: str = Field(
        ..., max_length=80, description="Barcode / tracking reference"
    )
    package_type: PackageType = PackageType.BOX
    pallet_id: Optional[str] = Field(None, max_length=100)
    delivery_note_id: Optional[str] = Field(None, max_length=100)
    length_mm: Optional[int] = Field(None, gt=0)
    width_mm: Optional[int] = Field(None, gt=0)
    height_mm: Optional[int] = Field(None, gt=0)
    gross_weight_kg: Optional[float] = Field(None, ge=0)
    net_weight_kg: Optional[float] = Field(None, ge=0)
    items: list[PackageItem] = Field(default_factory=list)
    is_fragile: bool = False
    is_hazardous: bool = False
    un_number: Optional[str] = Field(None, max_length=4)
    do_not_stack: bool = False
    requires_refrigeration: bool = False
    temp_min_celsius: Optional[float] = None
    temp_max_celsius: Optional[float] = None
    tracking_number: Optional[str] = Field(None, max_length=100)
    status: DeliveryStatus = DeliveryStatus.PENDING
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: Optional[datetime] = None

    model_config = {"str_strip_whitespace": True}

    @computed_field  # type: ignore[misc]
    @property
    def volume_cm3(self) -> Optional[float]:
        if self.length_mm and self.width_mm and self.height_mm:
            return round(self.length_mm * self.width_mm * self.height_mm / 1_000, 2)
        return None

    @model_validator(mode="after")
    def hazardous_needs_un(self) -> "Package":
        if self.is_hazardous and not self.un_number:
            raise ValueError("un_number required when is_hazardous is True")
        return self

    @model_validator(mode="after")
    def refrigeration_range(self) -> "Package":
        if self.requires_refrigeration:
            if self.temp_min_celsius is None or self.temp_max_celsius is None:
                raise ValueError(
                    "temp_min/max_celsius required when requires_refrigeration is True"
                )
            if self.temp_min_celsius >= self.temp_max_celsius:
                raise ValueError("temp_min_celsius must be less than temp_max_celsius")
        return self


class PackageCreate(Package):
    pass


class PackageUpdate(BaseModel):
    package_code: Optional[str] = Field(None, max_length=80)
    package_type: Optional[PackageType] = None
    pallet_id: Optional[str] = Field(None, max_length=100)
    delivery_note_id: Optional[str] = Field(None, max_length=100)
    length_mm: Optional[int] = Field(None, gt=0)
    width_mm: Optional[int] = Field(None, gt=0)
    height_mm: Optional[int] = Field(None, gt=0)
    gross_weight_kg: Optional[float] = Field(None, ge=0)
    net_weight_kg: Optional[float] = Field(None, ge=0)
    items: Optional[list[PackageItem]] = None
    is_fragile: Optional[bool] = None
    is_hazardous: Optional[bool] = None
    un_number: Optional[str] = Field(None, max_length=4)
    do_not_stack: Optional[bool] = None
    requires_refrigeration: Optional[bool] = None
    temp_min_celsius: Optional[float] = None
    temp_max_celsius: Optional[float] = None
    tracking_number: Optional[str] = Field(None, max_length=100)
    status: Optional[DeliveryStatus] = None
    notes: Optional[str] = None
    updated_at: Optional[datetime] = None

    model_config = {"str_strip_whitespace": True}

    @model_validator(mode="after")
    def hazardous_needs_un(self) -> "PackageUpdate":
        if self.is_hazardous is True and not self.un_number:
            raise ValueError("un_number required when is_hazardous is True")
        return self

    @model_validator(mode="after")
    def refrigeration_range(self) -> "PackageUpdate":
        if self.requires_refrigeration is True:
            if self.temp_min_celsius is None or self.temp_max_celsius is None:
                raise ValueError(
                    "temp_min/max_celsius required when requires_refrigeration is True"
                )
        if self.temp_min_celsius is not None and self.temp_max_celsius is not None:
            if self.temp_min_celsius >= self.temp_max_celsius:
                raise ValueError("temp_min_celsius must be less than temp_max_celsius")
        return self


class PackageResponse(Package):
    class Config:
        from_attributes = True


class PaginatedPackageResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[PackageResponse]
