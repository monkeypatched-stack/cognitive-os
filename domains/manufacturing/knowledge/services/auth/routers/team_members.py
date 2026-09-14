from fastapi import APIRouter, HTTPException, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from typing import List

from services.common.db import get_database
from services.common.auth import require_permission

from services.auth.models.teamMembers import (
    TeamMemberCreate,
    TeamMemberUpdate,
    TeamMemberResponse,
)

from services.auth.helpers import team_members as crud

router = APIRouter()


# ── List (with filters) ──────────────────────────────────────────────────────
@router.get("/", response_model=dict)
async def list_team_members(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    team_id: str | None = Query(None),
    search: str | None = Query(None),
    is_active: bool | None = Query(None),
    sort_desc: bool = Query(False),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-teams")),  # reuse same permission
):
    items, total = await crud.get_all(
        db,
        page=page,
        page_size=page_size,
        team_id=team_id,
        search=search,
        is_active=is_active,
        sort_desc=sort_desc,
    )

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "results": items,
    }


# ── Get members by team ──────────────────────────────────────────────────────
@router.get("/teams/{team_id}/", response_model=List[TeamMemberResponse])
async def get_members_by_team(
    team_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-teams")),
):
    return await crud.get_by_team(db, team_id)


# ── Get single member ────────────────────────────────────────────────────────
@router.get("/teams/{team_id}/{user_id}", response_model=TeamMemberResponse)
async def get_member(
    team_id: str,
    user_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-teams")),
):
    record = await crud.get_member(db, team_id, user_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Member '{user_id}' not found in team '{team_id}'",
        )
    return record


# ── Add member ───────────────────────────────────────────────────────────────
@router.post(
    "/teams/{team_id}/",
    response_model=TeamMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_member(
    team_id: str,
    data: TeamMemberCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(
        require_permission("perm-update-teams")
    ),  # adding = modifying team
):
    # prevent duplicate
    existing = await crud.get_member(db, team_id, data.user_id)
    if existing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"User '{data.user_id}' already in team '{team_id}'",
        )

    return await crud.create(db, team_id, data)


# ── Bulk add members (for dialog) ────────────────────────────────────────────
@router.post("/teams/{team_id}/bulk", response_model=List[TeamMemberResponse])
async def bulk_add_members(
    team_id: str,
    data: List[TeamMemberCreate],
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-teams")),
):
    return await crud.bulk_create(db, team_id, data)


# ── Update member ────────────────────────────────────────────────────────────
@router.patch("/teams/{team_id}/{user_id}", response_model=TeamMemberResponse)
async def update_member(
    team_id: str,
    user_id: str,
    data: TeamMemberUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-teams")),
):
    updated = await crud.update(db, team_id, user_id, data)

    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Member '{user_id}' not found in team '{team_id}'",
        )

    return updated


# ── Remove member ────────────────────────────────────────────────────────────
@router.delete("/teams/{team_id}/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    team_id: str,
    user_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-teams")),
):
    success = await crud.delete(db, team_id, user_id)

    if not success:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Member '{user_id}' not found in team '{team_id}'",
        )


# ── Delete all members of a team (optional) ───────────────────────────────────
@router.delete("/teams/{team_id}/", status_code=status.HTTP_204_NO_CONTENT)
async def remove_all_members(
    team_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-teams")),
):
    await crud.delete_by_team(db, team_id)
