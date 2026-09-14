from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Annotated, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, computed_field

from services.shipping.models.carrier import Address, CarrierMode


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CustomsDeclarationType(str, Enum):
    IMPORT = "import"
    EXPORT = "export"
    TRANSIT = "transit"
    RE_EXPORT = "re_export"
    TEMPORARY_IMPORT = "temporary_import"


class MoneyAmount(BaseModel):
    amount: Annotated[Decimal, Field(ge=0)]
    currency: str = Field(
        default="USD", min_length=3, max_length=3, description="ISO 4217"
    )

    model_config = {"str_strip_whitespace": True}


class CustomsLineItem(BaseModel):
    line_number: int = Field(..., ge=1)
    commodity_code: str = Field(..., max_length=20, description="HS / tariff code")
    description: str = Field(..., max_length=500)
    country_of_origin: str = Field(..., min_length=2, max_length=2)
    quantity: Annotated[Decimal, Field(gt=0)]
    unit_of_measure: str = Field(..., max_length=20)
    gross_weight_kg: Annotated[float, Field(gt=0)]
    net_weight_kg: Optional[Annotated[float, Field(gt=0)]] = None
    customs_value: MoneyAmount
    duty_rate_pct: Optional[Annotated[float, Field(ge=0, le=100)]] = None
    duty_amount: Optional[MoneyAmount] = None
    vat_rate_pct: Optional[Annotated[float, Field(ge=0, le=100)]] = None
    licence_number: Optional[str] = Field(None, max_length=100)

    model_config = {"str_strip_whitespace": True}


class CustomsDeclaration(BaseModel):
    """Import / export customs declaration linked to a shipment."""

    id: UUID = Field(default_factory=uuid4)
    declaration_reference: str = Field(..., min_length=1, max_length=80)
    declaration_type: CustomsDeclarationType
    declaration_date: date = Field(default_factory=date.today)
    shipment_id: Optional[str] = Field(None, max_length=100)
    delivery_note_id: Optional[str] = Field(None, max_length=100)
    exporter_name: str = Field(..., max_length=200)
    exporter_address: Address
    exporter_eori: Optional[str] = Field(None, max_length=50, description="EORI number")
    importer_name: str = Field(..., max_length=200)
    importer_address: Address
    importer_eori: Optional[str] = Field(None, max_length=50)
    country_of_export: str = Field(..., min_length=2, max_length=2)
    country_of_import: str = Field(..., min_length=2, max_length=2)
    port_of_loading: Optional[str] = Field(None, max_length=100)
    port_of_discharge: Optional[str] = Field(None, max_length=100)
    transport_mode: CarrierMode
    incoterms: Optional[str] = Field(None, max_length=10)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    lines: list[CustomsLineItem] = Field(default_factory=list, min_length=1)
    mrn: Optional[str] = Field(
        None, max_length=50, description="Movement Reference Number"
    )
    customs_cleared: bool = False
    clearance_date: Optional[date] = None
    broker_name: Optional[str] = Field(None, max_length=200)
    broker_reference: Optional[str] = Field(None, max_length=80)
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: Optional[datetime] = None

    model_config = {"str_strip_whitespace": True}

    @computed_field  # type: ignore[misc]
    @property
    def total_customs_value(self) -> Decimal:
        return sum(line.customs_value.amount for line in self.lines)

    @computed_field  # type: ignore[misc]
    @property
    def total_duty(self) -> Decimal:
        return sum(line.duty_amount.amount for line in self.lines if line.duty_amount)


class CustomsDeclarationCreate(CustomsDeclaration):
    pass


class CustomsDeclarationUpdate(BaseModel):
    declaration_reference: Optional[str] = Field(None, min_length=1, max_length=80)
    declaration_type: Optional[CustomsDeclarationType] = None
    declaration_date: Optional[date] = None
    shipment_id: Optional[str] = Field(None, max_length=100)
    delivery_note_id: Optional[str] = Field(None, max_length=100)
    exporter_name: Optional[str] = Field(None, max_length=200)
    exporter_address: Optional[Address] = None
    exporter_eori: Optional[str] = Field(None, max_length=50)
    importer_name: Optional[str] = Field(None, max_length=200)
    importer_address: Optional[Address] = None
    importer_eori: Optional[str] = Field(None, max_length=50)
    country_of_export: Optional[str] = Field(None, min_length=2, max_length=2)
    country_of_import: Optional[str] = Field(None, min_length=2, max_length=2)
    port_of_loading: Optional[str] = Field(None, max_length=100)
    port_of_discharge: Optional[str] = Field(None, max_length=100)
    transport_mode: Optional[CarrierMode] = None
    incoterms: Optional[str] = Field(None, max_length=10)
    currency: Optional[str] = Field(None, min_length=3, max_length=3)
    lines: Optional[list[CustomsLineItem]] = Field(None, min_length=1)
    mrn: Optional[str] = Field(None, max_length=50)
    customs_cleared: Optional[bool] = None
    clearance_date: Optional[date] = None
    broker_name: Optional[str] = Field(None, max_length=200)
    broker_reference: Optional[str] = Field(None, max_length=80)
    notes: Optional[str] = None
    updated_at: Optional[datetime] = None

    model_config = {"str_strip_whitespace": True}


class CustomsDeclarationResponse(CustomsDeclaration):
    class Config:
        from_attributes = True


class PaginatedCustomsDeclarationResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[CustomsDeclarationResponse]
