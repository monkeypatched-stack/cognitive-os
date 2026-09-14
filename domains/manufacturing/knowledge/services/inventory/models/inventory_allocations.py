from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.products.models.product_common import utc_now


class AllocationItemType(str, Enum):
    COMPONENT = "component"
    PRODUCT = "product"
    SPARE_PART = "spare_part"
    RAW_MATERIAL = "raw_material"


class AllocationRequestType(str, Enum):
    QUANTITY = "quantity"
    COUNT = "count"


class AllocationStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    ALLOCATED = "allocated"
    PICKED = "picked"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class AllocationUnitOfMeasure(str, Enum):
    PIECE = "piece"
    BOX = "box"
    PALLET = "pallet"
    KG = "kg"
    LITER = "liter"
    OTHER = "other"


class InventoryAllocationBase(BaseModel):
    workstation_id: str = Field(..., min_length=1)
    warehouse_id: str = Field(..., min_length=1)
    sku: str = Field(..., min_length=1, description="Stock Keeping Unit")
    srn: str | None = Field(None, description="Serial Reference Number")
    item_type: AllocationItemType
    request_type: AllocationRequestType
    requested_quantity: float | None = Field(default=None, gt=0)
    requested_count: int | None = Field(default=None, gt=0)
    unit_of_measure: AllocationUnitOfMeasure
    status: AllocationStatus = AllocationStatus.PENDING
    requested_by: str | None = None
    approved_by: str | None = None
    fulfilled_by: str | None = None
    rejected_reason: str | None = None
    due_at: datetime | None = None
    requested_at: datetime = Field(default_factory=utc_now)
    approved_at: datetime | None = None
    allocated_at: datetime | None = None
    delivered_at: datetime | None = None
    notes: str | None = None
    metadata: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_request_amount(self):
        if self.request_type == AllocationRequestType.QUANTITY:
            if self.requested_quantity is None:
                raise ValueError("requested_quantity is required for quantity requests")
            if self.requested_count is not None:
                raise ValueError(
                    "requested_count must not be set for quantity requests"
                )

        if self.request_type == AllocationRequestType.COUNT:
            if self.requested_count is None:
                raise ValueError("requested_count is required for count requests")
            if self.requested_quantity is not None:
                raise ValueError(
                    "requested_quantity must not be set for count requests"
                )

        return self


class InventoryAllocationCreate(InventoryAllocationBase):
    """Schema for workstation requests to allocate inventory from a warehouse."""


class InventoryAllocationUpdate(BaseModel):
    workstation_id: str | None = None
    warehouse_id: str | None = None
    sku: str | None = None
    srn: str | None = None
    item_type: AllocationItemType | None = None
    request_type: AllocationRequestType | None = None
    requested_quantity: float | None = Field(default=None, gt=0)
    requested_count: int | None = Field(default=None, gt=0)
    unit_of_measure: AllocationUnitOfMeasure | None = None
    status: AllocationStatus | None = None
    requested_by: str | None = None
    approved_by: str | None = None
    fulfilled_by: str | None = None
    rejected_reason: str | None = None
    due_at: datetime | None = None
    requested_at: datetime | None = None
    approved_at: datetime | None = None
    allocated_at: datetime | None = None
    delivered_at: datetime | None = None
    notes: str | None = None
    metadata: dict[str, Any] | None = None


class InventoryAllocationResponse(InventoryAllocationBase):
    id: str
    allocation_id: str
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaginatedInventoryAllocationResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[InventoryAllocationResponse]
