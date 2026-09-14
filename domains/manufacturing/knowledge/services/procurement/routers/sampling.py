from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import require_permission
from services.common.db import get_database
from services.procurement.helpers import sampling as crud
from services.procurement.models.sampling import (
    ChainOfCustodyEntry,
    PaginatedResamplingRecordResponse,
    PaginatedSampleResponse,
    PaginatedSamplingPlanResponse,
    ResamplingRecordCreate,
    ResamplingRecordResponse,
    ResamplingRecordUpdate,
    SampleCreate,
    SampleResponse,
    SampleStatus,
    SampleUpdate,
    SamplingPlanCreate,
    SamplingPlanResponse,
    SamplingPlanUpdate,
)

router = APIRouter()


@router.get("/plans", response_model=PaginatedSamplingPlanResponse)
async def list_sampling_plans(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    records, total = await crud.get_all_sampling_plans(
        db, page=page, page_size=page_size
    )
    return PaginatedSamplingPlanResponse(
        total=total, page=page, page_size=page_size, results=records
    )


@router.get(
    "/plans/by-material/{material_code}", response_model=list[SamplingPlanResponse]
)
async def list_sampling_plans_by_material(
    material_code: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_sampling_plans_by_material(db, material_code)


@router.get(
    "/plans/by-supplier/{supplier_id}", response_model=list[SamplingPlanResponse]
)
async def list_sampling_plans_by_supplier(
    supplier_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_sampling_plans_by_supplier(db, supplier_id)


@router.get("/plans/{plan_id}", response_model=SamplingPlanResponse)
async def get_sampling_plan(
    plan_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_sampling_plan_by_id(db, plan_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sampling plan '{plan_id}' not found",
        )
    return record


@router.post(
    "/plans", response_model=SamplingPlanResponse, status_code=status.HTTP_201_CREATED
)
async def create_sampling_plan(
    data: SamplingPlanCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-products")),
):
    if await crud.get_sampling_plan_by_code(db, data.plan_code):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Sampling plan '{data.plan_code}' already exists",
        )
    return await crud.create_sampling_plan(db, data)


@router.patch("/plans/{plan_id}", response_model=SamplingPlanResponse)
async def update_sampling_plan(
    plan_id: str,
    data: SamplingPlanUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-products")),
):
    updated = await crud.update_sampling_plan(db, plan_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sampling plan '{plan_id}' not found",
        )
    return updated


@router.delete("/plans/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sampling_plan(
    plan_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-products")),
):
    if not await crud.delete_sampling_plan(db, plan_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sampling plan '{plan_id}' not found",
        )


@router.get("/samples", response_model=PaginatedSampleResponse)
async def list_samples(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    records, total = await crud.get_all_samples(db, page=page, page_size=page_size)
    return PaginatedSampleResponse(
        total=total, page=page, page_size=page_size, results=records
    )


@router.get("/samples/by-gr/{gr_id}", response_model=list[SampleResponse])
async def list_samples_by_gr(
    gr_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_samples_by_gr_id(db, gr_id)


@router.get("/samples/by-status/{sample_status}", response_model=list[SampleResponse])
async def list_samples_by_status(
    sample_status: SampleStatus,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_samples_by_status(db, sample_status.value)


@router.get("/samples/{sample_id}", response_model=SampleResponse)
async def get_sample(
    sample_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_sample_by_id(db, sample_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sample '{sample_id}' not found",
        )
    return record


@router.post(
    "/samples", response_model=SampleResponse, status_code=status.HTTP_201_CREATED
)
async def create_sample(
    data: SampleCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-products")),
):
    if await crud.get_sample_by_code(db, data.sample_code):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Sample '{data.sample_code}' already exists",
        )
    return await crud.create_sample(db, data)


@router.patch("/samples/{sample_id}", response_model=SampleResponse)
async def update_sample(
    sample_id: str,
    data: SampleUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-products")),
):
    updated = await crud.update_sample(db, sample_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sample '{sample_id}' not found",
        )
    return updated


@router.post("/samples/{sample_id}/custody", response_model=SampleResponse)
async def add_sample_custody_entry(
    sample_id: str,
    data: ChainOfCustodyEntry,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-products")),
):
    updated = await crud.add_custody_entry(db, sample_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sample '{sample_id}' not found",
        )
    return updated


@router.delete("/samples/{sample_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sample(
    sample_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-products")),
):
    if not await crud.delete_sample(db, sample_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sample '{sample_id}' not found",
        )


@router.get("/resampling-records", response_model=PaginatedResamplingRecordResponse)
async def list_resampling_records(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    records, total = await crud.get_all_resampling_records(
        db, page=page, page_size=page_size
    )
    return PaginatedResamplingRecordResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=records,
    )


@router.get(
    "/resampling-records/by-original/{sample_id}",
    response_model=list[ResamplingRecordResponse],
)
async def list_resampling_records_by_original_sample(
    sample_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_resampling_records_by_original_sample(db, sample_id)


@router.get(
    "/resampling-records/{resample_id}", response_model=ResamplingRecordResponse
)
async def get_resampling_record(
    resample_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_resampling_record_by_id(db, resample_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Resampling record '{resample_id}' not found",
        )
    return record


@router.post(
    "/resampling-records",
    response_model=ResamplingRecordResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_resampling_record(
    data: ResamplingRecordCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-products")),
):
    return await crud.create_resampling_record(db, data)


@router.patch(
    "/resampling-records/{resample_id}", response_model=ResamplingRecordResponse
)
async def update_resampling_record(
    resample_id: str,
    data: ResamplingRecordUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-products")),
):
    updated = await crud.update_resampling_record(db, resample_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Resampling record '{resample_id}' not found",
        )
    return updated


@router.delete(
    "/resampling-records/{resample_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_resampling_record(
    resample_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-products")),
):
    if not await crud.delete_resampling_record(db, resample_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Resampling record '{resample_id}' not found",
        )
