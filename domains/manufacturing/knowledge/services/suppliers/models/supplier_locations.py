from typing import Optional

from pydantic import BaseModel, Field


class SupplierLocation(BaseModel):
    supplier_id: str = Field(..., description="Unique identifier for the supplier.")
    item_id: str = Field(..., description="Unique identifier for the inventory item.")
    location_id: str = Field(
        ..., description="Unique identifier for the supplier's specific location."
    )
    location_name: str = Field(
        ...,
        description="Name or description of the location, for example Head Office or Manufacturing Plant.",
    )
    address_line_1: str = Field(
        ..., description="Primary address line for the location."
    )
    address_line_2: Optional[str] = Field(
        None, description="Secondary address line, if applicable."
    )
    city: str = Field(..., description="City where the supplier's location is based.")
    state_province: str = Field(..., description="State or province of the location.")
    country: str = Field(..., description="Country of the supplier's location.")
    postal_code: str = Field(..., description="Postal or ZIP code for the location.")
    contact_number: str = Field(..., description="Phone number for the location.")
    email_address: str = Field(
        ..., description="Email address for location-specific communication."
    )
    facility_type: str = Field(
        ...,
        description="Type of facility at the location, for example Manufacturing, Warehouse, or Office.",
    )
    operational_hours: str = Field(
        ..., description="Standard hours of operation for the facility."
    )
    primary_function: str = Field(
        ...,
        description="Main activity performed at the location, for example Assembly or Distribution.",
    )
    geographical_coordinates: str = Field(
        ..., description="Latitude and longitude for mapping purposes."
    )
    annual_production_capacity: Optional[int] = Field(
        None, description="Maximum production output per year at this location."
    )
    employee_count: int = Field(
        ..., description="Total number of employees at the location."
    )
    certifications: Optional[list[str]] = Field(
        None,
        description="Certifications specific to the location, for example ISO 9001 or OSHA Compliance.",
    )
    storage_capacity: Optional[float] = Field(
        None,
        description="Maximum storage capacity at the location, for example in cubic meters.",
    )
    key_contact_person: str = Field(
        ..., description="Name of the primary contact person at the location."
    )
    key_contact_role: str = Field(
        ...,
        description="Role of the key contact person, for example Plant Manager or Logistics Coordinator.",
    )
    remarks: Optional[str] = Field(
        None, description="Additional notes or observations about the location."
    )


class SupplierLocationCreate(SupplierLocation):
    pass


class SupplierLocationUpdate(BaseModel):
    supplier_id: Optional[str] = None
    item_id: Optional[str] = None
    location_name: Optional[str] = None
    address_line_1: Optional[str] = None
    address_line_2: Optional[str] = None
    city: Optional[str] = None
    state_province: Optional[str] = None
    country: Optional[str] = None
    postal_code: Optional[str] = None
    contact_number: Optional[str] = None
    email_address: Optional[str] = None
    facility_type: Optional[str] = None
    operational_hours: Optional[str] = None
    primary_function: Optional[str] = None
    geographical_coordinates: Optional[str] = None
    annual_production_capacity: Optional[int] = None
    employee_count: Optional[int] = None
    certifications: Optional[list[str]] = None
    storage_capacity: Optional[float] = None
    key_contact_person: Optional[str] = None
    key_contact_role: Optional[str] = None
    remarks: Optional[str] = None


class SupplierLocationResponse(SupplierLocation):
    class Config:
        from_attributes = True


class PaginatedSupplierLocationResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[SupplierLocationResponse]
