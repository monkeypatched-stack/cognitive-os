from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Annotated, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from services.shipping.models.carrier import Address


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DeliveryStatus(str, Enum):
    PENDING = "pending"
    DISPATCHED = "dispatched"
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"
    PARTIALLY_DELIVERED = "partially_delivered"
    CANCELLED = "cancelled"
    RETURNED = "returned"


class DeliveryNoteItem(BaseModel):
    """A single line item on the delivery note."""

    line_number: int = Field(..., ge=1)
    product_code: str = Field(..., min_length=1, max_length=50)
    product_description: str = Field(..., max_length=255)
    purchase_order_number: Optional[str] = Field(None, max_length=50)
    sales_order_number: Optional[str] = Field(None, max_length=50)
    invoice_number: Optional[str] = Field(None, max_length=50)
    waybill_id: Optional[str] = Field(None, max_length=100)
    batch_number: Optional[str] = Field(None, max_length=50)
    serial_number: Optional[str] = Field(None, max_length=100)
    quantity_ordered: Annotated[Decimal, Field(gt=0)]
    quantity_delivered: Annotated[Decimal, Field(ge=0)]
    unit_of_measure: str = Field(..., max_length=20)
    unit_weight_kg: Optional[Annotated[float, Field(ge=0)]] = None
    pallet_label: Optional[str] = Field(
        None, max_length=50, description="Reference to Pallet.pallet_label"
    )
    notes: Optional[str] = None

    @computed_field  # type: ignore[misc]
    @property
    def quantity_outstanding(self) -> Decimal:
        return max(Decimal(0), self.quantity_ordered - self.quantity_delivered)


class DeliveryNote(BaseModel):
    """Delivery Note (also called Dispatch Note / Consignment Note)."""

    id: UUID = Field(default_factory=uuid4)
    delivery_note_number: str = Field(
        ..., min_length=1, max_length=50, description="Unique DN reference"
    )
    issue_date: date = Field(default_factory=date.today)
    expected_delivery_date: Optional[date] = None
    actual_delivery_date: Optional[date] = None
    status: DeliveryStatus = DeliveryStatus.PENDING
    manufacturer_name: str = Field(..., max_length=200)
    manufacturer_address: Address
    ship_from_address: Optional[Address] = None
    customer_name: str = Field(..., max_length=200)
    customer_code: Optional[str] = Field(None, max_length=50)
    delivery_address: Address
    billing_address: Optional[Address] = None
    purchase_order_numbers: list[str] = Field(default_factory=list)
    sales_order_numbers: list[str] = Field(default_factory=list)
    invoice_number: Optional[str] = Field(None, max_length=50)
    carrier_id: Optional[UUID] = None
    vehicle_id: Optional[UUID] = None
    tracking_number: Optional[str] = Field(None, max_length=100)
    pro_number: Optional[str] = Field(
        None, max_length=50, description="PRO / waybill number"
    )
    items: list[DeliveryNoteItem] = Field(default_factory=list, min_length=1)
    pallet_ids: list[UUID] = Field(default_factory=list)
    total_pallets: Optional[int] = Field(None, ge=0)
    total_gross_weight_kg: Optional[float] = Field(None, ge=0)
    total_net_weight_kg: Optional[float] = Field(None, ge=0)
    total_volume_m3: Optional[float] = Field(None, ge=0)
    requires_signature: bool = True
    hazardous_goods: bool = False
    special_instructions: Optional[str] = None
    received_by: Optional[str] = Field(None, max_length=150)
    signature_reference: Optional[str] = Field(
        None, max_length=255, description="URL or document ref"
    )
    pod_timestamp: Optional[datetime] = None
    discrepancy_notes: Optional[str] = None
    created_by: Optional[str] = Field(None, max_length=100)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: Optional[datetime] = None

    model_config = {"str_strip_whitespace": True}

    @field_validator("expected_delivery_date")
    @classmethod
    def delivery_date_not_in_past(cls, value: Optional[date]) -> Optional[date]:
        return value

    @model_validator(mode="after")
    def pod_requires_receiver(self) -> "DeliveryNote":
        if self.status == DeliveryStatus.DELIVERED and self.received_by is None:
            raise ValueError("received_by is required when status is DELIVERED")
        return self


class DeliveryNoteCreate(DeliveryNote):
    pass


class DeliveryNoteUpdate(BaseModel):
    delivery_note_number: Optional[str] = Field(None, min_length=1, max_length=50)
    issue_date: Optional[date] = None
    expected_delivery_date: Optional[date] = None
    actual_delivery_date: Optional[date] = None
    status: Optional[DeliveryStatus] = None
    manufacturer_name: Optional[str] = Field(None, max_length=200)
    manufacturer_address: Optional[Address] = None
    ship_from_address: Optional[Address] = None
    customer_name: Optional[str] = Field(None, max_length=200)
    customer_code: Optional[str] = Field(None, max_length=50)
    delivery_address: Optional[Address] = None
    billing_address: Optional[Address] = None
    purchase_order_numbers: Optional[list[str]] = None
    sales_order_numbers: Optional[list[str]] = None
    invoice_number: Optional[str] = Field(None, max_length=50)
    carrier_id: Optional[UUID] = None
    vehicle_id: Optional[UUID] = None
    tracking_number: Optional[str] = Field(None, max_length=100)
    pro_number: Optional[str] = Field(None, max_length=50)
    items: Optional[list[DeliveryNoteItem]] = Field(None, min_length=1)
    pallet_ids: Optional[list[UUID]] = None
    total_pallets: Optional[int] = Field(None, ge=0)
    total_gross_weight_kg: Optional[float] = Field(None, ge=0)
    total_net_weight_kg: Optional[float] = Field(None, ge=0)
    total_volume_m3: Optional[float] = Field(None, ge=0)
    requires_signature: Optional[bool] = None
    hazardous_goods: Optional[bool] = None
    special_instructions: Optional[str] = None
    received_by: Optional[str] = Field(None, max_length=150)
    signature_reference: Optional[str] = Field(None, max_length=255)
    pod_timestamp: Optional[datetime] = None
    discrepancy_notes: Optional[str] = None
    created_by: Optional[str] = Field(None, max_length=100)
    updated_at: Optional[datetime] = None

    model_config = {"str_strip_whitespace": True}

    @model_validator(mode="after")
    def pod_requires_receiver(self) -> "DeliveryNoteUpdate":
        if self.status == DeliveryStatus.DELIVERED and self.received_by is None:
            raise ValueError("received_by is required when status is DELIVERED")
        return self


class DeliveryNoteResponse(DeliveryNote):
    class Config:
        from_attributes = True


class PaginatedDeliveryNoteResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[DeliveryNoteResponse]
