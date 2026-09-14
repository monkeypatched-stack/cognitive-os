from fastapi import APIRouter, HTTPException, Depends, Query, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from motor.motor_asyncio import AsyncIOMotorDatabase
from jose import JWTError

from services.common.auth import permission_ids
from services.common.db import get_database
from services.auth.helpers.tokens import decode_access_token
from services.pm.models.sop import (
    SOPCreate,
    SOPUpdate,
    SOPResponse,
    PaginatedSOPResponse,
    SOPChangeImpactRequest,
    SOPChangeImpactResponse,
)
from services.pm.helpers import sop as crud
from services.pm.helpers.sop_cdc import sync_sop_to_process_definition
from services.process_definitions.models.process_definition import (
    ProcessDefinitionCanvasFlowSimulationRequest,
    ProcessDefinitionCanvasFlowSimulationResponse,
)
from services.process_definitions.node_services.flow_simulator import simulate_sop_flow
from services.common.auth import get_current_user, require_permission  # consolidated

router = APIRouter()

bearer_scheme = HTTPBearer()


# ── Auth helpers ──────────────────────────────────────────────────────────────


@router.get("/", response_model=PaginatedSOPResponse)
async def list_sops(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-sops")),
):
    """Return a paginated list of all SOPs."""
    sops, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedSOPResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=sops,
    )


# ── Filtered list endpoints ───────────────────────────────────────────────────


@router.get("/by-author/{authored_by}", response_model=list[SOPResponse])
async def list_sops_by_author(
    authored_by: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-sops")),
):
    """Return all SOPs created by the given author."""
    return await crud.get_by_author(db, authored_by)


@router.get("/by-approver/{approved_by}", response_model=list[SOPResponse])
async def list_sops_by_approver(
    approved_by: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-sops")),
):
    """Return all SOPs approved by the given approver."""
    return await crud.get_by_approver(db, approved_by)


@router.get("/by-tag/{tag}", response_model=list[SOPResponse])
async def list_sops_by_tag(
    tag: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-sops")),
):
    """Return all SOPs that carry the given tag."""
    return await crud.get_by_tag(db, tag)


@router.get("/by-version/{version}", response_model=PaginatedSOPResponse)
async def list_sops_by_version(
    version: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-sops")),
):
    """Return a paginated list of SOPs matching the given version string."""
    sops, total = await crud.get_by_version(db, version, page=page, page_size=page_size)
    return PaginatedSOPResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=sops,
    )


@router.get(
    "/by-process_definition/{process_definition_id}", response_model=SOPResponse
)
async def get_sop_by_process_definition(
    process_definition_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-sops")),
):
    """Fetch the SOP that wraps the given process_definition_id."""
    record = await crud.get_by_process_definition_id(db, process_definition_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No SOP found for process_definition '{process_definition_id}'",
        )
    return record


@router.get("/search", response_model=list[SOPResponse])
async def search_sops(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-sops")),
):
    """Search SOP nodes by free text across SOP and embedded process sections."""
    return await crud.search(db, q, limit=limit)


@router.post("/{sop_id}/change-impact", response_model=SOPChangeImpactResponse)
async def analyze_sop_change_impact(
    sop_id: str,
    data: SOPChangeImpactRequest,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(require_permission("perm-update-sops")),
):
    """
    Analyze a proposed SOP update against production connections/material flow.

    The response contains the linked SOP graph and proposed changes. When
    create_change_control is true, those changes are staged as proposed nodes
    and only apply after the normal change-control approval flow.
    """
    impact = await crud.analyze_sop_change_impact(
        db,
        sop_id,
        data.proposed_values,
        change_reason=data.change_reason,
        include_upstream=data.include_upstream,
        include_downstream=data.include_downstream,
        max_depth=data.max_depth,
    )
    if not impact:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SOP '{sop_id}' not found",
        )

    change_control = None
    if data.create_change_control:
        change_control = await crud.create_sop_change_control(
            db,
            impact,
            current_user=current_user,
            change_type=data.change_type,
            risk_score=data.risk_score,
            approval_chain=data.approval_chain,
        )
    return SOPChangeImpactResponse(**impact, change_control=change_control)


# ── Single record ─────────────────────────────────────────────────────────────


@router.get("/{sop_id}", response_model=SOPResponse)
async def get_sop(
    sop_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-sops")),
):
    """Fetch a single SOP by its unique id."""
    record = await crud.get_by_id(db, sop_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SOP '{sop_id}' not found",
        )
    return record


# ── Create ────────────────────────────────────────────────────────────────────


