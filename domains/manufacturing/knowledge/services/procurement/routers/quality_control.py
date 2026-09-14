from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import require_permission
from services.common.db import get_database
from services.procurement.helpers import quality_control as crud
from services.procurement.models.quality_control import (
    InspectionChecklistCreate,
    InspectionChecklistResponse,
    InspectionChecklistUpdate,
    InspectionStatus,
    InspectionType,
    MaterialSpecificationCreate,
    MaterialSpecificationResponse,
    MaterialSpecificationUpdate,
    NCRStatus,
    NonConformanceReportCreate,
    NonConformanceReportResponse,
    NonConformanceReportUpdate,
    PaginatedInspectionChecklistResponse,
    PaginatedMaterialSpecificationResponse,
    PaginatedNonConformanceReportResponse,
    PaginatedQCInspectionResponse,
    PaginatedReInspectionRecordResponse,
    QCInspectionCreate,
    QCInspectionResponse,
    QCInspectionUpdate,
    ReInspectionRecordCreate,
    ReInspectionRecordResponse,
    ReInspectionRecordUpdate,
)

router = APIRouter()


@router.get("/specifications")
async def list_material_specifications(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    records, total = await crud.get_all_material_specs(
        db, page=page, page_size=page_size
    )
    return {"total": total, "page": page, "page_size": page_size, "results": records}


@router.get(
    "/specifications/by-material/{material_code}",
    response_model=list[MaterialSpecificationResponse],
)
async def list_material_specifications_by_material(
    material_code: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_material_specs_by_material(db, material_code)


@router.get("/specifications/{spec_id}", response_model=MaterialSpecificationResponse)
async def get_material_specification(
    spec_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_material_spec_by_id(db, spec_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Material specification '{spec_id}' not found",
        )
    return record


@router.post(
    "/specifications",
    response_model=MaterialSpecificationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_material_specification(
    data: MaterialSpecificationCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-products")),
):
    if await crud.get_material_spec_by_code(db, data.spec_code):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Material specification '{data.spec_code}' already exists",
        )
    return await crud.create_material_spec(db, data)


@router.patch("/specifications/{spec_id}", response_model=MaterialSpecificationResponse)
async def update_material_specification(
    spec_id: str,
    data: MaterialSpecificationUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-products")),
):
    updated = await crud.update_material_spec(db, spec_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Material specification '{spec_id}' not found",
        )
    return updated


@router.delete("/specifications/{spec_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_material_specification(
    spec_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-products")),
):
    if not await crud.delete_material_spec(db, spec_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Material specification '{spec_id}' not found",
        )


@router.get("/checklists")
async def list_inspection_checklists(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    records, total = await crud.get_all_inspection_checklists(
        db, page=page, page_size=page_size
    )
    return {"total": total, "page": page, "page_size": page_size, "results": records}


@router.get(
    "/checklists/by-material/{material_code}",
    response_model=list[InspectionChecklistResponse],
)
async def list_inspection_checklists_by_material(
    material_code: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_inspection_checklists_by_material(db, material_code)


@router.get(
    "/checklists/by-type/{inspection_type}",
    response_model=list[InspectionChecklistResponse],
)
async def list_inspection_checklists_by_type(
    inspection_type: InspectionType,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_inspection_checklists_by_type(db, inspection_type.value)


@router.get("/checklists/{checklist_id}", response_model=InspectionChecklistResponse)
async def get_inspection_checklist(
    checklist_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_inspection_checklist_by_id(db, checklist_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Inspection checklist '{checklist_id}' not found",
        )
    return record


@router.post(
    "/checklists",
    response_model=InspectionChecklistResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_inspection_checklist(
    data: InspectionChecklistCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-products")),
):
    return await crud.create_inspection_checklist(db, data)


@router.patch("/checklists/{checklist_id}", response_model=InspectionChecklistResponse)
async def update_inspection_checklist(
    checklist_id: str,
    data: InspectionChecklistUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-products")),
):
    updated = await crud.update_inspection_checklist(db, checklist_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Inspection checklist '{checklist_id}' not found",
        )
    return updated


@router.delete("/checklists/{checklist_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_inspection_checklist(
    checklist_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-products")),
):
    if not await crud.delete_inspection_checklist(db, checklist_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Inspection checklist '{checklist_id}' not found",
        )


@router.get("/inspections")
async def list_qc_inspections(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    records, total = await crud.get_all_qc_inspections(
        db, page=page, page_size=page_size
    )
    return {"total": total, "page": page, "page_size": page_size, "results": records}


@router.get("/inspections/by-gr/{gr_id}", response_model=list[QCInspectionResponse])
async def list_qc_inspections_by_gr(
    gr_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_qc_inspections_by_gr(db, gr_id)


@router.get(
    "/inspections/by-status/{inspection_status}",
    response_model=list[QCInspectionResponse],
)
async def list_qc_inspections_by_status(
    inspection_status: InspectionStatus,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_qc_inspections_by_status(db, inspection_status.value)


@router.get("/inspections/{inspection_id}", response_model=QCInspectionResponse)
async def get_qc_inspection(
    inspection_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_qc_inspection_by_id(db, inspection_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"QC inspection '{inspection_id}' not found",
        )
    return record


@router.post(
    "/inspections",
    response_model=QCInspectionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_qc_inspection(
    data: QCInspectionCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-products")),
):
    if await crud.get_qc_inspection_by_code(db, data.inspection_code):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"QC inspection '{data.inspection_code}' already exists",
        )
    return await crud.create_qc_inspection(db, data)


@router.patch("/inspections/{inspection_id}", response_model=QCInspectionResponse)
async def update_qc_inspection(
    inspection_id: str,
    data: QCInspectionUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-products")),
):
    updated = await crud.update_qc_inspection(db, inspection_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"QC inspection '{inspection_id}' not found",
        )
    return updated


@router.delete("/inspections/{inspection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_qc_inspection(
    inspection_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-products")),
):
    if not await crud.delete_qc_inspection(db, inspection_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"QC inspection '{inspection_id}' not found",
        )


@router.get("/reinspections")
async def list_reinspections(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    records, total = await crud.get_all_reinspections(
        db, page=page, page_size=page_size
    )
    return {"total": total, "page": page, "page_size": page_size, "results": records}


@router.get(
    "/reinspections/by-original/{inspection_id}",
    response_model=list[ReInspectionRecordResponse],
)
async def list_reinspections_by_original(
    inspection_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_reinspections_by_original(db, inspection_id)


@router.get(
    "/reinspections/{reinspection_id}", response_model=ReInspectionRecordResponse
)
async def get_reinspection(
    reinspection_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_reinspection_by_id(db, reinspection_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Re-inspection '{reinspection_id}' not found",
        )
    return record


@router.post(
    "/reinspections",
    response_model=ReInspectionRecordResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_reinspection(
    data: ReInspectionRecordCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-products")),
):
    return await crud.create_reinspection(db, data)


@router.patch(
    "/reinspections/{reinspection_id}", response_model=ReInspectionRecordResponse
)
async def update_reinspection(
    reinspection_id: str,
    data: ReInspectionRecordUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-products")),
):
    updated = await crud.update_reinspection(db, reinspection_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Re-inspection '{reinspection_id}' not found",
        )
    return updated


@router.delete(
    "/reinspections/{reinspection_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_reinspection(
    reinspection_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-products")),
):
    if not await crud.delete_reinspection(db, reinspection_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Re-inspection '{reinspection_id}' not found",
        )


@router.get("/ncrs")
async def list_ncrs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    records, total = await crud.get_all_ncrs(db, page=page, page_size=page_size)
    return {"total": total, "page": page, "page_size": page_size, "results": records}


@router.get("/ncrs/by-gr/{gr_id}", response_model=list[NonConformanceReportResponse])
async def list_ncrs_by_gr(
    gr_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_ncrs_by_gr(db, gr_id)


@router.get(
    "/ncrs/by-status/{ncr_status}", response_model=list[NonConformanceReportResponse]
)
async def list_ncrs_by_status(
    ncr_status: NCRStatus,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.get_ncrs_by_status(db, ncr_status.value)


@router.get("/ncrs/{ncr_id}", response_model=NonConformanceReportResponse)
async def get_ncr(
    ncr_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_ncr_by_id(db, ncr_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"NCR '{ncr_id}' not found"
        )
    return record


@router.post(
    "/ncrs",
    response_model=NonConformanceReportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_ncr(
    data: NonConformanceReportCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-products")),
):
    if await crud.get_ncr_by_number(db, data.ncr_number):
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"NCR '{data.ncr_number}' already exists"
        )
    return await crud.create_ncr(db, data)


@router.patch("/ncrs/{ncr_id}", response_model=NonConformanceReportResponse)
async def update_ncr(
    ncr_id: str,
    data: NonConformanceReportUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-products")),
):
    updated = await crud.update_ncr(db, ncr_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"NCR '{ncr_id}' not found"
        )
    return updated


@router.delete("/ncrs/{ncr_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ncr(
    ncr_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-products")),
):
    if not await crud.delete_ncr(db, ncr_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"NCR '{ncr_id}' not found"
        )
