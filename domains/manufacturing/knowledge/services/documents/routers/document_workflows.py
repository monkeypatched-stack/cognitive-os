from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import require_permission
from services.common.db import get_database
from services.documents.helpers import document_workflows as crud
from services.documents.models.document_workflows import (
    DocumentWorkflowCreate,
    DocumentWorkflowResponse,
    DocumentWorkflowStatus,
    DocumentWorkflowStep,
    DocumentWorkflowStepUpdate,
    DocumentWorkflowUpdate,
    PaginatedDocumentWorkflowResponse,
)

router = APIRouter()


@router.get("/", response_model=PaginatedDocumentWorkflowResponse)
async def list_document_workflows(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-documents")),
):
    records, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedDocumentWorkflowResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=records,
    )


@router.get("/by-document/{document_id}", response_model=list[DocumentWorkflowResponse])
async def list_document_workflows_by_document(
    document_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-documents")),
):
    return await crud.get_by_document_id(db, document_id)


@router.get(
    "/by-status/{workflow_status}", response_model=list[DocumentWorkflowResponse]
)
async def list_document_workflows_by_status(
    workflow_status: DocumentWorkflowStatus,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-documents")),
):
    return await crud.get_by_status(db, workflow_status.value)


@router.get("/by-assignee/{assignee_id}", response_model=list[DocumentWorkflowResponse])
async def list_document_workflows_by_assignee(
    assignee_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-documents")),
):
    return await crud.get_by_assignee(db, assignee_id)


@router.get("/{workflow_id}", response_model=DocumentWorkflowResponse)
async def get_document_workflow(
    workflow_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-documents")),
):
    record = await crud.get_by_workflow_id(db, workflow_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document workflow '{workflow_id}' not found",
        )
    return record


@router.post(
    "/", response_model=DocumentWorkflowResponse, status_code=status.HTTP_201_CREATED
)
async def create_document_workflow(
    data: DocumentWorkflowCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-documents")),
):
    return await crud.create(db, data)


@router.patch("/{workflow_id}", response_model=DocumentWorkflowResponse)
async def update_document_workflow(
    workflow_id: str,
    data: DocumentWorkflowUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-documents")),
):
    updated = await crud.update(db, workflow_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document workflow '{workflow_id}' not found",
        )
    return updated


@router.post("/{workflow_id}/steps", response_model=DocumentWorkflowResponse)
async def add_document_workflow_step(
    workflow_id: str,
    data: DocumentWorkflowStep,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-documents")),
):
    updated = await crud.add_step(db, workflow_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document workflow '{workflow_id}' not found",
        )
    return updated


@router.patch("/{workflow_id}/steps/{step_id}", response_model=DocumentWorkflowResponse)
async def update_document_workflow_step(
    workflow_id: str,
    step_id: str,
    data: DocumentWorkflowStepUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-documents")),
):
    updated = await crud.update_step(db, workflow_id, step_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document workflow step '{step_id}' not found",
        )
    return updated


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document_workflow(
    workflow_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-documents")),
):
    if not await crud.delete(db, workflow_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document workflow '{workflow_id}' not found",
        )
