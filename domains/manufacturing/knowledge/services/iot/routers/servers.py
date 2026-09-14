from fastapi import APIRouter, HTTPException, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.common.db import get_database
from services.common.auth import require_permission
from services.iot.models.servers import (
    EdgeServerCreate,
    EdgeServerUpdate,
    EdgeServerResponse,
    PaginatedEdgeServerResponse,
)
from services.iot.models.sensors import SensorResponse
from services.iot.helpers import servers as crud
from services.iot.helpers import sensors as sensor_crud

router = APIRouter()


# ── List ──────────────────────────────────────────────────────────────────────


@router.get("/", response_model=PaginatedEdgeServerResponse)
async def list_edge_servers(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    servers, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedEdgeServerResponse(
        total=total, page=page, page_size=page_size, results=servers
    )


# ── Get by facility ───────────────────────────────────────────────────────────


@router.get("/by-facility/{facility}", response_model=list[EdgeServerResponse])
async def list_edge_servers_by_facility(
    facility: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    return await crud.get_by_facility(db, facility)


# ── Get by parent server ──────────────────────────────────────────────────────


@router.get("/by-parent/{parent_server_id}", response_model=list[EdgeServerResponse])
async def list_edge_servers_by_parent(
    parent_server_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    return await crud.get_by_parent(db, parent_server_id)


# ── Get one ───────────────────────────────────────────────────────────────────


@router.get("/{server_id}", response_model=EdgeServerResponse)
async def get_edge_server(
    server_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    record = await crud.get_by_id(db, server_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Edge server '{server_id}' not found",
        )
    return record


# ── Create ────────────────────────────────────────────────────────────────────


@router.post(
    "/", response_model=EdgeServerResponse, status_code=status.HTTP_201_CREATED
)
async def create_edge_server(
    data: EdgeServerCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    if await crud.get_by_id(db, data.server_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Edge server '{data.server_id}' already exists",
        )
    return await crud.create(db, data)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/{server_id}", response_model=EdgeServerResponse)
async def update_edge_server(
    server_id: str,
    data: EdgeServerUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    updated = await crud.update(db, server_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Edge server '{server_id}' not found",
        )
    return updated


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_edge_server(
    server_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    if not await crud.delete(db, server_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Edge server '{server_id}' not found",
        )


# ── List sensors by server ────────────────────────────────────────────────────


@router.get("/{server_id}/sensors", response_model=list[SensorResponse])
async def list_sensors_by_edge_server(
    server_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-sensors")),
):
    if not await crud.get_by_id(db, server_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Edge server '{server_id}' not found",
        )
    return await sensor_crud.get_by_edge_server(db, server_id)


# ── Server health ─────────────────────────────────────────────────────────────


@router.get("/{server_id}/health", response_model=EdgeServerResponse)
async def get_edge_server_health(
    server_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    record = await crud.get_by_id(db, server_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Edge server '{server_id}' not found",
        )
    return record
