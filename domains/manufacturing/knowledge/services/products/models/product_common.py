from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

# ──────────────────────────────────────────────────────────────
# Type Aliases & Constants
# ──────────────────────────────────────────────────────────────
ProductStatus = Literal[
    "Draft", "Active", "Inactive", "Discontinued", "Obsolete", "On-Hold"
]
ProductType = Literal[
    "Finished-Good",
    "Raw-Material",
    "Component",
    "Consumable",
    "Spare-Part",
    "Packaging",
    "Service",
    "Kit/Bundle",
    "Other",
]
LifecycleStage = Literal[
    "Development", "Introduction", "Growth", "Maturity", "Decline", "End-of-Life"
]
UnitOfMeasure = Literal[
    "Each", "Case", "Pallet", "Kg", "L", "M", "Box", "Roll", "Custom"
]
ComponentType = Literal[
    "Required", "Optional", "Accessory", "Service", "Consumable", "Spare-Part"
]
PricingType = Literal[
    "Standard",
    "Promotional",
    "Contract",
    "Tiered",
    "Subscription",
    "Cost-Plus",
    "Dynamic",
]
SupportedCurrency = Literal[
    "USD", "EUR", "GBP", "INR", "AUD", "CAD", "SGD", "AED", "JPY"
]

PRODUCT_STATUSES: tuple[str, ...] = (
    "Draft",
    "Active",
    "Inactive",
    "Discontinued",
    "Obsolete",
    "On-Hold",
)
PRODUCT_TYPES: tuple[str, ...] = (
    "Finished-Good",
    "Raw-Material",
    "Component",
    "Consumable",
    "Spare-Part",
    "Packaging",
    "Service",
    "Kit/Bundle",
    "Other",
)
LIFECYCLE_STAGES: tuple[str, ...] = (
    "Development",
    "Introduction",
    "Growth",
    "Maturity",
    "Decline",
    "End-of-Life",
)
UNITS_OF_MEASURE: tuple[str, ...] = (
    "Each",
    "Case",
    "Pallet",
    "Kg",
    "L",
    "M",
    "Box",
    "Roll",
    "Custom",
)
COMPONENT_TYPES: tuple[str, ...] = (
    "Required",
    "Optional",
    "Accessory",
    "Service",
    "Consumable",
    "Spare-Part",
)
PRICING_TYPES: tuple[str, ...] = (
    "Standard",
    "Promotional",
    "Contract",
    "Tiered",
    "Subscription",
    "Cost-Plus",
    "Dynamic",
)
SUPPORTED_CURRENCIES: tuple[str, ...] = (
    "USD",
    "EUR",
    "GBP",
    "INR",
    "AUD",
    "CAD",
    "SGD",
    "AED",
    "JPY",
)


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────
def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ──────────────────────────────────────────────────────────────
# Shared Embedded Models
# ──────────────────────────────────────────────────────────────
class AlternateUom(BaseModel):
    code: str = Field(..., min_length=1)
    factor: float = Field(..., gt=0)


class PricingTier(BaseModel):
    min_quantity: int = Field(default=1, gt=0)
    max_quantity: Optional[int] = Field(default=None, gt=0)
    price_per_unit: float = Field(default=0, ge=0)

    @model_validator(mode="after")
    def max_quantity_after_min_quantity(self) -> "PricingTier":
        if self.max_quantity is not None and self.max_quantity < self.min_quantity:
            raise ValueError(
                "max_quantity must be greater than or equal to min_quantity"
            )
        return self
