from fastapi import APIRouter, HTTPException, Depends, Query, Body, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from motor.motor_asyncio import AsyncIOMotorDatabase
from jose import JWTError

from services.common.db import get_database
from services.auth.helpers.tokens import decode_access_token
from services.process_definitions.models.process_post_checks import (
    ProcessPostchecksCreate,
    ProcessPostchecksUpdate,
    ProcessPostchecksResponse,
    PaginatedProcessPostchecksResponse,
    ProcessStepPostchecksCreate,
    ProcessStepPostchecksUpdate,
    ProcessStepPostchecksResponse,
    PostCheckConditionCreate,
    PostCheckConditionResponse,
)
from services.process_definitions.helpers import process_post_checks as crud
from services.common.auth import get_current_user, require_permission  # consolidated

router = APIRouter()

bearer_scheme = HTTPBearer()


# ── Auth helpers ──────────────────────────────────────────────────────────────


@router.get("/process_definition", response_model=PaginatedProcessPostchecksResponse)
async def list_process_definition_postchecks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Return a paginated list of all ProcessPostchecks containers."""
    results, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedProcessPostchecksResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=results,
    )


# ── Filtered reads ────────────────────────────────────────────────────────────


@router.get(
    "/process_definition/by-process_definition/{process_definition_id}",
    response_model=ProcessPostchecksResponse,
)
async def get_process_definition_postchecks_by_process_definition(
    process_definition_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Fetch the postchecks container that belongs to the given process_definition."""
    record = await crud.get_by_process_definition_id(db, process_definition_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No ProcessPostchecks found for process_definition '{process_definition_id}'",
        )
    return record


