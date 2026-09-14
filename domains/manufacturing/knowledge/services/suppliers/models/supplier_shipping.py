from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class SupplierShippingData(BaseModel):
    supplier_id: str = Field(..., description="Unique identifier for the supplier.")
    item_id: str = Field(..., description="Unique identifier for the inventory item.")
    location_id: str = Field(
        ..., description="Unique identifier for the supplier's specific location."
    )
    shipping_method: str = Field(
        ...,
        description="The mode of transportation used, for example Air, Sea, Rail, Road, or Courier.",
    )
    shipping_carrier: str = Field(
        ..., description="Name of the logistics provider or shipping company."
    )
    shipping_terms: str = Field(
        ...,
        description="Delivery terms as per the Incoterms, for example FOB, CIF, DDP, or EXW.",
    )
    origin_address: str = Field(
        ..., description="Address from which the goods will be shipped."
    )
    destination_address: str = Field(..., description="Delivery address for the goods.")
    average_transit_time_days: int = Field(
        ...,
        description="Average number of days required for shipping to the destination.",
    )
    shipping_cost: float = Field(
        ..., description="Estimated or actual shipping cost per shipment."
    )
    shipping_currency: str = Field(
        ..., description="Currency in which the shipping cost is quoted."
    )
    packaging_type: str = Field(
        ...,
        description="Type of packaging used for shipments, for example Pallet, Crate, or Box.",
    )
    max_weight_per_shipment: float = Field(
        ..., description="Maximum allowable weight for a single shipment in kg."
    )
    max_volume_per_shipment: float = Field(
        ...,
        description="Maximum allowable volume for a single shipment in cubic meters.",
    )
    tracking_available: bool = Field(
        ..., description="Indicates if shipment tracking is available."
    )
    tracking_url: Optional[str] = Field(
        None, description="URL for shipment tracking, if applicable."
    )
    preferred_delivery_time: str = Field(
        ..., description="Preferred time window for delivery, for example 9 AM - 5 PM."
    )
    insurance_provided: bool = Field(
        ..., description="Indicates if the shipment is insured."
    )
    insurance_coverage_amount: Optional[float] = Field(
        None, description="Value covered by shipping insurance."
    )
    freight_class: Optional[str] = Field(
        None,
        description="Freight classification for the shipment, for example Hazardous or Perishable.",
    )
    customs_clearance_included: bool = Field(
        ...,
        description="Indicates if customs clearance is necessary for international shipments.",
    )
    customs_documentation: list[str] = Field(
        ...,
        description="List of documents required for customs clearance, for example Invoice or Packing List.",
    )
    last_shipping_date: Optional[datetime] = Field(
        None,
        description="The most recent date a shipment was dispatched by the supplier.",
    )
    remarks: Optional[str] = Field(
        None,
        description="Additional notes or special instructions related to shipping.",
    )


class SupplierShippingCreate(SupplierShippingData):
    pass


class SupplierShippingUpdate(BaseModel):
    supplier_id: Optional[str] = None
    item_id: Optional[str] = None
    location_id: Optional[str] = None
    shipping_method: Optional[str] = None
    shipping_carrier: Optional[str] = None
    shipping_terms: Optional[str] = None
    origin_address: Optional[str] = None
    destination_address: Optional[str] = None
    average_transit_time_days: Optional[int] = None
    shipping_cost: Optional[float] = None
    shipping_currency: Optional[str] = None
    packaging_type: Optional[str] = None
    max_weight_per_shipment: Optional[float] = None
    max_volume_per_shipment: Optional[float] = None
    tracking_available: Optional[bool] = None
    tracking_url: Optional[str] = None
    preferred_delivery_time: Optional[str] = None
    insurance_provided: Optional[bool] = None
    insurance_coverage_amount: Optional[float] = None
    freight_class: Optional[str] = None
    customs_clearance_included: Optional[bool] = None
    customs_documentation: Optional[list[str]] = None
    last_shipping_date: Optional[datetime] = None
    remarks: Optional[str] = None


class SupplierShippingResponse(SupplierShippingData):
    supplier_shipping_id: str

    class Config:
        from_attributes = True


class PaginatedSupplierShippingResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[SupplierShippingResponse]
