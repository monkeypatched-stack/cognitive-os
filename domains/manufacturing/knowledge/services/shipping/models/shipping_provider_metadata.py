from typing import Optional

from pydantic import BaseModel, Field


class ShippingProviderMetadata(BaseModel):
    provider_id: str = Field(
        ..., description="Unique identifier for the shipping provider"
    )
    base_shipping_rate: Optional[float] = Field(
        None,
        description="Base cost for shipping services in the provider's pricing model",
    )
    cost_per_kg: Optional[float] = Field(
        None, description="Cost per kilogram for shipping"
    )
    cost_per_km: Optional[float] = Field(
        None, description="Cost per kilometer for shipping"
    )
    surcharge_details: Optional[str] = Field(
        None,
        description="Details of any surcharges, for example fuel surcharge or peak season fees",
    )
    fleet_details: Optional[list[str]] = Field(
        None,
        description="Details about the provider's fleet, for example trucks, ships, or drones",
    )
    average_delivery_time: Optional[str] = Field(
        None,
        description="Average delivery time for shipments handled by the provider, for example 3-5 business days",
    )
    pricing_model: Optional[str] = Field(
        None,
        description="Description of the provider's pricing model, for example flat rate, per kilogram, or per distance",
    )
    insurance_offered: Optional[bool] = Field(
        None,
        description="Indicates whether the provider offers insurance for shipments",
    )
    category: str = Field(
        ...,
        description="The category of the logistics capability, for example Transportation or Warehousing.",
    )
    capabilities: str = Field(
        ..., description="Specific capability under the category."
    )
    description: str = Field(
        ..., description="A detailed description of the capability."
    )
    key_features: list[str] = Field(..., description="Key features of the capability.")
    certifications: Optional[list[str]] = Field(
        None, description="Relevant certifications for the capability."
    )
    remarks: Optional[str] = Field(
        None,
        description="Additional notes or comments related to the provider or capability.",
    )


class ShippingProviderMetadataCreate(ShippingProviderMetadata):
    pass


class ShippingProviderMetadataUpdate(BaseModel):
    base_shipping_rate: Optional[float] = None
    cost_per_kg: Optional[float] = None
    cost_per_km: Optional[float] = None
    surcharge_details: Optional[str] = None
    fleet_details: Optional[list[str]] = None
    average_delivery_time: Optional[str] = None
    pricing_model: Optional[str] = None
    insurance_offered: Optional[bool] = None
    category: Optional[str] = None
    capabilities: Optional[str] = None
    description: Optional[str] = None
    key_features: Optional[list[str]] = None
    certifications: Optional[list[str]] = None
    remarks: Optional[str] = None


class ShippingProviderMetadataResponse(ShippingProviderMetadata):
    class Config:
        from_attributes = True


class PaginatedShippingProviderMetadataResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: list[ShippingProviderMetadataResponse]
