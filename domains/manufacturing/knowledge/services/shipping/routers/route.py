from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import require_permission
from services.common.db import get_database
from services.shipping.helpers import route as crud
from services.shipping.models.route import (
    PaginatedRouteResponse,
    RouteCreate,
    RouteResponse,
    RouteUpdate,
)

router = APIRouter()


@router.get("/", response_model=PaginatedRouteResponse)
async def list_routes(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedRouteResponse(
        total=total, page=page, page_size=page_size, results=records
    )


@router.get("/by-reference/{route_reference}", response_model=RouteResponse)
async def get_route_by_reference(
    route_reference: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_by_reference(db, route_reference)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Route '{route_reference}' not found"
        )
    return record


@router.get("/by-vehicle/{vehicle_id}", response_model=list[RouteResponse])
async def list_routes_by_vehicle(
    vehicle_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_vehicle(db, vehicle_id)


@router.get("/by-carrier/{carrier_id}", response_model=list[RouteResponse])
async def list_routes_by_carrier(
    carrier_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_carrier(db, carrier_id)


@router.get("/by-planned-date/{planned_date}", response_model=list[RouteResponse])
async def list_routes_by_planned_date(
    planned_date: date,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_planned_date(db, planned_date)


@router.get("/by-completion/{completed}", response_model=list[RouteResponse])
async def list_routes_by_completion(
    completed: bool,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_completion(db, completed)


@router.get("/{route_id}", response_model=RouteResponse)
async def get_route(
    route_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_by_id(db, route_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Route '{route_id}' not found"
        )
    return record


@router.post("/", response_model=RouteResponse, status_code=status.HTTP_201_CREATED)
async def create_route(
    data: RouteCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-products")),
):
    route_id = str(data.id)
    if await crud.get_by_id(db, route_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"Route '{route_id}' already exists"
        )
    if await crud.get_by_reference(db, data.route_reference):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Route '{data.route_reference}' already exists",
        )
    return await crud.create(db, data)


@router.patch("/{route_id}", response_model=RouteResponse)
async def update_route(
    route_id: str,
    data: RouteUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-products")),
):
    if data.route_reference:
        existing = await crud.get_by_reference(db, data.route_reference)
        if existing and existing.get("id") != route_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"Route '{data.route_reference}' already exists",
            )
    updated = await crud.update(db, route_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Route '{route_id}' not found"
        )
    return updated


@router.delete("/{route_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_route(
    route_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-products")),
):
    if not await crud.delete(db, route_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Route '{route_id}' not found"
        )
