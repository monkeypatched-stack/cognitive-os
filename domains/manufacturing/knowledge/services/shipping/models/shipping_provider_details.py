from typing import Optional

from pydantic import BaseModel, Field


class ShippingProviderDetails(BaseModel):
    provider_id: str = Field(
        ..., description="Unique identifier for the shipping provider"
    )
    provider_name: str = Field(
        ..., description="Name of the shipping provider or carrier"
    )
    contact_number: Optional[str] = Field(
        None, description="Contact phone number for the shipping provider"
    )
    email_address: Optional[str] = Field(
        None, description="Email address of the shipping provider"
    )
    address: Optional[str] = Field(
        None, description="Physical address of the shipping provider"
    )
    service_areas: Optional[str] = Field(
        None, description="Geographic regions or areas serviced by the provider"
    )
    available_services: Optional[str] = Field(
        None,
        description="Details of shipping services offered, for example Air Freight or Express Delivery",
    )
    rating: Optional[float] = Field(
        None, description="Customer rating of the shipping provider out of 5"
    )
    website_url: Optional[str] = Field(
        None, description="Website URL of the shipping provider"
    )


class ShippingProviderDetailsCreate(ShippingProviderDetails):
    pass


class ShippingProviderDetailsUpdate(BaseModel):
    provider_name: Optional[str] = None
    contact_number: Optional[str] = None
    email_address: Optional[str] = None
    address: Optional[str] = None
    service_areas: Optional[str] = None
    available_services: Optional[str] = None
    rating: Optional[float] = None
    website_url: Optional[str] = None


class ShippingProviderDetailsResponse(ShippingProviderDetails):
    class Config:
        from_attributes = True


class PaginatedShippingProviderDetailsResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[ShippingProviderDetailsResponse]
