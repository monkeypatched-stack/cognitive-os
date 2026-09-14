from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import require_permission
from services.common.db import get_database
from services.documents.helpers import report_templates as crud
from services.documents.models.report_templates import (
    PaginatedReportTemplateResponse,
    ReportTemplateCreate,
    ReportTemplateResponse,
    ReportTemplateUpdate,
)

router = APIRouter()


@router.get("/", response_model=PaginatedReportTemplateResponse)
async def list_report_templates(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    template_type: str | None = Query(None),
    process_definition_id: str | None = Query(None),
    batch_execution_record_id: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-documents")),
):
    records, total = await crud.get_all(
        db,
        page=page,
        page_size=page_size,
        template_type=template_type,
        process_definition_id=process_definition_id,
        batch_execution_record_id=batch_execution_record_id,
        status=status_filter,
    )
    return PaginatedReportTemplateResponse(
        total=total, page=page, page_size=page_size, results=records
    )


@router.get("/{report_template_id}", response_model=ReportTemplateResponse)
async def get_report_template(
    report_template_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-documents")),
):
    record = await crud.get_by_id(db, report_template_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Report template '{report_template_id}' not found",
        )
    return record


@router.post(
    "/", response_model=ReportTemplateResponse, status_code=status.HTTP_201_CREATED
)
async def create_report_template(
    data: ReportTemplateCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(require_permission("perm-create-documents")),
):
    if await crud.get_by_id(db, data.report_template_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Report template '{data.report_template_id}' already exists",
        )
    if data.created_by is None:
        data.created_by = current_user.get("sub")
    return await crud.create(db, data)


@router.patch("/{report_template_id}", response_model=ReportTemplateResponse)
async def update_report_template(
    report_template_id: str,
    data: ReportTemplateUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-documents")),
):
    updated = await crud.update(db, report_template_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Report template '{report_template_id}' not found",
        )
    return updated


@router.delete("/{report_template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_report_template(
    report_template_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-documents")),
):
    if not await crud.delete(db, report_template_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Report template '{report_template_id}' not found",
        )
