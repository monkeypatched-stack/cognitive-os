from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import require_permission
from services.common.db import get_database
from services.orders.helpers import invoices as crud
from services.orders.models.invoices import (
    InvoiceCreate,
    InvoiceResponse,
    InvoiceStatus,
    InvoiceUpdate,
    PaginatedInvoiceResponse,
)

router = APIRouter()


@router.get("/", response_model=PaginatedInvoiceResponse)
async def list_invoices(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-order")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedInvoiceResponse(
        total=total, page=page, page_size=page_size, results=records
    )


@router.get("/by-number/{invoice_no}", response_model=InvoiceResponse)
async def get_invoice_by_number(
    invoice_no: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-order")),
):
    record = await crud.get_by_invoice_number(db, invoice_no)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Invoice '{invoice_no}' not found"
        )
    return record


@router.get("/by-customer/{customer_id}", response_model=list[InvoiceResponse])
async def list_invoices_by_customer(
    customer_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-order")),
):
    return await crud.get_by_customer_id(db, customer_id)


@router.get("/by-status/{invoice_status}", response_model=list[InvoiceResponse])
async def list_invoices_by_status(
    invoice_status: InvoiceStatus,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-order")),
):
    return await crud.get_by_status(db, invoice_status.value)


@router.get("/{invoice_id}", response_model=InvoiceResponse)
async def get_invoice(
    invoice_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-order")),
):
    record = await crud.get_by_id(db, invoice_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Invoice '{invoice_id}' not found"
        )
    return record


@router.post("/", response_model=InvoiceResponse, status_code=status.HTTP_201_CREATED)
async def create_invoice(
    data: InvoiceCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-order")),
):
    invoice_no = data.invoice_details.invoice_no if data.invoice_details else None
    if invoice_no and await crud.get_by_invoice_number(db, invoice_no):
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"Invoice '{invoice_no}' already exists"
        )
    return await crud.create(db, data)


@router.patch("/{invoice_id}", response_model=InvoiceResponse)
async def update_invoice(
    invoice_id: str,
    data: InvoiceUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-order")),
):
    if data.invoice_details and data.invoice_details.invoice_no:
        existing = await crud.get_by_invoice_number(db, data.invoice_details.invoice_no)
        if existing and existing.get("id") != invoice_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"Invoice '{data.invoice_details.invoice_no}' already exists",
            )
    updated = await crud.update(db, invoice_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Invoice '{invoice_id}' not found"
        )
    return updated


@router.delete("/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_invoice(
    invoice_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-order")),
):
    if not await crud.delete(db, invoice_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Invoice '{invoice_id}' not found"
        )
