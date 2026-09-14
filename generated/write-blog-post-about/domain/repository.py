from abc import ABC, abstractmethod
from typing import Optional


class WriteBlogPostAboutItem:
    pass


class Reference:
    pass


class Status:
    pass


class WriteBlogPostAboutIdentity:
    pass


class WriteBlogPostAboutCreated:
    pass


class WriteBlogPostAboutUpdated:
    pass


class WriteBlogPostAboutDeleted:
    pass


class IRepository(ABC):
    @abstractmethod
    async def find_by_id(
        self, blog_post_id: str
    ) -> Optional[WriteBlogPostAboutItem]: ...

    @abstractmethod
    async def create(self, blog_post_item: WriteBlogPostAboutItem) -> None: ...

    @abstractmethod
    async def update(self, blog_post_item: WriteBlogPostAboutItem) -> None: ...

    @abstractmethod
    async def delete(self, blog_post_id: str) -> None: ...
