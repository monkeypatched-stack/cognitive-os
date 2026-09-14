from typing import Optional


class Status:
    PUBLISHED = "published"
    DRAFT = "draft"


class Reference:
    def __init__(self, url: str):
        self.url = url


class WriteBlogOnCognitionItem:
    def __init__(
        self,
        id: int,
        title: str,
        content: str,
        status: Status,
        references: Optional[list[Reference]] = None,
    ):
        self.id = id
        self.title = title
        self.content = content
        self.status = status
        self.references = references or []

    def update(
        self,
        title: Optional[str] = None,
        content: Optional[str] = None,
        status: Optional[Status] = None,
        references: Optional[list[Reference]] = None,
    ):
        if title is not None:
            self.title = title
        if content is not None:
            self.content = content
        if status is not None:
            self.status = status
        if references is not None:
            self.references = references

    def __eq__(self, other):
        if not isinstance(other, WriteBlogOnCognitionItem):
            return False
        return (
            self.id == other.id
            and self.title == other.title
            and self.content == other.content
            and self.status == other.status
            and self.references == other.references
        )

    def __hash__(self):
        return hash(
            (self.id, self.title, self.content, self.status, tuple(self.references))
        )
