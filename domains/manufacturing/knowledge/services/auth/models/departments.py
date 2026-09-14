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


class Department(BaseModel):
    department_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    description: Optional[str] = None
    head_user_id: Optional[str] = None  # references UserEntry.user_id
    is_active: bool = True

    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "Department":
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self


class DepartmentCreate(Department):
    pass


class DepartmentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    head_user_id: Optional[str] = None
    is_active: Optional[bool] = None
    updated_at: datetime = Field(default_factory=utc_now)


class DepartmentResponse(Department):
    class Config:
        from_attributes = True


class PaginatedDepartmentResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: List[DepartmentResponse]
