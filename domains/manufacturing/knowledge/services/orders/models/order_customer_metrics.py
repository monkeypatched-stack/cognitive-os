from typing import Optional

from pydantic import BaseModel, Field


class CustomerOrderMetrics(BaseModel):
    order_id: str = Field(
        ..., description="Unique identifier for the order", example="ORD123456"
    )
    returns_exchanges_requested: Optional[int] = Field(
        None, description="Number of items requested for return or exchange", example=5
    )
    backordered_items: Optional[int] = Field(
        None,
        description="Number of items that were backordered in the order",
        example=2,
    )
    shipping_tracking_number: Optional[str] = Field(
        None,
        description="Tracking number provided by the shipping carrier",
        example="12999AA10123456784",
    )
    order_fulfillment_location: Optional[str] = Field(
        None,
        description="Location from which the order was fulfilled, for example Warehouse A",
        example="Warehouse B",
    )
    customs_clearance_status: Optional[str] = Field(
        None,
        description="Customs status for international shipments, if applicable",
        example="Cleared",
    )
    order_handling_time: Optional[int] = Field(
        None,
        description="Time taken to handle and prepare the order before shipment in days",
        example=1,
    )
    shipping_status: Optional[str] = Field(
        None,
        description="Current status of the order's shipment, for example In Transit, Delivered, or Pending",
        example="In Transit",
    )
    order_delivery_time: Optional[int] = Field(
        None,
        description="Time taken for the order to be delivered from shipment in days",
        example=3,
    )
    customer_feedback_score: Optional[int] = Field(
        None,
        description="Customer rating or feedback score for the order experience from 1-10",
        example=8,
    )
    order_quality_rating: Optional[int] = Field(
        None,
        description="Rating based on the quality of the received items from 1-5",
        example=4,
    )
    failed_delivery_attempts: Optional[int] = Field(
        None, description="Number of failed delivery attempts for the order", example=1
    )
    damage_defect_rate: Optional[float] = Field(
        None,
        description="Percentage of orders with damage or defects reported by the customer",
        example=2.0,
    )
    order_resolution_time: Optional[int] = Field(
        None, description="Time taken to resolve any issues in days", example=1
    )
    customer_service_interaction_count: Optional[int] = Field(
        None,
        description="Number of customer service interactions for the order",
        example=2,
    )
    order_accuracy_rate: Optional[float] = Field(
        None,
        description="Percentage of orders that were fulfilled correctly",
        example=98.0,
    )
    late_shipment_rate: Optional[float] = Field(
        None,
        description="Percentage of orders shipped later than the expected date",
        example=4.0,
    )
    customer_loyalty_index: Optional[float] = Field(
        None,
        description="A score indicating the likelihood of the customer returning",
        example=85.0,
    )
    order_fulfillment_time: Optional[int] = Field(
        None,
        description="Total time taken to process an order from placement to delivery in days",
        example=10,
    )
    lead_time_variability: Optional[str] = Field(
        None,
        description="Consistency of lead times for fulfilling orders",
        example="±2 days",
    )
    cycle_time_for_time_sensitive_orders: Optional[int] = Field(
        None,
        description="Time taken to process and fulfill priority orders in days",
        example=5,
    )
    missed_priority_order_rate: Optional[float] = Field(
        None,
        description="Percentage of priority orders not fulfilled within the expected timeframe",
        example=3.0,
    )
    backlogged_order_rate: Optional[float] = Field(
        None,
        description="Percentage of orders delivered after the promised deadline",
        example=4.0,
    )
    critical_path_completion_rate: Optional[float] = Field(
        None,
        description="Percentage of critical path tasks completed on time",
        example=95.0,
    )
    order_cycle_time: Optional[int] = Field(
        None, description="Time from order placement to delivery in days", example=12
    )
    order_fill_rate: Optional[float] = Field(
        None,
        description="Percentage of customer orders fully fulfilled on the first attempt",
        example=99.0,
    )
    perfect_order_rate: Optional[float] = Field(
        None, description="Percentage of orders delivered without issues", example=96.0
    )


class CustomerOrderMetricsCreate(CustomerOrderMetrics):
    pass


class CustomerOrderMetricsUpdate(BaseModel):
    returns_exchanges_requested: Optional[int] = None
    backordered_items: Optional[int] = None
    shipping_tracking_number: Optional[str] = None
    order_fulfillment_location: Optional[str] = None
    customs_clearance_status: Optional[str] = None
    order_handling_time: Optional[int] = None
    shipping_status: Optional[str] = None
    order_delivery_time: Optional[int] = None
    customer_feedback_score: Optional[int] = None
    order_quality_rating: Optional[int] = None
    failed_delivery_attempts: Optional[int] = None
    damage_defect_rate: Optional[float] = None
    order_resolution_time: Optional[int] = None
    customer_service_interaction_count: Optional[int] = None
    order_accuracy_rate: Optional[float] = None
    late_shipment_rate: Optional[float] = None
    customer_loyalty_index: Optional[float] = None
    order_fulfillment_time: Optional[int] = None
    lead_time_variability: Optional[str] = None
    cycle_time_for_time_sensitive_orders: Optional[int] = None
    missed_priority_order_rate: Optional[float] = None
    backlogged_order_rate: Optional[float] = None
    critical_path_completion_rate: Optional[float] = None
    order_cycle_time: Optional[int] = None
    order_fill_rate: Optional[float] = None
    perfect_order_rate: Optional[float] = None


class CustomerOrderMetricsResponse(CustomerOrderMetrics):
    class Config:
        from_attributes = True


class PaginatedCustomerOrderMetricsResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[CustomerOrderMetricsResponse]
