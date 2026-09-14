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


class EquipmentSubClass(BaseModel):
    id: str = None
    family_id: Optional[str] = None
    class_id: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "EquipmentSubClass":
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self


class EquipmentSubClassCreate(EquipmentSubClass):
    pass


class EquipmentSubClassUpdate(BaseModel):
    family_id: Optional[str] = None
    class_id: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    updated_at: datetime = Field(default_factory=utc_now)


class EquipmentSubClassResponse(EquipmentSubClass):
    class Config:
        from_attributes = True


class PaginatedEquipmentSubClassResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[EquipmentSubClassResponse]
