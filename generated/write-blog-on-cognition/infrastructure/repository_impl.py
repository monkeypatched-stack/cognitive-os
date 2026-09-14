from typing import Optional, List
from motor.motor_asyncio import (
    AsyncIOMotorClient,
    AsyncIOMotorDatabase,
    AsyncIOMotorCollection,
)
from bson.objectid import ObjectId
from domain.entities import WriteBlogOnCognitionItem


class MotorWriteBlogOnCognitionRepository:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.collection: AsyncIOMotorCollection = db.write_blog_on_cognition_items

    async def get_write_blog_on_cognition_item_by_id(
        self, item_id: int
    ) -> Optional[WriteBlogOnCognitionItem]:
        result = await self.collection.find_one({"id": item_id})
        if result:
            return WriteBlogOnCognitionItem.from_value(result)
        return None

    async def add_write_blog_on_cognition_item(
        self, write_blog_on_cognition_item: WriteBlogOnCognitionItem
    ) -> None:
        await self.collection.insert_one(write_blog_on_cognition_item.to_dict())

    async def update_write_blog_on_cognition_item(
        self, write_blog_on_cognition_item: WriteBlogOnCognitionItem
    ) -> None:
        await self.collection.update_one(
            {"id": write_blog_on_cognition_item.id},
            {"$set": write_blog_on_cognition_item.to_dict()},
        )

    async def delete_write_blog_on_cognition_item(self, item_id: int) -> None:
        await self.collection.delete_one({"id": item_id})
