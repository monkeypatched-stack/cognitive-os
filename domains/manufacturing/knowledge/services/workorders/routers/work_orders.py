from fastapi import APIRouter, HTTPException, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.common.auth import require_permission
from services.workorders.models.work_orders import (
    WorkOrderCreate,
    WorkOrderSubtaskCreate,
    WorkOrderSubtaskUpdate,
    WorkOrderUpdate,
    WorkOrderResponse,
    PaginatedWorkOrderResponse,
    WorkOrderCountStatsResponse,
)
from services.workorders.helpers import work_orders as crud

router = APIRouter()

SUBTASK_CREATOR_ROLES = {
    "admin",
    "worker",
    "project-manager",
    "project_manager",
    "project manager",
}


def _ensure_subtask_creator(current_user: dict) -> None:
    role = str(current_user.get("role", "")).lower()
    if role not in SUBTASK_CREATOR_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workers and project managers can create linked work orders under a work order",
        )


# ── List ──────────────────────────────────────────────────────────────────────


@router.get("/", response_model=PaginatedWorkOrderResponse)
async def list_work_orders(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-tasks")),
):
    work_orders, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedWorkOrderResponse(
        total=total, page=page, page_size=page_size, results=work_orders
    )


# ── Filters ───────────────────────────────────────────────────────────────────


@router.get("/by-status/{status}", response_model=list[WorkOrderResponse])
async def list_work_orders_by_status(
    status: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-tasks")),
):
    return await crud.get_by_status(db, status)


@router.get("/by-priority/{priority}", response_model=list[WorkOrderResponse])
async def list_work_orders_by_priority(
    priority: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-tasks")),
):
    return await crud.get_by_priority(db, priority)


@router.get("/by-machine/{machine_id}", response_model=list[WorkOrderResponse])
async def list_work_orders_by_machine(
    machine_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-tasks")),
):
    return await crud.get_by_machine(db, machine_id)


@router.get("/by-equipment/{equipment_id}", response_model=list[WorkOrderResponse])
async def list_work_orders_by_equipment(
    equipment_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-tasks")),
):
    return await crud.get_by_equipment_id(db, equipment_id)


@router.get("/by-line/{line_id}", response_model=list[WorkOrderResponse])
async def list_work_orders_by_line(
    line_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-tasks")),
):
    return await crud.get_by_line(db, line_id)


@router.get("/by-circuit/{circuit_id}", response_model=list[WorkOrderResponse])
async def list_work_orders_by_circuit(
    circuit_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-tasks")),
):
    return await crud.get_by_circuit(db, circuit_id)


@router.get("/by-team/{team_id}", response_model=list[WorkOrderResponse])
async def list_work_orders_by_team(
    team_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-tasks")),
):
    return await crud.get_by_team(db, team_id)


@router.get("/overdue", response_model=list[WorkOrderResponse])
async def list_overdue_work_orders(
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-tasks")),
):
    return await crud.get_overdue(db)


@router.get("/by-user/{user_id}", response_model=list[WorkOrderResponse])
async def list_work_orders_by_user(
    user_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-tasks")),
):
    return await crud.get_by_user(db, user_id)


@router.get("/by-parent/{parent_work_order_id}", response_model=list[WorkOrderResponse])
async def list_work_orders_by_parent(
    parent_work_order_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-tasks")),
):
    return await crud.get_subtasks(db, parent_work_order_id)


@router.get("/stats/counts", response_model=WorkOrderCountStatsResponse)
async def get_work_order_count_stats(
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-tasks")),
):
    return await crud.get_count_stats(db)


# ── Get one ───────────────────────────────────────────────────────────────────


@router.get("/{work_order_id}", response_model=WorkOrderResponse)
async def get_work_order(
    work_order_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-tasks")),
):
    record = await crud.get_by_id(db, work_order_id)
    if not record:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Work order '{work_order_id}' not found"
        )
    return record


# ── Create ────────────────────────────────────────────────────────────────────


@router.post("/", response_model=WorkOrderResponse, status_code=status.HTTP_201_CREATED)
async def create_work_order(
    data: WorkOrderCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-tasks")),
):
    if await crud.get_by_id(db, data.work_order_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Work order '{data.work_order_id}' already exists",
        )
    return await crud.create(db, data)


# ── Update ────────────────────────────────────────────────────────────────────


@router.patch("/{work_order_id}", response_model=WorkOrderResponse)
async def update_work_order(
    work_order_id: str,
    data: WorkOrderUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-tasks")),
):
    updated = await crud.update(db, work_order_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Work order '{work_order_id}' not found"
        )
    return updated


@router.get("/{work_order_id}/subtasks", response_model=list[WorkOrderResponse])
async def list_work_order_subtasks(
    work_order_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-tasks")),
):
    if not await crud.get_by_id(db, work_order_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Work order '{work_order_id}' not found"
        )
    return await crud.get_subtasks(db, work_order_id)


@router.post(
    "/{work_order_id}/subtasks",
    response_model=WorkOrderResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_work_order_subtask(
    work_order_id: str,
    data: WorkOrderSubtaskCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: dict = Depends(require_permission("perm-update-tasks")),
):
    _ensure_subtask_creator(current_user)
    if await crud.get_by_id(db, data.work_order_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Work order '{data.work_order_id}' already exists",
        )
    if data.created_by is None:
        data.created_by = current_user.get("sub")
    created = await crud.add_subtask(db, work_order_id, data)
    if not created:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Work order '{work_order_id}' not found"
        )
    return created


@router.patch(
    "/{work_order_id}/subtasks/{subtask_work_order_id}",
    response_model=WorkOrderResponse,
)
async def update_work_order_subtask(
    work_order_id: str,
    subtask_work_order_id: str,
    data: WorkOrderSubtaskUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-tasks")),
):
    updated = await crud.update_subtask(db, work_order_id, subtask_work_order_id, data)
    if not updated:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Linked work order '{subtask_work_order_id}' not found under work order '{work_order_id}'",
        )
    return updated


@router.delete(
    "/{work_order_id}/subtasks/{subtask_work_order_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_work_order_subtask(
    work_order_id: str,
    subtask_work_order_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-tasks")),
):
    if not await crud.remove_subtask(db, work_order_id, subtask_work_order_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Linked work order '{subtask_work_order_id}' not found under work order '{work_order_id}'",
        )


# ── Delete ────────────────────────────────────────────────────────────────────


@router.delete("/{work_order_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_work_order(
    work_order_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-tasks")),
):
    if not await crud.delete(db, work_order_id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Work order '{work_order_id}' not found"
        )
