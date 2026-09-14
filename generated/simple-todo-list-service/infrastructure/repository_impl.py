from typing import List, Optional
from motor.motor_asyncio import AsyncIOMotorClient
from bson.objectid import ObjectId

from domain.entities import Reference, Status, SimpleTodoListServiceItem
from domain.value_objects import Reference, Status
from infrastructure.repository import ISimpleTodoListServiceRepository


class MotorSimpleTodoListServiceRepository(ISimpleTodoListServiceRepository):
    def __init__(self, db: AsyncIOMotorClient):
        self._db = db.simple_todo_list_service

    async def add_item(
        self, todo_list_service_reference: Reference, item: SimpleTodoListServiceItem
    ) -> None:
        item_data = {
            "todo_list_service_id": str(todo_list_service_reference.id),
            "description": item.description,
            "status": item.status.value,
        }
        if await self._db.item.find_one(
            {
                "todo_list_service_id": str(todo_list_service_reference.id),
                "reference.id": str(item.reference.id),
            }
        ):
            raise ValueError("Item with the same reference already exists")
        await self._db.item.insert_one(item_data)
        logger.info(
            f"Item created for todo list service {todo_list_service_reference.id}"
        )

    async def update_item_status(
        self,
        todo_list_service_reference: Reference,
        item_reference: Reference,
        new_status: str,
    ) -> None:
        query = {
            "todo_list_service_id": str(todo_list_service_reference.id),
            "reference.id": str(item_reference.id),
        }
        update = {"$set": {"status": new_status}}
        result = await self._db.item.update_one(query, update)
        if result.modified_count == 0:
            raise ValueError("Item not found")
        logger.info(
            f"Item status updated for todo list service {todo_list_service_reference.id}"
        )

    async def delete_item(
        self, todo_list_service_reference: Reference, item_reference: Reference
    ) -> None:
        query = {
            "todo_list_service_id": str(todo_list_service_reference.id),
            "reference.id": str(item_reference.id),
        }
        result = await self._db.item.delete_one(query)
        if result.deleted_count == 0:
            raise ValueError("Item not found")
        logger.info(
            f"Item deleted for todo list service {todo_list_service_reference.id}"
        )

    async def get_items_by_todo_list_service_reference(
        self, todo_list_service_reference: Reference
    ) -> List[SimpleTodoListServiceItem]:
        items = await self._db.item.find(
            {"todo_list_service_id": str(todo_list_service_reference.id)}
        ).to_list(None)
        return [
            SimpleTodoListServiceItem(
                reference=Reference(id=item["reference"]["id"]),
                description=item["description"],
                status=Status(item["status"]),
            )
            for item in items
        ]
