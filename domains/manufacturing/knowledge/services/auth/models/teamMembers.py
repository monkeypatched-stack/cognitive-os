from datetime import datetime, timezone
from typing import Optional, List
from pydantic import BaseModel, Field, model_validator


# ── Helpers (reuse same) ──────────────────────────────────────────────────────
def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ── Team Member ───────────────────────────────────────────────────────────────
class TeamMember(BaseModel):
    user_id: str = Field(..., min_length=1)
    employee_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)

    department: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None

    is_active: bool = True
    joined_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "TeamMember":
        self.joined_at = ensure_utc(self.joined_at)
        return self


# ── Create ────────────────────────────────────────────────────────────────────
class TeamMemberCreate(TeamMember):
    pass


# ── Update ────────────────────────────────────────────────────────────────────
class TeamMemberUpdate(BaseModel):
    department: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None


# ── Response ──────────────────────────────────────────────────────────────────
class TeamMemberResponse(TeamMember):
    class Config:
        from_attributes = True


# ── Mapping: team_id → members[] ──────────────────────────────────────────────
class TeamMembersMapResponse(BaseModel):
    data: dict[str, List[TeamMemberResponse]]
