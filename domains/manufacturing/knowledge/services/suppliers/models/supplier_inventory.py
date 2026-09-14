from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class SupplierInventory(BaseModel):
    supplier_id: str = Field(..., description="Unique identifier for the supplier.")
    item_id: str = Field(..., description="Unique identifier for the inventory item.")
    item_name: str = Field(
        ..., description="Name or description of the inventory item."
    )
    sku: str = Field(
        ...,
        description="Unique stock-keeping unit identifier assigned by the supplier.",
    )
    category: str = Field(
        ...,
        description="Category or classification of the inventory item, for example Raw Material or Component.",
    )
    current_stock: int = Field(
        ..., description="Quantity of the item currently available in stock."
    )
    unit_of_measure: str = Field(
        ...,
        description="Unit in which the inventory is measured, for example Kg or Units.",
    )
    reorder_level: int = Field(
        ..., description="Minimum stock level at which new stock needs to be ordered."
    )
    safety_stock: int = Field(
        ..., description="Buffer stock maintained to avoid stockouts."
    )
    maximum_stock_level: Optional[int] = Field(
        None, description="Maximum quantity that can be stored."
    )
    location_id: str = Field(
        ..., description="Identifier for the location where the inventory is stored."
    )
    batch_number: Optional[str] = Field(
        None, description="Batch identifier for tracking purposes."
    )
    manufacturing_date: Optional[datetime] = Field(
        None, description="Date when the item was manufactured."
    )
    expiry_date: Optional[datetime] = Field(
        None, description="Expiry date for perishable or limited-lifecycle items."
    )
    cost_per_unit: float = Field(..., description="Cost of one unit of the item.")
    currency: str = Field(..., description="Currency in which the item is priced.")
    reserved_stock: Optional[int] = Field(
        None, description="Quantity reserved for existing orders or commitments."
    )
    available_stock: int = Field(
        ...,
        description="Stock available for new orders after accounting for reserved quantities.",
    )
    lead_time: int = Field(
        ..., description="Time in days required to restock the item."
    )
    last_stock_update_date: Optional[datetime] = Field(
        None, description="Date when the inventory data was last updated."
    )
    supplier_lead_time: Optional[int] = Field(
        None, description="Time taken by the supplier to replenish stock."
    )
    stock_turnover_rate: Optional[float] = Field(
        None, description="Rate at which stock is consumed or sold."
    )
    quality_check_status: bool = Field(
        ..., description="Indicates whether the stock has passed quality checks."
    )
    remarks: Optional[str] = Field(
        None, description="Additional notes or observations about the stock."
    )


class SupplierInventoryCreate(SupplierInventory):
    pass


class SupplierInventoryUpdate(BaseModel):
    supplier_id: Optional[str] = None
    item_id: Optional[str] = None
    item_name: Optional[str] = None
    sku: Optional[str] = None
    category: Optional[str] = None
    current_stock: Optional[int] = None
    unit_of_measure: Optional[str] = None
    reorder_level: Optional[int] = None
    safety_stock: Optional[int] = None
    maximum_stock_level: Optional[int] = None
    location_id: Optional[str] = None
    batch_number: Optional[str] = None
    manufacturing_date: Optional[datetime] = None
    expiry_date: Optional[datetime] = None
    cost_per_unit: Optional[float] = None
    currency: Optional[str] = None
    reserved_stock: Optional[int] = None
    available_stock: Optional[int] = None
    lead_time: Optional[int] = None
    last_stock_update_date: Optional[datetime] = None
    supplier_lead_time: Optional[int] = None
    stock_turnover_rate: Optional[float] = None
    quality_check_status: Optional[bool] = None
    remarks: Optional[str] = None


class SupplierInventoryResponse(SupplierInventory):
    supplier_inventory_id: str

    class Config:
        from_attributes = True


class PaginatedSupplierInventoryResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[SupplierInventoryResponse]
