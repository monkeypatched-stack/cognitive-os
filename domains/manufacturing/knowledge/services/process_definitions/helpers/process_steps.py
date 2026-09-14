"""
helpers/process_steps_helper.py
---------------------------------
MongoDB CRUD helper for ProcessSteps and ProcessStep models.
Mirrors the pattern established in the devices helper.
"""

from typing import Optional
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.process_definitions.models.process_steps import (
    ProcessStepsCreate,
    ProcessStepsUpdate,
    ProcessStepUpdate,
)

COLLECTION = "process_steps"


def _serialize(doc: dict) -> dict:
    """Strip MongoDB's internal _id before returning a document."""
    doc.pop("_id", None)
    return doc


# ---------------------------------------------------------------------------
# ProcessSteps — top-level container
# ---------------------------------------------------------------------------


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    """Return a paginated list of all ProcessSteps containers and the total count."""
    query: dict = {}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(d) async for d in cursor], total


async def get_by_id(
    db: AsyncIOMotorDatabase,
    steps_id: str,
) -> Optional[dict]:
    """Fetch a ProcessSteps container by its id."""
    doc = await db[COLLECTION].find_one({"id": steps_id})
    return _serialize(doc) if doc else None


async def get_by_process_definition_id(
    db: AsyncIOMotorDatabase,
    process_definition_id: str,
) -> Optional[dict]:
    """Fetch the ProcessSteps container that belongs to a given process_definition."""
    if not process_definition_id:
        return None
    doc = await db[COLLECTION].find_one(
        {"process_definition_id": process_definition_id}
    )
    return _serialize(doc) if doc else None


async def create(
    db: AsyncIOMotorDatabase,
    data: ProcessStepsCreate,
) -> dict:
    """Insert a new ProcessSteps container and return it."""
    doc = data.model_dump()
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    steps_id: str,
    data: ProcessStepsUpdate,
) -> Optional[dict]:
    """
    Partially update a ProcessSteps container.
    Only non-None fields are written.
    """
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        return await get_by_id(db, steps_id)
    result = await db[COLLECTION].find_one_and_update(
        {"id": steps_id},
        {"$set": fields},
        return_document=True,
    )
    return _serialize(result) if result else None


async def delete(
    db: AsyncIOMotorDatabase,
    steps_id: str,
) -> bool:
    """Delete a ProcessSteps container by ID. Returns True if removed."""
    result = await db[COLLECTION].delete_one({"id": steps_id})
    return result.deleted_count == 1


async def delete_by_process_definition_id(
    db: AsyncIOMotorDatabase,
    process_definition_id: str,
) -> bool:
    """Delete the ProcessSteps container that belongs to a process_definition."""
    result = await db[COLLECTION].delete_one(
        {"process_definition_id": process_definition_id}
    )
    return result.deleted_count == 1


# ---------------------------------------------------------------------------
# Individual ProcessStep — operations on steps[] array inside the container
# ---------------------------------------------------------------------------


async def get_step_by_id(
    db: AsyncIOMotorDatabase,
    steps_id: str,
    step_id: str,
) -> Optional[dict]:
    """Return a single step from within a ProcessSteps container."""
    doc = await db[COLLECTION].find_one(
        {"id": steps_id, "steps.id": step_id},
        {"steps.$": 1},
    )
    if not doc or not doc.get("steps"):
        return None
    return doc["steps"][0]


async def get_steps_by_status(
    db: AsyncIOMotorDatabase,
    steps_id: str,
    status: str,
) -> list[dict]:
    """Return all steps within a container that match a given status."""
    doc = await db[COLLECTION].find_one({"id": steps_id})
    if not doc:
        return []
    return [s for s in doc.get("steps", []) if s.get("status") == status]


async def get_steps_by_action_type(
    db: AsyncIOMotorDatabase,
    steps_id: str,
    action_type: str,
) -> list[dict]:
    """Return all steps within a container that match a given action_type."""
    doc = await db[COLLECTION].find_one({"id": steps_id})
    if not doc:
        return []
    return [s for s in doc.get("steps", []) if s.get("action_type") == action_type]


async def add_step(
    db: AsyncIOMotorDatabase,
    steps_id: str,
    step: dict,
) -> Optional[dict]:
    """Append a new ProcessStep to the steps array of a container."""
    result = await db[COLLECTION].find_one_and_update(
        {"id": steps_id},
        {"$push": {"steps": step}},
        return_document=True,
    )
    return _serialize(result) if result else None


async def update_step(
    db: AsyncIOMotorDatabase,
    steps_id: str,
    step_id: str,
    data: ProcessStepUpdate,
) -> Optional[dict]:
    """
    Update fields of a specific step inside the steps array.
    Uses the positional $ operator to target the matched element.
    Only non-None fields are written.
    """
    fields = {f"steps.$.{k}": v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        return await get_by_id(db, steps_id)
    result = await db[COLLECTION].find_one_and_update(
        {"id": steps_id, "steps.id": step_id},
        {"$set": fields},
        return_document=True,
    )
    return _serialize(result) if result else None


async def update_step_status(
    db: AsyncIOMotorDatabase,
    steps_id: str,
    step_id: str,
    status: str,
) -> Optional[dict]:
    """Convenience helper: flip only the status of a single step."""
    result = await db[COLLECTION].find_one_and_update(
        {"id": steps_id, "steps.id": step_id},
        {"$set": {"steps.$.status": status}},
        return_document=True,
    )
    return _serialize(result) if result else None


async def remove_step(
    db: AsyncIOMotorDatabase,
    steps_id: str,
    step_id: str,
) -> Optional[dict]:
    """Remove a specific step from the steps array. Returns the updated container."""
    result = await db[COLLECTION].find_one_and_update(
        {"id": steps_id},
        {"$pull": {"steps": {"id": step_id}}},
        return_document=True,
    )
    return _serialize(result) if result else None
