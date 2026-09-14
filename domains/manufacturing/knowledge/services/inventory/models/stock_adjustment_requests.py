from datetime import date, datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from services.inventory.models.inventory_enums import (
    AdjustmentStatus,
    AdjustmentType,
    ReferenceType,
)
from services.products.models.product_common import utc_now


class StockAdjustmentItemType(str, Enum):
    PRODUCT = "product"
    COMPONENT = "component"
    SPARE_PART = "spare_part"
    RAW_MATERIAL = "raw_material"


class StockAdjustmentRequest(BaseModel):
    request_id: str
    item_type: StockAdjustmentItemType = StockAdjustmentItemType.RAW_MATERIAL
    inventory_id: str
    product_id: str
    location_id: str
    adjustment_type: AdjustmentType
    quantity: float = Field(..., gt=0)
    reason: str
    reference: Optional[ReferenceType] = Field(
        None,
        json_schema_extra={
            "placeholder": "Select reference type",
            "ui_widget": "select",
        },
    )
    batch_number: Optional[str] = None
    serial_numbers: Optional[list[str]] = None
    expiry_date: Optional[date] = None
    status: AdjustmentStatus = "Pending"
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    rejected_reason: Optional[str] = None
    notes: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None
    requested_by: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("approved_at", mode="before")
    @classmethod
    def blank_optional_datetime_to_none(cls, value):
        if value == "":
            return None
        return value

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def blank_timestamp_to_now(cls, value):
        if value == "":
            return utc_now()
        return value


class StockAdjustmentRequestCreate(StockAdjustmentRequest):
    pass


class StockAdjustmentRequestUpdate(BaseModel):
    item_type: Optional[StockAdjustmentItemType] = None
    inventory_id: Optional[str] = None
    product_id: Optional[str] = None
    location_id: Optional[str] = None
    adjustment_type: Optional[AdjustmentType] = None
    quantity: Optional[float] = Field(default=None, gt=0)
    reason: Optional[str] = None
    reference: Optional[ReferenceType] = Field(
        None,
        json_schema_extra={
            "placeholder": "Select reference type",
            "ui_widget": "select",
        },
    )
    batch_number: Optional[str] = None
    serial_numbers: Optional[list[str]] = None
    expiry_date: Optional[date] = None
    status: Optional[AdjustmentStatus] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    rejected_reason: Optional[str] = None
    notes: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None
    requested_by: Optional[str] = None

    @field_validator("approved_at", mode="before")
    @classmethod
    def blank_optional_datetime_to_none(cls, value):
        if value == "":
            return None
        return value


class StockAdjustmentRequestResponse(StockAdjustmentRequest):
    class Config:
        from_attributes = True


class PaginatedStockAdjustmentRequestResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[StockAdjustmentRequestResponse]
