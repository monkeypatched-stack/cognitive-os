from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import require_permission
from services.common.db import get_database
from services.shipping.helpers import package as crud
from services.shipping.models.package import (
    PackageCreate,
    PackageResponse,
    PackageUpdate,
    PaginatedPackageResponse,
)

router = APIRouter()


@router.get("/", response_model=PaginatedPackageResponse)
async def list_packages(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedPackageResponse(
        total=total, page=page, page_size=page_size, results=records
    )


@router.get("/by-code/{package_code}", response_model=PackageResponse)
async def get_package_by_code(
    package_code: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_by_code(db, package_code)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Package '{package_code}' not found"
        )
    return record


@router.get("/by-pallet/{pallet_id}", response_model=list[PackageResponse])
async def list_packages_by_pallet(
    pallet_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_pallet(db, pallet_id)


@router.get(
    "/by-delivery-note/{delivery_note_id}", response_model=list[PackageResponse]
)
async def list_packages_by_delivery_note(
    delivery_note_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_delivery_note(db, delivery_note_id)


@router.get("/by-tracking/{tracking_number}", response_model=PackageResponse)
async def get_package_by_tracking_number(
    tracking_number: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_by_tracking_number(db, tracking_number)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Package with tracking number '{tracking_number}' not found",
        )
    return record


@router.get("/by-status/{status_value}", response_model=list[PackageResponse])
async def list_packages_by_status(
    status_value: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_status(db, status_value)


@router.get("/{package_id}", response_model=PackageResponse)
async def get_package(
    package_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_by_id(db, package_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Package '{package_id}' not found"
        )
    return record


@router.post("/", response_model=PackageResponse, status_code=status.HTTP_201_CREATED)
async def create_package(
    data: PackageCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-products")),
):
    package_id = str(data.id)
    if await crud.get_by_id(db, package_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"Package '{package_id}' already exists"
        )
    if await crud.get_by_code(db, data.package_code):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Package '{data.package_code}' already exists",
        )
    if data.tracking_number and await crud.get_by_tracking_number(
        db, data.tracking_number
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Package with tracking number '{data.tracking_number}' already exists",
        )
    return await crud.create(db, data)


@router.patch("/{package_id}", response_model=PackageResponse)
async def update_package(
    package_id: str,
    data: PackageUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-products")),
):
    if data.package_code:
        existing = await crud.get_by_code(db, data.package_code)
        if existing and existing.get("id") != package_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"Package '{data.package_code}' already exists",
            )
    if data.tracking_number:
        existing = await crud.get_by_tracking_number(db, data.tracking_number)
        if existing and existing.get("id") != package_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"Package with tracking number '{data.tracking_number}' already exists",
            )
    updated = await crud.update(db, package_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Package '{package_id}' not found"
        )
    return updated


@router.delete("/{package_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_package(
    package_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-products")),
):
    if not await crud.delete(db, package_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Package '{package_id}' not found"
        )
