#!/usr/bin/env python3
"""Generate embeddings for all MongoDB collections that need semantic search."""

import asyncio
import sys
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, "/Users/prashunjaveri/Code/monkeypatched")

from services.common.config import settings
from services.common.embeddings import build_embedding

# Collections to skip (only system collections)
SKIP_collections = {
    "system.",
}


async def generate_embeddings():
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DB_NAME]

    all_collections = await db.list_collection_names()
    collections_to_embed = [
        c for c in sorted(all_collections) if not c.startswith("system.") and c not in SKIP_collections
    ]

    print(f"Found {len(collections_to_embed)} collections to embed:")
    for c in collections_to_embed:
        print(f"  - {c}")

    total_updated = 0
    for collection_name in collections_to_embed:
        collection = db[collection_name]
        count = await collection.count_documents({})
        if count == 0:
            print(f"\n{collection_name}: 0 documents (skipped)")
            continue

        print(f"\n{collection_name}: {count} documents")

        updated = 0
        async for document in collection.find({}):
            doc_id = document.get("_id")
            embedding = build_embedding(collection_name, document)
            await collection.update_one({"_id": doc_id}, {"$set": {"embedding": embedding}})
            updated += 1

        print(f"  Updated {updated} documents with embeddings")
        total_updated += updated

    print(f"\nTotal: {total_updated} documents updated with embeddings")
    client.close()


if __name__ == "__main__":
    asyncio.run(generate_embeddings())
