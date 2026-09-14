#!/usr/bin/env python3
"""Script to inspect MongoDB collections for seeding."""

import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
import sys

sys.path.insert(0, "/Users/prashunjaveri/Code/monkeypatched")
from services.common.config import settings


async def inspect_collections():
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DB_NAME]

    collections = ["materials", "equipment", "production_batches"]

    for coll_name in collections:
        print(f"\n--- {coll_name} (first 5) ---")
        cursor = db[coll_name].find().limit(5)
        async for doc in cursor:
            print(doc)

    client.close()


if __name__ == "__main__":
    asyncio.run(inspect_collections())
