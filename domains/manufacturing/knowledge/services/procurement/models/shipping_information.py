from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class PurchaseOrderShippingInformation(BaseModel):
    po_number: str = Field(..., description="Unique identifier for the purchase order.")
    shipping_id: str = Field(
        ..., description="Unique identifier for the shipping record"
    )
    shipping_address: str = Field(
        ..., description="Address where the component is being shipped to"
    )
    shipping_date: Optional[datetime] = Field(
        None, description="Date when the component was shipped"
    )
    expected_delivery_date: Optional[datetime] = Field(
        None, description="Date when the shipment is expected to arrive"
    )
    shipping_method: Optional[str] = Field(
        None, description="Method of shipment, for example Air, Sea, or Ground"
    )
    tracking_number: Optional[str] = Field(
        None, description="Tracking number provided by the shipping carrier"
    )
    carrier_name: Optional[str] = Field(
        None, description="Name of the shipping carrier or service"
    )
    shipment_status: Optional[str] = Field(
        None, description="Current status of the shipment"
    )
    weight: Optional[float] = Field(
        None, description="Weight of the shipment in kilograms"
    )
    shipping_cost: Optional[float] = Field(
        None, description="Cost of shipping the component"
    )
    insurance_details: Optional[str] = Field(
        None, description="Details about any insurance coverage for the shipment"
    )
    remarks: Optional[str] = Field(
        None, description="Additional notes or comments related to the shipment"
    )


class PurchaseOrderShippingInformationCreate(PurchaseOrderShippingInformation):
    pass


class PurchaseOrderShippingInformationUpdate(BaseModel):
    po_number: Optional[str] = None
    shipping_address: Optional[str] = None
    shipping_date: Optional[datetime] = None
    expected_delivery_date: Optional[datetime] = None
    shipping_method: Optional[str] = None
    tracking_number: Optional[str] = None
    carrier_name: Optional[str] = None
    shipment_status: Optional[str] = None
    weight: Optional[float] = None
    shipping_cost: Optional[float] = None
    insurance_details: Optional[str] = None
    remarks: Optional[str] = None


class PurchaseOrderShippingInformationResponse(PurchaseOrderShippingInformation):
    class Config:
        from_attributes = True


class PaginatedPurchaseOrderShippingInformationResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[PurchaseOrderShippingInformationResponse]
