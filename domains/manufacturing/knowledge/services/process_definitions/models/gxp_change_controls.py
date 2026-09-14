from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


ChangeControlStatus = Literal[
    "Draft", "Under Review", "Approved", "Implemented", "Rejected", "Closed"
]
ChangeControlPriority = Literal["Low", "Medium", "High", "Critical"]
ChangeControlImpact = Literal["None", "Minor", "Major", "Critical"]


class GxpChangeControl(BaseModel):
    change_control_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    plant_id: Optional[str] = None
    line_id: Optional[str] = None
    initiated_by: str = Field(..., min_length=1)
    initiated_at: datetime = Field(default_factory=utc_now)
    status: ChangeControlStatus = "Draft"
    priority: ChangeControlPriority = "Medium"
    impact_assessment: Optional[str] = None
    impact_level: ChangeControlImpact = "None"
    affected_entities: list[dict] = Field(default_factory=list)
    affected_batches: list[str] = Field(default_factory=list)
    affected_sops: list[str] = Field(default_factory=list)
    approval_steps: list[dict] = Field(default_factory=list)
    rationale: Optional[str] = None
    implementation_plan: Optional[str] = None
    implemented_by: Optional[str] = None
    implemented_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
