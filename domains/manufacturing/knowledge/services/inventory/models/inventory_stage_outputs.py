from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.products.models.product_common import utc_now


class StageOutputUnitOfMeasure(str, Enum):
    PIECE = "piece"
    BOX = "box"
    PALLET = "pallet"
    KG = "kg"
    LITER = "liter"
    OTHER = "other"


class InventoryStageOutputBase(BaseModel):
    product_id: str = Field(..., min_length=1)
    sku: str = Field(..., min_length=1, description="Stock Keeping Unit")
    stage_id: str = Field(..., min_length=1)
    stage_name: str | None = None
    workstation_id: str | None = None
    line_id: str | None = None

    stage_count: int | None = Field(default=None, ge=0)
    stage_quantity: float | None = Field(default=None, ge=0)
    unit_of_measure: StageOutputUnitOfMeasure = StageOutputUnitOfMeasure.PIECE

    is_final_stage: bool = False
    final_product_count: int | None = Field(default=None, ge=0)
    final_product_quantity: float | None = Field(default=None, ge=0)

    recorded_by: str | None = None
    recorded_at: datetime = Field(default_factory=utc_now)
    notes: str | None = None
    metadata: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_stage_and_final_output(self):
        if self.stage_count is None and self.stage_quantity is None:
            raise ValueError("Provide at least one of: stage_count or stage_quantity")

        if self.is_final_stage:
            has_final_count = self.final_product_count is not None
            has_final_quantity = self.final_product_quantity is not None
            if not has_final_count and not has_final_quantity:
                raise ValueError(
                    "Final stage records require final_product_count or "
                    "final_product_quantity"
                )
        elif (
            self.final_product_count is not None
            or self.final_product_quantity is not None
        ):
            raise ValueError(
                "final_product_count/final_product_quantity require is_final_stage=True"
            )

        return self


class InventoryStageOutputCreate(InventoryStageOutputBase):
    """Schema for recording product count or quantity at a production stage."""


class InventoryStageOutputUpdate(BaseModel):
    product_id: str | None = None
    sku: str | None = None
    stage_id: str | None = None
    stage_name: str | None = None
    workstation_id: str | None = None
    line_id: str | None = None
    stage_count: int | None = Field(default=None, ge=0)
    stage_quantity: float | None = Field(default=None, ge=0)
    unit_of_measure: StageOutputUnitOfMeasure | None = None
    is_final_stage: bool | None = None
    final_product_count: int | None = Field(default=None, ge=0)
    final_product_quantity: float | None = Field(default=None, ge=0)
    recorded_by: str | None = None
    recorded_at: datetime | None = None
    notes: str | None = None
    metadata: dict[str, Any] | None = None


class InventoryStageOutputResponse(InventoryStageOutputBase):
    id: str
    stage_output_id: str
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaginatedInventoryStageOutputResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[InventoryStageOutputResponse]
