"""
helpers/postchecks_helper.py
-----------------------------
MongoDB CRUD helper for ProcessPostchecks and ProcessStepPostchecks models.
Mirrors the pattern established in the devices helper.

Collections
-----------
  process_definition_postchecks       → ProcessPostchecks documents (one per process_definition)
  process_step_postchecks  → ProcessStepPostchecks documents (one per step)
"""

from typing import Optional
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.process_definitions.models.process_post_checks import (
    ProcessPostchecksCreate,
    ProcessPostchecksUpdate,
    ProcessStepPostchecksCreate,
    ProcessStepPostchecksUpdate,
    PostCheckConditionCreate,
)

WORKFLOW_COLLECTION = "process_definition_postchecks"
STEP_COLLECTION = "process_step_postchecks"


def _serialize(doc: dict) -> dict:
    """Strip MongoDB's internal _id before returning a document."""
    doc.pop("_id", None)
    return doc


# ===========================================================================
# ProcessPostchecks  (process_definition-level)
# ===========================================================================

# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    """Return a paginated list of all ProcessPostchecks containers."""
    query: dict = {}
    total = await db[WORKFLOW_COLLECTION].count_documents(query)
    cursor = (
        db[WORKFLOW_COLLECTION]
        .find(query)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return [_serialize(d) async for d in cursor], total


async def get_by_id(
    db: AsyncIOMotorDatabase,
    postchecks_id: str,
) -> Optional[dict]:
    """Fetch a ProcessPostchecks container by its id."""
    doc = await db[WORKFLOW_COLLECTION].find_one({"id": postchecks_id})
    return _serialize(doc) if doc else None


async def get_by_process_definition_id(
    db: AsyncIOMotorDatabase,
    process_definition_id: str,
) -> Optional[dict]:
    """Fetch the postchecks container that belongs to a given process_definition."""
    if not process_definition_id:
        return None
    doc = await db[WORKFLOW_COLLECTION].find_one(
        {"process_definition_id": process_definition_id}
    )
    return _serialize(doc) if doc else None


async def get_conditions_by_severity(
    db: AsyncIOMotorDatabase,
    postchecks_id: str,
    severity: str,
) -> list[dict]:
    """Return all conditions within a container that match a given severity."""
    doc = await db[WORKFLOW_COLLECTION].find_one({"id": postchecks_id})
    if not doc:
        return []
    return [
        c
        for c in doc.get("process_definition_post_check_conditions", [])
        if c.get("severity") == severity
    ]


async def get_mandatory_conditions(
    db: AsyncIOMotorDatabase,
    postchecks_id: str,
) -> list[dict]:
    """Return only the mandatory conditions from a container."""
    doc = await db[WORKFLOW_COLLECTION].find_one({"id": postchecks_id})
    if not doc:
        return []
    return [
        c
        for c in doc.get("process_definition_post_check_conditions", [])
        if c.get("is_mandatory") is True
    ]


async def get_conditions_by_corrective_action(
    db: AsyncIOMotorDatabase,
    postchecks_id: str,
    corrective_action_id: str,
) -> list[dict]:
    """Return all conditions that link to a specific corrective action ID."""
    doc = await db[WORKFLOW_COLLECTION].find_one({"id": postchecks_id})
    if not doc:
        return []
    return [
        c
        for c in doc.get("process_definition_post_check_conditions", [])
        if corrective_action_id in c.get("links_to_corrective_action_ids", [])
    ]


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


async def create(
    db: AsyncIOMotorDatabase,
    data: ProcessPostchecksCreate,
) -> dict:
    """Insert a new ProcessPostchecks container and return it."""
    doc = data.model_dump()
    await db[WORKFLOW_COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    postchecks_id: str,
    data: ProcessPostchecksUpdate,
) -> Optional[dict]:
    """
    Partially update a ProcessPostchecks container.
    Only non-None fields are written.
    """
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        return await get_by_id(db, postchecks_id)
    result = await db[WORKFLOW_COLLECTION].find_one_and_update(
        {"id": postchecks_id},
        {"$set": fields},
        return_document=True,
    )
    return _serialize(result) if result else None


async def add_condition(
    db: AsyncIOMotorDatabase,
    postchecks_id: str,
    condition: PostCheckConditionCreate,
) -> Optional[dict]:
    """Append a new PostCheckCondition to the conditions array."""
    result = await db[WORKFLOW_COLLECTION].find_one_and_update(
        {"id": postchecks_id},
        {"$push": {"process_definition_post_check_conditions": condition.model_dump()}},
        return_document=True,
    )
    return _serialize(result) if result else None


async def update_condition(
    db: AsyncIOMotorDatabase,
    postchecks_id: str,
    condition_id: str,
    fields: dict,
) -> Optional[dict]:
    """
    Update specific fields of a PostCheckCondition inside the conditions array.
    Pass only the fields you want to change in the `fields` dict.
    """
    updates = {
        f"process_definition_post_check_conditions.$.{k}": v for k, v in fields.items()
    }
    result = await db[WORKFLOW_COLLECTION].find_one_and_update(
        {
            "id": postchecks_id,
            "process_definition_post_check_conditions.id": condition_id,
        },
        {"$set": updates},
        return_document=True,
    )
    return _serialize(result) if result else None


async def link_corrective_action(
    db: AsyncIOMotorDatabase,
    postchecks_id: str,
    condition_id: str,
    corrective_action_id: str,
) -> Optional[dict]:
    """Add a corrective action ID to a condition's links_to_corrective_action_ids array."""
    result = await db[WORKFLOW_COLLECTION].find_one_and_update(
        {
            "id": postchecks_id,
            "process_definition_post_check_conditions.id": condition_id,
        },
        {
            "$addToSet": {
                "process_definition_post_check_conditions.$.links_to_corrective_action_ids": corrective_action_id
            }
        },
        return_document=True,
    )
    return _serialize(result) if result else None


async def unlink_corrective_action(
    db: AsyncIOMotorDatabase,
    postchecks_id: str,
    condition_id: str,
    corrective_action_id: str,
) -> Optional[dict]:
    """Remove a corrective action ID from a condition's links array."""
    result = await db[WORKFLOW_COLLECTION].find_one_and_update(
        {
            "id": postchecks_id,
            "process_definition_post_check_conditions.id": condition_id,
        },
        {
            "$pull": {
                "process_definition_post_check_conditions.$.links_to_corrective_action_ids": corrective_action_id
            }
        },
        return_document=True,
    )
    return _serialize(result) if result else None


async def remove_condition(
    db: AsyncIOMotorDatabase,
    postchecks_id: str,
    condition_id: str,
) -> Optional[dict]:
    """Remove a specific condition from the conditions array."""
    result = await db[WORKFLOW_COLLECTION].find_one_and_update(
        {"id": postchecks_id},
        {"$pull": {"process_definition_post_check_conditions": {"id": condition_id}}},
        return_document=True,
    )
    return _serialize(result) if result else None


async def delete(
    db: AsyncIOMotorDatabase,
    postchecks_id: str,
) -> bool:
    """Delete a ProcessPostchecks container by ID."""
    result = await db[WORKFLOW_COLLECTION].delete_one({"id": postchecks_id})
    return result.deleted_count == 1


async def delete_by_process_definition_id(
    db: AsyncIOMotorDatabase,
    process_definition_id: str,
) -> bool:
    """Delete the postchecks container that belongs to a process_definition (cascade delete)."""
    result = await db[WORKFLOW_COLLECTION].delete_one(
        {"process_definition_id": process_definition_id}
    )
    return result.deleted_count == 1


# ===========================================================================
# ProcessStepPostchecks  (step-level)
# ===========================================================================

# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


async def get_step_postchecks_by_id(
    db: AsyncIOMotorDatabase,
    postchecks_id: str,
) -> Optional[dict]:
    """Fetch a ProcessStepPostchecks container by its id."""
    doc = await db[STEP_COLLECTION].find_one({"id": postchecks_id})
    return _serialize(doc) if doc else None


async def get_step_postchecks_by_step_id(
    db: AsyncIOMotorDatabase,
    step_id: str,
) -> Optional[dict]:
    """Fetch the postchecks container that belongs to a given process_definition step."""
    if not step_id:
        return None
    doc = await db[STEP_COLLECTION].find_one({"process_step_id": step_id})
    return _serialize(doc) if doc else None


async def get_step_postchecks_by_process_definition_id(
    db: AsyncIOMotorDatabase,
    process_definition_id: str,
) -> list[dict]:
    """Return all step-level postchecks containers that share the same process_definition_id."""
    if not process_definition_id:
        return []
    cursor = db[STEP_COLLECTION].find({"process_definition_id": process_definition_id})
    return [_serialize(d) async for d in cursor]


async def get_step_conditions_by_severity(
    db: AsyncIOMotorDatabase,
    postchecks_id: str,
    severity: str,
) -> list[dict]:
    """Return all step-level conditions that match a given severity."""
    doc = await db[STEP_COLLECTION].find_one({"id": postchecks_id})
    if not doc:
        return []
    return [
        c
        for c in doc.get("process_step_post_check_conditions", [])
        if c.get("severity") == severity
    ]


async def get_step_conditions_by_corrective_action(
    db: AsyncIOMotorDatabase,
    postchecks_id: str,
    corrective_action_id: str,
) -> list[dict]:
    """Return step-level conditions that link to a specific corrective action ID."""
    doc = await db[STEP_COLLECTION].find_one({"id": postchecks_id})
    if not doc:
        return []
    return [
        c
        for c in doc.get("process_step_post_check_conditions", [])
        if corrective_action_id in c.get("links_to_corrective_action_ids", [])
    ]


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


async def create_step_postchecks(
    db: AsyncIOMotorDatabase,
    data: ProcessStepPostchecksCreate,
) -> dict:
    """Insert a new ProcessStepPostchecks container and return it."""
    doc = data.model_dump()
    await db[STEP_COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update_step_postchecks(
    db: AsyncIOMotorDatabase,
    postchecks_id: str,
    data: ProcessStepPostchecksUpdate,
) -> Optional[dict]:
    """Partially update a ProcessStepPostchecks container."""
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        return await get_step_postchecks_by_id(db, postchecks_id)
    result = await db[STEP_COLLECTION].find_one_and_update(
        {"id": postchecks_id},
        {"$set": fields},
        return_document=True,
    )
    return _serialize(result) if result else None


async def add_step_condition(
    db: AsyncIOMotorDatabase,
    postchecks_id: str,
    condition: PostCheckConditionCreate,
) -> Optional[dict]:
    """Append a new PostCheckCondition to a step's conditions array."""
    result = await db[STEP_COLLECTION].find_one_and_update(
        {"id": postchecks_id},
        {"$push": {"process_step_post_check_conditions": condition.model_dump()}},
        return_document=True,
    )
    return _serialize(result) if result else None


async def update_step_condition(
    db: AsyncIOMotorDatabase,
    postchecks_id: str,
    condition_id: str,
    fields: dict,
) -> Optional[dict]:
    """Update specific fields of a condition inside a step's conditions array."""
    updates = {
        f"process_step_post_check_conditions.$.{k}": v for k, v in fields.items()
    }
    result = await db[STEP_COLLECTION].find_one_and_update(
        {"id": postchecks_id, "process_step_post_check_conditions.id": condition_id},
        {"$set": updates},
        return_document=True,
    )
    return _serialize(result) if result else None


async def link_step_corrective_action(
    db: AsyncIOMotorDatabase,
    postchecks_id: str,
    condition_id: str,
    corrective_action_id: str,
) -> Optional[dict]:
    """Add a corrective action ID to a step condition's links array."""
    result = await db[STEP_COLLECTION].find_one_and_update(
        {"id": postchecks_id, "process_step_post_check_conditions.id": condition_id},
        {
            "$addToSet": {
                "process_step_post_check_conditions.$.links_to_corrective_action_ids": corrective_action_id
            }
        },
        return_document=True,
    )
    return _serialize(result) if result else None


async def unlink_step_corrective_action(
    db: AsyncIOMotorDatabase,
    postchecks_id: str,
    condition_id: str,
    corrective_action_id: str,
) -> Optional[dict]:
    """Remove a corrective action ID from a step condition's links array."""
    result = await db[STEP_COLLECTION].find_one_and_update(
        {"id": postchecks_id, "process_step_post_check_conditions.id": condition_id},
        {
            "$pull": {
                "process_step_post_check_conditions.$.links_to_corrective_action_ids": corrective_action_id
            }
        },
        return_document=True,
    )
    return _serialize(result) if result else None


async def remove_step_condition(
    db: AsyncIOMotorDatabase,
    postchecks_id: str,
    condition_id: str,
) -> Optional[dict]:
    """Remove a specific condition from a step's conditions array."""
    result = await db[STEP_COLLECTION].find_one_and_update(
        {"id": postchecks_id},
        {"$pull": {"process_step_post_check_conditions": {"id": condition_id}}},
        return_document=True,
    )
    return _serialize(result) if result else None


async def delete_step_postchecks(
    db: AsyncIOMotorDatabase,
    postchecks_id: str,
) -> bool:
    """Delete a ProcessStepPostchecks container by ID."""
    result = await db[STEP_COLLECTION].delete_one({"id": postchecks_id})
    return result.deleted_count == 1


async def delete_step_postchecks_by_step_id(
    db: AsyncIOMotorDatabase,
    step_id: str,
) -> bool:
    """Delete the step postchecks container that belongs to a step (cascade delete)."""
    result = await db[STEP_COLLECTION].delete_one({"process_step_id": step_id})
    return result.deleted_count == 1