@router.post("/", response_model=SOPResponse, status_code=status.HTTP_201_CREATED)
async def create_sop(
    data: SOPCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-sops")),
):
    """Register a new SOP. Returns 409 if the SOP id already exists."""
    if await crud.get_by_id(db, data.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"SOP '{data.id}' already exists",
        )
    result = await crud.create(db, data)
    try:
        await sync_sop_to_process_definition(db, result)
    except Exception:
        pass
    return result


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/{sop_id}", response_model=SOPResponse)
async def update_sop(
    sop_id: str,
    data: SOPUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-sops")),
):
    """Partially update a SOP's fields. Returns 404 if the SOP does not exist."""
    updated = await crud.update(db, sop_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SOP '{sop_id}' not found",
        )
    try:
        await sync_sop_to_process_definition(db, updated)
    except Exception:
        pass
    return updated


@router.patch("/{sop_id}/process_definition", response_model=SOPResponse)
async def update_sop_process_definition(
    sop_id: str,
    process_definition: dict,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-sops")),
):
    """Replace the embedded process_definition sub-document of a SOP."""
    updated = await crud.update_process_definition(db, sop_id, process_definition)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SOP '{sop_id}' not found",
        )
    try:
        await sync_sop_to_process_definition(db, updated)
    except Exception:
        pass
    return updated


@router.patch("/{sop_id}/prechecks", response_model=SOPResponse)
async def update_sop_prechecks(
    sop_id: str,
    prechecks: dict,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-sops")),
):
    """Replace the embedded prechecks sub-document of a SOP."""
    updated = await crud.update_prechecks(db, sop_id, prechecks)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SOP '{sop_id}' not found",
        )
    try:
        await sync_sop_to_process_definition(db, updated)
    except Exception:
        pass
    return updated


@router.patch("/{sop_id}/postchecks", response_model=SOPResponse)
async def update_sop_postchecks(
    sop_id: str,
    postchecks: dict,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-sops")),
):
    """Replace the embedded postchecks sub-document of a SOP."""
    updated = await crud.update_postchecks(db, sop_id, postchecks)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SOP '{sop_id}' not found",
        )
    try:
        await sync_sop_to_process_definition(db, updated)
    except Exception:
        pass
    return updated


@router.patch("/{sop_id}/constraints", response_model=SOPResponse)
async def update_sop_constraints(
    sop_id: str,
    constraints: dict,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-sops")),
):
    """Replace the embedded constraints sub-document of a SOP."""
    updated = await crud.update_constraints(db, sop_id, constraints)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SOP '{sop_id}' not found",
        )
    try:
        await sync_sop_to_process_definition(db, updated)
    except Exception:
        pass
    return updated


@router.patch("/{sop_id}/corrective-actions", response_model=SOPResponse)
async def update_sop_corrective_actions(
    sop_id: str,
    corrective_actions: dict,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-sops")),
):
    """Replace the embedded corrective_actions sub-document of a SOP."""
    updated = await crud.update_corrective_actions(db, sop_id, corrective_actions)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SOP '{sop_id}' not found",
        )
    try:
        await sync_sop_to_process_definition(db, updated)
    except Exception:
        pass
    return updated


@router.post(
    "/{sop_id}/simulate", response_model=ProcessDefinitionCanvasFlowSimulationResponse
)
async def simulate_sop_process_flow(
    sop_id: str,
    data: ProcessDefinitionCanvasFlowSimulationRequest,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-sops")),
):
    """Compile a SOP's process sections into a transient canvas and simulate it."""
    record = await crud.get_by_id(db, sop_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SOP '{sop_id}' not found",
        )
    result = await simulate_sop_flow(
        db=db,
        sop=record,
        payload=data.payload,
        start_node_id=data.start_node_id,
        max_steps=data.max_steps,
        persist_trace=data.persist_trace,
    )
    return ProcessDefinitionCanvasFlowSimulationResponse(**result)


# ── References ────────────────────────────────────────────────────────────────


@router.post("/{sop_id}/references", response_model=SOPResponse)
async def add_sop_reference(
    sop_id: str,
    reference: str = Query(...),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-sops")),
):
    """Append a reference URL/document to the SOP's references array (no duplicates)."""
    updated = await crud.add_reference(db, sop_id, reference)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SOP '{sop_id}' not found",
        )
    return updated


@router.delete("/{sop_id}/references", response_model=SOPResponse)
async def remove_sop_reference(
    sop_id: str,
    reference: str = Query(...),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-sops")),
):
    """Remove a specific reference from the SOP's references array."""
    updated = await crud.remove_reference(db, sop_id, reference)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SOP '{sop_id}' not found",
        )
    return updated


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete("/{sop_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sop(
    sop_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-sops")),
):
    """Permanently remove a SOP record. Returns 404 if the SOP does not exist."""
    if not await crud.delete(db, sop_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SOP '{sop_id}' not found",
        )
