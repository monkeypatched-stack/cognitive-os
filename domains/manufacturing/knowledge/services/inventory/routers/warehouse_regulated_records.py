from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

from services.common.auth import require_permission
from services.common.db import get_database
from services.inventory.helpers import warehouse_regulated_records as crud
from services.inventory.models.warehouse_regulated_records import (
    InventoryDispositionRecord,
    InventoryDispositionRecordCreate,
    InventoryDispositionRecordUpdate,
    MaterialInwardLog,
    MaterialInwardLogCreate,
    MaterialInwardLogUpdate,
    PaginatedInventoryDispositionRecordResponse,
    PaginatedMaterialInwardLogResponse,
    PaginatedSpecialStorageRequirementResponse,
    PaginatedWarehouseDispensingRecordResponse,
    PaginatedWarehouseStatusLabelResponse,
    SpecialStorageRequirement,
    SpecialStorageRequirementCreate,
    SpecialStorageRequirementUpdate,
    WarehouseDispensingRecord,
    WarehouseDispensingRecordCreate,
    WarehouseDispensingRecordUpdate,
    WarehouseStatusLabel,
    WarehouseStatusLabelCreate,
    WarehouseStatusLabelUpdate,
)

router = APIRouter()


def _query(**filters: str | None) -> dict[str, Any] | None:
    query = {key: value for key, value in filters.items() if value not in (None, "")}
    return query or None


async def _list_records(
    record_type: str,
    response_cls,
    page: int,
    page_size: int,
    db: AsyncIOMotorDatabase,
    query: dict[str, Any] | None = None,
):
    records, total = await crud.get_all(
        db, record_type, page=page, page_size=page_size, query=query
    )
    return response_cls(total=total, page=page, page_size=page_size, results=records)


async def _get_record(
    record_type: str, record_id: str, db: AsyncIOMotorDatabase, label: str
):
    record = await crud.get_by_id(db, record_type, record_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{label} '{record_id}' not found",
        )
    return record


async def _create_record(
    record_type: str,
    data: BaseModel,
    db: AsyncIOMotorDatabase,
    record_id: str,
    label: str,
):
    if await crud.get_by_id(db, record_type, record_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{label} '{record_id}' already exists",
        )
    return await crud.create(db, record_type, data)


async def _update_record(
    record_type: str,
    record_id: str,
    data: BaseModel,
    db: AsyncIOMotorDatabase,
    label: str,
):
    updated = await crud.update(db, record_type, record_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{label} '{record_id}' not found",
        )
    return updated


async def _delete_record(
    record_type: str, record_id: str, db: AsyncIOMotorDatabase, label: str
):
    if not await crud.delete(db, record_type, record_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{label} '{record_id}' not found",
        )


