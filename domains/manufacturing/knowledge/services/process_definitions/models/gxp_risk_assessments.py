from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class GxpRiskAssessment(BaseModel):
    risk_assessment_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    description: Optional[str] = None
    plant_id: Optional[str] = None
    line_id: Optional[str] = None
    scope: Optional[str] = None
    initiated_by: str = Field(..., min_length=1)
    risk_category: Optional[str] = None
    severity: Literal["Low", "Medium", "High", "Critical"] = "Medium"
    likelihood: Literal["Rare", "Unlikely", "Possible", "Likely", "Almost Certain"] = (
        "Possible"
    )
    risk_score: Optional[int] = None
    mitigation_actions: list[dict] = Field(default_factory=list)
    residual_risk: Optional[str] = None
    reviewed_by: Optional[str] = None
    approved_by: Optional[str] = None
    status: Literal["Draft", "Under Review", "Approved", "Closed"] = "Draft"
    linked_change_control_id: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
