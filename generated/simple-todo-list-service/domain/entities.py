from typing import Optional


class Reference:
    def __init__(self, id: str):
        self.id = id


class Status(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"


class SimpleTodoListServiceItem:
    def __init__(
        self,
        reference: Reference,
        description: str,
        status: Status,
        completed_at: Optional[datetime.datetime] = None,
    ):
        self.reference = reference
        self.description = description
        self.status = status
        self.completed_at = completed_at


class SimpleTodoListService:
    def __init__(self, reference: Reference):
        self.reference = reference
        self.items = []

    def add_item(self, item: SimpleTodoListServiceItem) -> None:
        self.items.append(item)

    def update_item_status(self, item_reference: Reference, new_status: Status) -> None:
        for item in self.items:
            if item.reference == item_reference:
                item.status = new_status

    def delete_item(self, item_reference: Reference) -> None:
        self.items = [item for item in self.items if item.reference != item_reference]
