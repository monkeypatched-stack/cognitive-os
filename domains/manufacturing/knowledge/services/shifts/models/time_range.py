# ---------------------------------------------------------------------------
# Shared / Value Objects
# ---------------------------------------------------------------------------

from datetime import date, datetime, time
from typing import Optional
from pydantic import BaseModel, Field, computed_field, model_validator


class TimeRange(BaseModel):
    """An inclusive start-to-end time window within a single day."""

    start: time
    end: time

    @model_validator(mode="after")
    def end_after_start(self):
        if self.end <= self.start:
            raise ValueError(
                f"end time ({self.end}) must be after start time ({self.start})"
            )
        return self

    @computed_field  # type: ignore[misc]
    @property
    def duration_minutes(self) -> int:
        today = date.today()
        dt_start = datetime.combine(today, self.start)
        dt_end = datetime.combine(today, self.end)
        return int((dt_end - dt_start).total_seconds() // 60)
