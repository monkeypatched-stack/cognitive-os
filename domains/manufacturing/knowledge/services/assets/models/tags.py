from datetime import date, datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

RFIDTagStatus = Literal["Active", "Inactive", "Lost", "Damaged"]
RFIDAssignedType = Literal["User", "Equipment", "Asset"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class RFIDTagBase(BaseModel):
    tag_code: str = Field(..., min_length=1)
    name: Optional[str] = Field(None, min_length=1)
    description: Optional[str] = None
    issue_date: Optional[date] = None
    expiry_date: Optional[date] = None
    status: RFIDTagStatus = "Active"
    tag_type: Optional[str] = None
    barcode: Optional[str] = Field(None, max_length=80, description="Barcode reference")
    manufacturer: Optional[str] = None
    serial_number: Optional[str] = None

    @model_validator(mode="after")
    def validate_dates(self) -> "RFIDTagBase":
        if self.issue_date and self.expiry_date and self.expiry_date < self.issue_date:
            raise ValueError("expiry_date cannot be before issue_date")
        return self


class RFIDTagCreate(RFIDTagBase):
    pass


class RFIDTagRecord(RFIDTagBase):
    rfid_tag_id: str = Field(..., min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def normalize_datetimes(self) -> "RFIDTagRecord":
        self.created_at = ensure_utc(self.created_at)
        self.updated_at = ensure_utc(self.updated_at)
        return self


class RFIDTagUpdate(BaseModel):
    tag_code: Optional[str] = Field(default=None, min_length=1)
    name: Optional[str] = Field(default=None, min_length=1)
    description: Optional[str] = None
    issue_date: Optional[date] = None
    expiry_date: Optional[date] = None
    status: Optional[RFIDTagStatus] = None
    tag_type: Optional[str] = None
    barcode: Optional[str] = Field(None, max_length=80)
    manufacturer: Optional[str] = None
    serial_number: Optional[str] = None
    updated_at: Optional[datetime] = None


class PaginatedRFIDTagResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[RFIDTagRecord]
