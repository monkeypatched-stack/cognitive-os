from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.db import get_database
from services.common.auth import require_permission
from services.pm.helpers import kanban_boards as crud
from services.workorders.helpers import work_orders as work_order_crud
from services.pm.models.kanban_board import (
    KanbanBoardCreate,
    KanbanBoardResponse,
    KanbanBoardUpdate,
    PaginatedKanbanBoardResponse,
    WORK_ORDER_KANBAN_COLUMNS,
    WorkOrderKanbanBoardResponse,
    WorkOrderKanbanColumn,
    WorkOrderKanbanMove,
)
from services.workorders.models.work_orders import (
    WorkOrderResponse,
    WorkOrderSubtaskCreate,
    WorkOrderSubtaskUpdate,
    WorkOrderUpdate,
)

router = APIRouter()

SUBTASK_CREATOR_ROLES = {
    "admin",
    "worker",
    "project-manager",
    "project_manager",
    "project manager",
}


def _clean_filter(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if cleaned.lower().startswith("kanban ") and cleaned.lower().endswith(" id"):
        return None
    return cleaned


def _ensure_subtask_creator(current_user: dict) -> None:
    role = str(current_user.get("role", "")).lower()
    if role not in SUBTASK_CREATOR_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only workers and project managers can create subtasks under a work order",
        )


def _work_order_board_response(work_orders: list[dict]) -> WorkOrderKanbanBoardResponse:
    grouped = {status_value: [] for status_value in WORK_ORDER_KANBAN_COLUMNS}
    for work_order in work_orders:
        status_value = work_order.get("status", "Todo")
        grouped.setdefault(status_value, []).append(work_order)
    return WorkOrderKanbanBoardResponse(
        columns=[
            WorkOrderKanbanColumn(
                status=status_value,
                title=status_value,
                work_orders=grouped.get(status_value, []),
            )
            for status_value in WORK_ORDER_KANBAN_COLUMNS
        ]
    )


@router.get("/", response_model=PaginatedKanbanBoardResponse)
async def list_kanban_boards(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-kanban-boards")),
):
    boards, total = await crud.get_all(db, page=page, page_size=page_size)
    return PaginatedKanbanBoardResponse(
        total=total,
        page=page,
        page_size=page_size,
        results=boards,
    )


@router.get("/work-orders", response_model=WorkOrderKanbanBoardResponse)
async def get_work_order_kanban_board(
    line_id: str | None = Query(None),
    circuit_id: str | None = Query(None),
    workstation_id: str | None = Query(None),
    machine_id: str | None = Query(None),
    equipment_id: str | None = Query(None),
    team_id: str | None = Query(None),
    assigned_to: str | None = Query(None),
    parent_work_order_id: str | None = Query(None),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-tasks")),
):
    work_orders = await work_order_crud.get_for_kanban(
        db,
        line_id=_clean_filter(line_id),
        circuit_id=_clean_filter(circuit_id),
        workstation_id=_clean_filter(workstation_id),
        machine_id=_clean_filter(machine_id),
        equipment_id=_clean_filter(equipment_id),
        team_id=_clean_filter(team_id),
        assigned_to=_clean_filter(assigned_to),
        parent_work_order_id=_clean_filter(parent_work_order_id),
    )
    return _work_order_board_response(work_orders)


@router.get(
    "/work-orders/{work_order_id}/subtasks", response_model=list[WorkOrderResponse]
)
async def list_work_order_subtasks(
    work_order_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-tasks")),
):
    if not await work_order_crud.get_by_id(db, work_order_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Work order '{work_order_id}' not found",
        )
    return await work_order_crud.get_subtasks(db, work_order_id)


@router.patch("/work-orders/{work_order_id}/move", response_model=WorkOrderResponse)
async def move_work_order_on_kanban_board(
    work_order_id: str,
    data: WorkOrderKanbanMove,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-tasks")),
):
    updated = await work_order_crud.update(
        db, work_order_id, WorkOrderUpdate(status=data.status)
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Work order '{work_order_id}' not found",
        )
    return updated


@router.post(
    "/work-orders/{work_order_id}/subtasks",
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
    if await work_order_crud.get_by_id(db, data.work_order_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Work order '{data.work_order_id}' already exists",
        )
    if data.created_by is None:
        data.created_by = current_user.get("sub")
    updated = await work_order_crud.add_subtask(db, work_order_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Work order '{work_order_id}' not found",
        )
    return updated


@router.patch(
    "/work-orders/{work_order_id}/subtasks/{subtask_work_order_id}",
    response_model=WorkOrderResponse,
)
async def update_work_order_subtask(
    work_order_id: str,
    subtask_work_order_id: str,
    data: WorkOrderSubtaskUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-tasks")),
):
    updated = await work_order_crud.update_subtask(
        db, work_order_id, subtask_work_order_id, data
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Linked work order '{subtask_work_order_id}' not found under work order '{work_order_id}'",
        )
    return updated


@router.delete(
    "/work-orders/{work_order_id}/subtasks/{subtask_work_order_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_work_order_subtask(
    work_order_id: str,
    subtask_work_order_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-tasks")),
):
    if not await work_order_crud.remove_subtask(
        db, work_order_id, subtask_work_order_id
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Linked work order '{subtask_work_order_id}' not found under work order '{work_order_id}'",
        )


@router.get(
    "/by-workstation/{workstation_id}", response_model=list[KanbanBoardResponse]
)
async def list_kanban_boards_by_workstation(
    workstation_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-kanban-boards")),
):
    return await crud.get_by_workstation(db, workstation_id)


@router.get("/by-line/{line_id}", response_model=list[KanbanBoardResponse])
async def list_kanban_boards_by_line(
    line_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-kanban-boards")),
):
    return await crud.get_by_line(db, line_id)


@router.get("/by-stage/{stage_id}", response_model=list[KanbanBoardResponse])
async def list_kanban_boards_by_stage(
    stage_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-kanban-boards")),
):
    return await crud.get_by_stage(db, stage_id)


@router.get("/{board_id}", response_model=KanbanBoardResponse)
async def get_kanban_board(
    board_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-kanban-boards")),
):
    record = await crud.get_by_id(db, board_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Kanban board '{board_id}' not found",
        )
    return record


@router.post(
    "/", response_model=KanbanBoardResponse, status_code=status.HTTP_201_CREATED
)
async def create_kanban_board(
    data: KanbanBoardCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-kanban-boards")),
):
    if await crud.get_by_id(db, str(data.board_id)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Kanban board '{data.board_id}' already exists",
        )
    return await crud.create(db, data)


@router.patch("/{board_id}", response_model=KanbanBoardResponse)
async def update_kanban_board(
    board_id: str,
    data: KanbanBoardUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-kanban-boards")),
):
    updated = await crud.update(db, board_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Kanban board '{board_id}' not found",
        )
    return updated


@router.delete("/{board_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_kanban_board(
    board_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-kanban-boards")),
):
    if not await crud.delete(db, board_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Kanban board '{board_id}' not found",
        )
