from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class EquipmentClass(BaseModel):
    id: str
    family_id: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "EquipmentClass":
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self


class EquipmentClassCreate(EquipmentClass):
    pass


class EquipmentClassUpdate(BaseModel):
    family_id: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    updated_at: datetime = Field(default_factory=utc_now)


class EquipmentClassResponse(EquipmentClass):
    class Config:
        from_attributes = True


class PaginatedEquipmentClassResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[EquipmentClassResponse]
