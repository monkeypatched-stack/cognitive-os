from typing import List, Optional


class Reference:
    def __init__(self, reference_id: str):
        if not reference_id:
            raise ValueError("Reference ID cannot be empty")
        self.reference_id = reference_id


class Status:
    DRAFT = "draft"
    PUBLISHED = "published"

    @classmethod
    def from_string(cls, status_str: str) -> "Status":
        if status_str not in [cls.DRAFT, cls.PUBLISHED]:
            raise ValueError(f"Invalid status: {status_str}")
        return cls(status_str)


class WriteBlogPostAboutItem:
    def __init__(self, title: str, content: str, reference: Reference, status: Status):
        self.title = title
        self.content = content
        self.reference = reference
        self.status = status

    def update_content(self, new_content: str) -> None:
        if not new_content:
            raise ValueError("Content cannot be empty")
        self.content = new_content

    def publish(self) -> None:
        if self.status != Status.DRAFT:
            raise ValueError("Blog post is not in draft status")
        self.status = Status.PUBLISHED

    def unpublish(self) -> None:
        if self.status != Status.PUBLISHED:
            raise ValueError("Blog post is not in published status")
        self.status = Status.DRAFT
