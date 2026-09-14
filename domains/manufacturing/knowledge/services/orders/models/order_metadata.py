from typing import Optional

from pydantic import BaseModel, Field


class OrderMetadata(BaseModel):
    order_id: Optional[str] = Field(
        None, description="Unique identifier for the order", example="ORD123456"
    )
    customer_segment: Optional[str] = Field(
        None,
        description="Segment of the customer, for example Retail, Wholesale, or VIP",
        example="VIP",
    )
    sales_region: Optional[str] = Field(
        None,
        description="Region where the sale occurred, for example North America, Europe, or Asia",
        example="North America",
    )
    salesperson: Optional[str] = Field(
        None,
        description="Name or ID of the salesperson responsible for the sale",
        example="Jane Smith",
    )
    sales_channel: Optional[str] = Field(
        None,
        description="The channel through which the sale occurred, for example Online, In-Store, or Distributor",
        example="Online",
    )
    customs_declaration_id: Optional[str] = Field(
        None,
        description="Customs declaration ID for the shipment",
        example="CD-98765432",
    )
    total_sales_value: Optional[float] = Field(
        None,
        gt=0,
        description="Total revenue generated from the product",
        example=200.0,
    )
    discount_applied: Optional[float] = Field(
        0.0, ge=0, description="Total discount applied to the product", example=20.0
    )
    net_sales_value: Optional[float] = Field(
        None, gt=0, description="Revenue after discounts", example=180.0
    )
    tax_amount: Optional[float] = Field(
        0.0, ge=0, description="Tax amount applied to the product", example=18.0
    )
    shipping_charges: Optional[float] = Field(
        0.0,
        ge=0,
        description="Shipping charges for the product, if applicable",
        example=5.0,
    )
    total_revenue: Optional[float] = Field(
        None,
        gt=0,
        description="Total revenue including tax and shipping charges",
        example=203.0,
    )
    commission_rate: Optional[float] = Field(
        0.0, ge=0, le=100, description="Commission rate as a percentage", example=5.0
    )
    commission_amount: Optional[float] = Field(
        0.0, ge=0, description="Total commission earned for the product", example=10.0
    )
    promo_code_used: Optional[bool] = Field(
        False, description="Indicates whether a promo code was applied", example=True
    )
    promo_code_value: Optional[float] = Field(
        0.0, ge=0, description="Value of the promo code discount", example=10.0
    )
    upsell_or_cross_sell: Optional[str] = Field(
        None,
        description="Indicates if the product was part of an upsell or cross-sell strategy",
        example="Upsell",
    )
    refund_amount: Optional[float] = Field(
        0.0,
        ge=0,
        description="Refund amount issued for the product, if applicable",
        example=50.0,
    )


class OrderMetadataCreate(OrderMetadata):
    pass


class OrderMetadataUpdate(BaseModel):
    customer_segment: Optional[str] = None
    sales_region: Optional[str] = None
    salesperson: Optional[str] = None
    sales_channel: Optional[str] = None
    customs_declaration_id: Optional[str] = None
    total_sales_value: Optional[float] = Field(None, gt=0)
    discount_applied: Optional[float] = Field(None, ge=0)
    net_sales_value: Optional[float] = Field(None, gt=0)
    tax_amount: Optional[float] = Field(None, ge=0)
    shipping_charges: Optional[float] = Field(None, ge=0)
    total_revenue: Optional[float] = Field(None, gt=0)
    commission_rate: Optional[float] = Field(None, ge=0, le=100)
    commission_amount: Optional[float] = Field(None, ge=0)
    promo_code_used: Optional[bool] = None
    promo_code_value: Optional[float] = Field(None, ge=0)
    upsell_or_cross_sell: Optional[str] = None
    refund_amount: Optional[float] = Field(None, ge=0)


class OrderMetadataResponse(OrderMetadata):
    class Config:
        from_attributes = True


class PaginatedOrderMetadataResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[OrderMetadataResponse]
