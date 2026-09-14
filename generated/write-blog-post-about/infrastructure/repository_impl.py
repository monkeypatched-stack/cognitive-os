from motor.motor_asyncio import AsyncIOMotorClient
from bson.objectid import ObjectId
from app.domain import (
    WriteBlogPostAboutItem,
    Reference,
    Status,
)
from typing import Optional


class MotorWriteBlogPostAboutRepository:
    def __init__(self, db: AsyncIOMotorClient):
        self.db = db.write_blog_post_about

    async def find_by_id(self, blog_post_id: str) -> Optional[WriteBlogPostAboutItem]:
        document = await self.db.find_one({"blog_post_id": blog_post_id})
        if document:
            return WriteBlogPostAboutItem(
                title=document["title"],
                content=document["content"],
                reference=Reference(reference_id=document["reference"]["reference_id"]),
                status=Status.from_string(document["status"]),
            )
        return None

    async def create(self, blog_post_item: WriteBlogPostAboutItem) -> None:
        document = {
            "blog_post_id": blog_post_item.blog_post_id,
            "title": blog_post_item.title,
            "content": blog_post_item.content,
            "reference": {"reference_id": blog_post_item.reference.reference_id},
            "status": blog_post_item.status.value,
        }
        await self.db.insert_one(document)

    async def update(self, blog_post_item: WriteBlogPostAboutItem) -> None:
        document = {
            "blog_post_id": blog_post_item.blog_post_id,
            "title": blog_post_item.title,
            "content": blog_post_item.content,
            "reference": {"reference_id": blog_post_item.reference.reference_id},
            "status": blog_post_item.status.value,
        }
        await self.db.replace_one(
            {"blog_post_id": blog_post_item.blog_post_id}, document
        )

    async def delete(self, blog_post_id: str) -> None:
        await self.db.delete_one({"blog_post_id": blog_post_id})
