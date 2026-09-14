from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import require_permission
from services.common.db import get_database
from services.shipping.helpers import customs_declaration as crud
from services.shipping.models.customs_declaration import (
    CustomsDeclarationCreate,
    CustomsDeclarationResponse,
    CustomsDeclarationUpdate,
    PaginatedCustomsDeclarationResponse,
)

router = APIRouter()


@router.get("/", response_model=PaginatedCustomsDeclarationResponse)
async def list_customs_declarations(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedCustomsDeclarationResponse(
        total=total, page=page, page_size=page_size, results=records
    )


@router.get(
    "/by-reference/{declaration_reference}", response_model=CustomsDeclarationResponse
)
async def get_customs_declaration_by_reference(
    declaration_reference: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_by_reference(db, declaration_reference)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Customs declaration '{declaration_reference}' not found",
        )
    return record


@router.get(
    "/by-type/{declaration_type}", response_model=list[CustomsDeclarationResponse]
)
async def list_customs_declarations_by_type(
    declaration_type: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_type(db, declaration_type)


@router.get(
    "/by-shipment/{shipment_id}", response_model=list[CustomsDeclarationResponse]
)
async def list_customs_declarations_by_shipment(
    shipment_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_shipment(db, shipment_id)


@router.get(
    "/by-delivery-note/{delivery_note_id}",
    response_model=list[CustomsDeclarationResponse],
)
async def list_customs_declarations_by_delivery_note(
    delivery_note_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_delivery_note(db, delivery_note_id)


@router.get(
    "/by-clearance/{customs_cleared}", response_model=list[CustomsDeclarationResponse]
)
async def list_customs_declarations_by_clearance(
    customs_cleared: bool,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_by_clearance(db, customs_cleared)


@router.get("/{declaration_id}", response_model=CustomsDeclarationResponse)
async def get_customs_declaration(
    declaration_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_by_id(db, declaration_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Customs declaration '{declaration_id}' not found",
        )
    return record


@router.post(
    "/", response_model=CustomsDeclarationResponse, status_code=status.HTTP_201_CREATED
)
async def create_customs_declaration(
    data: CustomsDeclarationCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-products")),
):
    declaration_id = str(data.id)
    if await crud.get_by_id(db, declaration_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Customs declaration '{declaration_id}' already exists",
        )
    if await crud.get_by_reference(db, data.declaration_reference):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Customs declaration '{data.declaration_reference}' already exists",
        )
    return await crud.create(db, data)


@router.patch("/{declaration_id}", response_model=CustomsDeclarationResponse)
async def update_customs_declaration(
    declaration_id: str,
    data: CustomsDeclarationUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-products")),
):
    if data.declaration_reference:
        existing = await crud.get_by_reference(db, data.declaration_reference)
        if existing and existing.get("id") != declaration_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"Customs declaration '{data.declaration_reference}' already exists",
            )
    updated = await crud.update(db, declaration_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Customs declaration '{declaration_id}' not found",
        )
    return updated


@router.delete("/{declaration_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_customs_declaration(
    declaration_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-products")),
):
    if not await crud.delete(db, declaration_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Customs declaration '{declaration_id}' not found",
        )
