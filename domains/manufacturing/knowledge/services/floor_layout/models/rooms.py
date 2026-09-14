from datetime import datetime, timezone
from typing import Optional, Literal, List
from pydantic import BaseModel, Field, model_validator

RoomType = Literal[
    "Production",
    "Storage",
    "Laboratory",
    "Office",
    "Utility",
    "Cleanroom",
    "Server Room",
    "Maintenance",
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


class Room(BaseModel):
    room_id: str = Field(..., min_length=1)
    floor_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "Room":
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self


class RoomCreate(Room):
    pass


class RoomUpdate(BaseModel):
    name: str = Field(..., min_length=1)
    updated_at: datetime = Field(default_factory=utc_now)


class RoomResponse(Room):
    class Config:
        from_attributes = True


class PaginatedRoomResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: List[RoomResponse]
