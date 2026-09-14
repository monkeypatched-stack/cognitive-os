from abc import ABC, abstractmethod


class WriteBlogOnCognitionRepository(ABC):
    @abstractmethod
    async def get_write_blog_on_cognition_item_by_id(
        self, item_id: int
    ) -> Optional["WriteBlogOnCognitionItem"]:
        pass

    @abstractmethod
    async def add_write_blog_on_cognition_item(
        self, write_blog_on_cognition_item: "WriteBlogOnCognitionItem"
    ) -> None:
        pass

    @abstractmethod
    async def update_write_blog_on_cognition_item(
        self, write_blog_on_cognition_item: "WriteBlogOnCognitionItem"
    ) -> None:
        pass

    @abstractmethod
    async def delete_write_blog_on_cognition_item(self, item_id: int) -> None:
        pass
