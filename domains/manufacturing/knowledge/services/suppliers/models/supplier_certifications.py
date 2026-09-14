from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


def datetime_now() -> datetime:
    return datetime.now(timezone.utc)


class CertificationModel(BaseModel):
    supplier_id: str = Field(..., description="Unique identifier for the supplier.")
    certification_name: str = Field(
        ...,
        description="Name of the certification or standard achieved by the supplier.",
    )
    certification_type: str = Field(
        ...,
        description="Type of certification, for example Quality, Environmental, Safety, or Industry-Specific.",
    )
    issuing_authority: str = Field(
        ..., description="Organization or authority that issued the certification."
    )
    certification_number: Optional[str] = Field(
        None,
        description="Unique identification number for the certification, if applicable.",
    )
    issue_date: datetime = Field(
        ..., description="Date on which the certification was issued."
    )
    expiry_date: Optional[datetime] = Field(default_factory=datetime_now)
    renewal_required: bool = Field(
        ..., description="Indicates if the certification requires periodic renewal."
    )
    renewal_frequency: Optional[str] = Field(
        None,
        description="How often the certification must be renewed, for example Annual or Every 3 Years.",
    )
    scope_of_certification: str = Field(
        ...,
        description="Description of the areas or processes covered by the certification.",
    )
    audit_required: bool = Field(
        ...,
        description="Indicates if regular audits are required to maintain the certification.",
    )
    last_audit_date: Optional[datetime] = Field(default_factory=datetime_now)
    next_audit_date: Optional[datetime] = Field(default_factory=datetime_now)
    certification_status: str = Field(
        ...,
        description="Current status of the certification, for example Active, Pending Renewal, or Expired.",
    )
    certificate_document: Optional[str] = Field(
        None,
        description="Reference to the digital copy of the certificate or a link to its location.",
    )
    remarks: Optional[str] = Field(
        None, description="Additional notes or observations about the certification."
    )


class CertificationCreate(CertificationModel):
    pass


class CertificationUpdate(BaseModel):
    supplier_id: Optional[str] = None
    certification_name: Optional[str] = None
    certification_type: Optional[str] = None
    issuing_authority: Optional[str] = None
    certification_number: Optional[str] = None
    issue_date: Optional[datetime] = None
    expiry_date: Optional[datetime] = None
    renewal_required: Optional[bool] = None
    renewal_frequency: Optional[str] = None
    scope_of_certification: Optional[str] = None
    audit_required: Optional[bool] = None
    last_audit_date: Optional[datetime] = None
    next_audit_date: Optional[datetime] = None
    certification_status: Optional[str] = None
    certificate_document: Optional[str] = None
    remarks: Optional[str] = None


class CertificationResponse(CertificationModel):
    certification_id: str

    class Config:
        from_attributes = True


class PaginatedCertificationResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[CertificationResponse]
