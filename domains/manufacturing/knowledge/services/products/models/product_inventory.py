from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator

from services.inventory.models.inventory_enums import (
    InventoryStatus,
    InventoryValuationMethod,
)
from services.products.models.product_common import ensure_utc, utc_now


class ProductInventoryBase(BaseModel):
    product_id: str = Field(..., min_length=1)
    location_id: str = Field(..., min_length=1)
    quantity_on_hand: float = Field(default=0, ge=0)
    quantity_reserved: float = Field(default=0, ge=0)
    quantity_incoming: float = Field(default=0, ge=0)
    quantity_committed: float = Field(default=0, ge=0)
    reorder_point: Optional[float] = Field(default=None, ge=0)
    reorder_quantity: Optional[float] = Field(default=None, ge=0)
    max_stock_level: Optional[float] = Field(default=None, ge=0)
    safety_stock: Optional[float] = Field(default=None, ge=0)
    valuation_method: InventoryValuationMethod = "Weighted-Average"
    unit_cost: Optional[float] = Field(default=None, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    batch_tracking: bool = False
    serial_tracking: bool = False
    expiry_tracking: bool = False
    last_counted_at: Optional[date] = None
    next_count_due: Optional[date] = None
    status: InventoryStatus = "In-Stock"
    notes: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


class ProductInventoryRecord(ProductInventoryBase):
    inventory_id: str = Field(..., min_length=1)
    sku: str = Field(..., min_length=1)
    product_name: str = Field(..., min_length=1)
    quantity_available: float = Field(default=0, ge=0)
    total_value: Optional[float] = Field(default=None, ge=0)
    is_below_reorder: bool = False
    is_overstocked: bool = False
    is_expired: bool = False
    alert_flags: list[str] = Field(default_factory=list)
    created_by: Optional[str] = None
    updated_by: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def compute_fields(self) -> "ProductInventoryRecord":
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        self.quantity_available = max(self.quantity_on_hand - self.quantity_reserved, 0)

        if self.unit_cost is not None:
            self.total_value = round(self.quantity_on_hand * self.unit_cost, 2)

        self.is_below_reorder = (
            self.reorder_point is not None
            and self.quantity_available <= self.reorder_point
        )

        self.is_overstocked = (
            self.max_stock_level is not None
            and self.quantity_on_hand > self.max_stock_level
        )

        self.is_expired = self.status == "Expired"

        flags: list[str] = []
        if self.is_below_reorder:
            flags.append("low-stock")
        if self.quantity_available == 0:
            flags.append("out-of-stock")
        if self.is_overstocked:
            flags.append("overstock")
        if self.is_expired:
            flags.append("expired")
        self.alert_flags = flags
        return self


class ProductInventoryCreate(ProductInventoryBase):
    pass


class ProductInventoryUpdate(BaseModel):
    quantity_on_hand: Optional[float] = Field(default=None, ge=0)
    quantity_reserved: Optional[float] = Field(default=None, ge=0)
    quantity_incoming: Optional[float] = Field(default=None, ge=0)
    quantity_committed: Optional[float] = Field(default=None, ge=0)
    reorder_point: Optional[float] = Field(default=None, ge=0)
    reorder_quantity: Optional[float] = Field(default=None, ge=0)
    max_stock_level: Optional[float] = Field(default=None, ge=0)
    safety_stock: Optional[float] = Field(default=None, ge=0)
    valuation_method: Optional[InventoryValuationMethod] = None
    unit_cost: Optional[float] = Field(default=None, ge=0)
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    batch_tracking: Optional[bool] = None
    serial_tracking: Optional[bool] = None
    expiry_tracking: Optional[bool] = None
    last_counted_at: Optional[date] = None
    next_count_due: Optional[date] = None
    status: Optional[InventoryStatus] = None
    notes: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None
    updated_by: Optional[str] = None


class PaginatedProductInventoryResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[ProductInventoryRecord]
