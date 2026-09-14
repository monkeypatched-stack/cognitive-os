from fastapi import APIRouter, HTTPException, Depends, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.common.db import get_database
from services.pm.models.calibration_point import (
    CalibrationPointCreate,
    CalibrationPointUpdate,
    CalibrationPointResponse,
)
from services.pm.helpers import calibration_point as crud
from fastapi import Depends
from services.common.auth import get_current_user

# Every route here was UNAUTHENTICATED — anyone could read, create, modify and DELETE
# GxP calibration points (incl. POST/PATCH/DELETE). Sibling routers in this service already require auth; this was an oversight.
router = APIRouter(dependencies=[Depends(get_current_user)])


# ── List ──────────────────────────────────────────────────────────────────────


@router.get("/", response_model=list[CalibrationPointResponse])
async def list_calibration_points(
    calibration_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    points = await crud.get_all(db, calibration_id)
    if points is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Calibration '{calibration_id}' not found",
        )
    return points


# ── Get one ───────────────────────────────────────────────────────────────────


@router.get("/{point_id}", response_model=CalibrationPointResponse)
async def get_calibration_point(
    calibration_id: str,
    point_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    record = await crud.get_by_id(db, calibration_id, point_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Point '{point_id}' not found in calibration '{calibration_id}'",
        )
    return record


# ── Create ────────────────────────────────────────────────────────────────────


@router.post(
    "/", response_model=CalibrationPointResponse, status_code=status.HTTP_201_CREATED
)
async def create_calibration_point(
    calibration_id: str,
    data: CalibrationPointCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    created = await crud.create(db, calibration_id, data)
    if not created:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Calibration '{calibration_id}' not found",
        )
    return created


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/{point_id}", response_model=CalibrationPointResponse)
async def update_calibration_point(
    calibration_id: str,
    point_id: str,
    data: CalibrationPointUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    updated = await crud.update(db, calibration_id, point_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Point '{point_id}' not found in calibration '{calibration_id}'",
        )
    return updated


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete("/{point_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_calibration_point(
    calibration_id: str,
    point_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    if not await crud.delete(db, calibration_id, point_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Point '{point_id}' not found in calibration '{calibration_id}'",
        )
