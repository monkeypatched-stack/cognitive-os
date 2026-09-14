from fastapi import APIRouter, HTTPException, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.common.auth import require_permission
from services.auth.models.teams import (
    TeamCreate,
    TeamUpdate,
    TeamResponse,
    PaginatedTeamResponse,
)
from services.auth.helpers import teams as crud

router = APIRouter()


# ── List ──────────────────────────────────────────────────────────────────────

# ── List (with filters) ──────────────────────────────────────────────────────


@router.get("/", response_model=PaginatedTeamResponse)
async def list_teams(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    department_id: str | None = Query(None),
    search: str | None = Query(None),
    sort_desc: bool = Query(True),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-teams")),
):
    teams, total = await crud.get_all(
        db,
        page=page,
        page_size=page_size,
        department_id=department_id,
        search=search,
        sort_desc=sort_desc,
    )

    return PaginatedTeamResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=teams,
    )


# ── Get by department ─────────────────────────────────────────────────────────


@router.get("/by-department/{department_id}", response_model=list[TeamResponse])
async def list_teams_by_department(
    department_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-teams")),
):
    return await crud.get_by_department(db, department_id)


# ── Get one ───────────────────────────────────────────────────────────────────


@router.get("/{team_id}", response_model=TeamResponse)
async def get_team(
    team_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-teams")),
):
    record = await crud.get_by_id(db, team_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Team '{team_id}' not found"
        )
    return record


# ── Create ────────────────────────────────────────────────────────────────────


@router.post("/", response_model=TeamResponse, status_code=status.HTTP_201_CREATED)
async def create_team(
    data: TeamCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-teams")),
):
    if await crud.get_by_id(db, data.team_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"Team '{data.team_id}' already exists"
        )
    return await crud.create(db, data)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/{team_id}", response_model=TeamResponse)
async def update_team(
    team_id: str,
    data: TeamUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-teams")),
):
    updated = await crud.update(db, team_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Team '{team_id}' not found"
        )
    return updated


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete("/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_team(
    team_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-teams")),
):
    if not await crud.delete(db, team_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Team '{team_id}' not found"
        )


# TODO_ENDPOINT: GET /api/v1/teams/{team_id}/users — list all users in a team
