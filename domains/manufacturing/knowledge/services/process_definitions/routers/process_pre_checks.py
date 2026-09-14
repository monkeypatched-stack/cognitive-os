from fastapi import APIRouter, HTTPException, Depends, Query, Body, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from motor.motor_asyncio import AsyncIOMotorDatabase
from jose import JWTError

from services.common.db import get_database
from services.auth.helpers.tokens import decode_access_token
from services.process_definitions.models.process_pre_checks import (
    ProcessPrechecksCreate,
    ProcessPrechecksUpdate,
    ProcessPrechecksResponse,
    PaginatedProcessPrechecksResponse,
    ProcessStepPrechecksCreate,
    ProcessStepPrechecksUpdate,
    ProcessStepPrechecksResponse,
    PreCheckConditionCreate,
    PreCheckConditionResponse,
)
from services.process_definitions.helpers import process_pre_checks as crud
from services.common.auth import get_current_user, require_permission  # consolidated

router = APIRouter()

bearer_scheme = HTTPBearer()


# ── Auth helpers ──────────────────────────────────────────────────────────────


@router.get("/process_definition", response_model=PaginatedProcessPrechecksResponse)
async def list_process_definition_prechecks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Return a paginated list of all ProcessPrechecks containers."""
    results, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedProcessPrechecksResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=results,
    )


# ── Filtered reads ────────────────────────────────────────────────────────────


@router.get(
    "/process_definition/by-process_definition/{process_definition_id}",
    response_model=ProcessPrechecksResponse,
)
async def get_process_definition_prechecks_by_process_definition(
    process_definition_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Fetch the prechecks container that belongs to the given process_definition."""
    record = await crud.get_by_process_definition_id(db, process_definition_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No ProcessPrechecks found for process_definition '{process_definition_id}'",
        )
    return record


