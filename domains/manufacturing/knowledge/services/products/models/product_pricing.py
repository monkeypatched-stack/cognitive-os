from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.products.models.product_common import (
    PricingType,
    SupportedCurrency,
    PricingTier,
    utc_now,
    ensure_utc,
)


class ProductPricingBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: str = Field(..., min_length=1)
    pricing_type: PricingType = "Standard"
    currency: str = Field(default="USD", min_length=3, max_length=3)
    price_per_unit: float = Field(default=0, ge=0)
    min_order_quantity: Optional[int] = Field(default=None, gt=0)
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None
    tier_rules: Optional[list[PricingTier]] = None
    is_active: bool = True
    notes: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None

    @model_validator(mode="after")
    def effective_to_after_from(self) -> "ProductPricingBase":
        if (
            self.effective_from
            and self.effective_to
            and self.effective_to < self.effective_from
        ):
            raise ValueError("effective_to must be on or after effective_from")
        return self


class ProductPricingRecord(ProductPricingBase):
    pricing_id: str = Field(..., min_length=1)
    created_by: Optional[str] = None
    updated_by: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "ProductPricingRecord":
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self


class ProductPricingCreate(ProductPricingBase):
    pass


class ProductPricingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: Optional[str] = None
    pricing_type: Optional[PricingType] = None
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    price_per_unit: Optional[float] = Field(default=None, ge=0)
    min_order_quantity: Optional[int] = Field(default=None, gt=0)
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None
    tier_rules: Optional[list[PricingTier]] = None
    is_active: Optional[bool] = None
    notes: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None
    updated_by: Optional[str] = None
    updated_at: datetime = Field(default_factory=utc_now)


class PaginatedProductPricingResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[ProductPricingRecord]
