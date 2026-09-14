from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Annotated, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

from services.shipping.models.carrier import Address


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WaybillStatus(str, Enum):
    DRAFT = "draft"
    ISSUED = "issued"
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    RETURNED = "returned"


class ShipmentMode(str, Enum):
    AIR = "air"
    ROAD = "road"
    RAIL = "rail"
    SEA = "sea"
    MULTIMODAL = "multimodal"


class PaymentTerms(str, Enum):
    PREPAID = "prepaid"
    COLLECT = "collect"
    THIRD_PARTY = "third_party"


class WeightUnit(str, Enum):
    KG = "kg"
    LB = "lb"


class ChargeType(str, Enum):
    FREIGHT = "freight"
    FUEL = "fuel"
    HANDLING = "handling"
    INSURANCE = "insurance"
    CUSTOMS = "customs"
    OTHER = "other"


class WaybillPackage(BaseModel):
    package_reference: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = Field(None, max_length=255)
    quantity: int = Field(..., ge=1)
    gross_weight: Annotated[Decimal, Field(gt=0)]
    length_cm: Optional[Annotated[Decimal, Field(gt=0)]] = None
    width_cm: Optional[Annotated[Decimal, Field(gt=0)]] = None
    height_cm: Optional[Annotated[Decimal, Field(gt=0)]] = None

    model_config = {"str_strip_whitespace": True}


class FreightCharge(BaseModel):
    charge_type: ChargeType = ChargeType.FREIGHT
    description: Optional[str] = Field(None, max_length=255)
    amount: Annotated[Decimal, Field(ge=0)]
    currency: str = Field(default="USD", min_length=3, max_length=3)

    model_config = {"str_strip_whitespace": True}


class CustomsInfo(BaseModel):
    customs_declaration_id: Optional[str] = Field(None, max_length=100)
    hs_code: Optional[str] = Field(None, max_length=20)
    country_of_origin: Optional[str] = Field(None, min_length=2, max_length=2)
    declared_value: Optional[Annotated[Decimal, Field(ge=0)]] = None
    currency: str = Field(default="USD", min_length=3, max_length=3)
    notes: Optional[str] = None

    model_config = {"str_strip_whitespace": True}


class Waybill(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    waybill_number: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Unique waybill / AWB / BOL number",
    )
    status: WaybillStatus = WaybillStatus.DRAFT
    shipment_mode: ShipmentMode
    issue_date: datetime = Field(default_factory=utc_now)
    pickup_date: Optional[datetime] = None
    estimated_delivery_date: Optional[datetime] = None
    actual_delivery_date: Optional[datetime] = None
    shipper: Address = Field(..., description="Sender / consignor")
    consignee: Address = Field(..., description="Recipient / consignee")
    notify_party: Optional[Address] = Field(
        None, description="Third party to be notified upon arrival"
    )
    carrier_name: str = Field(..., min_length=1, max_length=200)
    carrier_code: Optional[str] = Field(
        None, max_length=20, description="IATA carrier code or SCAC (road/rail)"
    )
    service_type: Optional[str] = Field(
        None, max_length=100, description="E.g. Express, Economy, Same-Day"
    )
    payment_terms: PaymentTerms = PaymentTerms.PREPAID
    origin_port: Optional[str] = Field(
        None, max_length=20, description="IATA / LOCODE of origin hub"
    )
    destination_port: Optional[str] = Field(
        None, max_length=20, description="IATA / LOCODE of destination hub"
    )
    routing: Optional[str] = Field(
        None,
        max_length=255,
        description="Intermediate routing points, e.g. 'BOM-DXB-LHR'",
    )
    packages: list[WaybillPackage] = Field(..., min_length=1)
    total_packages: Optional[int] = Field(None, ge=1)
    total_gross_weight: Optional[Annotated[Decimal, Field(gt=0)]] = None
    total_volumetric_weight: Optional[Annotated[Decimal, Field(gt=0)]] = None
    weight_unit: WeightUnit = WeightUnit.KG
    freight_charges: list[FreightCharge] = Field(default_factory=list)
    total_charges: Optional[Annotated[Decimal, Field(ge=0)]] = None
    currency: str = Field(default="USD", min_length=3, max_length=3)
    customs_info: Optional[CustomsInfo] = None
    special_instructions: Optional[str] = None
    reference_number: Optional[str] = Field(
        None, max_length=100, description="Shipper's reference or PO number"
    )
    barcode: Optional[str] = Field(None, max_length=100)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    model_config = {"str_strip_whitespace": True}

    @model_validator(mode="after")
    def compute_totals(self) -> "Waybill":
        if self.total_packages is None:
            self.total_packages = sum(package.quantity for package in self.packages)
        if self.total_gross_weight is None:
            self.total_gross_weight = sum(
                package.gross_weight * package.quantity for package in self.packages
            )
        if self.total_charges is None and self.freight_charges:
            self.total_charges = sum(charge.amount for charge in self.freight_charges)
        return self

    @model_validator(mode="after")
    def delivery_date_after_pickup(self) -> "Waybill":
        if self.pickup_date and self.estimated_delivery_date:
            if self.estimated_delivery_date < self.pickup_date:
                raise ValueError(
                    "estimated_delivery_date must be on or after pickup_date"
                )
        return self


class WaybillCreate(Waybill):
    pass


class WaybillUpdate(BaseModel):
    waybill_number: Optional[str] = Field(None, min_length=1, max_length=100)
    status: Optional[WaybillStatus] = None
    shipment_mode: Optional[ShipmentMode] = None
    issue_date: Optional[datetime] = None
    pickup_date: Optional[datetime] = None
    estimated_delivery_date: Optional[datetime] = None
    actual_delivery_date: Optional[datetime] = None
    shipper: Optional[Address] = None
    consignee: Optional[Address] = None
    notify_party: Optional[Address] = None
    carrier_name: Optional[str] = Field(None, min_length=1, max_length=200)
    carrier_code: Optional[str] = Field(None, max_length=20)
    service_type: Optional[str] = Field(None, max_length=100)
    payment_terms: Optional[PaymentTerms] = None
    origin_port: Optional[str] = Field(None, max_length=20)
    destination_port: Optional[str] = Field(None, max_length=20)
    routing: Optional[str] = Field(None, max_length=255)
    packages: Optional[list[WaybillPackage]] = Field(None, min_length=1)
    total_packages: Optional[int] = Field(None, ge=1)
    total_gross_weight: Optional[Annotated[Decimal, Field(gt=0)]] = None
    total_volumetric_weight: Optional[Annotated[Decimal, Field(gt=0)]] = None
    weight_unit: Optional[WeightUnit] = None
    freight_charges: Optional[list[FreightCharge]] = None
    total_charges: Optional[Annotated[Decimal, Field(ge=0)]] = None
    currency: Optional[str] = Field(None, min_length=3, max_length=3)
    customs_info: Optional[CustomsInfo] = None
    special_instructions: Optional[str] = None
    reference_number: Optional[str] = Field(None, max_length=100)
    barcode: Optional[str] = Field(None, max_length=100)
    updated_at: Optional[datetime] = None

    model_config = {"str_strip_whitespace": True}


class WaybillResponse(Waybill):
    class Config:
        from_attributes = True


class PaginatedWaybillResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[WaybillResponse]