@router.get(
    "/process_definition/{prechecks_id}/conditions/by-severity/{severity}",
    response_model=list[PreCheckConditionResponse],
)
async def list_process_definition_conditions_by_severity(
    prechecks_id: str,
    severity: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Return all conditions within a container that match the given severity."""
    return await crud.get_conditions_by_severity(db, prechecks_id, severity)


@router.get(
    "/process_definition/{prechecks_id}/conditions/mandatory",
    response_model=list[PreCheckConditionResponse],
)
async def list_mandatory_process_definition_conditions(
    prechecks_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Return only the mandatory conditions from a ProcessPrechecks container."""
    return await crud.get_mandatory_conditions(db, prechecks_id)


# ── Single record ─────────────────────────────────────────────────────────────


@router.get(
    "/process_definition/{prechecks_id}", response_model=ProcessPrechecksResponse
)
async def get_process_definition_prechecks(
    prechecks_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Fetch a single ProcessPrechecks container by its id."""
    record = await crud.get_by_id(db, prechecks_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessPrechecks '{prechecks_id}' not found",
        )
    return record


# ── Create ────────────────────────────────────────────────────────────────────


@router.post(
    "/process_definition",
    response_model=ProcessPrechecksResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_process_definition_prechecks(
    data: ProcessPrechecksCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-prechecks")),
):
    """Create a new ProcessPrechecks container. Returns 409 if id already exists."""
    if await crud.get_by_id(db, data.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"ProcessPrechecks '{data.id}' already exists",
        )
    return await crud.create(db, data)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch(
    "/process_definition/{prechecks_id}", response_model=ProcessPrechecksResponse
)
async def update_process_definition_prechecks(
    prechecks_id: str,
    data: ProcessPrechecksUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-prechecks")),
):
    """Partially update a ProcessPrechecks container."""
    updated = await crud.update(db, prechecks_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessPrechecks '{prechecks_id}' not found",
        )
    return updated


# ── Condition mutations ───────────────────────────────────────────────────────


@router.post(
    "/process_definition/{prechecks_id}/conditions",
    response_model=ProcessPrechecksResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_process_definition_condition(
    prechecks_id: str,
    data: PreCheckConditionCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-prechecks")),
):
    """Append a new condition to a ProcessPrechecks container."""
    updated = await crud.add_condition(db, prechecks_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessPrechecks '{prechecks_id}' not found",
        )
    return updated


@router.patch(
    "/process_definition/{prechecks_id}/conditions/{condition_id}",
    response_model=ProcessPrechecksResponse,
)
async def update_process_definition_condition(
    prechecks_id: str,
    condition_id: str,
    fields: dict = Body(...),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-prechecks")),
):
    """Update specific fields of a condition inside a ProcessPrechecks container."""
    updated = await crud.update_condition(db, prechecks_id, condition_id, fields)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Condition '{condition_id}' not found in ProcessPrechecks '{prechecks_id}'",
        )
    return updated


@router.delete(
    "/process_definition/{prechecks_id}/conditions/{condition_id}",
    response_model=ProcessPrechecksResponse,
)
async def remove_process_definition_condition(
    prechecks_id: str,
    condition_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-prechecks")),
):
    """Remove a specific condition from a ProcessPrechecks container."""
    updated = await crud.remove_condition(db, prechecks_id, condition_id)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Condition '{condition_id}' not found in ProcessPrechecks '{prechecks_id}'",
        )
    return updated


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete(
    "/process_definition/by-process_definition/{process_definition_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_process_definition_prechecks_by_process_definition(
    process_definition_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-prechecks")),
):
    """Cascade-delete the prechecks container that belongs to the given process_definition."""
    if not await crud.delete_by_process_definition_id(db, process_definition_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No ProcessPrechecks found for process_definition '{process_definition_id}'",
        )


@router.delete(
    "/process_definition/{prechecks_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_process_definition_prechecks(
    prechecks_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-prechecks")),
):
    """Delete a ProcessPrechecks container by its id."""
    if not await crud.delete(db, prechecks_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessPrechecks '{prechecks_id}' not found",
        )


# ============================================================================
# ProcessStepPrechecks  — /steps
# ============================================================================

# ── Filtered reads ────────────────────────────────────────────────────────────


@router.get("/steps/by-step/{step_id}", response_model=ProcessStepPrechecksResponse)
async def get_step_prechecks_by_step(
    step_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Fetch the prechecks container that belongs to the given process_definition step."""
    record = await crud.get_step_prechecks_by_step_id(db, step_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No ProcessStepPrechecks found for step '{step_id}'",
        )
    return record


@router.get(
    "/steps/by-process_definition/{process_definition_id}",
    response_model=list[ProcessStepPrechecksResponse],
)
async def list_step_prechecks_by_process_definition(
    process_definition_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Return all step-level prechecks containers that share the given process_definition_id."""
    return await crud.get_step_prechecks_by_process_definition_id(
        db, process_definition_id
    )


@router.get(
    "/steps/{prechecks_id}/conditions/by-severity/{severity}",
    response_model=list[PreCheckConditionResponse],
)
async def list_step_conditions_by_severity(
    prechecks_id: str,
    severity: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Return all step-level conditions that match the given severity."""
    return await crud.get_step_conditions_by_severity(db, prechecks_id, severity)


# ── Single record ─────────────────────────────────────────────────────────────


@router.get("/steps/{prechecks_id}", response_model=ProcessStepPrechecksResponse)
async def get_step_prechecks(
    prechecks_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Fetch a single ProcessStepPrechecks container by its id."""
    record = await crud.get_step_prechecks_by_id(db, prechecks_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessStepPrechecks '{prechecks_id}' not found",
        )
    return record


# ── Create ────────────────────────────────────────────────────────────────────


@router.post(
    "/steps",
    response_model=ProcessStepPrechecksResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_step_prechecks(
    data: ProcessStepPrechecksCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-prechecks")),
):
    """Create a new ProcessStepPrechecks container. Returns 409 if id already exists."""
    if await crud.get_step_prechecks_by_id(db, data.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"ProcessStepPrechecks '{data.id}' already exists",
        )
    return await crud.create_step_prechecks(db, data)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/steps/{prechecks_id}", response_model=ProcessStepPrechecksResponse)
async def update_step_prechecks(
    prechecks_id: str,
    data: ProcessStepPrechecksUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-prechecks")),
):
    """Partially update a ProcessStepPrechecks container."""
    updated = await crud.update_step_prechecks(db, prechecks_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessStepPrechecks '{prechecks_id}' not found",
        )
    return updated


# ── Condition mutations ───────────────────────────────────────────────────────


@router.post(
    "/steps/{prechecks_id}/conditions",
    response_model=ProcessStepPrechecksResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_step_condition(
    prechecks_id: str,
    data: PreCheckConditionCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-prechecks")),
):
    """Append a new condition to a ProcessStepPrechecks container."""
    updated = await crud.add_step_condition(db, prechecks_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessStepPrechecks '{prechecks_id}' not found",
        )
    return updated


@router.patch(
    "/steps/{prechecks_id}/conditions/{condition_id}",
    response_model=ProcessStepPrechecksResponse,
)
async def update_step_condition(
    prechecks_id: str,
    condition_id: str,
    fields: dict = Body(...),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-prechecks")),
):
    """Update specific fields of a condition inside a ProcessStepPrechecks container."""
    updated = await crud.update_step_condition(db, prechecks_id, condition_id, fields)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Condition '{condition_id}' not found in ProcessStepPrechecks '{prechecks_id}'",
        )
    return updated


@router.delete(
    "/steps/{prechecks_id}/conditions/{condition_id}",
    response_model=ProcessStepPrechecksResponse,
)
async def remove_step_condition(
    prechecks_id: str,
    condition_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-prechecks")),
):
    """Remove a specific condition from a ProcessStepPrechecks container."""
    updated = await crud.remove_step_condition(db, prechecks_id, condition_id)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Condition '{condition_id}' not found in ProcessStepPrechecks '{prechecks_id}'",
        )
    return updated


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete("/steps/by-step/{step_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_step_prechecks_by_step(
    step_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-prechecks")),
):
    """Cascade-delete the prechecks container that belongs to the given step."""
    if not await crud.delete_step_prechecks_by_step_id(db, step_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No ProcessStepPrechecks found for step '{step_id}'",
        )


@router.delete("/steps/{prechecks_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_step_prechecks(
    prechecks_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-prechecks")),
):
    """Delete a ProcessStepPrechecks container by its id."""
    if not await crud.delete_step_prechecks(db, prechecks_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessStepPrechecks '{prechecks_id}' not found",
        )
