from typing import List, Optional
from datetime import datetime
from .entities import Reference, Status, SimpleTodoListServiceItem
from .value_objects import Reference, Status


class SimpleTodoListServiceCreated:
    def __init__(self, reference: Reference):
        self.reference = reference


class SimpleTodoListServiceUpdated:
    def __init__(self, reference: Reference):
        self.reference = reference


class SimpleTodoListServiceDeleted:
    def __init__(self, reference: Reference):
        self.reference = reference


class SimpleTodoListService:
    def __init__(self, reference: Reference):
        self._reference = reference
        self._items: List[SimpleTodoListServiceItem] = []

    @property
    def reference(self) -> Reference:
        return self._reference

    @property
    def items(self) -> List[SimpleTodoListServiceItem]:
        return self._items

    def add_item(self, item: SimpleTodoListServiceItem) -> None:
        if any(
            item.reference == existing_item.reference for existing_item in self.items
        ):
            raise ValueError("Item with the same reference already exists")
        self._items.append(item)
        raise SimpleTodoListServiceUpdated(self._reference)

    def update_item_status(self, item_reference: Reference, new_status: Status) -> None:
        item = next(
            (item for item in self.items if item.reference == item_reference), None
        )
        if item is None:
            raise ValueError("Item not found")
        item.status = new_status
        raise SimpleTodoListServiceUpdated(self._reference)

    def delete_item(self, item_reference: Reference) -> None:
        updated_items = [
            item for item in self.items if item.reference != item_reference
        ]
        if len(updated_items) == len(self.items):
            raise ValueError("Item not found")
        self._items = updated_items
        raise SimpleTodoListServiceUpdated(self._reference)
