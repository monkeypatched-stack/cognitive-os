from typing import Optional

from pydantic import BaseModel, model_validator

from services.orders.models.order_customer_metrics import (
    CustomerOrderMetricsCreate,
    CustomerOrderMetricsResponse,
    CustomerOrderMetricsUpdate,
)
from services.orders.models.order_details import (
    OrderDetailsCreate,
    OrderDetailsResponse,
    OrderDetailsUpdate,
)
from services.orders.models.order_metadata import (
    OrderMetadataCreate,
    OrderMetadataResponse,
    OrderMetadataUpdate,
)
from services.orders.models.order_payment_metadata import (
    OrderPaymentMetadataCreate,
    OrderPaymentMetadataResponse,
    OrderPaymentMetadataUpdate,
)


class OrderCreate(BaseModel):
    details: OrderDetailsCreate
    metadata: Optional[OrderMetadataCreate] = None
    payment: Optional[OrderPaymentMetadataCreate] = None
    metrics: Optional[CustomerOrderMetricsCreate] = None

    @model_validator(mode="after")
    def child_order_ids_match(self) -> "OrderCreate":
        order_id = self.details.order_id
        for label, payload in (
            ("metadata", self.metadata),
            ("payment", self.payment),
            ("metrics", self.metrics),
        ):
            if (
                payload is not None
                and payload.order_id
                and payload.order_id != order_id
            ):
                raise ValueError(f"{label}.order_id must match details.order_id")
        return self


class OrderUpdate(BaseModel):
    details: Optional[OrderDetailsUpdate] = None
    metadata: Optional[OrderMetadataUpdate] = None
    payment: Optional[OrderPaymentMetadataUpdate] = None
    metrics: Optional[CustomerOrderMetricsUpdate] = None


class OrderResponse(BaseModel):
    order_id: str
    details: OrderDetailsResponse
    metadata: Optional[OrderMetadataResponse] = None
    payment: Optional[OrderPaymentMetadataResponse] = None
    metrics: Optional[CustomerOrderMetricsResponse] = None


class PaginatedOrderResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[OrderResponse]
