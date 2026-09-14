from fastapi import APIRouter, Depends, HTTPException
from starlette.status import HTTP_404_NOT_FOUND

from application.commands import (
    CreateSimpleTodoListServiceItemCommandHandler,
    UpdateSimpleTodoListServiceItemCommandHandler,
)
from application.dto import (
    SimpleTodoListServiceItemCreate,
    SimpleTodoListServiceItemUpdate,
    SimpleTodoListServiceItemResponse,
)
from application.queries import (
    GetSimpleTodoListServiceItemQueryHandler,
    GetSimpleTodoListServiceItemsQueryHandler,
)
from domain.entities import SimpleTodoListServiceItem
from infrastructure.persistence.motor import MotorSimpleTodoListServiceItemRepository

router = APIRouter()


@router.post("/items/", response_model=SimpleTodoListServiceItemResponse)
async def create_item(
    item: SimpleTodoListServiceItemCreate,
    repo: MotorSimpleTodoListServiceItemRepository = Depends(),
):
    handler = CreateSimpleTodoListServiceItemCommandHandler(repo)
    return await handler.handle(item)


@router.get("/items/{item_id}", response_model=SimpleTodoListServiceItemResponse)
async def read_item(
    item_id: str, repo: MotorSimpleTodoListServiceItemRepository = Depends()
):
    handler = GetSimpleTodoListServiceItemQueryHandler(repo)
    try:
        item_id = UUID(item_id)
        return await handler.handle(item_id)
    except ValueError as e:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Invalid item ID")


@router.get("/items/", response_model=list[SimpleTodoListServiceItemResponse])
async def read_items(repo: MotorSimpleTodoListServiceItemRepository = Depends()):
    handler = GetSimpleTodoListServiceItemsQueryHandler(repo)
    return await handler.handle()


@router.put("/items/{item_id}", response_model=SimpleTodoListServiceItemResponse)
async def update_item(
    item_id: str,
    item_update: SimpleTodoListServiceItemUpdate,
    repo: MotorSimpleTodoListServiceItemRepository = Depends(),
):
    try:
        item_id = UUID(item_id)
        handler = UpdateSimpleTodoListServiceItemCommandHandler(repo)
        return await handler.handle(item_id, item_update)
    except ValueError as e:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Invalid item ID")
