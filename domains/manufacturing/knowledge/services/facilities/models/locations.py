"""Pydantic models for plant location hierarchy."""

from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field


class LocationBase(BaseModel):
    """Base location model."""

    name: str = Field(..., description="Location name")
    location_id: str = Field(..., description="Location ID/code")
    type: str = Field(
        ..., description="Location type (plant, building, floor, room, bay)"
    )
    level: int = Field(..., ge=1, le=5, description="Hierarchy level (1-5)")
    path: str = Field(..., description="Full hierarchical path")
    description: Optional[str] = Field(None, description="Detailed description")
    status: str = Field("Operational", description="Operational status")


class LocationCreate(LocationBase):
    """Model for creating locations."""

    parent_id: Optional[str] = Field(None, description="Parent location ID")


class LocationUpdate(BaseModel):
    """Model for updating locations."""

    name: Optional[str] = Field(None, description="Location name")
    description: Optional[str] = Field(None, description="Detailed description")
    status: Optional[str] = Field(None, description="Operational status")


class LocationResponse(LocationBase):
    """Model for location responses."""

    id: str = Field(..., description="MongoDB ID")
    parent_id: Optional[str] = Field(None, description="Parent location ID")
    plant_id: Optional[str] = Field(None, description="Plant ID")
    building_id: Optional[str] = Field(None, description="Building ID")
    floor_id: Optional[str] = Field(None, description="Floor ID")
    room_id: Optional[str] = Field(None, description="Room ID")
    bay_id: Optional[str] = Field(None, description="Bay ID")
    cleanroom_class: Optional[str] = Field(
        None, description="ISO cleanroom classification"
    )
    workstation_id: Optional[str] = Field(None, description="Associated workstation ID")
    room_number: Optional[str] = Field(None, description="Room number")
    bay_number: Optional[str] = Field(None, description="Bay number")
    floor_number: Optional[int] = Field(None, description="Floor number")
    has_ahu: Optional[bool] = Field(None, description="Has AHU system")
    has_exhaust: Optional[bool] = Field(None, description="Has exhaust system")
    temperature_controlled: Optional[bool] = Field(
        None, description="Temperature controlled"
    )
    humidity_controlled: Optional[bool] = Field(None, description="Humidity controlled")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")


class PaginatedLocationResponse(BaseModel):
    """Paginated response model for locations."""

    total: int = Field(..., description="Total number of locations")
    page: int = Field(..., description="Current page")
    page_size: int = Field(..., description="Items per page")
    results: List[LocationResponse] = Field(..., description="Location results")


class LocationHierarchyResponse(BaseModel):
    """Model for location hierarchy responses."""

    plant: Optional[LocationResponse] = Field(None, description="Plant location")
    building: Optional[LocationResponse] = Field(None, description="Building location")
    floor: Optional[LocationResponse] = Field(None, description="Floor location")
    room: Optional[LocationResponse] = Field(None, description="Room location")
    bay: Optional[LocationResponse] = Field(None, description="Bay location")


class LocationWithChildrenResponse(BaseModel):
    """Model for location with children."""

    location: LocationResponse = Field(..., description="Location details")
    children: List[LocationResponse] = Field(
        default_factory=list, description="Child locations"
    )
    children_count: int = Field(0, description="Number of child locations")
