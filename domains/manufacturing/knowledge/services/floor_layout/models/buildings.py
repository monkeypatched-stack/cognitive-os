from datetime import datetime, timezone
from typing import Optional, List
from pydantic import BaseModel, Field, model_validator

BuildingType = str  # e.g. "Manufacturing", "Warehouse", "Office", "Lab"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class Building(BaseModel):
    building_id: str
    name: str = Field(..., min_length=1)
    plant_id: str = Field(..., min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "Building":
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self


class BuildingCreate(Building):
    pass


class BuildingUpdate(BaseModel):
    building_id: str
    name: str = Field(..., min_length=1)
    plant_id: str = Field(..., min_length=1)
    updated_at: datetime = Field(default_factory=utc_now)


class BuildingResponse(Building):
    class Config:
        from_attributes = True


class PaginatedBuildingResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: List[BuildingResponse]
