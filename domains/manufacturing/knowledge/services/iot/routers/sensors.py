from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.common.auth import require_permission
from services.iot.models.sensors import (
    SensorCreate,
    SensorUpdate,
    SensorResponse,
    PaginatedSensorResponse,
)
from services.iot.helpers import sensors as crud
from services.iot.helpers import sensor_readings

router = APIRouter()


# ── List ──────────────────────────────────────────────────────────────────────


@router.get("/", response_model=PaginatedSensorResponse)
async def list_sensors(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-sensors")),
):
    sensors, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedSensorResponse(
        total=total, page=page, page_size=page_size, results=sensors
    )


# ── Get by machine ────────────────────────────────────────────────────────────


@router.get("/by-machine/{machine_id}", response_model=list[SensorResponse])
async def list_sensors_by_machine(
    machine_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-sensors")),
):
    return await crud.get_by_machine(db, machine_id)


@router.get("/by-equipment/{equipment_id}", response_model=list[SensorResponse])
async def list_sensors_by_equipment(
    equipment_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-sensors")),
):
    return await crud.get_by_equipment_id(db, equipment_id)


# ── Get by edge server ────────────────────────────────────────────────────────


@router.get("/by-edge-server/{edge_server_id}", response_model=list[SensorResponse])
async def list_sensors_by_edge_server(
    edge_server_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-sensors")),
):
    return await crud.get_by_edge_server(db, edge_server_id)


# ── Get by facility ───────────────────────────────────────────────────────────


@router.get("/by-facility/{facility}", response_model=list[SensorResponse])
async def list_sensors_by_facility(
    facility: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-sensors")),
):
    return await crud.get_by_facility(db, facility)


# ── Get one ───────────────────────────────────────────────────────────────────


@router.get("/{sensor_id}", response_model=SensorResponse)
async def get_sensor(
    sensor_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-sensors")),
):
    record = await crud.get_by_id(db, sensor_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Sensor '{sensor_id}' not found"
        )
    return record


@router.get("/{sensor_id}/readings")
async def list_sensor_readings(
    sensor_id: str,
    limit: int = Query(100, ge=1, le=1000),
    _: dict = Depends(require_permission("perm-view-sensors")),
):
    return await sensor_readings.get_sensor_readings(sensor_id, limit=limit)


@router.get("/{sensor_id}/readings/latest")
async def get_latest_sensor_reading(
    sensor_id: str,
    _: dict = Depends(require_permission("perm-view-sensors")),
):
    reading = await sensor_readings.get_latest_sensor_reading(sensor_id)
    if reading is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"No readings found for sensor '{sensor_id}'",
        )
    return reading


# ── Create ────────────────────────────────────────────────────────────────────


@router.post("/", response_model=SensorResponse, status_code=status.HTTP_201_CREATED)
async def create_sensor(
    data: SensorCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-sensors")),
):
    if await crud.get_by_id(db, data.sensor_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"Sensor '{data.sensor_id}' already exists"
        )
    return await crud.create(db, data)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/{sensor_id}", response_model=SensorResponse)
async def update_sensor(
    sensor_id: str,
    data: SensorUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-sensors")),
):
    updated = await crud.update(db, sensor_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Sensor '{sensor_id}' not found"
        )
    return updated


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete("/{sensor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sensor(
    sensor_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-sensors")),
):
    if not await crud.delete(db, sensor_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Sensor '{sensor_id}' not found"
        )
