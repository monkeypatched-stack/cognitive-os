from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.assets.helpers import tools as crud
from services.common.auth import require_permission
from services.assets.models.tools import (
    AssignmentScope,
    PaginatedToolResponse,
    ToolCategory,
    ToolCreate,
    ToolRecord,
    ToolStatus,
    ToolUpdate,
)

router = APIRouter()


@router.get("", response_model=PaginatedToolResponse)
@router.get("/", response_model=PaginatedToolResponse, include_in_schema=False)
async def list_tools(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    category: Optional[ToolCategory] = Query(None),
    status_filter: Optional[ToolStatus] = Query(None, alias="status"),
    assignment_scope: Optional[AssignmentScope] = Query(None),
    assigned_to_id: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-tools")),
):
    tools, total = await crud.get_all(
        db,
        page=page,
        page_size=page_size,
        category=category,
        status=status_filter,
        assignment_scope=assignment_scope,
        assigned_to_id=assigned_to_id,
        q=q,
    )
    return PaginatedToolResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=tools,
    )


@router.get("/{tool_id}", response_model=ToolRecord)
async def get_tool(
    tool_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-tools")),
):
    record = await crud.get_by_id(db, tool_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool '{tool_id}' not found",
        )
    return record


@router.post("", response_model=ToolRecord, status_code=status.HTTP_201_CREATED)
@router.post(
    "/",
    response_model=ToolRecord,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
async def create_tool(
    data: ToolCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-tools")),
):
    if await crud.get_by_id(db, data.tool_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"Team '{data.tool_id}' already exists"
        )
    return await crud.create(db, data)


@router.patch("/{tool_id}", response_model=ToolRecord)
async def update_tool(
    tool_id: str,
    data: ToolUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-tools")),
):
    if data.tool_code:
        existing_code = await crud.get_by_code(db, data.tool_code)
        if existing_code and existing_code["tool_id"] != tool_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Tool code '{data.tool_code}' already exists",
            )

    updated = await crud.update(db, tool_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool '{tool_id}' not found",
        )
    return updated


@router.delete("/{tool_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tool(
    tool_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-tools")),
):
    if not await crud.delete(db, tool_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool '{tool_id}' not found",
        )
