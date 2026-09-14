from fastapi import APIRouter, HTTPException, Depends, Query, Body, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from motor.motor_asyncio import AsyncIOMotorDatabase
from jose import JWTError
from pydantic import ValidationError

from services.common.db import get_database
from services.auth.helpers.tokens import decode_access_token
from services.process_definitions.models.process_constraints import (
    ProcessConstraintsCreate,
    ProcessConstraintsUpdate,
    ProcessConstraintsResponse,
    PaginatedProcessConstraintsResponse,
    ProcessStepConstraintsCreate,
    ProcessStepConstraintsUpdate,
    ProcessStepConstraintsResponse,
    ConstraintCreate,
    ConstraintResponse,
)
from services.process_definitions.helpers import process_constraints as crud
from services.common.auth import get_current_user, require_permission  # consolidated

router = APIRouter()

bearer_scheme = HTTPBearer()


def _strip_canvas_prefix(value: str | None) -> str | None:
    if not value:
        return None
    return value.split(":", 1)[1] if ":" in value else value


def _first_text(payload: dict, *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _normalize_constraint_type(value: object) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        aliases = {
            "constraint": "operational",
            "operation": "operational",
            "operations": "operational",
        }
        return aliases.get(normalized, normalized or "operational")
    return "operational"


def _normalize_severity(value: object) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    return "critical"


def _constraint_from_canvas_payload(
    payload: dict | None,
    step_id: str,
    constraint_id: str | None = None,
) -> ConstraintCreate:
    data = dict(payload or {})
    raw_id = (
        constraint_id
        or data.get("constraint_id")
        or data.get("constraintId")
        or data.get("id")
        or data.get("nodeId")
    )
    clean_id = _strip_canvas_prefix(str(raw_id)) if raw_id else None
    process_definition_id = (
        _first_text(data, "process_definition_id", "process_definitionId") or step_id
    )
    payload_for_model = {
        "id": clean_id or f"CONSTRAINT-{step_id}",
        "process_definition_id": process_definition_id,
        "name": _first_text(data, "name", "title", "label", "itemLabel")
        or "Constraint",
        "description": _first_text(data, "description", "details", "notes") or "",
        "constraint_type": _normalize_constraint_type(
            data.get("constraint_type")
            or data.get("constraintType")
            or data.get("type")
        ),
        "is_hard_constraint": data.get(
            "is_hard_constraint",
            data.get("isHardConstraint", True),
        ),
        "severity": _normalize_severity(data.get("severity")),
    }
    for source_key, target_key in (
        ("enforcement_mechanism", "enforcement_mechanism"),
        ("enforcementMechanism", "enforcement_mechanism"),
        ("violation_message", "violation_message"),
        ("violationMessage", "violation_message"),
    ):
        value = data.get(source_key)
        if value is not None and target_key not in payload_for_model:
            payload_for_model[target_key] = value
    try:
        return ConstraintCreate(**payload_for_model)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.errors(),
        ) from exc


# ── Auth helpers ──────────────────────────────────────────────────────────────


