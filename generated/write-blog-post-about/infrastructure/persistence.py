from motor.motor_asyncio import AsyncIOMotorClient
import os


class MongoDBClient:
    def __init__(self, uri: str):
        self.client = AsyncIOMotorClient(uri)
        self.db = self.client.write_blog_post_about

    async def close(self):
        await self.client.close()


async def get_mongodb_client():
    uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017/")
    client = MongoDBClient(uri)
    return client
