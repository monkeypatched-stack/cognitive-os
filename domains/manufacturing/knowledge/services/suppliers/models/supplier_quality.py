from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class SupplierQualityData(BaseModel):
    supplier_id: str = Field(..., description="Unique identifier for the supplier.")
    item_id: str = Field(..., description="Unique identifier for the inventory item.")
    location_id: str = Field(
        ..., description="Unique identifier for the supplier's specific location."
    )
    quality_rating: float = Field(
        ...,
        description="Overall quality score based on historical performance, for example 1 to 100.",
    )
    defect_rate: float = Field(
        ..., description="Percentage of defective items in delivered goods."
    )
    on_time_delivery_rate: float = Field(
        ...,
        description="Percentage of orders delivered on or before the agreed-upon date.",
    )
    return_rate: float = Field(
        ..., description="Percentage of delivered goods returned due to quality issues."
    )
    non_conformance_reports: int = Field(
        ..., description="Number of non-conformance reports filed against the supplier."
    )
    iso_certifications: list[str] = Field(
        ...,
        description="List of ISO or equivalent certifications related to quality management.",
    )
    quality_audit_compliance_rate: float = Field(
        ..., description="Percentage of quality audits passed successfully."
    )
    inspection_pass_rate: float = Field(
        ...,
        description="Percentage of items that pass quality inspection upon delivery.",
    )
    warranty_claims_rate: float = Field(
        ...,
        description="Percentage of delivered goods that resulted in warranty claims.",
    )
    supplier_quality_manager: str = Field(
        ...,
        description="Name of the person responsible for quality assurance at the supplier.",
    )
    corrective_action_turnaround_time: int = Field(
        ...,
        description="Average number of days to resolve quality issues raised by the buyer.",
    )
    customer_complaint_rate: float = Field(
        ...,
        description="Percentage of complaints related to supplier's products or services.",
    )
    continuous_improvement_programs: bool = Field(
        ...,
        description="Indicates if the supplier has active quality improvement initiatives.",
    )
    last_quality_audit_date: datetime = Field(
        ..., description="Date of the most recent quality audit conducted."
    )
    next_quality_audit_date: datetime = Field(
        ..., description="Scheduled date for the next quality audit."
    )
    inspection_process_details: Optional[str] = Field(
        None,
        description="Description of the supplier's internal quality inspection processes.",
    )
    first_pass_yield: float = Field(
        ...,
        description="Percentage of goods that meet quality standards without rework.",
    )
    material_traceability: bool = Field(
        ...,
        description="Indicates whether the supplier provides traceability for materials used.",
    )
    adherence_to_specifications: float = Field(
        ...,
        description="Percentage of delivered goods meeting exact design or technical specifications.",
    )
    remarks: Optional[str] = Field(
        None,
        description="Additional notes or observations about the supplier's quality performance.",
    )


class SupplierQualityCreate(SupplierQualityData):
    pass


class SupplierQualityUpdate(BaseModel):
    supplier_id: Optional[str] = None
    item_id: Optional[str] = None
    location_id: Optional[str] = None
    quality_rating: Optional[float] = None
    defect_rate: Optional[float] = None
    on_time_delivery_rate: Optional[float] = None
    return_rate: Optional[float] = None
    non_conformance_reports: Optional[int] = None
    iso_certifications: Optional[list[str]] = None
    quality_audit_compliance_rate: Optional[float] = None
    inspection_pass_rate: Optional[float] = None
    warranty_claims_rate: Optional[float] = None
    supplier_quality_manager: Optional[str] = None
    corrective_action_turnaround_time: Optional[int] = None
    customer_complaint_rate: Optional[float] = None
    continuous_improvement_programs: Optional[bool] = None
    last_quality_audit_date: Optional[datetime] = None
    next_quality_audit_date: Optional[datetime] = None
    inspection_process_details: Optional[str] = None
    first_pass_yield: Optional[float] = None
    material_traceability: Optional[bool] = None
    adherence_to_specifications: Optional[float] = None
    remarks: Optional[str] = None


class SupplierQualityResponse(SupplierQualityData):
    supplier_quality_id: str

    class Config:
        from_attributes = True


class PaginatedSupplierQualityResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[SupplierQualityResponse]
