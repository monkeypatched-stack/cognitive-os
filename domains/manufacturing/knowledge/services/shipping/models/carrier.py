from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, EmailStr, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CarrierMode(str, Enum):
    ROAD = "road"
    RAIL = "rail"
    AIR = "air"
    OCEAN = "ocean"
    PARCEL = "parcel"
    INTERMODAL = "intermodal"


class Address(BaseModel):
    line1: str = Field(..., min_length=1, max_length=200)
    line2: Optional[str] = Field(None, max_length=200)
    city: str = Field(..., min_length=1, max_length=100)
    state: Optional[str] = Field(None, max_length=100)
    postal_code: Optional[str] = Field(None, max_length=30)
    country: str = Field(..., min_length=1, max_length=100)

    model_config = {"str_strip_whitespace": True}


class CarrierContact(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    role: Optional[str] = Field(
        None, max_length=100, examples=["Dispatcher", "Account Manager"]
    )
    phone: str = Field(..., max_length=30)
    email: Optional[EmailStr] = None

    model_config = {"str_strip_whitespace": True}


class Carrier(BaseModel):
    """Freight / logistics carrier (third-party or in-house)."""

    id: UUID = Field(default_factory=uuid4)
    name: str = Field(..., min_length=1, max_length=200)
    code: str = Field(
        ..., min_length=1, max_length=20, description="Internal carrier code"
    )
    transport_mode: CarrierMode = CarrierMode.ROAD
    scac_code: Optional[str] = Field(
        None,
        max_length=4,
        description="Standard Carrier Alpha Code (SCAC) - North America",
    )
    tax_id: Optional[str] = Field(None, max_length=50)
    address: Address
    contacts: list[CarrierContact] = Field(default_factory=list)
    insurance_policy_number: Optional[str] = Field(None, max_length=100)
    insurance_expiry: Optional[date] = None
    contract_reference: Optional[str] = Field(None, max_length=100)
    active: bool = True
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: Optional[datetime] = None

    model_config = {"str_strip_whitespace": True}


class CarrierCreate(Carrier):
    pass


class CarrierUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    code: Optional[str] = Field(None, min_length=1, max_length=20)
    transport_mode: Optional[CarrierMode] = None
    scac_code: Optional[str] = Field(None, max_length=4)
    tax_id: Optional[str] = Field(None, max_length=50)
    address: Optional[Address] = None
    contacts: Optional[list[CarrierContact]] = None
    insurance_policy_number: Optional[str] = Field(None, max_length=100)
    insurance_expiry: Optional[date] = None
    contract_reference: Optional[str] = Field(None, max_length=100)
    active: Optional[bool] = None
    notes: Optional[str] = None
    updated_at: Optional[datetime] = None

    model_config = {"str_strip_whitespace": True}


class CarrierResponse(Carrier):
    class Config:
        from_attributes = True


class PaginatedCarrierResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[CarrierResponse]
