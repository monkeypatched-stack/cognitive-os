from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from bson import ObjectId
from fastapi.encoders import jsonable_encoder
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    constr,
    field_validator,
    model_validator,
)


class DocumentStatus(str, Enum):
    DRAFT = "Draft"
    IN_REVIEW = "In Review"
    APPROVED = "Approved"
    REJECTED = "Rejected"


class DocumentType(str, Enum):
    REPORT = "Report"
    INVOICE = "Invoice"
    MEMO = "Memo"
    CONTRACT = "Contract"
    OTHER = "Other"


ShortText = constr(strip_whitespace=True, min_length=1, max_length=255)
VersionText = constr(strip_whitespace=True, min_length=1, max_length=50)
UrlText = constr(strip_whitespace=True, min_length=1, max_length=2083)
DescriptionText = constr(strip_whitespace=True, min_length=1, max_length=1024)
TagText = constr(strip_whitespace=True, min_length=1, max_length=50)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def serialize_document(doc: dict) -> dict:
    """Convert MongoDB documents to JSON-serializable API payloads."""
    doc = dict(doc)
    if "_id" in doc:
        doc["id"] = str(doc.pop("_id"))
    return jsonable_encoder(doc, custom_encoder={ObjectId: str})


class Comment(BaseModel):
    comment_id: str = Field(default_factory=lambda: f"comment-{uuid4().hex[:12]}")
    comment_text: ShortText
    commented_by: ShortText
    commented_at: datetime = Field(default_factory=utc_now)

    @field_validator("commented_at")
    @classmethod
    def normalize_commented_at(cls, value: datetime) -> datetime:
        return ensure_utc(value) or utc_now()


class DocumentMetadataBase(BaseModel):
    document_id: Optional[ShortText] = Field(default=None, title="Document ID")
    document_name: ShortText
    document_type: DocumentType
    document_version: VersionText
    document_tags: list[TagText] = Field(default_factory=list)
    document_url: UrlText
    document_status: DocumentStatus
    document_owner: ShortText
    document_preparer: ShortText
    document_reviewer: ShortText
    document_approver: ShortText
    document_due_date: datetime
    document_department: Optional[ShortText] = None
    document_project: Optional[ShortText] = None
    document_confidentiality: Optional[ShortText] = None
    document_retention_period: Optional[ShortText] = None
    document_description: Optional[DescriptionText] = None
    comments: Optional[list[Comment]] = None
    created_by: ShortText
    created_at: datetime = Field(default_factory=utc_now)
    last_modified_by: ShortText
    last_modified_at: datetime = Field(default_factory=utc_now)

    storage_backend: Optional[str] = Field(default=None, title="Storage Backend")
    file_name: Optional[str] = Field(default=None, title="Original File Name")
    content_type: Optional[str] = Field(default=None, title="File Content Type")
    file_size: Optional[int] = Field(default=None, ge=0, title="File Size in Bytes")
    s3_bucket: Optional[str] = Field(default=None, title="S3 Bucket")
    s3_key: Optional[str] = Field(default=None, title="S3 Object Key")
    s3_url: Optional[str] = Field(default=None, title="S3 Object URL")
    local_path: Optional[str] = Field(default=None, title="Local File Path")
    storage_error: Optional[str] = Field(default=None, title="Storage Error")

    model_config = ConfigDict(
        json_encoders={
            ObjectId: str,
            datetime: lambda value: value.isoformat(),
        }
    )

    @field_validator("created_at")
    @classmethod
    def created_not_in_future(cls, value: datetime) -> datetime:
        value_utc = ensure_utc(value) or utc_now()
        if value_utc > utc_now():
            raise ValueError("created_at cannot be in the future")
        return value_utc

    @field_validator("document_due_date")
    @classmethod
    def normalize_due_date(cls, value: datetime) -> datetime:
        return ensure_utc(value) or utc_now()

    @field_validator("document_tags")
    @classmethod
    def validate_unique_tags(cls, value: list[str]) -> list[str]:
        lower_tags = [tag.lower() for tag in value]
        if len(lower_tags) != len(set(lower_tags)):
            raise ValueError("document_tags must contain unique values")
        return value

    @model_validator(mode="after")
    def validate_people_and_dates(self):
        self.last_modified_at = ensure_utc(self.last_modified_at) or utc_now()

        if self.last_modified_at < self.created_at:
            raise ValueError("last_modified_at cannot be before created_at")

        if self.document_reviewer.lower() == self.document_preparer.lower():
            raise ValueError(
                "document_reviewer cannot be the same as document_preparer"
            )

        if self.document_approver.lower() == self.document_preparer.lower():
            raise ValueError(
                "document_approver cannot be the same as document_preparer"
            )

        if self.document_approver.lower() == self.document_reviewer.lower():
            raise ValueError(
                "document_approver cannot be the same as document_reviewer"
            )

        return self


class DocumentMetadataCreate(DocumentMetadataBase):
    """Schema for creating document metadata."""


class DocumentMetadataUpdate(BaseModel):
    document_name: Optional[ShortText] = None
    document_type: Optional[DocumentType] = None
    document_version: Optional[VersionText] = None
    document_tags: Optional[list[TagText]] = None
    document_url: Optional[UrlText] = None
    document_status: Optional[DocumentStatus] = None
    document_owner: Optional[ShortText] = None
    document_preparer: Optional[ShortText] = None
    document_reviewer: Optional[ShortText] = None
    document_approver: Optional[ShortText] = None
    document_due_date: Optional[datetime] = None
    document_department: Optional[ShortText] = None
    document_project: Optional[ShortText] = None
    document_confidentiality: Optional[ShortText] = None
    document_retention_period: Optional[ShortText] = None
    document_description: Optional[DescriptionText] = None
    comments: Optional[list[Comment]] = None
    created_by: Optional[ShortText] = None
    created_at: Optional[datetime] = None
    last_modified_by: Optional[ShortText] = None
    last_modified_at: Optional[datetime] = None
    storage_backend: Optional[str] = None
    file_name: Optional[str] = None
    content_type: Optional[str] = None
    file_size: Optional[int] = Field(default=None, ge=0)
    s3_bucket: Optional[str] = None
    s3_key: Optional[str] = None
    s3_url: Optional[str] = None
    local_path: Optional[str] = None
    storage_error: Optional[str] = None

    @field_validator("document_due_date", "created_at", "last_modified_at")
    @classmethod
    def normalize_optional_dates(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value)

    @field_validator("document_tags")
    @classmethod
    def validate_unique_tags(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        lower_tags = [tag.lower() for tag in value]
        if len(lower_tags) != len(set(lower_tags)):
            raise ValueError("document_tags must contain unique values")
        return value


class DocumentViewLinkResponse(BaseModel):
    document_id: str
    storage_backend: Optional[str] = None
    view_url: str
    expires_in: Optional[int] = None


class DocumentMetadataResponse(DocumentMetadataBase):
    document_id: str

    model_config = ConfigDict(from_attributes=True)


class PaginatedDocumentMetadataResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[DocumentMetadataResponse]
