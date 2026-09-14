from pymongo import MongoClient
from pymongo.collection import Collection

from domain.entities import TodoAgentItem, Status, Reference, TodoAgent
from domain.value_objects import Status, Reference
from domain.aggregate import TodoAgentAggregate
from domain.repository import (
    TodoAgentRepository,
    RepositoryException,
    RepositoryConflict,
    RepositoryNotFound,
)
from infrastructure.persistence.motor import MotorTodoAgentRepository


class MongoDBTodoAgentRepository(TodoAgentRepository):
    def __init__(self, collection: Collection):
        self.collection = collection

    async def get(self, id: str) -> Optional[TodoAgent]:
        result = await self.collection.find_one({"id": id})
        if not result:
            return None
        items = [
            TodoAgentItem(
                id=item["id"],
                description=item["description"],
                status=Status(item["status"]),
                references=[Reference(ref_id) for ref_id in item.get("references", [])],
            )
            for item in result.get("items", [])
        ]
        return TodoAgent(id=result["id"], items=items)

    async def add(self, todo_agent: TodoAgent) -> None:
        await self.collection.insert_one(todo_agent.to_dict())

    async def update(self, todo_agent: TodoAgent) -> None:
        await self.collection.update_one(
            {"id": todo_agent.id}, {"$set": todo_agent.to_dict()}
        )

    async def delete(self, id: str) -> None:
        await self.collection.delete_one({"id": id})
