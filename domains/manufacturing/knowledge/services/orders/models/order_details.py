from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


def datetime_now() -> datetime:
    return datetime.now(timezone.utc)


class OrderDetailsModel(BaseModel):
    order_id: str = Field(..., description="Unique identifier for the order")
    order_date: datetime = Field(default_factory=datetime_now)
    order_status: str = Field(
        ...,
        description="Current status of the order, for example Pending, Shipped, Delivered, or Canceled",
    )
    order_type: str = Field(
        ..., description="Type of order, for example Standard, Rush, or Backorder"
    )
    priority_level: Optional[str] = Field(
        None,
        description="Priority level of the order, for example High, Medium, or Low",
    )
    shipping_method: Optional[str] = Field(
        None,
        description="Shipping method used for the order, for example Ground or Air Freight",
    )
    payment_method: str = Field(
        ...,
        description="Method used to pay for the order, for example Credit Card or Bank Transfer",
    )


class OrderDetailsCreate(OrderDetailsModel):
    pass


class OrderDetailsUpdate(BaseModel):
    order_date: Optional[datetime] = None
    order_status: Optional[str] = None
    order_type: Optional[str] = None
    priority_level: Optional[str] = None
    shipping_method: Optional[str] = None
    payment_method: Optional[str] = None


class OrderDetailsResponse(OrderDetailsModel):
    class Config:
        from_attributes = True


class PaginatedOrderDetailsResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[OrderDetailsResponse]
