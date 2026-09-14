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


# ---------------------------------------------------------------------------
# Permission
# ---------------------------------------------------------------------------
class Permission(BaseModel):
    permission_id: str = Field(..., min_length=1)
    role_id: Optional[str] = None
    name: str = Field(..., min_length=1)
    resource: str = Field(..., min_length=1)
    action: str = Field(..., min_length=1)
    description: Optional[str] = None
    is_active: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "Permission":
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self


class PermissionCreate(Permission):
    pass


class PermissionUpdate(BaseModel):
    role_id: Optional[str] = None
    name: Optional[str] = None
    resource: Optional[str] = None
    action: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    updated_at: datetime = Field(default_factory=utc_now)


class PermissionResponse(Permission):
    class Config:
        from_attributes = True


class PaginatedPermissionResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: List[PermissionResponse]
