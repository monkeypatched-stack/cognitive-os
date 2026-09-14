from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import require_permission
from services.common.db import get_database
from services.process_definitions.helpers import cpp_cqa_registry as crud
from services.process_definitions.models.cpp_cqa_registry import (
    CppCqaRegistryCreate,
    CppCqaRegistryResponse,
    CppCqaRegistryUpdate,
    PaginatedCppCqaRegistryResponse,
)

router = APIRouter()


@router.get("/", response_model=PaginatedCppCqaRegistryResponse)
async def list_cpp_cqa_registry(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    registry_type: str | None = Query(None),
    process_definition_id: str | None = Query(None),
    process_step_id: str | None = Query(None),
    stage_id: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-process-definitions")),
):
    records, total = await crud.get_all(
        db,
        page=page,
        page_size=page_size,
        registry_type=registry_type,
        process_definition_id=process_definition_id,
        process_step_id=process_step_id,
        stage_id=stage_id,
        status=status_filter,
    )
    return PaginatedCppCqaRegistryResponse(
        total=total, page=page, page_size=page_size, results=records
    )


@router.get("/{registry_id}", response_model=CppCqaRegistryResponse)
async def get_cpp_cqa_registry_entry(
    registry_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-process-definitions")),
):
    record = await crud.get_by_id(db, registry_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"CPP/CQA registry entry '{registry_id}' not found",
        )
    return record


@router.post(
    "/", response_model=CppCqaRegistryResponse, status_code=status.HTTP_201_CREATED
)
async def create_cpp_cqa_registry_entry(
    data: CppCqaRegistryCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-process-definitions")),
):
    if await crud.get_by_id(db, data.registry_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"CPP/CQA registry entry '{data.registry_id}' already exists",
        )
    return await crud.create(db, data)


@router.patch("/{registry_id}", response_model=CppCqaRegistryResponse)
async def update_cpp_cqa_registry_entry(
    registry_id: str,
    data: CppCqaRegistryUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-process-definitions")),
):
    updated = await crud.update(db, registry_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"CPP/CQA registry entry '{registry_id}' not found",
        )
    return updated


@router.delete("/{registry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cpp_cqa_registry_entry(
    registry_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-process-definitions")),
):
    if not await crud.delete(db, registry_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"CPP/CQA registry entry '{registry_id}' not found",
        )
