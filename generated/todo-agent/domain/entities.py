from typing import Optional, List


class Status:
    def __init__(self, value: str):
        self.value = value

    def is_valid(self) -> bool:
        return self.value in ["pending", "completed"]


class Reference:
    def __init__(self, id: str):
        self.id = id


class TodoAgentItem:
    def __init__(
        self,
        description: str,
        status: Status,
        references: Optional[List[Reference]] = None,
    ):
        self.description = description
        self.status = status
        self.references = references if references else []

    def update_status(self, new_status: Status) -> bool:
        if not new_status.is_valid():
            return False
        self.status = new_status
        return True


class TodoAgent:
    def __init__(self, id: str, items: Optional[List[TodoAgentItem]] = None):
        self.id = id
        self.items = items if items else []

    def add_item(self, item: TodoAgentItem) -> bool:
        self.items.append(item)
        return True

    def remove_item(self, item_id: str) -> bool:
        for i, item in enumerate(self.items):
            if item.id == item_id:
                del self.items[i]
                return True
        return False


class TodoAgentCreatedEvent:
    pass


class TodoAgentUpdatedEvent:
    pass


class TodoAgentDeletedEvent:
    pass
