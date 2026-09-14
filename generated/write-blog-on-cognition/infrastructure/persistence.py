from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase


class MongoDBRepository:
    def __init__(self, client: AsyncIOMotorClient):
        self.db: AsyncIOMotorDatabase = client.write_blog_on_cognition_db

    async def get_connection(self) -> AsyncIOMotorDatabase:
        return self.db
