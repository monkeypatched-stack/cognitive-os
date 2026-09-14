from typing import Optional

from fastapi import HTTPException, Depends
from lemon import logger
from sentry_sdk import capture_exception

from .dto import (
    SimpleTodoListServiceItemCreate,
    SimpleTodoListServiceItemUpdate,
    SimpleTodoListServiceItemListResponse,
    TodoListServiceReference,
)
from ..domain.aggregate import SimpleTodoListService
from ..domain.events import (
    SimpleTodoListServiceCreated,
    SimpleTodoListServiceUpdated,
    SimpleTodoListServiceDeleted,
)
from ..domain.exceptions import DomainError
from ..domain.repository import ISimpleTodoListServiceRepository
from ..infrastructure.persistence.motor import MotorSimpleTodoListServiceRepository


class CreateItemCommand:
    def __init__(self, repository: ISimpleTodoListServiceRepository):
        self._repository = repository

    async def execute(
        self,
        todo_list_service_reference: "Reference",
        item_data: SimpleTodoListServiceItemCreate,
    ) -> None:
        try:
            new_item = SimpleTodoListServiceItem(
                reference=todo_list_service_reference,
                description=item_data.description,
                status=Status.from_str(item_data.status),
            )
            await self._repository.add_item(todo_list_service_reference, new_item)
            logger.info(
                f"Item created for todo list service {todo_list_service_reference.id}"
            )
        except DomainError as e:
            capture_exception(e)
            raise HTTPException(status_code=400, detail=str(e))


class UpdateItemStatusCommand:
    def __init__(self, repository: ISimpleTodoListServiceRepository):
        self._repository = repository

    async def execute(
        self,
        todo_list_service_reference: "Reference",
        item_reference: "Reference",
        new_status: str,
    ) -> None:
        try:
            await self._repository.update_item_status(
                todo_list_service_reference, item_reference, new_status
            )
            logger.info(
                f"Item status updated for todo list service {todo_list_service_reference.id}"
            )
        except DomainError as e:
            capture_exception(e)
            raise HTTPException(status_code=400, detail=str(e))


class DeleteItemCommand:
    def __init__(self, repository: ISimpleTodoListServiceRepository):
        self._repository = repository

    async def execute(
        self, todo_list_service_reference: "Reference", item_reference: "Reference"
    ) -> None:
        try:
            await self._repository.delete_item(
                todo_list_service_reference, item_reference
            )
            logger.info(
                f"Item deleted for todo list service {todo_list_service_reference.id}"
            )
        except DomainError as e:
            capture_exception(e)
            raise HTTPException(status_code=400, detail=str(e))
