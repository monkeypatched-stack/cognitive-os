from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator

from services.products.models.product_common import (
    ProductStatus,
    ProductType,
    LifecycleStage,
    UnitOfMeasure,
    utc_now,
    ensure_utc,
)


class Product(BaseModel):
    product_id: str = Field(..., min_length=1)
    asset_tag: Optional[str] = None
    sku: str = Field(..., min_length=1)
    gtin: Optional[str] = None
    upc: Optional[str] = None
    ean: Optional[str] = None
    cas_number: Optional[str] = None
    barcode: Optional[str] = None
    upc_ean: Optional[str] = None
    mpn: Optional[str] = None
    ndc: Optional[str] = None
    internal_code: Optional[str] = None

    name: str = Field(..., min_length=1)
    description: Optional[str] = None
    short_description: Optional[str] = None
    brand: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    product_type: ProductType = "Finished-Good"
    lifecycle_stage: Optional[LifecycleStage] = None
    status: ProductStatus = "Draft"

    base_unit_of_measure: UnitOfMeasure = "Each"
    packaging_type: Optional[str] = None
    units_per_case: Optional[float] = Field(default=None, gt=0)
    units_per_pallet: Optional[float] = Field(default=None, gt=0)
    alternate_uoms: Optional[list[Any]] = None  # AlternateUom handled via dict in DB

    weight_kg: Optional[float] = Field(default=None, ge=0)
    volume_liters: Optional[float] = Field(default=None, ge=0)
    length_cm: Optional[float] = Field(default=None, ge=0)
    width_cm: Optional[float] = Field(default=None, ge=0)
    height_cm: Optional[float] = Field(default=None, ge=0)

    reorder_point: Optional[float] = Field(default=None, ge=0)
    max_stock_level: Optional[float] = Field(default=None, ge=0)
    min_order_quantity: Optional[int] = Field(default=None, gt=0)
    lead_time_days: Optional[int] = Field(default=None, ge=0)
    shelf_life_days: Optional[int] = Field(default=None, ge=0)
    storage_requirements: Optional[str] = None

    batch_tracking_required: bool = False
    serial_tracking_required: bool = False
    expiry_tracking_required: bool = False
    hazmat: bool = False
    temp_sensitive: bool = False
    controlled_substance: bool = False
    regulatory_certifications: Optional[list[str]] = None
    msds_url: Optional[str] = None

    cost_price: Optional[float] = Field(default=None, ge=0)
    selling_price: Optional[float] = Field(default=None, ge=0)
    currency: Optional[str] = Field(default="USD", min_length=3, max_length=3)
    tax_category: Optional[str] = None

    default_supplier_id: Optional[str] = None
    default_supplier_name: Optional[str] = None
    manufacturer_id: Optional[str] = None
    manufacturer_name: Optional[str] = None
    country_of_origin: Optional[str] = None

    images: list[str] = Field(default_factory=list)
    attachments: list[str] = Field(default_factory=list)


class ProductCreate(Product):
    pass


class ProductUpdate(BaseModel):
    product_id: Optional[str] = None
    asset_tag: Optional[str] = None
    sku: Optional[str] = None
    gtin: Optional[str] = None
    upc: Optional[str] = None
    ean: Optional[str] = None
    cas_number: Optional[str] = None
    barcode: Optional[str] = None
    upc_ean: Optional[str] = None
    mpn: Optional[str] = None
    internal_code: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    short_description: Optional[str] = None
    brand: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    tags: Optional[list[str]] = None
    product_type: Optional[ProductType] = None
    lifecycle_stage: Optional[LifecycleStage] = None
    status: Optional[ProductStatus] = None
    base_unit_of_measure: Optional[UnitOfMeasure] = None
    packaging_type: Optional[str] = None
    units_per_case: Optional[float] = Field(default=None, gt=0)
    units_per_pallet: Optional[float] = Field(default=None, gt=0)
    alternate_uoms: Optional[list[Any]] = None
    weight_kg: Optional[float] = Field(default=None, ge=0)
    volume_liters: Optional[float] = Field(default=None, ge=0)
    length_cm: Optional[float] = Field(default=None, ge=0)
    width_cm: Optional[float] = Field(default=None, ge=0)
    height_cm: Optional[float] = Field(default=None, ge=0)
    reorder_point: Optional[float] = Field(default=None, ge=0)
    max_stock_level: Optional[float] = Field(default=None, ge=0)
    min_order_quantity: Optional[int] = Field(default=None, gt=0)
    lead_time_days: Optional[int] = Field(default=None, ge=0)
    shelf_life_days: Optional[int] = Field(default=None, ge=0)
    storage_requirements: Optional[str] = None
    batch_tracking_required: Optional[bool] = None
    serial_tracking_required: Optional[bool] = None
    expiry_tracking_required: Optional[bool] = None
    hazmat: Optional[bool] = None
    temp_sensitive: Optional[bool] = None
    controlled_substance: Optional[bool] = None
    regulatory_certifications: Optional[list[str]] = None
    msds_url: Optional[str] = None
    cost_price: Optional[float] = Field(default=None, ge=0)
    selling_price: Optional[float] = Field(default=None, ge=0)
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    tax_category: Optional[str] = None
    default_supplier_id: Optional[str] = None
    default_supplier_name: Optional[str] = None
    manufacturer_id: Optional[str] = None
    manufacturer_name: Optional[str] = None
    country_of_origin: Optional[str] = None
    images: Optional[list[str]] = None
    attachments: Optional[list[str]] = None
    updated_by: Optional[str] = None
    updated_at: datetime = Field(default_factory=utc_now)


class ProductResponse(Product):
    class Config:
        from_attributes = True


class PaginatedProductResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[ProductResponse]
