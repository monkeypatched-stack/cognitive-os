from datetime import datetime, timezone
from typing import Optional, Literal
from pydantic import BaseModel, Field, model_validator

WorkstationMode = Literal["Robotic", "Manual", "Hybrid"]
WorkstationStatus = Literal["Active", "Alert", "Idle", "Offline"]

# ─────────────────────────────────────────────
# UTILS (INLINE SAFE UTC HANDLING)
# ─────────────────────────────────────────────


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class Workstation(BaseModel):
    id: str
    name: str
    line_id: Optional[str] = None
    stage_id: Optional[str] = None
    mode: WorkstationMode
    status: WorkstationStatus
    description: Optional[str] = None
    operator: Optional[str] = Field(
        None, description="Required for Manual and Hybrid modes"
    )
    cycle_time_actual: float = Field(
        ..., ge=0, description="Actual cycle time in seconds"
    )
    cycle_time_target: float = Field(
        ..., gt=0, description="Target cycle time in seconds"
    )
    is_bottleneck: bool = Field(
        ..., description="True when cycle_time_actual exceeds cycle_time_target"
    )
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "Workstation":
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self

    @model_validator(mode="after")
    def check_constraints(self) -> "Workstation":
        # Operator rules
        if self.mode == "Robotic" and self.operator is not None:
            raise ValueError("operator must be None when mode is 'Robotic'")
        if self.mode in ("Manual", "Hybrid") and not self.operator:
            raise ValueError(f"operator is required when mode is '{self.mode}'")

        # Idle stations must have zero actual cycle time
        if self.status == "Idle" and self.cycle_time_actual > 0:
            raise ValueError("cycle_time_actual must be 0 when status is 'Idle'")

        # Bottleneck must reflect actual vs target
        expected_bottleneck = self.cycle_time_actual > self.cycle_time_target
        if self.is_bottleneck != expected_bottleneck:
            raise ValueError(
                f"is_bottleneck must be {expected_bottleneck} "
                f"(cycle_time_actual={self.cycle_time_actual} vs "
                f"cycle_time_target={self.cycle_time_target})"
            )
        return self


class WorkstationCreate(Workstation):
    pass


class WorkstationUpdate(BaseModel):
    name: str
    line_id: Optional[str] = None
    stage_id: Optional[str] = None
    mode: WorkstationMode
    status: WorkstationStatus
    description: Optional[str] = None
    operator: Optional[str] = Field(
        None, description="Required for Manual and Hybrid modes"
    )
    cycle_time_actual: float = Field(
        ..., ge=0, description="Actual cycle time in seconds"
    )
    cycle_time_target: float = Field(
        ..., gt=0, description="Target cycle time in seconds"
    )
    is_bottleneck: bool = Field(
        ..., description="True when cycle_time_actual exceeds cycle_time_target"
    )
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def check_constraints(self) -> "WorkstationUpdate":
        # Operator rules — only validate when both fields are present in the patch
        if self.mode == "Robotic" and self.operator is not None:
            raise ValueError("operator must be None when mode is 'Robotic'")
        if (
            self.mode in ("Manual", "Hybrid")
            and self.operator is not None
            and not self.operator
        ):
            raise ValueError(f"operator cannot be empty when mode is '{self.mode}'")

        # Bottleneck consistency — only validate when all three fields are provided
        if (
            self.is_bottleneck is not None
            and self.cycle_time_actual is not None
            and self.cycle_time_target is not None
        ):
            expected = self.cycle_time_actual > self.cycle_time_target
            if self.is_bottleneck != expected:
                raise ValueError(
                    f"is_bottleneck must be {expected} "
                    f"(cycle_time_actual={self.cycle_time_actual} vs "
                    f"cycle_time_target={self.cycle_time_target})"
                )
        return self


class WorkstationResponse(Workstation):
    class Config:
        from_attributes = True


class PaginatedWorkstationResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[WorkstationResponse]