@router.get(
    "/special-storage-requirements",
    response_model=PaginatedSpecialStorageRequirementResponse,
)
async def list_special_storage_requirements(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    product_id: str | None = None,
    component_id: str | None = None,
    material_code: str | None = None,
    status_value: str | None = Query(None, alias="status"),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await _list_records(
        "special_storage_requirements",
        PaginatedSpecialStorageRequirementResponse,
        page,
        page_size,
        db,
        _query(
            product_id=product_id,
            component_id=component_id,
            material_code=material_code,
            status=status_value,
        ),
    )


@router.post(
    "/special-storage-requirements",
    response_model=SpecialStorageRequirement,
    status_code=status.HTTP_201_CREATED,
)
async def create_special_storage_requirement(
    data: SpecialStorageRequirementCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-inventory")),
):
    return await _create_record(
        "special_storage_requirements",
        data,
        db,
        data.requirement_id,
        "Special storage requirement",
    )


@router.get(
    "/special-storage-requirements/{requirement_id}",
    response_model=SpecialStorageRequirement,
)
async def get_special_storage_requirement(
    requirement_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await _get_record(
        "special_storage_requirements",
        requirement_id,
        db,
        "Special storage requirement",
    )


@router.patch(
    "/special-storage-requirements/{requirement_id}",
    response_model=SpecialStorageRequirement,
)
async def update_special_storage_requirement(
    requirement_id: str,
    data: SpecialStorageRequirementUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-inventory")),
):
    return await _update_record(
        "special_storage_requirements",
        requirement_id,
        data,
        db,
        "Special storage requirement",
    )


@router.delete(
    "/special-storage-requirements/{requirement_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_special_storage_requirement(
    requirement_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-inventory")),
):
    await _delete_record(
        "special_storage_requirements",
        requirement_id,
        db,
        "Special storage requirement",
    )


@router.get("/material-inward-logs", response_model=PaginatedMaterialInwardLogResponse)
async def list_material_inward_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    gr_id: str | None = None,
    gr_number: str | None = None,
    grn_record_id: str | None = None,
    material_code: str | None = None,
    warehouse_location_id: str | None = None,
    status_value: str | None = Query(None, alias="status"),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await _list_records(
        "material_inward_logs",
        PaginatedMaterialInwardLogResponse,
        page,
        page_size,
        db,
        _query(
            gr_id=gr_id,
            gr_number=gr_number,
            grn_record_id=grn_record_id,
            material_code=material_code,
            warehouse_location_id=warehouse_location_id,
            status=status_value,
        ),
    )


@router.post(
    "/material-inward-logs",
    response_model=MaterialInwardLog,
    status_code=status.HTTP_201_CREATED,
)
async def create_material_inward_log(
    data: MaterialInwardLogCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-inventory")),
):
    return await _create_record(
        "material_inward_logs", data, db, data.inward_log_id, "Material inward log"
    )


@router.get("/material-inward-logs/{inward_log_id}", response_model=MaterialInwardLog)
async def get_material_inward_log(
    inward_log_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await _get_record(
        "material_inward_logs", inward_log_id, db, "Material inward log"
    )


@router.patch("/material-inward-logs/{inward_log_id}", response_model=MaterialInwardLog)
async def update_material_inward_log(
    inward_log_id: str,
    data: MaterialInwardLogUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-inventory")),
):
    return await _update_record(
        "material_inward_logs", inward_log_id, data, db, "Material inward log"
    )


@router.delete(
    "/material-inward-logs/{inward_log_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_material_inward_log(
    inward_log_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-inventory")),
):
    await _delete_record(
        "material_inward_logs", inward_log_id, db, "Material inward log"
    )


@router.get("/status-labels", response_model=PaginatedWarehouseStatusLabelResponse)
async def list_status_labels(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    entity_type: str | None = None,
    entity_id: str | None = None,
    label_type: str | None = None,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await _list_records(
        "warehouse_status_labels",
        PaginatedWarehouseStatusLabelResponse,
        page,
        page_size,
        db,
        _query(entity_type=entity_type, entity_id=entity_id, label_type=label_type),
    )


@router.post(
    "/status-labels",
    response_model=WarehouseStatusLabel,
    status_code=status.HTTP_201_CREATED,
)
async def create_status_label(
    data: WarehouseStatusLabelCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-inventory")),
):
    return await _create_record(
        "warehouse_status_labels", data, db, data.label_id, "Warehouse status label"
    )


@router.get("/status-labels/{label_id}", response_model=WarehouseStatusLabel)
async def get_status_label(
    label_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await _get_record(
        "warehouse_status_labels", label_id, db, "Warehouse status label"
    )


@router.patch("/status-labels/{label_id}", response_model=WarehouseStatusLabel)
async def update_status_label(
    label_id: str,
    data: WarehouseStatusLabelUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-inventory")),
):
    return await _update_record(
        "warehouse_status_labels", label_id, data, db, "Warehouse status label"
    )


@router.delete("/status-labels/{label_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_status_label(
    label_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-inventory")),
):
    await _delete_record(
        "warehouse_status_labels", label_id, db, "Warehouse status label"
    )


@router.get(
    "/dispensing-records", response_model=PaginatedWarehouseDispensingRecordResponse
)
async def list_dispensing_records(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    work_order_id: str | None = None,
    batch_id: str | None = None,
    bom_id: str | None = None,
    component_id: str | None = None,
    material_code: str | None = None,
    status_value: str | None = Query(None, alias="status"),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await _list_records(
        "warehouse_dispensing_records",
        PaginatedWarehouseDispensingRecordResponse,
        page,
        page_size,
        db,
        _query(
            work_order_id=work_order_id,
            batch_id=batch_id,
            bom_id=bom_id,
            component_id=component_id,
            material_code=material_code,
            status=status_value,
        ),
    )


@router.post(
    "/dispensing-records",
    response_model=WarehouseDispensingRecord,
    status_code=status.HTTP_201_CREATED,
)
async def create_dispensing_record(
    data: WarehouseDispensingRecordCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-inventory")),
):
    return await _create_record(
        "warehouse_dispensing_records",
        data,
        db,
        data.dispensing_record_id,
        "Warehouse dispensing record",
    )


@router.get(
    "/dispensing-records/{dispensing_record_id}",
    response_model=WarehouseDispensingRecord,
)
async def get_dispensing_record(
    dispensing_record_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await _get_record(
        "warehouse_dispensing_records",
        dispensing_record_id,
        db,
        "Warehouse dispensing record",
    )


@router.patch(
    "/dispensing-records/{dispensing_record_id}",
    response_model=WarehouseDispensingRecord,
)
async def update_dispensing_record(
    dispensing_record_id: str,
    data: WarehouseDispensingRecordUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-inventory")),
):
    return await _update_record(
        "warehouse_dispensing_records",
        dispensing_record_id,
        data,
        db,
        "Warehouse dispensing record",
    )


@router.delete(
    "/dispensing-records/{dispensing_record_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_dispensing_record(
    dispensing_record_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-inventory")),
):
    await _delete_record(
        "warehouse_dispensing_records",
        dispensing_record_id,
        db,
        "Warehouse dispensing record",
    )


@router.get(
    "/quarantine-release-rejection-records",
    response_model=PaginatedInventoryDispositionRecordResponse,
)
async def list_quarantine_release_rejection_records(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    entity_type: str | None = None,
    entity_id: str | None = None,
    disposition: str | None = None,
    gr_id: str | None = None,
    inventory_id: str | None = None,
    lot_id: str | None = None,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await _list_records(
        "inventory_disposition_records",
        PaginatedInventoryDispositionRecordResponse,
        page,
        page_size,
        db,
        _query(
            entity_type=entity_type,
            entity_id=entity_id,
            disposition=disposition,
            gr_id=gr_id,
            inventory_id=inventory_id,
            lot_id=lot_id,
        ),
    )


@router.post(
    "/quarantine-release-rejection-records",
    response_model=InventoryDispositionRecord,
    status_code=status.HTTP_201_CREATED,
)
async def create_quarantine_release_rejection_record(
    data: InventoryDispositionRecordCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-inventory")),
):
    return await _create_record(
        "inventory_disposition_records",
        data,
        db,
        data.disposition_id,
        "Inventory disposition record",
    )


@router.get(
    "/quarantine-release-rejection-records/{disposition_id}",
    response_model=InventoryDispositionRecord,
)
async def get_quarantine_release_rejection_record(
    disposition_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-inventory")),
):
    return await _get_record(
        "inventory_disposition_records",
        disposition_id,
        db,
        "Inventory disposition record",
    )


@router.patch(
    "/quarantine-release-rejection-records/{disposition_id}",
    response_model=InventoryDispositionRecord,
)
async def update_quarantine_release_rejection_record(
    disposition_id: str,
    data: InventoryDispositionRecordUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-inventory")),
):
    return await _update_record(
        "inventory_disposition_records",
        disposition_id,
        data,
        db,
        "Inventory disposition record",
    )


@router.delete(
    "/quarantine-release-rejection-records/{disposition_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_quarantine_release_rejection_record(
    disposition_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-inventory")),
):
    await _delete_record(
        "inventory_disposition_records",
        disposition_id,
        db,
        "Inventory disposition record",
    )
