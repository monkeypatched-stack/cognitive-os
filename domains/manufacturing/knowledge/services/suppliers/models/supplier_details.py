from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class SupplierDetails(BaseModel):
    supplier_id: str = Field(..., description="Unique identifier for the supplier.")
    supplier_name: str = Field(..., description="Official name of the supplier.")
    supplier_type: str = Field(
        ...,
        description="Type of supplier, for example Manufacturer, Distributor, Wholesaler, or Service Provider.",
    )
    address: str = Field(..., description="Registered address of the supplier.")
    city: str = Field(..., description="City where the supplier is located.")
    state_region: str = Field(
        ..., description="State or region where the supplier operates."
    )
    country: str = Field(..., description="Country of the supplier.")
    postal_code: str = Field(..., description="Postal code for the supplier's address.")
    contact_name: str = Field(..., description="Primary contact person's name.")
    contact_email: EmailStr = Field(..., description="Email address for communication.")
    contact_phone: str = Field(..., description="Phone number for the primary contact.")
    website: Optional[str] = Field(
        None, description="Official website of the supplier, if available."
    )
    tax_id: str = Field(
        ..., description="Taxpayer Identification Number of the supplier."
    )
    payment_terms: str = Field(
        ..., description="Agreed payment terms, for example Net 30 or Net 45."
    )
    currency: str = Field(
        ..., description="Default currency for transactions with the supplier."
    )
    lead_time_days: int = Field(
        ..., description="Average time required to fulfill an order in days."
    )
    annual_spend: float = Field(
        ..., description="Total annual expenditure on this supplier."
    )
    approval_status: str = Field(
        ...,
        description="Supplier's approval status, for example Approved, Pending, or Blacklisted.",
    )
    risk_rating: int = Field(
        ...,
        description="Risk score assigned to the supplier based on financial and operational factors.",
    )
    certifications: list[str] = Field(
        ...,
        description="Certifications held by the supplier, for example ISO 9001, CE, or RoHS.",
    )
    industry: str = Field(..., description="Industry in which the supplier operates.")
    past_performance_score: float = Field(
        ..., description="Rating based on historical performance on a 1-10 scale."
    )
    preferred_supplier: bool = Field(
        ..., description="Indicates if this supplier is a preferred vendor."
    )
    last_order_date: Optional[datetime] = Field(
        None, description="The most recent date an order was placed with the supplier."
    )
    remarks: Optional[str] = Field(
        None, description="Additional metadata or context not covered by other fields."
    )


class SupplierDetailsCreate(SupplierDetails):
    pass


class SupplierDetailsUpdate(BaseModel):
    supplier_name: Optional[str] = None
    supplier_type: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state_region: Optional[str] = None
    country: Optional[str] = None
    postal_code: Optional[str] = None
    contact_name: Optional[str] = None
    contact_email: Optional[EmailStr] = None
    contact_phone: Optional[str] = None
    website: Optional[str] = None
    tax_id: Optional[str] = None
    payment_terms: Optional[str] = None
    currency: Optional[str] = None
    lead_time_days: Optional[int] = None
    annual_spend: Optional[float] = None
    approval_status: Optional[str] = None
    risk_rating: Optional[int] = None
    certifications: Optional[list[str]] = None
    industry: Optional[str] = None
    past_performance_score: Optional[float] = None
    preferred_supplier: Optional[bool] = None
    last_order_date: Optional[datetime] = None
    remarks: Optional[str] = None


class SupplierDetailsResponse(SupplierDetails):
    class Config:
        from_attributes = True


class PaginatedSupplierDetailsResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[SupplierDetailsResponse]
