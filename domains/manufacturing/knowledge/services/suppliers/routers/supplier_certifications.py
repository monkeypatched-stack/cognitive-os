from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.suppliers.helpers import supplier_certifications as crud
from services.common.auth import require_permission
from services.suppliers.models.supplier_certifications import (
    CertificationCreate,
    CertificationResponse,
    CertificationUpdate,
    PaginatedCertificationResponse,
)

router = APIRouter()


@router.get("/", response_model=PaginatedCertificationResponse)
async def list_supplier_certifications(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedCertificationResponse(
        total=total, page=page, page_size=page_size, results=records
    )


@router.get("/by-supplier/{supplier_id}", response_model=list[CertificationResponse])
async def list_supplier_certifications_by_supplier(
    supplier_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_supplier(db, supplier_id)


@router.get("/{certification_id}", response_model=CertificationResponse)
async def get_supplier_certification(
    certification_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_by_id(db, certification_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Supplier certification '{certification_id}' not found",
        )
    return record


@router.post(
    "/", response_model=CertificationResponse, status_code=status.HTTP_201_CREATED
)
async def create_supplier_certification(
    data: CertificationCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-products")),
):
    return await crud.create(db, data)


@router.patch("/{certification_id}", response_model=CertificationResponse)
async def update_supplier_certification(
    certification_id: str,
    data: CertificationUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-products")),
):
    updated = await crud.update(db, certification_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Supplier certification '{certification_id}' not found",
        )
    return updated


@router.delete("/{certification_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_supplier_certification(
    certification_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-products")),
):
    if not await crud.delete(db, certification_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Supplier certification '{certification_id}' not found",
        )
