from datetime import datetime, timezone
from typing import Literal, Optional
from pydantic import BaseModel, Field, model_validator

MachineFamilyLiteral = Literal[
    "Primary Manufacturing",
    "Secondary Manufacturing",
    "Packaging",
    "Quality Control",
    "Utilities & Support",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class Family(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "Family":
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self


class FamilyCreate(Family):
    pass


class FamilyUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    updated_at: datetime = Field(default_factory=utc_now)


class FamilyResponse(Family):
    class Config:
        from_attributes = True


class PaginatedFamilyResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[FamilyResponse]
