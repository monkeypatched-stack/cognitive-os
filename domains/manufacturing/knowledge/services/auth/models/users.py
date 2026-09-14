from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class UserEntry(BaseModel):
    user_id: str = Field(..., min_length=1)
    employee_id: str = None
    name: str = Field(..., min_length=1)
    department: Optional[str] = None
    team: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    is_active: bool = False
    role: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "UserEntry":
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self


class UserEntryCreate(UserEntry):
    password: SecretStr = Field(..., min_length=8)


class UserEntryUpdate(BaseModel):
    employee_id: Optional[str] = None
    name: Optional[str] = None
    department: Optional[str] = None
    team: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    is_active: Optional[bool] = None
    role: Optional[str] = None
    password: Optional[SecretStr] = Field(None, min_length=8)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("password", mode="before")
    @classmethod
    def blank_password_means_unchanged(cls, value):
        if isinstance(value, str) and not value:
            return None
        return value


class UserEntryResponse(UserEntry):
    """Password is intentionally absent — never serialized back to the client."""

    class Config:
        from_attributes = True


class PaginatedUserEntryResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[UserEntryResponse]


class UserResponse(UserEntry):
    class Config:
        from_attributes = True
