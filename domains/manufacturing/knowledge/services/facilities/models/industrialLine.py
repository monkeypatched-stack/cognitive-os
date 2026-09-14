from datetime import datetime, timezone
from typing import Optional, Literal
from pydantic import BaseModel, Field, model_validator

LineType = Literal["Assembly", "Continuous", "Batch"]
LineStatus = Literal["Operational", "Maintenance", "Down", "Stalled"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class IndustrialLine(BaseModel):
    id: str
    name: str
    plant_id: str
    type: LineType
    takt_time: float = Field(..., gt=0, description="Cycle time in seconds")
    status: LineStatus
    efficiency: int = Field(..., ge=0, le=100)
    description: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "IndustrialLine":
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self

    @model_validator(mode="after")
    def check_efficiency_vs_status(self) -> "IndustrialLine":
        if self.status == "Down" and self.efficiency > 0:
            raise ValueError("efficiency must be 0 when status is 'Down'")
        if self.status == "Maintenance" and self.efficiency >= 100:
            raise ValueError("efficiency must be < 100 when status is 'Maintenance'")
        return self


class IndustrialLineCreate(IndustrialLine):
    pass


class IndustrialLineUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[LineType] = None
    takt_time: Optional[float] = Field(None, gt=0)
    status: Optional[LineStatus] = None
    efficiency: Optional[int] = Field(None, ge=0, le=100)
    description: Optional[str] = None
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def check_efficiency_vs_status(self) -> "IndustrialLineUpdate":
        if (
            self.status == "Down"
            and self.efficiency is not None
            and self.efficiency > 0
        ):
            raise ValueError("efficiency must be 0 when status is 'Down'")
        if (
            self.status == "Maintenance"
            and self.efficiency is not None
            and self.efficiency >= 100
        ):
            raise ValueError("efficiency must be < 100 when status is 'Maintenance'")
        return self


class IndustrialLineResponse(IndustrialLine):
    class Config:
        from_attributes = True


class PaginatedLineResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[IndustrialLineResponse]
