from datetime import datetime, timezone
from typing import Optional, Literal, List
from pydantic import BaseModel, Field, model_validator

BayType = Literal[
    "Assembly",
    "Packaging",
    "Inspection",
    "Storage",
    "Welding",
    "Painting",
    "Machining",
    "Other",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class Bay(BaseModel):
    bay_id: str = Field(..., min_length=1)
    room_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    machine_id: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "Bay":
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self


class BayCreate(Bay):
    pass


class BayUpdate(BaseModel):
    room_id: Optional[str] = None
    floor_id: Optional[str] = None
    machine_id: Optional[str] = None
    name: Optional[str] = None
    updated_at: datetime = Field(default_factory=utc_now)


class BayResponse(Bay):
    class Config:
        from_attributes = True


class PaginatedBayResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: List[BayResponse]
