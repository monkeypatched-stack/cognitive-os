from fastapi import APIRouter, HTTPException, Depends, Query, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from motor.motor_asyncio import AsyncIOMotorDatabase
from jose import JWTError

from services.common.db import get_database
from services.auth.helpers.tokens import decode_access_token
from services.process_definitions.models.process_steps import (
    ProcessStepsCreate,
    ProcessStepsUpdate,
    ProcessStepCreate,
    ProcessStepUpdate,
    ProcessStepsResponse,
    ProcessStepResponse,
    PaginatedProcessStepsResponse,
)
from services.process_definitions.helpers import process_steps as crud
from services.common.auth import get_current_user, require_permission  # consolidated

router = APIRouter()

bearer_scheme = HTTPBearer()


# ── Auth helpers ──────────────────────────────────────────────────────────────


@router.get("/", response_model=PaginatedProcessStepsResponse)
async def list_process_steps(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Return a paginated list of all ProcessSteps containers."""
    results, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedProcessStepsResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=results,
    )


@router.get(
    "/by-process_definition/{process_definition_id}",
    response_model=ProcessStepsResponse,
)
async def get_process_steps_by_process_definition(
    process_definition_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Fetch the ProcessSteps container that belongs to the given process_definition."""
    record = await crud.get_by_process_definition_id(db, process_definition_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No ProcessSteps found for process_definition '{process_definition_id}'",
        )
    return record


@router.get("/{steps_id}", response_model=ProcessStepsResponse)
async def get_process_steps(
    steps_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Fetch a single ProcessSteps container by its id."""
    record = await crud.get_by_id(db, steps_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessSteps '{steps_id}' not found",
        )
    return record


@router.post(
    "/", response_model=ProcessStepsResponse, status_code=status.HTTP_201_CREATED
)
async def create_process_steps(
    data: ProcessStepsCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-process-steps")),
):
    """Create a new ProcessSteps container. Returns 409 if one already exists for that id."""
    if await crud.get_by_id(db, data.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"ProcessSteps '{data.id}' already exists",
        )
    return await crud.create(db, data)


@router.patch("/{steps_id}", response_model=ProcessStepsResponse)
async def update_process_steps(
    steps_id: str,
    data: ProcessStepsUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-process-steps")),
):
    """Partially update a ProcessSteps container. Returns 404 if not found."""
    updated = await crud.update(db, steps_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessSteps '{steps_id}' not found",
        )
    return updated


@router.delete("/{steps_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_process_steps(
    steps_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-process-steps")),
):
    """Delete a ProcessSteps container by its id."""
    if not await crud.delete(db, steps_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessSteps '{steps_id}' not found",
        )


@router.delete(
    "/by-process_definition/{process_definition_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_process_steps_by_process_definition(
    process_definition_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-process-steps")),
):
    """Delete the ProcessSteps container that belongs to the given process_definition."""
    if not await crud.delete_by_process_definition_id(db, process_definition_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No ProcessSteps found for process_definition '{process_definition_id}'",
        )


# ── Individual steps — nested under /{steps_id}/steps ────────────────────────


@router.get("/{steps_id}/steps/{step_id}", response_model=ProcessStepResponse)
async def get_step(
    steps_id: str,
    step_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Fetch a single step by its id from within a ProcessSteps container."""
    step = await crud.get_step_by_id(db, steps_id, step_id)
    if not step:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Step '{step_id}' not found in ProcessSteps '{steps_id}'",
        )
    return step


@router.get(
    "/{steps_id}/steps/by-status/{status_value}",
    response_model=list[ProcessStepResponse],
)
async def list_steps_by_status(
    steps_id: str,
    status_value: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Return all steps within a container that match the given status."""
    return await crud.get_steps_by_status(db, steps_id, status_value)


@router.get(
    "/{steps_id}/steps/by-action-type/{action_type}",
    response_model=list[ProcessStepResponse],
)
async def list_steps_by_action_type(
    steps_id: str,
    action_type: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Return all steps within a container that match the given action_type."""
    return await crud.get_steps_by_action_type(db, steps_id, action_type)


@router.post(
    "/{steps_id}/steps",
    response_model=ProcessStepsResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_step(
    steps_id: str,
    data: ProcessStepCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-process-steps")),
):
    """Append a new step to the steps array of the given container."""
    updated = await crud.add_step(db, steps_id, data.model_dump())
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessSteps '{steps_id}' not found",
        )
    return updated


@router.patch("/{steps_id}/steps/{step_id}", response_model=ProcessStepsResponse)
async def update_step(
    steps_id: str,
    step_id: str,
    data: ProcessStepUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-process-steps")),
):
    """Partially update a specific step inside a ProcessSteps container."""
    updated = await crud.update_step(db, steps_id, step_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Step '{step_id}' not found in ProcessSteps '{steps_id}'",
        )
    return updated


@router.patch("/{steps_id}/steps/{step_id}/status", response_model=ProcessStepsResponse)
async def update_step_status(
    steps_id: str,
    step_id: str,
    status_value: str = Query(..., alias="value"),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-process-steps")),
):
    """Update only the status field of a specific step."""
    updated = await crud.update_step_status(db, steps_id, step_id, status_value)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Step '{step_id}' not found in ProcessSteps '{steps_id}'",
        )
    return updated


@router.delete("/{steps_id}/steps/{step_id}", response_model=ProcessStepsResponse)
async def remove_step(
    steps_id: str,
    step_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-process-steps")),
):
    """Remove a specific step from the steps array. Returns the updated container."""
    updated = await crud.remove_step(db, steps_id, step_id)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Step '{step_id}' not found in ProcessSteps '{steps_id}'",
        )
    return updated
