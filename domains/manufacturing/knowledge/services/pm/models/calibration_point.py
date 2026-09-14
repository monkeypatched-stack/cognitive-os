# ─────────────────────────────────────────────
# CALIBRATION POINT
# ─────────────────────────────────────────────

from typing import Optional
from pydantic import BaseModel, model_validator


class CalibrationPoint(BaseModel):
    reference_value: float
    measured_value: float
    unit: Optional[str] = None
    deviation: Optional[float] = None  # auto-computed if not provided

    @model_validator(mode="after")
    def compute_deviation(self):
        if self.deviation is None:
            self.deviation = round(self.measured_value - self.reference_value, 6)
        return self


class CalibrationPointCreate(CalibrationPoint):
    pass


class CalibrationPointUpdate(BaseModel):
    reference_value: Optional[float] = None
    measured_value: Optional[float] = None
    unit: Optional[str] = None
    deviation: Optional[float] = None

    @model_validator(mode="after")
    def recompute_deviation(self):
        """Recompute deviation if both values are supplied in the update."""
        if self.reference_value is not None and self.measured_value is not None:
            self.deviation = round(self.measured_value - self.reference_value, 6)
        return self


class CalibrationPointResponse(CalibrationPoint):
    class Config:
        from_attributes = True
