from decimal import Decimal
from enum import Enum
from typing import Annotated, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, computed_field, model_validator


class PalletType(str, Enum):
    EURO = "euro"
    STANDARD = "standard"
    BLOCK = "block"
    STRINGER = "stringer"
    PLASTIC = "plastic"
    CUSTOM = "custom"


class PalletCondition(str, Enum):
    GOOD = "good"
    DAMAGED = "damaged"
    REPAIRED = "repaired"
    QUARANTINED = "quarantined"
    DISPOSED = "disposed"


class PalletDimensions(BaseModel):
    """Physical pallet dimensions (millimetres)."""

    length_mm: Annotated[int, Field(gt=0)]
    width_mm: Annotated[int, Field(gt=0)]
    height_mm: Annotated[int, Field(gt=0)]


class PalletContent(BaseModel):
    """Line item loaded onto a pallet."""

    line_number: int = Field(..., ge=1)
    product_code: str = Field(..., min_length=1, max_length=50)
    product_description: str = Field(..., max_length=255)
    batch_number: Optional[str] = Field(None, max_length=50)
    quantity: Annotated[Decimal, Field(gt=0)]
    unit_of_measure: str = Field(..., max_length=20, examples=["EA", "KG", "L", "M"])
    gross_weight_kg: Optional[Annotated[float, Field(ge=0)]] = None
    net_weight_kg: Optional[Annotated[float, Field(ge=0)]] = None
    hazardous: bool = False
    un_number: Optional[str] = Field(
        None, max_length=4, description="UN hazmat number if hazardous"
    )


class Pallet(BaseModel):
    """Individual pallet / loading unit."""

    id: UUID = Field(default_factory=uuid4)
    pallet_label: str = Field(
        ..., min_length=1, max_length=50, description="Barcode / SSCC label"
    )
    pallet_type: PalletType = PalletType.EURO
    condition: PalletCondition = PalletCondition.GOOD
    dimensions: Optional[PalletDimensions] = None
    tare_weight_kg: Optional[Annotated[float, Field(ge=0)]] = None
    max_load_kg: Optional[Annotated[float, Field(gt=0)]] = None
    contents: list[PalletContent] = Field(default_factory=list)
    is_stackable: bool = True
    stack_limit: Optional[int] = Field(
        None, ge=1, description="Max units that can be stacked on top"
    )
    stretch_wrapped: bool = False
    temperature_controlled: bool = False
    temperature_min_celsius: Optional[float] = None
    temperature_max_celsius: Optional[float] = None
    notes: Optional[str] = None

    model_config = {"str_strip_whitespace": True}

    @computed_field  # type: ignore[misc]
    @property
    def total_gross_weight_kg(self) -> float:
        tare = self.tare_weight_kg or 0.0
        payload = sum(c.gross_weight_kg or 0 for c in self.contents)
        return round(tare + payload, 3)

    @model_validator(mode="after")
    def validate_temperature_fields(self) -> "Pallet":
        if self.temperature_controlled:
            if (
                self.temperature_min_celsius is None
                or self.temperature_max_celsius is None
            ):
                raise ValueError(
                    "temperature_min_celsius and temperature_max_celsius are required "
                    "when temperature_controlled is True"
                )
            if self.temperature_min_celsius >= self.temperature_max_celsius:
                raise ValueError(
                    "temperature_min_celsius must be less than temperature_max_celsius"
                )
        return self


class PalletCreate(Pallet):
    pass


class PalletUpdate(BaseModel):
    pallet_label: Optional[str] = Field(None, min_length=1, max_length=50)
    pallet_type: Optional[PalletType] = None
    condition: Optional[PalletCondition] = None
    dimensions: Optional[PalletDimensions] = None
    tare_weight_kg: Optional[Annotated[float, Field(ge=0)]] = None
    max_load_kg: Optional[Annotated[float, Field(gt=0)]] = None
    contents: Optional[list[PalletContent]] = None
    is_stackable: Optional[bool] = None
    stack_limit: Optional[int] = Field(None, ge=1)
    stretch_wrapped: Optional[bool] = None
    temperature_controlled: Optional[bool] = None
    temperature_min_celsius: Optional[float] = None
    temperature_max_celsius: Optional[float] = None
    notes: Optional[str] = None

    model_config = {"str_strip_whitespace": True}

    @model_validator(mode="after")
    def validate_temperature_fields(self) -> "PalletUpdate":
        if self.temperature_controlled is True:
            if (
                self.temperature_min_celsius is None
                or self.temperature_max_celsius is None
            ):
                raise ValueError(
                    "temperature_min_celsius and temperature_max_celsius are required "
                    "when temperature_controlled is True"
                )
        if (
            self.temperature_min_celsius is not None
            and self.temperature_max_celsius is not None
        ):
            if self.temperature_min_celsius >= self.temperature_max_celsius:
                raise ValueError(
                    "temperature_min_celsius must be less than temperature_max_celsius"
                )
        return self


class PalletResponse(Pallet):
    class Config:
        from_attributes = True


class PaginatedPalletResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[PalletResponse]
