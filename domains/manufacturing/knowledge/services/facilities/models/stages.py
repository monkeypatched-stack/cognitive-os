from datetime import datetime, timezone
from typing import Optional, Literal
from pydantic import BaseModel, Field, field_validator, model_validator

StageType = Literal["Assembly", "Continuous", "Batch"]
StageStatus = Literal["Operational", "Maintenance", "Down", "Decommissioned"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _normalize_stage_status(value):
    if value == "Active":
        return "Operational"
    return value


class IndustrialStage(BaseModel):
    id: str
    name: str
    plant_id: Optional[str] = None
    type: StageType
    takt_time: float = Field(..., gt=0, description="Cycle time in seconds")
    status: StageStatus
    line_id: Optional[str] = None
    efficiency: int = Field(..., ge=0, le=100)
    description: Optional[str] = None
    last_pm: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "IndustrialStage":
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value):
        return _normalize_stage_status(value)

    @model_validator(mode="after")
    def check_efficiency_vs_status(self) -> "IndustrialStage":
        if self.status == "Down" and self.efficiency > 0:
            raise ValueError("efficiency must be 0 when status is 'Down'")
        if self.status == "Maintenance" and self.efficiency >= 100:
            raise ValueError("efficiency must be < 100 when status is 'Maintenance'")
        return self


class IndustrialStageCreate(IndustrialStage):
    pass


class IndustrialStageUpdate(BaseModel):
    name: str
    plant_name: Optional[str] = None
    type: StageType
    takt_time: float = Field(..., gt=0, description="Cycle time in seconds")
    status: StageStatus
    line_id: Optional[str] = None
    efficiency: int = Field(..., ge=0, le=100)
    description: Optional[str] = None
    last_pm: Optional[str] = None
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value):
        return _normalize_stage_status(value)

    @model_validator(mode="after")
    def check_efficiency_vs_status(self) -> "IndustrialStageUpdate":
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


class IndustrialStageResponse(IndustrialStage):
    class Config:
        from_attributes = True


class PaginatedStageResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[IndustrialStageResponse]
