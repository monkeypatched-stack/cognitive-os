from typing import List, Optional

from fastapi import HTTPException, Depends
from lemon import logger
from sentry_sdk import capture_exception

from .dto import (
    SimpleTodoListServiceItemResponse,
    SimpleTodoListServiceItemListResponse,
)
from ..domain.entities import Reference
from ..domain.exceptions import DomainError
from ..domain.repository import ISimpleTodoListServiceRepository


class GetItemsQuery:
    def __init__(self, repository: ISimpleTodoListServiceRepository):
        self._repository = repository

    async def execute(
        self, todo_list_service_reference: Reference
    ) -> List[SimpleTodoListServiceItemResponse]:
        try:
            items = await self._repository.get_items_by_todo_list_service_reference(
                todo_list_service_reference
            )
            item_responses = [
                SimpleTodoListServiceItemResponse(
                    reference=item.reference.id,
                    description=item.description,
                    status=item.status.value,
                    completed_at=(
                        item.completed_at.isoformat() if item.completed_at else None
                    ),
                )
                for item in items
            ]
            logger.info(
                f"Items retrieved for todo list service {todo_list_service_reference.id}"
            )
            return item_responses
        except DomainError as e:
            capture_exception(e)
            raise HTTPException(status_code=400, detail=str(e))
