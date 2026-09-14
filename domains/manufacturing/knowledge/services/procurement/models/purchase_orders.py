from typing import Optional

from pydantic import BaseModel, Field


class PurchaseOrder(BaseModel):
    po_number: str = Field(..., description="Unique identifier for the purchase order.")
    po_date: str = Field(
        ..., description="The date when the purchase order was created."
    )
    supplier_id: str = Field(
        ..., description="Unique identifier for the supplier associated with the PO."
    )
    supplier_name: str = Field(..., description="Name of the supplier.")
    buyer_id: str = Field(
        ..., description="Unique identifier for the buyer organization."
    )
    buyer_name: str = Field(..., description="Name of the buyer organization.")
    total_amount: float = Field(
        ..., description="The total monetary value of the purchase order."
    )
    currency: str = Field(
        ..., description="Currency in which the transaction is conducted."
    )
    payment_terms: str = Field(
        ..., description="Terms of payment agreed upon with the supplier."
    )
    delivery_date: str = Field(
        ..., description="The expected delivery date for the items in the order."
    )
    shipping_address: str = Field(
        ..., description="The address where the items should be delivered."
    )
    billing_address: str = Field(
        ..., description="The address where the invoice will be sent."
    )
    status: str = Field(
        ...,
        description="The current status of the PO, for example Open, Approved, or Closed.",
    )
    approval_date: Optional[str] = Field(
        None, description="The date when the PO was approved, null if not yet approved."
    )
    remarks: Optional[str] = Field(
        None, description="Additional comments or notes about the purchase order."
    )


class PurchaseOrderCreate(PurchaseOrder):
    pass


class PurchaseOrderUpdate(BaseModel):
    po_date: Optional[str] = None
    supplier_id: Optional[str] = None
    supplier_name: Optional[str] = None
    buyer_id: Optional[str] = None
    buyer_name: Optional[str] = None
    total_amount: Optional[float] = None
    currency: Optional[str] = None
    payment_terms: Optional[str] = None
    delivery_date: Optional[str] = None
    shipping_address: Optional[str] = None
    billing_address: Optional[str] = None
    status: Optional[str] = None
    approval_date: Optional[str] = None
    remarks: Optional[str] = None


class PurchaseOrderResponse(PurchaseOrder):
    class Config:
        from_attributes = True


class PaginatedPurchaseOrderResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[PurchaseOrderResponse]
