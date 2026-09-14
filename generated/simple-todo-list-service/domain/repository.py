from abc import ABC, abstractmethod
from typing import List

from .entities import SimpleTodoListServiceItem
from .value_objects import Reference


class ISimpleTodoListServiceRepository(ABC):
    @abstractmethod
    async def add_item(
        self, todo_list_service_reference: Reference, item: SimpleTodoListServiceItem
    ) -> None:
        pass

    @abstractmethod
    async def update_item_status(
        self,
        todo_list_service_reference: Reference,
        item_reference: Reference,
        new_status: str,
    ) -> None:
        pass

    @abstractmethod
    async def delete_item(
        self, todo_list_service_reference: Reference, item_reference: Reference
    ) -> None:
        pass

    @abstractmethod
    async def get_items_by_todo_list_service_reference(
        self, todo_list_service_reference: Reference
    ) -> List[SimpleTodoListServiceItem]:
        pass
