"""Helper functions for plant location operations."""

from typing import Optional, List, Tuple
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId

from services.facilities.models.locations import (
    LocationCreate,
    LocationUpdate,
    LocationResponse,
)

COLLECTION = "plant_locations"


def _get_location_query(location_id: str) -> dict:
    """Get query for location lookup supporting both ObjectId and string id."""
    try:
        # First try as MongoDB ObjectId
        return {"_id": ObjectId(location_id)}
    except Exception:
        # If ObjectId conversion fails, use string id field
        return {"id": location_id}


def _serialize_location(doc: dict) -> dict:
    """Serialize location document to response format."""
    if not doc:
        return None

    serialized = doc.copy()
    mongo_id = str(serialized.pop("_id"))

    if "id" not in serialized or not serialized["id"]:
        serialized["id"] = mongo_id

    if "parent_id" in serialized and serialized["parent_id"]:
        serialized["parent_id"] = str(serialized["parent_id"])

    return serialized


async def get_location_by_id(
    db: AsyncIOMotorDatabase, location_id: str
) -> Optional[dict]:
    """Get a location by its ID (supports both MongoDB ObjectId and string id)."""
    try:
        # First try as MongoDB ObjectId
        doc = await db[COLLECTION].find_one({"_id": ObjectId(location_id)})
        if doc:
            return _serialize_location(doc)

        # If not found as ObjectId, try as string id field
        doc = await db[COLLECTION].find_one({"id": location_id})
        if doc:
            return _serialize_location(doc)

        return None
    except Exception:
        # If ObjectId conversion fails, try as string id field
        doc = await db[COLLECTION].find_one({"id": location_id})
        return _serialize_location(doc)


async def get_location_by_location_id(
    db: AsyncIOMotorDatabase, location_id: str
) -> Optional[dict]:
    """Get a location by its location_id."""
    doc = await db[COLLECTION].find_one({"location_id": location_id})
    return _serialize_location(doc)


async def get_all_locations(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
    location_type: Optional[str] = None,
    parent_id: Optional[str] = None,
    level: Optional[int] = None,
) -> Tuple[List[dict], int]:
    """Get all locations with filtering and pagination."""
    query: dict = {}

    if location_type:
        query["type"] = location_type
    if parent_id:
        query["parent_id"] = ObjectId(parent_id)
    if level:
        query["level"] = level

    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)

    return [_serialize_location(doc) async for doc in cursor], total


async def create_location(
    db: AsyncIOMotorDatabase, location_data: LocationCreate
) -> dict:
    """Create a new location."""
    # Convert to dict and add timestamps
    doc = location_data.model_dump()
    doc["created_at"] = datetime.utcnow()
    doc["updated_at"] = datetime.utcnow()

    # Convert parent_id to ObjectId if it exists
    if doc.get("parent_id"):
        doc["parent_id"] = ObjectId(doc["parent_id"])

    result = await db[COLLECTION].insert_one(doc)
    created = await db[COLLECTION].find_one({"_id": result.inserted_id})

    return _serialize_location(created)


async def update_location(
    db: AsyncIOMotorDatabase, location_id: str, update_data: LocationUpdate
) -> Optional[dict]:
    """Update a location."""
    update_dict = update_data.model_dump(exclude_unset=True)
    if update_dict:
        update_dict["updated_at"] = datetime.utcnow()

        query = _get_location_query(location_id)
        result = await db[COLLECTION].update_one(query, {"$set": update_dict})

        if result.modified_count > 0:
            updated = await db[COLLECTION].find_one(query)
            return _serialize_location(updated)

    return None


async def delete_location(db: AsyncIOMotorDatabase, location_id: str) -> bool:
    """Delete a location."""
    query = _get_location_query(location_id)
    result = await db[COLLECTION].delete_one(query)
    return result.deleted_count > 0


async def get_location_hierarchy(
    db: AsyncIOMotorDatabase, location_id: str
) -> List[dict]:
    """Get the complete hierarchy for a location."""
    hierarchy = []
    current_id = location_id

    while current_id:
        location = await get_location_by_id(db, current_id)
        if not location:
            break

        hierarchy.append(location)
        current_id = location.get("parent_id")

    # Reverse to get from plant down to the specific location
    return hierarchy[::-1]


async def get_children(db: AsyncIOMotorDatabase, parent_id: str) -> List[dict]:
    """Get child locations of a parent."""
    try:
        # Try as MongoDB ObjectId first
        cursor = db[COLLECTION].find({"parent_id": ObjectId(parent_id)})
    except Exception:
        # If ObjectId conversion fails, use string parent_id
        cursor = db[COLLECTION].find({"parent_id": parent_id})
    return [_serialize_location(doc) async for doc in cursor]


async def get_location_with_children(
    db: AsyncIOMotorDatabase, location_id: str
) -> Optional[dict]:
    """Get a location with its children."""
    location = await get_location_by_id(db, location_id)
    if not location:
        return None

    children = await get_children(db, location_id)

    return {"location": location, "children": children, "children_count": len(children)}


async def search_locations(
    db: AsyncIOMotorDatabase, search_term: str, limit: int = 10
) -> List[dict]:
    """Search locations by name or location_id."""
    query = {
        "$or": [
            {"name": {"$regex": search_term, "$options": "i"}},
            {"location_id": {"$regex": search_term, "$options": "i"}},
            {"path": {"$regex": search_term, "$options": "i"}},
        ]
    }

    cursor = db[COLLECTION].find(query).limit(limit)
    return [_serialize_location(doc) async for doc in cursor]


async def get_locations_by_type(
    db: AsyncIOMotorDatabase, location_type: str
) -> List[dict]:
    """Get all locations of a specific type."""
    cursor = db[COLLECTION].find({"type": location_type})
    return [_serialize_location(doc) async for doc in cursor]
