from datetime import datetime, timezone
from typing import Optional, List
from pydantic import BaseModel, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class Floor(BaseModel):
    floor_id: str = Field(..., min_length=1)
    building_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)  # e.g. "Ground Floor", "Level 1"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "Floor":
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self


class FloorCreate(Floor):
    pass


class FloorUpdate(BaseModel):
    name: str = Field(..., min_length=1)  # e.g. "Ground Floor", "Level 1"
    updated_at: datetime = Field(default_factory=utc_now)


class FloorResponse(Floor):
    class Config:
        from_attributes = True


class PaginatedFloorResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: List[FloorResponse]
