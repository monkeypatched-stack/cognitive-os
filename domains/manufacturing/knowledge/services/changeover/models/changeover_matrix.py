from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


class ChangeoverMatrixEntry(BaseModel):
    """Standard target duration for switching products on a workstation."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    workstation_id: str
    from_product_id: str
    to_product_id: str
    standard_minutes: int = Field(
        ..., ge=1, description="Target changeover duration (minutes)"
    )
    best_achieved_minutes: Optional[int] = Field(
        None,
        ge=1,
        description="Best actual time ever recorded - used for SMED benchmarking",
    )
    requires_cleaning: bool = False
    requires_tooling: bool = False
    min_operators: int = Field(default=1, ge=1)
    notes: Optional[str] = Field(None, max_length=400)

    @model_validator(mode="after")
    def different_products(self):
        if self.from_product_id == self.to_product_id and self.requires_cleaning:
            raise ValueError(
                "from_product_id and to_product_id must differ when cleaning is required"
            )
        return self

    @model_validator(mode="after")
    def best_not_worse_than_standard(self):
        if (
            self.best_achieved_minutes is not None
            and self.best_achieved_minutes > self.standard_minutes
        ):
            raise ValueError("best_achieved_minutes cannot exceed standard_minutes")
        return self


class ChangeoverMatrixEntryCreate(ChangeoverMatrixEntry):
    pass


class ChangeoverMatrixEntryUpdate(BaseModel):
    workstation_id: Optional[str] = None
    from_product_id: Optional[str] = None
    to_product_id: Optional[str] = None
    standard_minutes: Optional[int] = Field(None, ge=1)
    best_achieved_minutes: Optional[int] = Field(None, ge=1)
    requires_cleaning: Optional[bool] = None
    requires_tooling: Optional[bool] = None
    min_operators: Optional[int] = Field(None, ge=1)
    notes: Optional[str] = Field(None, max_length=400)


class ChangeoverMatrixEntryResponse(ChangeoverMatrixEntry):
    class Config:
        from_attributes = True


class PaginatedChangeoverMatrixEntryResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[ChangeoverMatrixEntryResponse]
