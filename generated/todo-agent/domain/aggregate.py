from typing import List, Optional


class TodoAgentCreatedEvent:
    pass


class TodoAgentUpdatedEvent:
    pass


class TodoAgentDeletedEvent:
    pass


class TodoAgentAggregate:
    def __init__(self, id: str, items: Optional[List["TodoAgentItem"]] = None):
        self.id = id
        self.items = items if items else []

    def add_item(self, item: "TodoAgentItem") -> "TodoAgentUpdatedEvent":
        self.items.append(item)
        return TodoAgentUpdatedEvent()

    def remove_item(self, item_id: str) -> "TodoAgentUpdatedEvent":
        for i, item in enumerate(self.items):
            if item.id == item_id:
                del self.items[i]
                return TodoAgentUpdatedEvent()
        raise Exception("Item not found")

    def update_status(
        self, item_id: str, new_status: "Status"
    ) -> "TodoAgentUpdatedEvent":
        for item in self.items:
            if item.id == item_id:
                if item.update_status(new_status):
                    return TodoAgentUpdatedEvent()
                else:
                    raise Exception("Invalid status")
        raise Exception("Item not found")

    def delete(self) -> "TodoAgentDeletedEvent":
        self.items = []
        return TodoAgentDeletedEvent()


class TodoAgentItem:
    def __init__(
        self,
        id: str,
        description: str,
        status: "Status",
        references: Optional[List["Reference"]] = None,
    ):
        self.id = id
        self.description = description
        self.status = status
        self.references = references if references else []

    def update_status(self, new_status: "Status") -> bool:
        if not new_status.is_valid():
            return False
        self.status = new_status
        return True


class Status:
    def __init__(self, value: str):
        self.value = value

    def is_valid(self) -> bool:
        return self.value in ["pending", "completed"]


class Reference:
    def __init__(self, id: str):
        self.id = id
