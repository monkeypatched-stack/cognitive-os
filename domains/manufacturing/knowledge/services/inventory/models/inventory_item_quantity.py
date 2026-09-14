from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.products.models.product_common import utc_now


class ItemType(str, Enum):
    COMPONENT = "component"
    PRODUCT = "product"
    SPARE_PART = "spare_part"
    RAW_MATERIAL = "raw_material"


class UnitOfMeasure(str, Enum):
    PIECE = "piece"
    BOX = "box"
    PALLET = "pallet"
    KG = "kg"
    LITER = "liter"
    OTHER = "other"


class InventoryItemQuantityBase(BaseModel):
    item_type: ItemType = ItemType.RAW_MATERIAL
    sku: str = Field(..., min_length=1, description="Stock Keeping Unit")
    srn: str | None = Field(None, description="Serial Reference Number")
    warehouse_id: str | None = None
    bin_location: str | None = None
    quantity: float = Field(default=0, ge=0, description="Quantity of units on hand")
    quantity_allocated: float = Field(default=0, ge=0)
    quantity_reserved: float = Field(default=0, ge=0)
    quantity_available: float | None = Field(default=None, ge=0)
    unit_of_measure: UnitOfMeasure = Field(
        ..., description="Unit of measure for the quantity"
    )
    updated_by: str | None = None
    measured_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_quantity_state(self):
        expected = max(
            self.quantity - self.quantity_allocated - self.quantity_reserved, 0
        )
        if self.quantity_available is None:
            self.quantity_available = expected
        elif self.quantity_available != expected:
            raise ValueError(
                "quantity_available must equal "
                "quantity - quantity_allocated - quantity_reserved"
            )
        return self


class InventoryItemQuantityCreate(InventoryItemQuantityBase):
    """Schema for creating an inventory item quantity snapshot."""


class InventoryItemQuantityUpdate(BaseModel):
    item_type: ItemType | None = None
    sku: str | None = None
    srn: str | None = None
    warehouse_id: str | None = None
    bin_location: str | None = None
    quantity: float | None = Field(default=None, ge=0)
    quantity_allocated: float | None = Field(default=None, ge=0)
    quantity_reserved: float | None = Field(default=None, ge=0)
    quantity_available: float | None = Field(default=None, ge=0)
    unit_of_measure: UnitOfMeasure | None = None
    updated_by: str | None = None
    measured_at: datetime | None = None


class InventoryItemQuantityResponse(InventoryItemQuantityBase):
    id: str
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaginatedInventoryItemQuantityResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[InventoryItemQuantityResponse]
