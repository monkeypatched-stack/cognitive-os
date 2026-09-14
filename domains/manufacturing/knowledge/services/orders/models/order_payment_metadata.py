from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


def datetime_now() -> datetime:
    return datetime.now(timezone.utc)


class OrderPaymentMetadata(BaseModel):
    order_id: str = Field(..., description="Unique identifier for the order")
    order_payment_status: Optional[str] = Field(
        None, description="Current payment status of the order", example="Paid"
    )
    payment_method: Optional[str] = Field(
        None,
        description="Method used by the customer to pay for the order",
        example="Credit Card",
    )
    payment_terms: Optional[str] = Field(
        None,
        description="Payment terms agreed upon with the customer",
        example="Net 30",
    )
    order_payment_due_date: Optional[datetime] = Field(
        None,
        description="Date by which payment is due for the order",
        example="2025-01-20",
    )
    payment_received_date: Optional[datetime] = Field(
        None, description="Actual date the payment was received", example="2025-01-15"
    )
    payment_amount: Optional[float] = Field(
        None,
        description="Total amount paid by the customer for the order",
        example=1200.0,
    )
    outstanding_payment_amount: Optional[float] = Field(
        None, description="Remaining balance to be paid on the order", example=0.0
    )
    payment_overdue_amount: Optional[float] = Field(
        None, description="Total amount that is overdue for payment", example=150.0
    )
    early_payment_discount: Optional[bool] = Field(
        None,
        description="Indicates whether an early payment discount has been applied",
        example=True,
    )
    late_payment_penalty: Optional[bool] = Field(
        None,
        description="Indicates whether a late payment penalty has been applied",
        example=True,
    )
    credit_limit_utilization: Optional[float] = Field(
        None,
        description="Percentage of the customer's available credit limit utilized",
        example=80.0,
    )
    customer_payment_history: Optional[str] = Field(
        None,
        description="Summary of historical payment behavior",
        example="Consistently Early",
    )
    payment_dispute_status: Optional[str] = Field(
        None,
        description="Indicates whether there is any payment dispute",
        example="Resolved",
    )
    payment_risk_rating: Optional[str] = Field(
        None, description="Risk level associated with the payment", example="Low"
    )
    payment_processing_time: Optional[int] = Field(
        None,
        description="Time taken for payment to be processed and received in days",
        example=3,
    )
    payment_approval_status: Optional[str] = Field(
        None, description="Status of payment approval", example="Approved"
    )


class OrderPaymentMetadataCreate(OrderPaymentMetadata):
    pass


class OrderPaymentMetadataUpdate(BaseModel):
    order_payment_status: Optional[str] = None
    payment_method: Optional[str] = None
    payment_terms: Optional[str] = None
    order_payment_due_date: Optional[datetime] = None
    payment_received_date: Optional[datetime] = None
    payment_amount: Optional[float] = None
    outstanding_payment_amount: Optional[float] = None
    payment_overdue_amount: Optional[float] = None
    early_payment_discount: Optional[bool] = None
    late_payment_penalty: Optional[bool] = None
    credit_limit_utilization: Optional[float] = None
    customer_payment_history: Optional[str] = None
    payment_dispute_status: Optional[str] = None
    payment_risk_rating: Optional[str] = None
    payment_processing_time: Optional[int] = None
    payment_approval_status: Optional[str] = None


class OrderPaymentMetadataResponse(OrderPaymentMetadata):
    class Config:
        from_attributes = True


class PaginatedOrderPaymentMetadataResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[OrderPaymentMetadataResponse]
