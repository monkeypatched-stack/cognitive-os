"""MongoDB Capability — document database integration."""

from __future__ import annotations

from typing import Any
from cerebellum.capability import Capability


class MongoDBCapability(Capability):
    """MongoDB integration capability."""

    def __init__(self, url: str = "mongodb://localhost:27017", database: str = "agentos"):
        super().__init__(name="mongodb")
        self._url = url
        self._database = database
        self._client = None

    async def execute(self, state: dict[str, Any], **kwargs) -> dict[str, Any]:
        """Execute MongoDB operation."""
        from motor.motor_asyncio import AsyncIOMotorClient

        if not self._client:
            self._client = AsyncIOMotorClient(self._url)

        db = self._client[self._database]
        operation = state.get("operation", "find")
        collection = state.get("collection", "")
        query = state.get("query", {})
        data = state.get("data")

        coll = db[collection]

        if operation == "find":
            cursor = coll.find(query)
            results = await cursor.to_list(length=state.get("limit", 100))
            return {"results": results, "count": len(results)}

        elif operation == "insert":
            result = await coll.insert_one(data)
            return {"inserted_id": str(result.inserted_id)}

        elif operation == "update":
            result = await coll.update_one(query, {"$set": data}, upsert=True)
            return {"modified": result.modified_count}

        elif operation == "delete":
            result = await coll.delete_one(query)
            return {"deleted": result.deleted_count}

        return {"status": "unknown_operation"}
