from typing import Optional
from .value_objects import Reference, Status, WriteBlogPostAboutItem


class WriteBlogPostAboutCreated:
    def __init__(self, blog_post_id: str):
        self.blog_post_id = blog_post_id


class WriteBlogPostAboutUpdated:
    def __init__(self, blog_post_id: str):
        self.blog_post_id = blog_post_id


class WriteBlogPostAboutDeleted:
    def __init__(self, blog_post_id: str):
        self.blog_post_id = blog_post_id


class WriteBlogPostAboutAggregateRoot:
    def __init__(
        self,
        blog_post_id: Optional[str] = None,
        title: Optional[str] = None,
        content: Optional[str] = None,
        reference: Optional[Reference] = None,
        status: Optional[Status] = Status.DRAFT,
    ):
        self.blog_post_id = blog_post_id
        self.title = title
        self.content = content
        self.reference = reference
        self.status = status

    def create(self, title: str, content: str, reference: Reference) -> None:
        if not self.blog_post_id:
            self.blog_post_id = reference.reference_id
        else:
            raise ValueError("Blog post already created")
        if not title or not content:
            raise ValueError("Title and content cannot be empty")
        if not isinstance(reference, Reference):
            raise ValueError("Invalid reference")
        self.title = title
        self.content = content
        self.reference = reference
        self.status = Status.DRAFT
        # Emit event
        return WriteBlogPostAboutCreated(blog_post_id=self.blog_post_id)

    def update(self, new_content: str) -> None:
        if not self.blog_post_id:
            raise ValueError("Blog post not created")
        if not new_content:
            raise ValueError("Content cannot be empty")
        self.content = new_content
        # Emit event
        return WriteBlogPostAboutUpdated(blog_post_id=self.blog_post_id)

    def publish(self) -> None:
        if not self.blog_post_id:
            raise ValueError("Blog post not created")
        if self.status != Status.DRAFT:
            raise ValueError("Blog post is not in draft status")
        self.status = Status.PUBLISHED
        # Emit event

    def unpublish(self) -> None:
        if not self.blog_post_id:
            raise ValueError("Blog post not created")
        if self.status != Status.PUBLISHED:
            raise ValueError("Blog post is not in published status")
        self.status = Status.DRAFT
        # Emit event

    def delete(self) -> None:
        if not self.blog_post_id:
            raise ValueError("Blog post not created")
        # Emit event
