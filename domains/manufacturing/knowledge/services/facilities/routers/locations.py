"""API routers for plant location management."""

from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.common.auth import require_permission
from services.facilities.models.locations import (
    LocationCreate,
    LocationUpdate,
    LocationResponse,
    PaginatedLocationResponse,
    LocationHierarchyResponse,
    LocationWithChildrenResponse,
)
from services.facilities.helpers import locations as crud

router = APIRouter()


def get_db() -> AsyncIOMotorDatabase:
    """Get database dependency."""
    return get_database()


@router.get("/", response_model=PaginatedLocationResponse)
async def list_locations(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    location_type: Optional[str] = Query(None, description="Filter by location type"),
    parent_id: Optional[str] = Query(None, description="Filter by parent ID"),
    level: Optional[int] = Query(None, description="Filter by hierarchy level"),
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-view-locations")),
) -> PaginatedLocationResponse:
    """List all locations with filtering and pagination."""
    locations, total = await crud.get_all_locations(
        db, page, page_size, location_type, parent_id, level
    )
    return PaginatedLocationResponse(
        total=total, page=page, page_size=page_size, results=locations
    )


@router.post("/", response_model=LocationResponse, status_code=status.HTTP_201_CREATED)
async def create_location(
    location_data: LocationCreate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-create-locations")),
) -> LocationResponse:
    """Create a new location."""
    try:
        return await crud.create_location(db, location_data)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to create location: {str(e)}",
        )


@router.get("/{location_id}", response_model=LocationResponse)
async def get_location(
    location_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-view-locations")),
) -> LocationResponse:
    """Get a specific location by ID."""
    location = await crud.get_location_by_id(db, location_id)
    if not location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Location not found"
        )
    return location


@router.put("/{location_id}", response_model=LocationResponse)
async def update_location(
    location_id: str,
    update_data: LocationUpdate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-update-locations")),
) -> LocationResponse:
    """Update a location."""
    location = await crud.update_location(db, location_id, update_data)
    if not location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Location not found or no changes made",
        )
    return location


@router.delete("/{location_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_location(
    location_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-delete-locations")),
) -> None:
    """Delete a location."""
    success = await crud.delete_location(db, location_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Location not found"
        )


@router.get("/{location_id}/hierarchy", response_model=LocationHierarchyResponse)
async def get_location_hierarchy(
    location_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-view-locations")),
) -> LocationHierarchyResponse:
    """Get the complete hierarchy for a location."""
    hierarchy = await crud.get_location_hierarchy(db, location_id)
    if not hierarchy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Location not found"
        )

    # Organize hierarchy into response structure
    response = LocationHierarchyResponse()
    for location in hierarchy:
        if location["type"] == "plant":
            response.plant = location
        elif location["type"] == "building":
            response.building = location
        elif location["type"] == "floor":
            response.floor = location
        elif location["type"] == "room":
            response.room = location
        elif location["type"] == "bay":
            response.bay = location

    return response


@router.get("/{location_id}/children", response_model=LocationWithChildrenResponse)
async def get_location_children(
    location_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-view-locations")),
) -> LocationWithChildrenResponse:
    """Get a location with its children."""
    result = await crud.get_location_with_children(db, location_id)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Location not found"
        )
    return result


@router.get("/by-location-id/{location_id}", response_model=LocationResponse)
async def get_location_by_location_id(
    location_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-view-locations")),
) -> LocationResponse:
    """Get a location by its location_id (not MongoDB ID)."""
    location = await crud.get_location_by_location_id(db, location_id)
    if not location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Location not found"
        )
    return location


@router.get("/types/{location_type}", response_model=PaginatedLocationResponse)
async def get_locations_by_type(
    location_type: str,
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-view-locations")),
) -> PaginatedLocationResponse:
    """Get all locations of a specific type."""
    locations = await crud.get_locations_by_type(db, location_type)

    # Apply pagination
    start = (page - 1) * page_size
    end = start + page_size
    paginated_results = locations[start:end]

    return PaginatedLocationResponse(
        total=len(locations), page=page, page_size=page_size, results=paginated_results
    )


@router.get("/search/", response_model=list[LocationResponse])
async def search_locations(
    search_term: str = Query(..., min_length=1, description="Search term"),
    limit: int = Query(10, ge=1, le=50, description="Maximum results"),
    db: AsyncIOMotorDatabase = Depends(get_db),
    _: dict = Depends(require_permission("perm-view-locations")),
) -> list[LocationResponse]:
    """Search locations by name, location_id, or path."""
    return await crud.search_locations(db, search_term, limit)
