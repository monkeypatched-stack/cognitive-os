from typing import Optional

from pydantic import BaseModel, Field


class SupplierCapabilitiesModel(BaseModel):
    supplier_id: str = Field(..., description="Unique identifier for the supplier.")
    core_competencies: list[str] = Field(
        ..., description="Key areas of expertise or specialization."
    )
    production_capacity: int = Field(
        ..., description="Maximum production output per month."
    )
    capacity_unit: str = Field(
        ...,
        description="Unit of measurement for production capacity, for example units or tons.",
    )
    lead_time: int = Field(
        ..., description="Average time in days required for order fulfillment."
    )
    technology_capability: list[str] = Field(
        ...,
        description="Technologies or equipment used, for example CNC, Robotics, or 3D Printing.",
    )
    certifications: list[str] = Field(
        ...,
        description="List of certifications that validate the supplier's capabilities.",
    )
    quality_control_measures: str = Field(
        ...,
        description="Description of the supplier's quality assurance and control processes.",
    )
    rad_capabilities: bool = Field(
        ...,
        description="Indicates if the supplier has in-house research and development facilities.",
    )
    customization_capability: bool = Field(
        ...,
        description="Indicates if the supplier can produce custom designs or products.",
    )
    geographical_reach: list[str] = Field(
        ..., description="List of regions or countries the supplier can serve."
    )
    material_expertise: list[str] = Field(
        ...,
        description="Types of materials the supplier specializes in, for example Steel or Aluminum.",
    )
    sustainability_practices: Optional[str] = Field(
        None,
        description="Description of any eco-friendly or sustainable practices followed by the supplier.",
    )
    subcontracting_capability: bool = Field(
        ...,
        description="Indicates if the supplier can manage subcontracted production.",
    )
    maintenance_services: bool = Field(
        ...,
        description="Indicates if the supplier provides maintenance or after-sales services.",
    )
    packaging_capabilities: str = Field(
        ...,
        description="Types of packaging options offered, for example Bulk or Retail-Ready.",
    )
    export_compliance: bool = Field(
        ...,
        description="Indicates if the supplier complies with the required export regulations.",
    )
    testing_facilities: bool = Field(
        ...,
        description="Indicates if the supplier has in-house testing and validation facilities.",
    )
    automation_level: str = Field(
        ...,
        description="Level of automation in the supplier's production process, for example Manual, Semi-Automated, or Fully Automated.",
    )
    employee_count: int = Field(
        ..., description="Total number of employees at the supplier's facility."
    )
    partnership_initiatives: Optional[str] = Field(
        None,
        description="Details of significant partnerships or collaborations with other firms.",
    )
    innovation_awards: Optional[list[str]] = Field(
        None, description="Recognition received for innovation or excellence."
    )
    remarks: Optional[str] = Field(
        None,
        description="Additional notes or observations regarding the supplier's capabilities.",
    )


class SupplierCapabilitiesCreate(SupplierCapabilitiesModel):
    pass


class SupplierCapabilitiesUpdate(BaseModel):
    core_competencies: Optional[list[str]] = None
    production_capacity: Optional[int] = None
    capacity_unit: Optional[str] = None
    lead_time: Optional[int] = None
    technology_capability: Optional[list[str]] = None
    certifications: Optional[list[str]] = None
    quality_control_measures: Optional[str] = None
    rad_capabilities: Optional[bool] = None
    customization_capability: Optional[bool] = None
    geographical_reach: Optional[list[str]] = None
    material_expertise: Optional[list[str]] = None
    sustainability_practices: Optional[str] = None
    subcontracting_capability: Optional[bool] = None
    maintenance_services: Optional[bool] = None
    packaging_capabilities: Optional[str] = None
    export_compliance: Optional[bool] = None
    testing_facilities: Optional[bool] = None
    automation_level: Optional[str] = None
    employee_count: Optional[int] = None
    partnership_initiatives: Optional[str] = None
    innovation_awards: Optional[list[str]] = None
    remarks: Optional[str] = None


class SupplierCapabilitiesResponse(SupplierCapabilitiesModel):
    class Config:
        from_attributes = True


class PaginatedSupplierCapabilitiesResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[SupplierCapabilitiesResponse]
