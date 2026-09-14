#!/usr/bin/env python3
"""Seed pharmaceutical equipment taxonomy data into MongoDB."""

import asyncio
import sys
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, "/Users/prashunjaveri/Code/monkeypatched")

from services.common.config import settings

# Import the taxonomy data
from src.mock.taxonomy.family import INIT_FAMILIES
from src.mock.taxonomy.classes import INIT_CLASSES
from src.mock.taxonomy.subclass import INIT_SUBCLASSES


async def seed_pharma_taxonomy():
    """Seed pharmaceutical equipment taxonomy data into MongoDB."""
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DB_NAME]

    print("Starting pharmaceutical equipment taxonomy seeding...")

    # Seed families
    families_collection = db["equipment_families"]
    classes_collection = db["equipment_classes"]
    subclasses_collection = db["equipment_subclasses"]

    # Check if data already exists
    existing_families = await families_collection.count_documents({})
    existing_classes = await classes_collection.count_documents({})
    existing_subclasses = await subclasses_collection.count_documents({})

    if existing_families > 0 or existing_classes > 0 or existing_subclasses > 0:
        print(
            f"Found existing data - Families: {existing_families}, Classes: {existing_classes}, Subclasses: {existing_subclasses}"
        )
        confirm = input("Do you want to continue and potentially duplicate data? (y/n): ")
        if confirm.lower() != "y":
            print("Seeding cancelled.")
            return

    # Insert families
    if INIT_FAMILIES:
        result = await families_collection.insert_many(INIT_FAMILIES)
        print(f"Inserted {len(result.inserted_ids)} equipment families")

    # Insert classes
    if INIT_CLASSES:
        result = await classes_collection.insert_many(INIT_CLASSES)
        print(f"Inserted {len(result.inserted_ids)} equipment classes")

    # Insert subclasses
    if INIT_SUBCLASSES:
        result = await subclasses_collection.insert_many(INIT_SUBCLASSES)
        print(f"Inserted {len(result.inserted_ids)} equipment subclasses")

    print("Pharmaceutical equipment taxonomy seeding completed successfully!")


if __name__ == "__main__":
    asyncio.run(seed_pharma_taxonomy())
