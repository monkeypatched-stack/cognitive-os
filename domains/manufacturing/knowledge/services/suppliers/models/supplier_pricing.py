from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class SupplierPricingModel(BaseModel):
    supplier_id: str = Field(..., description="Unique identifier for the supplier.")
    item_id: str = Field(..., description="Unique identifier for the inventory item.")
    location_id: str = Field(
        ..., description="Unique identifier for the supplier's specific location."
    )
    item_name: str = Field(..., description="Name or description of the item.")
    unit_price: float = Field(..., description="Price per unit of the item.")
    currency: str = Field(..., description="Currency in which the price is quoted.")
    pricing_tier: str = Field(
        ...,
        description="Price category based on order volume, for example Bulk, Standard, or Wholesale.",
    )
    discount_rate: float = Field(
        ..., description="Discount percentage for bulk purchases or special agreements."
    )
    net_unit_price: float = Field(
        ..., description="Final price per unit after applying any discounts."
    )
    quantity_range: str = Field(
        ..., description="Range of quantities applicable for the specific pricing tier."
    )
    pricing_validity_start: datetime = Field(
        ..., description="Start datetime for the validity of the quoted price."
    )
    pricing_validity_end: datetime = Field(
        ..., description="End datetime for the validity of the quoted price."
    )
    price_adjustment_terms: str = Field(
        ...,
        description="Terms for adjusting prices due to inflation, raw material costs, etc.",
    )
    tax_rate: float = Field(..., description="Applicable tax percentage for the item.")
    tax_amount: float = Field(..., description="Calculated tax amount per unit.")
    total_unit_cost: float = Field(..., description="Unit price including taxes.")
    shipping_cost_per_unit: float = Field(
        ..., description="Estimated or actual shipping cost per unit."
    )
    total_landed_cost: float = Field(
        ..., description="Total cost per unit including price, taxes, and shipping."
    )
    payment_terms: str = Field(
        ...,
        description="Terms of payment specific to pricing agreements, for example Net 30 or Advance Payment.",
    )
    minimum_order_quantity: int = Field(
        ...,
        description="Minimum quantity required for purchase at the specified price.",
    )
    price_escalation_clause: Optional[str] = Field(
        None, description="Conditions under which price increases are allowed."
    )
    currency_exchange_rate: Optional[float] = Field(
        None, description="Exchange rate if pricing is in a foreign currency."
    )
    custom_pricing_notes: Optional[str] = Field(
        None, description="Additional notes or special agreements regarding pricing."
    )


class SupplierPricingCreate(SupplierPricingModel):
    pass


class SupplierPricingUpdate(BaseModel):
    supplier_id: Optional[str] = None
    item_id: Optional[str] = None
    location_id: Optional[str] = None
    item_name: Optional[str] = None
    unit_price: Optional[float] = None
    currency: Optional[str] = None
    pricing_tier: Optional[str] = None
    discount_rate: Optional[float] = None
    net_unit_price: Optional[float] = None
    quantity_range: Optional[str] = None
    pricing_validity_start: Optional[datetime] = None
    pricing_validity_end: Optional[datetime] = None
    price_adjustment_terms: Optional[str] = None
    tax_rate: Optional[float] = None
    tax_amount: Optional[float] = None
    total_unit_cost: Optional[float] = None
    shipping_cost_per_unit: Optional[float] = None
    total_landed_cost: Optional[float] = None
    payment_terms: Optional[str] = None
    minimum_order_quantity: Optional[int] = None
    price_escalation_clause: Optional[str] = None
    currency_exchange_rate: Optional[float] = None
    custom_pricing_notes: Optional[str] = None


class SupplierPricingResponse(SupplierPricingModel):
    supplier_pricing_id: str

    class Config:
        from_attributes = True


class PaginatedSupplierPricingResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[SupplierPricingResponse]