@router.get(
    "/process_definition/{postchecks_id}/conditions/by-severity/{severity}",
    response_model=list[PostCheckConditionResponse],
)
async def list_process_definition_conditions_by_severity(
    postchecks_id: str,
    severity: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Return all conditions within a container that match the given severity."""
    return await crud.get_conditions_by_severity(db, postchecks_id, severity)


@router.get(
    "/process_definition/{postchecks_id}/conditions/mandatory",
    response_model=list[PostCheckConditionResponse],
)
async def list_mandatory_process_definition_conditions(
    postchecks_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Return only the mandatory conditions from a ProcessPostchecks container."""
    return await crud.get_mandatory_conditions(db, postchecks_id)


@router.get(
    "/process_definition/{postchecks_id}/conditions/by-corrective-action/{corrective_action_id}",
    response_model=list[PostCheckConditionResponse],
)
async def list_process_definition_conditions_by_corrective_action(
    postchecks_id: str,
    corrective_action_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Return all conditions that link to the given corrective action ID."""
    return await crud.get_conditions_by_corrective_action(
        db, postchecks_id, corrective_action_id
    )


# ── Single record ─────────────────────────────────────────────────────────────


@router.get(
    "/process_definition/{postchecks_id}", response_model=ProcessPostchecksResponse
)
async def get_process_definition_postchecks(
    postchecks_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Fetch a single ProcessPostchecks container by its id."""
    record = await crud.get_by_id(db, postchecks_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessPostchecks '{postchecks_id}' not found",
        )
    return record


# ── Create ────────────────────────────────────────────────────────────────────


@router.post(
    "/process_definition",
    response_model=ProcessPostchecksResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_process_definition_postchecks(
    data: ProcessPostchecksCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-postchecks")),
):
    """Create a new ProcessPostchecks container. Returns 409 if id already exists."""
    if await crud.get_by_id(db, data.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"ProcessPostchecks '{data.id}' already exists",
        )
    return await crud.create(db, data)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch(
    "/process_definition/{postchecks_id}", response_model=ProcessPostchecksResponse
)
async def update_process_definition_postchecks(
    postchecks_id: str,
    data: ProcessPostchecksUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-postchecks")),
):
    """Partially update a ProcessPostchecks container."""
    updated = await crud.update(db, postchecks_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessPostchecks '{postchecks_id}' not found",
        )
    return updated


# ── Condition mutations ───────────────────────────────────────────────────────


@router.post(
    "/process_definition/{postchecks_id}/conditions",
    response_model=ProcessPostchecksResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_process_definition_condition(
    postchecks_id: str,
    data: PostCheckConditionCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-postchecks")),
):
    """Append a new condition to a ProcessPostchecks container."""
    updated = await crud.add_condition(db, postchecks_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessPostchecks '{postchecks_id}' not found",
        )
    return updated


@router.patch(
    "/process_definition/{postchecks_id}/conditions/{condition_id}",
    response_model=ProcessPostchecksResponse,
)
async def update_process_definition_condition(
    postchecks_id: str,
    condition_id: str,
    fields: dict = Body(...),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-postchecks")),
):
    """Update specific fields of a condition inside a ProcessPostchecks container."""
    updated = await crud.update_condition(db, postchecks_id, condition_id, fields)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Condition '{condition_id}' not found in ProcessPostchecks '{postchecks_id}'",
        )
    return updated


@router.post(
    "/process_definition/{postchecks_id}/conditions/{condition_id}/corrective-actions/{corrective_action_id}",
    response_model=ProcessPostchecksResponse,
)
async def link_process_definition_corrective_action(
    postchecks_id: str,
    condition_id: str,
    corrective_action_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-postchecks")),
):
    """Add a corrective action ID to a condition's links_to_corrective_action_ids array."""
    updated = await crud.link_corrective_action(
        db, postchecks_id, condition_id, corrective_action_id
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Condition '{condition_id}' not found in ProcessPostchecks '{postchecks_id}'",
        )
    return updated


@router.delete(
    "/process_definition/{postchecks_id}/conditions/{condition_id}/corrective-actions/{corrective_action_id}",
    response_model=ProcessPostchecksResponse,
)
async def unlink_process_definition_corrective_action(
    postchecks_id: str,
    condition_id: str,
    corrective_action_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-postchecks")),
):
    """Remove a corrective action ID from a condition's links_to_corrective_action_ids array."""
    updated = await crud.unlink_corrective_action(
        db, postchecks_id, condition_id, corrective_action_id
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Condition '{condition_id}' not found in ProcessPostchecks '{postchecks_id}'",
        )
    return updated


@router.delete(
    "/process_definition/{postchecks_id}/conditions/{condition_id}",
    response_model=ProcessPostchecksResponse,
)
async def remove_process_definition_condition(
    postchecks_id: str,
    condition_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-postchecks")),
):
    """Remove a specific condition from a ProcessPostchecks container."""
    updated = await crud.remove_condition(db, postchecks_id, condition_id)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Condition '{condition_id}' not found in ProcessPostchecks '{postchecks_id}'",
        )
    return updated


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete(
    "/process_definition/by-process_definition/{process_definition_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_process_definition_postchecks_by_process_definition(
    process_definition_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-postchecks")),
):
    """Cascade-delete the postchecks container that belongs to the given process_definition."""
    if not await crud.delete_by_process_definition_id(db, process_definition_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No ProcessPostchecks found for process_definition '{process_definition_id}'",
        )


@router.delete(
    "/process_definition/{postchecks_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_process_definition_postchecks(
    postchecks_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-postchecks")),
):
    """Delete a ProcessPostchecks container by its id."""
    if not await crud.delete(db, postchecks_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessPostchecks '{postchecks_id}' not found",
        )


# ============================================================================
# ProcessStepPostchecks  — /steps
# ============================================================================

# ── Filtered reads ────────────────────────────────────────────────────────────


@router.get("/steps/by-step/{step_id}", response_model=ProcessStepPostchecksResponse)
async def get_step_postchecks_by_step(
    step_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Fetch the postchecks container that belongs to the given process_definition step."""
    record = await crud.get_step_postchecks_by_step_id(db, step_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No ProcessStepPostchecks found for step '{step_id}'",
        )
    return record


@router.get(
    "/steps/by-process_definition/{process_definition_id}",
    response_model=list[ProcessStepPostchecksResponse],
)
async def list_step_postchecks_by_process_definition(
    process_definition_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Return all step-level postchecks containers that share the given process_definition_id."""
    return await crud.get_step_postchecks_by_process_definition_id(
        db, process_definition_id
    )


@router.get(
    "/steps/{postchecks_id}/conditions/by-severity/{severity}",
    response_model=list[PostCheckConditionResponse],
)
async def list_step_conditions_by_severity(
    postchecks_id: str,
    severity: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Return all step-level conditions that match the given severity."""
    return await crud.get_step_conditions_by_severity(db, postchecks_id, severity)


@router.get(
    "/steps/{postchecks_id}/conditions/by-corrective-action/{corrective_action_id}",
    response_model=list[PostCheckConditionResponse],
)
async def list_step_conditions_by_corrective_action(
    postchecks_id: str,
    corrective_action_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Return step-level conditions that link to the given corrective action ID."""
    return await crud.get_step_conditions_by_corrective_action(
        db, postchecks_id, corrective_action_id
    )


# ── Single record ─────────────────────────────────────────────────────────────


@router.get("/steps/{postchecks_id}", response_model=ProcessStepPostchecksResponse)
async def get_step_postchecks(
    postchecks_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Fetch a single ProcessStepPostchecks container by its id."""
    record = await crud.get_step_postchecks_by_id(db, postchecks_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessStepPostchecks '{postchecks_id}' not found",
        )
    return record


# ── Create ────────────────────────────────────────────────────────────────────


@router.post(
    "/steps",
    response_model=ProcessStepPostchecksResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_step_postchecks(
    data: ProcessStepPostchecksCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-postchecks")),
):
    """Create a new ProcessStepPostchecks container. Returns 409 if id already exists."""
    if await crud.get_step_postchecks_by_id(db, data.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"ProcessStepPostchecks '{data.id}' already exists",
        )
    return await crud.create_step_postchecks(db, data)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/steps/{postchecks_id}", response_model=ProcessStepPostchecksResponse)
async def update_step_postchecks(
    postchecks_id: str,
    data: ProcessStepPostchecksUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-postchecks")),
):
    """Partially update a ProcessStepPostchecks container."""
    updated = await crud.update_step_postchecks(db, postchecks_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessStepPostchecks '{postchecks_id}' not found",
        )
    return updated


# ── Condition mutations ───────────────────────────────────────────────────────


@router.post(
    "/steps/{postchecks_id}/conditions",
    response_model=ProcessStepPostchecksResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_step_condition(
    postchecks_id: str,
    data: PostCheckConditionCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-postchecks")),
):
    """Append a new condition to a ProcessStepPostchecks container."""
    updated = await crud.add_step_condition(db, postchecks_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessStepPostchecks '{postchecks_id}' not found",
        )
    return updated


@router.patch(
    "/steps/{postchecks_id}/conditions/{condition_id}",
    response_model=ProcessStepPostchecksResponse,
)
async def update_step_condition(
    postchecks_id: str,
    condition_id: str,
    fields: dict = Body(...),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-postchecks")),
):
    """Update specific fields of a condition inside a ProcessStepPostchecks container."""
    updated = await crud.update_step_condition(db, postchecks_id, condition_id, fields)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Condition '{condition_id}' not found in ProcessStepPostchecks '{postchecks_id}'",
        )
    return updated


@router.post(
    "/steps/{postchecks_id}/conditions/{condition_id}/corrective-actions/{corrective_action_id}",
    response_model=ProcessStepPostchecksResponse,
)
async def link_step_corrective_action(
    postchecks_id: str,
    condition_id: str,
    corrective_action_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-postchecks")),
):
    """Add a corrective action ID to a step condition's links_to_corrective_action_ids array."""
    updated = await crud.link_step_corrective_action(
        db, postchecks_id, condition_id, corrective_action_id
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Condition '{condition_id}' not found in ProcessStepPostchecks '{postchecks_id}'",
        )
    return updated


@router.delete(
    "/steps/{postchecks_id}/conditions/{condition_id}/corrective-actions/{corrective_action_id}",
    response_model=ProcessStepPostchecksResponse,
)
async def unlink_step_corrective_action(
    postchecks_id: str,
    condition_id: str,
    corrective_action_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-postchecks")),
):
    """Remove a corrective action ID from a step condition's links_to_corrective_action_ids array."""
    updated = await crud.unlink_step_corrective_action(
        db, postchecks_id, condition_id, corrective_action_id
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Condition '{condition_id}' not found in ProcessStepPostchecks '{postchecks_id}'",
        )
    return updated


@router.delete(
    "/steps/{postchecks_id}/conditions/{condition_id}",
    response_model=ProcessStepPostchecksResponse,
)
async def remove_step_condition(
    postchecks_id: str,
    condition_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-postchecks")),
):
    """Remove a specific condition from a ProcessStepPostchecks container."""
    updated = await crud.remove_step_condition(db, postchecks_id, condition_id)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Condition '{condition_id}' not found in ProcessStepPostchecks '{postchecks_id}'",
        )
    return updated


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete("/steps/by-step/{step_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_step_postchecks_by_step(
    step_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-postchecks")),
):
    """Cascade-delete the postchecks container that belongs to the given step."""
    if not await crud.delete_step_postchecks_by_step_id(db, step_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No ProcessStepPostchecks found for step '{step_id}'",
        )


@router.delete("/steps/{postchecks_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_step_postchecks(
    postchecks_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-postchecks")),
):
    """Delete a ProcessStepPostchecks container by its id."""
    if not await crud.delete_step_postchecks(db, postchecks_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessStepPostchecks '{postchecks_id}' not found",
        )
