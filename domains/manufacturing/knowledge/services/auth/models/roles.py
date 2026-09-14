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


class Role(BaseModel):
    role_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    description: Optional[str] = None
    permissions: List[str] = Field(default_factory=list)  # direct permission_ids
    parent_role_ids: List[str] = Field(
        default_factory=list
    )  # inherited roles (hierarchy)
    is_active: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "Role":
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self


class RoleCreate(Role):
    pass


class RoleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    permissions: Optional[List[str]] = None
    parent_role_ids: Optional[List[str]] = None
    is_active: Optional[bool] = None
    updated_at: datetime = Field(default_factory=utc_now)


class RoleResponse(Role):
    class Config:
        from_attributes = True


class PaginatedRoleResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: List[RoleResponse]
