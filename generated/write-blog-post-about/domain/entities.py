from datetime import datetime
from uuid import UUID, uuid4


class Reference:
    def __init__(self, value: str):
        if not isinstance(value, str) or len(value) > 255:
            raise ValueError("Reference must be a string up to 255 characters long")
        self.value = value


class Status(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"


class WriteBlogPostAboutItem:
    def __init__(
        self,
        title: str,
        content: str,
        reference: Reference,
        status: Status = Status.DRAFT,
    ):
        if not isinstance(title, str) or len(title) > 255:
            raise ValueError("Title must be a string up to 255 characters long")
        if not isinstance(content, str) or len(content) > 65535:
            raise ValueError("Content must be a string up to 65535 characters long")
        self.title = title
        self.content = content
        self.reference = reference
        self.status = status


class WriteBlogPostAbout:
    def __init__(self, id: UUID = None):
        if id is None:
            id = uuid4()
        self.id = id
        self.items = []
        self.created_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()

    def add_item(self, item: WriteBlogPostAboutItem):
        if any(item.id == existing_item.id for existing_item in self.items):
            raise ValueError("Duplicate blog post item")
        self.items.append(item)
        self.updated_at = datetime.utcnow()

    def remove_item(self, item_id: UUID):
        item_to_remove = next((item for item in self.items if item.id == item_id), None)
        if item_to_remove is None:
            raise ValueError("Item not found")
        self.items.remove(item_to_remove)
        self.updated_at = datetime.utcnow()

    def publish_item(self, item_id: UUID):
        item_to_publish = next(
            (item for item in self.items if item.id == item_id), None
        )
        if item_to_publish is None:
            raise ValueError("Item not found")
        item_to_publish.status = Status.PUBLISHED
        self.updated_at = datetime.utcnow()

    def publish_all_items(self):
        for item in self.items:
            item.status = Status.PUBLISHED
        self.updated_at = datetime.utcnow()
