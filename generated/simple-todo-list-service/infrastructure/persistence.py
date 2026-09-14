from motor.motor_asyncio import AsyncIOMotorClient


class MongoDBRepository:
    def __init__(self, db_name: str):
        self.client = AsyncIOMotorClient("mongodb://localhost:27017")
        self.db = self.client[db_name]

    async def add_item(self, collection_name: str, item_data: dict) -> None:
        await self.db[collection_name].insert_one(item_data)

    async def update_item_status(
        self, collection_name: str, query: dict, update: dict
    ) -> None:
        await self.db[collection_name].update_one(query, update)

    async def delete_item(self, collection_name: str, query: dict) -> None:
        await self.db[collection_name].delete_one(query)

    async def get_items_by_query(self, collection_name: str, query: dict) -> list:
        return await self.db[collection_name].find(query).to_list(None)
