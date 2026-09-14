from fastapi import APIRouter, HTTPException, Depends, Query, Body, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from motor.motor_asyncio import AsyncIOMotorDatabase
from jose import JWTError

from services.common.db import get_database
from services.auth.helpers.tokens import decode_access_token
from services.process_definitions.models.process_corrections import (
    CorrectiveActionsCreate,
    CorrectiveActionsUpdate,
    CorrectiveActionsResponse,
    PaginatedCorrectiveActionsResponse,
    CorrectiveActionCreate,
    CorrectiveActionResponse,
)
from services.process_definitions.helpers import process_corrections as crud
from services.common.auth import get_current_user, require_permission  # consolidated

router = APIRouter()

bearer_scheme = HTTPBearer()


# ── Auth helpers ──────────────────────────────────────────────────────────────


@router.get("/", response_model=PaginatedCorrectiveActionsResponse)
async def list_corrective_actions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Return a paginated list of all CorrectiveActions containers."""
    results, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedCorrectiveActionsResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=results,
    )


# ── Filtered reads ────────────────────────────────────────────────────────────


@router.get(
    "/by-process_definition/{process_definition_id}",
    response_model=list[CorrectiveActionsResponse],
)
async def list_corrective_actions_by_process_definition(
    process_definition_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Return all CorrectiveActions containers that belong to the given process_definition."""
    return await crud.get_by_process_definition_id(db, process_definition_id)


