"""
helpers/corrective_actions_helper.py
--------------------------------------
MongoDB CRUD helper for CorrectiveActions and CorrectiveAction models.
Mirrors the pattern established in the devices helper.

Collection
----------
  corrective_actions  → CorrectiveActions documents (one per process_definition step)
"""

from typing import Optional
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.process_definitions.models.process_corrections import (
    CorrectiveActionsCreate,
    CorrectiveActionsUpdate,
    CorrectiveActionCreate,
)

COLLECTION = "corrective_actions"


def _serialize(doc: dict) -> dict:
    """Strip MongoDB's internal _id before returning a document."""
    doc.pop("_id", None)
    return doc


# ===========================================================================
# CorrectiveActions  (container)
# ===========================================================================

# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    """Return a paginated list of all CorrectiveActions containers."""
    query: dict = {}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(d) async for d in cursor], total


async def get_by_id(
    db: AsyncIOMotorDatabase,
    corrective_actions_id: str,
) -> Optional[dict]:
    """Fetch a CorrectiveActions container by its id."""
    doc = await db[COLLECTION].find_one({"id": corrective_actions_id})
    return _serialize(doc) if doc else None


async def get_by_process_definition_id(
    db: AsyncIOMotorDatabase,
    process_definition_id: str,
) -> list[dict]:
    """Return all CorrectiveActions containers that belong to a process_definition."""
    if not process_definition_id:
        return []
    cursor = db[COLLECTION].find({"process_definition_id": process_definition_id})
    return [_serialize(d) async for d in cursor]


async def get_by_step_id(
    db: AsyncIOMotorDatabase,
    step_id: str,
) -> Optional[dict]:
    """Fetch the CorrectiveActions container that belongs to a given process_definition step."""
    if not step_id:
        return None
    doc = await db[COLLECTION].find_one({"process_step_id": step_id})
    return _serialize(doc) if doc else None


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


async def create(
    db: AsyncIOMotorDatabase,
    data: CorrectiveActionsCreate,
) -> dict:
    """Insert a new CorrectiveActions container and return it."""
    doc = data.model_dump()
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    corrective_actions_id: str,
    data: CorrectiveActionsUpdate,
) -> Optional[dict]:
    """
    Partially update a CorrectiveActions container.
    Only non-None fields are written.
    """
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        return await get_by_id(db, corrective_actions_id)
    result = await db[COLLECTION].find_one_and_update(
        {"id": corrective_actions_id},
        {"$set": fields},
        return_document=True,
    )
    return _serialize(result) if result else None


async def delete(
    db: AsyncIOMotorDatabase,
    corrective_actions_id: str,
) -> bool:
    """Delete a CorrectiveActions container by ID."""
    result = await db[COLLECTION].delete_one({"id": corrective_actions_id})
    return result.deleted_count == 1


async def delete_by_process_definition_id(
    db: AsyncIOMotorDatabase,
    process_definition_id: str,
) -> bool:
    """Delete all CorrectiveActions containers belonging to a process_definition (cascade delete)."""
    result = await db[COLLECTION].delete_many(
        {"process_definition_id": process_definition_id}
    )
    return result.deleted_count > 0


async def delete_by_step_id(
    db: AsyncIOMotorDatabase,
    step_id: str,
) -> bool:
    """Delete the CorrectiveActions container that belongs to a step (cascade delete)."""
    result = await db[COLLECTION].delete_one({"process_step_id": step_id})
    return result.deleted_count == 1


# ===========================================================================
# CorrectiveAction  (items inside actions[] array)
# ===========================================================================

# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


async def get_action_by_id(
    db: AsyncIOMotorDatabase,
    corrective_actions_id: str,
    action_id: str,
) -> Optional[dict]:
    """Return a single action from within a CorrectiveActions container."""
    doc = await db[COLLECTION].find_one(
        {"id": corrective_actions_id, "actions.id": action_id},
        {"actions.$": 1},
    )
    if not doc or not doc.get("actions"):
        return None
    return doc["actions"][0]


async def get_actions_by_type(
    db: AsyncIOMotorDatabase,
    corrective_actions_id: str,
    action_type: str,
) -> list[dict]:
    """Return all actions within a container that match a given action_type."""
    doc = await db[COLLECTION].find_one({"id": corrective_actions_id})
    if not doc:
        return []
    return [a for a in doc.get("actions", []) if a.get("action_type") == action_type]


async def get_actions_by_postcheck(
    db: AsyncIOMotorDatabase,
    corrective_actions_id: str,
    postcheck_id: str,
) -> list[dict]:
    """Return all actions that are linked to a given PostCheckCondition ID."""
    doc = await db[COLLECTION].find_one({"id": corrective_actions_id})
    if not doc:
        return []
    return [
        a
        for a in doc.get("actions", [])
        if postcheck_id in a.get("applies_to_postchecks", [])
    ]


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


async def add_action(
    db: AsyncIOMotorDatabase,
    corrective_actions_id: str,
    action: CorrectiveActionCreate,
) -> Optional[dict]:
    """Append a new CorrectiveAction to the actions array."""
    result = await db[COLLECTION].find_one_and_update(
        {"id": corrective_actions_id},
        {"$push": {"actions": action.model_dump()}},
        return_document=True,
    )
    return _serialize(result) if result else None


async def update_action(
    db: AsyncIOMotorDatabase,
    corrective_actions_id: str,
    action_id: str,
    fields: dict,
) -> Optional[dict]:
    """
    Update specific fields of a CorrectiveAction inside the actions array.
    Uses the positional $ operator to target the matched element.
    Pass only the fields you want to change in the `fields` dict.
    """
    updates = {f"actions.$.{k}": v for k, v in fields.items()}
    result = await db[COLLECTION].find_one_and_update(
        {"id": corrective_actions_id, "actions.id": action_id},
        {"$set": updates},
        return_document=True,
    )
    return _serialize(result) if result else None


async def link_postcheck(
    db: AsyncIOMotorDatabase,
    corrective_actions_id: str,
    action_id: str,
    postcheck_id: str,
) -> Optional[dict]:
    """Add a PostCheckCondition ID to an action's applies_to_postchecks array."""
    result = await db[COLLECTION].find_one_and_update(
        {"id": corrective_actions_id, "actions.id": action_id},
        {"$addToSet": {"actions.$.applies_to_postchecks": postcheck_id}},
        return_document=True,
    )
    return _serialize(result) if result else None


async def unlink_postcheck(
    db: AsyncIOMotorDatabase,
    corrective_actions_id: str,
    action_id: str,
    postcheck_id: str,
) -> Optional[dict]:
    """Remove a PostCheckCondition ID from an action's applies_to_postchecks array."""
    result = await db[COLLECTION].find_one_and_update(
        {"id": corrective_actions_id, "actions.id": action_id},
        {"$pull": {"actions.$.applies_to_postchecks": postcheck_id}},
        return_document=True,
    )
    return _serialize(result) if result else None


async def remove_action(
    db: AsyncIOMotorDatabase,
    corrective_actions_id: str,
    action_id: str,
) -> Optional[dict]:
    """Remove a specific action from the actions array."""
    result = await db[COLLECTION].find_one_and_update(
        {"id": corrective_actions_id},
        {"$pull": {"actions": {"id": action_id}}},
        return_document=True,
    )
    return _serialize(result) if result else None