@router.get("/process_definition", response_model=PaginatedProcessConstraintsResponse)
async def list_process_constraints(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Return a paginated list of all ProcessConstraints containers."""
    results, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedProcessConstraintsResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=results,
    )


# ── Filtered reads ────────────────────────────────────────────────────────────


@router.get(
    "/process_definition/by-process_definition/{process_definition_id}",
    response_model=ProcessConstraintsResponse,
)
async def get_process_constraints_by_process_definition(
    process_definition_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Fetch the constraints container that belongs to the given process_definition."""
    record = await crud.get_by_process_definition_id(db, process_definition_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No ProcessConstraints found for process_definition '{process_definition_id}'",
        )
    return record


@router.get(
    "/process_definition/{constraints_id}/constraints/by-type/{constraint_type}",
    response_model=list[ConstraintResponse],
)
async def list_process_constraints_by_type(
    constraints_id: str,
    constraint_type: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Return all constraints within a container that match the given constraint_type."""
    return await crud.get_constraints_by_type(db, constraints_id, constraint_type)


@router.get(
    "/process_definition/{constraints_id}/constraints/by-severity/{severity}",
    response_model=list[ConstraintResponse],
)
async def list_process_constraints_by_severity(
    constraints_id: str,
    severity: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Return all constraints within a container that match the given severity."""
    return await crud.get_constraints_by_severity(db, constraints_id, severity)


@router.get(
    "/process_definition/{constraints_id}/constraints/hard",
    response_model=list[ConstraintResponse],
)
async def list_process_definition_hard_constraints(
    constraints_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Return only hard constraints (is_hard_constraint=True) from a container."""
    return await crud.get_hard_constraints(db, constraints_id)


# ── Single record ─────────────────────────────────────────────────────────────


@router.get(
    "/process_definition/{constraints_id}", response_model=ProcessConstraintsResponse
)
async def get_process_constraints(
    constraints_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Fetch a single ProcessConstraints container by its id."""
    record = await crud.get_by_id(db, constraints_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessConstraints '{constraints_id}' not found",
        )
    return record


# ── Create ────────────────────────────────────────────────────────────────────


@router.post(
    "/process_definition",
    response_model=ProcessConstraintsResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_process_constraints(
    data: ProcessConstraintsCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-constraints")),
):
    """Create a new ProcessConstraints container. Returns 409 if id already exists."""
    if await crud.get_by_id(db, data.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"ProcessConstraints '{data.id}' already exists",
        )
    return await crud.create(db, data)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch(
    "/process_definition/{constraints_id}", response_model=ProcessConstraintsResponse
)
async def update_process_constraints(
    constraints_id: str,
    data: ProcessConstraintsUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-constraints")),
):
    """Partially update a ProcessConstraints container."""
    updated = await crud.update(db, constraints_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessConstraints '{constraints_id}' not found",
        )
    return updated


# ── Constraint mutations ──────────────────────────────────────────────────────


@router.post(
    "/process_definition/{constraints_id}/constraints",
    response_model=ProcessConstraintsResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_process_definition_constraint(
    constraints_id: str,
    data: ConstraintCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-constraints")),
):
    """Append a new constraint to a ProcessConstraints container."""
    updated = await crud.add_constraint(db, constraints_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessConstraints '{constraints_id}' not found",
        )
    return updated


@router.patch(
    "/process_definition/{constraints_id}/constraints/{constraint_id}",
    response_model=ProcessConstraintsResponse,
)
async def update_process_definition_constraint(
    constraints_id: str,
    constraint_id: str,
    fields: dict = Body(...),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-constraints")),
):
    """Update specific fields of a constraint inside a ProcessConstraints container."""
    updated = await crud.update_constraint(db, constraints_id, constraint_id, fields)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Constraint '{constraint_id}' not found in ProcessConstraints '{constraints_id}'",
        )
    return updated


@router.delete(
    "/process_definition/{constraints_id}/constraints/{constraint_id}",
    response_model=ProcessConstraintsResponse,
)
async def remove_process_definition_constraint(
    constraints_id: str,
    constraint_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-constraints")),
):
    """Remove a specific constraint from a ProcessConstraints container."""
    updated = await crud.remove_constraint(db, constraints_id, constraint_id)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Constraint '{constraint_id}' not found in ProcessConstraints '{constraints_id}'",
        )
    return updated


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete(
    "/process_definition/by-process_definition/{process_definition_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_process_constraints_by_process_definition(
    process_definition_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-constraints")),
):
    """Cascade-delete the constraints container that belongs to the given process_definition."""
    if not await crud.delete_by_process_definition_id(db, process_definition_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No ProcessConstraints found for process_definition '{process_definition_id}'",
        )


@router.delete(
    "/process_definition/{constraints_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_process_constraints(
    constraints_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-constraints")),
):
    """Delete a ProcessConstraints container by its id."""
    if not await crud.delete(db, constraints_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessConstraints '{constraints_id}' not found",
        )


# ============================================================================
# ProcessStepConstraints  — /steps
# ============================================================================

# ── Filtered reads ────────────────────────────────────────────────────────────


@router.get("/steps/by-step/{step_id}", response_model=ProcessStepConstraintsResponse)
async def get_step_constraints_by_step(
    step_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Fetch the constraints container that belongs to the given process_definition step."""
    record = await crud.get_step_constraints_by_step_id(db, step_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No ProcessStepConstraints found for step '{step_id}'",
        )
    return record


@router.post(
    "/steps/by-step/{step_id}/constraints",
    response_model=ProcessStepConstraintsResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_step_constraint_by_step(
    step_id: str,
    data: dict | None = Body(default=None),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-constraints")),
):
    """Append or replace a constraint using the owning process_definition step id."""
    constraint = _constraint_from_canvas_payload(data, step_id)
    return await crud.upsert_step_constraint_by_step_id(db, step_id, constraint)


@router.patch(
    "/steps/by-step/{step_id}/constraints/{constraint_id}",
    response_model=ProcessStepConstraintsResponse,
)
async def update_step_constraint_by_step(
    step_id: str,
    constraint_id: str,
    fields: dict | None = Body(default=None),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-constraints")),
):
    """Create/update a constraint by process_definition step id and child constraint id."""
    constraint = _constraint_from_canvas_payload(fields, step_id, constraint_id)
    return await crud.upsert_step_constraint_by_step_id(db, step_id, constraint)


@router.get(
    "/steps/by-process_definition/{process_definition_id}",
    response_model=list[ProcessStepConstraintsResponse],
)
async def list_step_constraints_by_process_definition(
    process_definition_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Return all step-level constraints containers that share the given process_definition_id."""
    return await crud.get_step_constraints_by_process_definition_id(
        db, process_definition_id
    )


@router.get(
    "/steps/{constraints_id}/constraints/by-type/{constraint_type}",
    response_model=list[ConstraintResponse],
)
async def list_step_constraints_by_type(
    constraints_id: str,
    constraint_type: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Return all step-level constraints that match the given constraint_type."""
    return await crud.get_step_constraints_by_type(db, constraints_id, constraint_type)


@router.get(
    "/steps/{constraints_id}/constraints/hard", response_model=list[ConstraintResponse]
)
async def list_step_hard_constraints(
    constraints_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Return only hard constraints from a ProcessStepConstraints container."""
    return await crud.get_step_hard_constraints(db, constraints_id)


# ── Single record ─────────────────────────────────────────────────────────────


@router.get("/steps/{constraints_id}", response_model=ProcessStepConstraintsResponse)
async def get_step_constraints(
    constraints_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    """Fetch a single ProcessStepConstraints container by its id."""
    record = await crud.get_step_constraints_by_id(db, constraints_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessStepConstraints '{constraints_id}' not found",
        )
    return record


# ── Create ────────────────────────────────────────────────────────────────────


@router.post(
    "/steps",
    response_model=ProcessStepConstraintsResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_step_constraints(
    data: ProcessStepConstraintsCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-constraints")),
):
    """Create a new ProcessStepConstraints container. Returns 409 if id already exists."""
    if await crud.get_step_constraints_by_id(db, data.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"ProcessStepConstraints '{data.id}' already exists",
        )
    return await crud.create_step_constraints(db, data)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/steps/{constraints_id}", response_model=ProcessStepConstraintsResponse)
async def update_step_constraints(
    constraints_id: str,
    data: ProcessStepConstraintsUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-constraints")),
):
    """Partially update a ProcessStepConstraints container."""
    updated = await crud.update_step_constraints(db, constraints_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessStepConstraints '{constraints_id}' not found",
        )
    return updated


# ── Constraint mutations ──────────────────────────────────────────────────────


@router.post(
    "/steps/{constraints_id}/constraints",
    response_model=ProcessStepConstraintsResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_step_constraint(
    constraints_id: str,
    data: ConstraintCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-constraints")),
):
    """Append a new constraint to a ProcessStepConstraints container."""
    updated = await crud.add_step_constraint(db, constraints_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessStepConstraints '{constraints_id}' not found",
        )
    return updated


@router.patch(
    "/steps/{constraints_id}/constraints/{constraint_id}",
    response_model=ProcessStepConstraintsResponse,
)
async def update_step_constraint(
    constraints_id: str,
    constraint_id: str,
    fields: dict = Body(...),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-constraints")),
):
    """Update specific fields of a constraint inside a ProcessStepConstraints container."""
    updated = await crud.update_step_constraint(
        db, constraints_id, constraint_id, fields
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Constraint '{constraint_id}' not found in ProcessStepConstraints '{constraints_id}'",
        )
    return updated


@router.delete(
    "/steps/{constraints_id}/constraints/{constraint_id}",
    response_model=ProcessStepConstraintsResponse,
)
async def remove_step_constraint(
    constraints_id: str,
    constraint_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-constraints")),
):
    """Remove a specific constraint from a ProcessStepConstraints container."""
    updated = await crud.remove_step_constraint(db, constraints_id, constraint_id)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Constraint '{constraint_id}' not found in ProcessStepConstraints '{constraints_id}'",
        )
    return updated


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete("/steps/by-step/{step_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_step_constraints_by_step(
    step_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-constraints")),
):
    """Cascade-delete the constraints container that belongs to the given step."""
    if not await crud.delete_step_constraints_by_step_id(db, step_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No ProcessStepConstraints found for step '{step_id}'",
        )


@router.delete("/steps/{constraints_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_step_constraints(
    constraints_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-constraints")),
):
    """Delete a ProcessStepConstraints container by its id."""
    if not await crud.delete_step_constraints(db, constraints_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ProcessStepConstraints '{constraints_id}' not found",
        )