@router.get("/by-step/{step_id}", response_model=CorrectiveActionsResponse)
async def get_corrective_actions_by_step(
    step_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Fetch the CorrectiveActions container that belongs to the given process_definition step."""
    record = await crud.get_by_step_id(db, step_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No CorrectiveActions found for step '{step_id}'",
        )
    return record


# ── Single record ─────────────────────────────────────────────────────────────


@router.get("/{corrective_actions_id}", response_model=CorrectiveActionsResponse)
async def get_corrective_actions(
    corrective_actions_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Fetch a single CorrectiveActions container by its id."""
    record = await crud.get_by_id(db, corrective_actions_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"CorrectiveActions '{corrective_actions_id}' not found",
        )
    return record


# ── Create ────────────────────────────────────────────────────────────────────


@router.post(
    "/", response_model=CorrectiveActionsResponse, status_code=status.HTTP_201_CREATED
)
async def create_corrective_actions(
    data: CorrectiveActionsCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-corrective-actions")),
):
    """Create a new CorrectiveActions container. Returns 409 if id already exists."""
    if await crud.get_by_id(db, data.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"CorrectiveActions '{data.id}' already exists",
        )
    return await crud.create(db, data)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/{corrective_actions_id}", response_model=CorrectiveActionsResponse)
async def update_corrective_actions(
    corrective_actions_id: str,
    data: CorrectiveActionsUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-corrective-actions")),
):
    """Partially update a CorrectiveActions container."""
    updated = await crud.update(db, corrective_actions_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"CorrectiveActions '{corrective_actions_id}' not found",
        )
    return updated


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete(
    "/by-process_definition/{process_definition_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_corrective_actions_by_process_definition(
    process_definition_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-corrective-actions")),
):
    """Cascade-delete all CorrectiveActions containers belonging to the given process_definition."""
    if not await crud.delete_by_process_definition_id(db, process_definition_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No CorrectiveActions found for process_definition '{process_definition_id}'",
        )


@router.delete("/by-step/{step_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_corrective_actions_by_step(
    step_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-corrective-actions")),
):
    """Cascade-delete the CorrectiveActions container that belongs to the given step."""
    if not await crud.delete_by_step_id(db, step_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No CorrectiveActions found for step '{step_id}'",
        )


@router.delete("/{corrective_actions_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_corrective_actions(
    corrective_actions_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-corrective-actions")),
):
    """Delete a CorrectiveActions container by its id."""
    if not await crud.delete(db, corrective_actions_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"CorrectiveActions '{corrective_actions_id}' not found",
        )


# ============================================================================
# CorrectiveAction items — nested under /{corrective_actions_id}/actions
# ============================================================================

# ── Filtered reads ────────────────────────────────────────────────────────────


@router.get(
    "/{corrective_actions_id}/actions/by-type/{action_type}",
    response_model=list[CorrectiveActionResponse],
)
async def list_actions_by_type(
    corrective_actions_id: str,
    action_type: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Return all actions within a container that match the given action_type."""
    return await crud.get_actions_by_type(db, corrective_actions_id, action_type)


@router.get(
    "/{corrective_actions_id}/actions/by-postcheck/{postcheck_id}",
    response_model=list[CorrectiveActionResponse],
)
async def list_actions_by_postcheck(
    corrective_actions_id: str,
    postcheck_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Return all actions that are linked to the given PostCheckCondition ID."""
    return await crud.get_actions_by_postcheck(db, corrective_actions_id, postcheck_id)


# ── Single action ─────────────────────────────────────────────────────────────


@router.get(
    "/{corrective_actions_id}/actions/{action_id}",
    response_model=CorrectiveActionResponse,
)
async def get_action(
    corrective_actions_id: str,
    action_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Fetch a single action by its id from within a CorrectiveActions container."""
    action = await crud.get_action_by_id(db, corrective_actions_id, action_id)
    if not action:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Action '{action_id}' not found in CorrectiveActions '{corrective_actions_id}'",
        )
    return action


# ── Add action ────────────────────────────────────────────────────────────────


@router.post(
    "/{corrective_actions_id}/actions",
    response_model=CorrectiveActionsResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_action(
    corrective_actions_id: str,
    data: CorrectiveActionCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-corrective-actions")),
):
    """Append a new CorrectiveAction to the actions array of a container."""
    updated = await crud.add_action(db, corrective_actions_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"CorrectiveActions '{corrective_actions_id}' not found",
        )
    return updated


# ── Update action ─────────────────────────────────────────────────────────────


@router.patch(
    "/{corrective_actions_id}/actions/{action_id}",
    response_model=CorrectiveActionsResponse,
)
async def update_action(
    corrective_actions_id: str,
    action_id: str,
    fields: dict = Body(...),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-corrective-actions")),
):
    """Update specific fields of an action inside a CorrectiveActions container."""
    updated = await crud.update_action(db, corrective_actions_id, action_id, fields)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Action '{action_id}' not found in CorrectiveActions '{corrective_actions_id}'",
        )
    return updated


# ── Postcheck link / unlink ───────────────────────────────────────────────────


@router.post(
    "/{corrective_actions_id}/actions/{action_id}/postchecks/{postcheck_id}",
    response_model=CorrectiveActionsResponse,
)
async def link_postcheck(
    corrective_actions_id: str,
    action_id: str,
    postcheck_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-corrective-actions")),
):
    """Add a PostCheckCondition ID to an action's applies_to_postchecks array."""
    updated = await crud.link_postcheck(
        db, corrective_actions_id, action_id, postcheck_id
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Action '{action_id}' not found in CorrectiveActions '{corrective_actions_id}'",
        )
    return updated


@router.delete(
    "/{corrective_actions_id}/actions/{action_id}/postchecks/{postcheck_id}",
    response_model=CorrectiveActionsResponse,
)
async def unlink_postcheck(
    corrective_actions_id: str,
    action_id: str,
    postcheck_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-corrective-actions")),
):
    """Remove a PostCheckCondition ID from an action's applies_to_postchecks array."""
    updated = await crud.unlink_postcheck(
        db, corrective_actions_id, action_id, postcheck_id
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Action '{action_id}' not found in CorrectiveActions '{corrective_actions_id}'",
        )
    return updated


# ── Remove action ─────────────────────────────────────────────────────────────


@router.delete(
    "/{corrective_actions_id}/actions/{action_id}",
    response_model=CorrectiveActionsResponse,
)
async def remove_action(
    corrective_actions_id: str,
    action_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-corrective-actions")),
):
    """Remove a specific action from the actions array. Returns the updated container."""
    updated = await crud.remove_action(db, corrective_actions_id, action_id)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Action '{action_id}' not found in CorrectiveActions '{corrective_actions_id}'",
        )
    return updated
