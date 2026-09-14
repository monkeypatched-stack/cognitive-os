"""
helpers/prechecks_helper.py
----------------------------
MongoDB CRUD helper for ProcessPrechecks and ProcessStepPrechecks models.
Mirrors the pattern established in the devices helper.

Collections
-----------
  process_definition_prechecks       → ProcessPrechecks documents (one per process_definition)
  process_step_prechecks  → ProcessStepPrechecks documents (one per step)
"""

from typing import Optional
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.process_definitions.models.process_pre_checks import (
    ProcessPrechecksCreate,
    ProcessPrechecksUpdate,
    ProcessStepPrechecksCreate,
    ProcessStepPrechecksUpdate,
    PreCheckConditionCreate,
)

WORKFLOW_COLLECTION = "process_definition_prechecks"
STEP_COLLECTION = "process_step_prechecks"


def _serialize(doc: dict) -> dict:
    """Strip MongoDB's internal _id before returning a document."""
    doc.pop("_id", None)
    return doc


# ===========================================================================
# ProcessPrechecks  (process_definition-level)
# ===========================================================================

# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    """Return a paginated list of all ProcessPrechecks containers."""
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
    prechecks_id: str,
) -> Optional[dict]:
    """Fetch a ProcessPrechecks container by its id."""
    doc = await db[WORKFLOW_COLLECTION].find_one({"id": prechecks_id})
    return _serialize(doc) if doc else None


async def get_by_process_definition_id(
    db: AsyncIOMotorDatabase,
    process_definition_id: str,
) -> Optional[dict]:
    """Fetch the prechecks container that belongs to a given process_definition."""
    if not process_definition_id:
        return None
    doc = await db[WORKFLOW_COLLECTION].find_one(
        {"process_definition_id": process_definition_id}
    )
    return _serialize(doc) if doc else None


async def get_conditions_by_severity(
    db: AsyncIOMotorDatabase,
    prechecks_id: str,
    severity: str,
) -> list[dict]:
    """Return all conditions within a container that match a given severity."""
    doc = await db[WORKFLOW_COLLECTION].find_one({"id": prechecks_id})
    if not doc:
        return []
    return [
        c
        for c in doc.get("process_definition_precheck_conditions", [])
        if c.get("severity") == severity
    ]


async def get_mandatory_conditions(
    db: AsyncIOMotorDatabase,
    prechecks_id: str,
) -> list[dict]:
    """Return only the mandatory conditions from a container."""
    doc = await db[WORKFLOW_COLLECTION].find_one({"id": prechecks_id})
    if not doc:
        return []
    return [
        c
        for c in doc.get("process_definition_precheck_conditions", [])
        if c.get("is_mandatory") is True
    ]


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


async def create(
    db: AsyncIOMotorDatabase,
    data: ProcessPrechecksCreate,
) -> dict:
    """Insert a new ProcessPrechecks container and return it."""
    doc = data.model_dump()
    await db[WORKFLOW_COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    prechecks_id: str,
    data: ProcessPrechecksUpdate,
) -> Optional[dict]:
    """
    Partially update a ProcessPrechecks container.
    Only non-None fields are written.
    """
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        return await get_by_id(db, prechecks_id)
    result = await db[WORKFLOW_COLLECTION].find_one_and_update(
        {"id": prechecks_id},
        {"$set": fields},
        return_document=True,
    )
    return _serialize(result) if result else None


async def add_condition(
    db: AsyncIOMotorDatabase,
    prechecks_id: str,
    condition: PreCheckConditionCreate,
) -> Optional[dict]:
    """Append a new PreCheckCondition to the conditions array."""
    result = await db[WORKFLOW_COLLECTION].find_one_and_update(
        {"id": prechecks_id},
        {"$push": {"process_definition_precheck_conditions": condition.model_dump()}},
        return_document=True,
    )
    return _serialize(result) if result else None


async def update_condition(
    db: AsyncIOMotorDatabase,
    prechecks_id: str,
    condition_id: str,
    fields: dict,
) -> Optional[dict]:
    """
    Update specific fields of a PreCheckCondition inside the conditions array.
    Pass only the fields you want to change in the `fields` dict.
    """
    updates = {
        f"process_definition_precheck_conditions.$.{k}": v for k, v in fields.items()
    }
    result = await db[WORKFLOW_COLLECTION].find_one_and_update(
        {"id": prechecks_id, "process_definition_precheck_conditions.id": condition_id},
        {"$set": updates},
        return_document=True,
    )
    return _serialize(result) if result else None


async def remove_condition(
    db: AsyncIOMotorDatabase,
    prechecks_id: str,
    condition_id: str,
) -> Optional[dict]:
    """Remove a specific condition from the conditions array."""
    result = await db[WORKFLOW_COLLECTION].find_one_and_update(
        {"id": prechecks_id},
        {"$pull": {"process_definition_precheck_conditions": {"id": condition_id}}},
        return_document=True,
    )
    return _serialize(result) if result else None


async def delete(
    db: AsyncIOMotorDatabase,
    prechecks_id: str,
) -> bool:
    """Delete a ProcessPrechecks container by ID."""
    result = await db[WORKFLOW_COLLECTION].delete_one({"id": prechecks_id})
    return result.deleted_count == 1


async def delete_by_process_definition_id(
    db: AsyncIOMotorDatabase,
    process_definition_id: str,
) -> bool:
    """Delete the prechecks container that belongs to a process_definition (cascade delete)."""
    result = await db[WORKFLOW_COLLECTION].delete_one(
        {"process_definition_id": process_definition_id}
    )
    return result.deleted_count == 1


# ===========================================================================
# ProcessStepPrechecks  (step-level)
# ===========================================================================

# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


async def get_step_prechecks_by_id(
    db: AsyncIOMotorDatabase,
    prechecks_id: str,
) -> Optional[dict]:
    """Fetch a ProcessStepPrechecks container by its id."""
    doc = await db[STEP_COLLECTION].find_one({"id": prechecks_id})
    return _serialize(doc) if doc else None


async def get_step_prechecks_by_step_id(
    db: AsyncIOMotorDatabase,
    step_id: str,
) -> Optional[dict]:
    """Fetch the prechecks container that belongs to a given process_definition step."""
    if not step_id:
        return None
    doc = await db[STEP_COLLECTION].find_one({"process_step_id": step_id})
    return _serialize(doc) if doc else None


async def get_step_prechecks_by_process_definition_id(
    db: AsyncIOMotorDatabase,
    process_definition_id: str,
) -> list[dict]:
    """Return all step-level prechecks containers that share the same process_definition_id."""
    if not process_definition_id:
        return []
    cursor = db[STEP_COLLECTION].find({"process_definition_id": process_definition_id})
    return [_serialize(d) async for d in cursor]


async def get_step_conditions_by_severity(
    db: AsyncIOMotorDatabase,
    prechecks_id: str,
    severity: str,
) -> list[dict]:
    """Return all step-level conditions that match a given severity."""
    doc = await db[STEP_COLLECTION].find_one({"id": prechecks_id})
    if not doc:
        return []
    return [
        c
        for c in doc.get("process_step_precheck_conditions", [])
        if c.get("severity") == severity
    ]


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


async def create_step_prechecks(
    db: AsyncIOMotorDatabase,
    data: ProcessStepPrechecksCreate,
) -> dict:
    """Insert a new ProcessStepPrechecks container and return it."""
    doc = data.model_dump()
    await db[STEP_COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update_step_prechecks(
    db: AsyncIOMotorDatabase,
    prechecks_id: str,
    data: ProcessStepPrechecksUpdate,
) -> Optional[dict]:
    """Partially update a ProcessStepPrechecks container."""
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        return await get_step_prechecks_by_id(db, prechecks_id)
    result = await db[STEP_COLLECTION].find_one_and_update(
        {"id": prechecks_id},
        {"$set": fields},
        return_document=True,
    )
    return _serialize(result) if result else None


async def add_step_condition(
    db: AsyncIOMotorDatabase,
    prechecks_id: str,
    condition: PreCheckConditionCreate,
) -> Optional[dict]:
    """Append a new PreCheckCondition to a step's conditions array."""
    result = await db[STEP_COLLECTION].find_one_and_update(
        {"id": prechecks_id},
        {"$push": {"process_step_precheck_conditions": condition.model_dump()}},
        return_document=True,
    )
    return _serialize(result) if result else None


async def update_step_condition(
    db: AsyncIOMotorDatabase,
    prechecks_id: str,
    condition_id: str,
    fields: dict,
) -> Optional[dict]:
    """Update specific fields of a condition inside a step's conditions array."""
    updates = {f"process_step_precheck_conditions.$.{k}": v for k, v in fields.items()}
    result = await db[STEP_COLLECTION].find_one_and_update(
        {"id": prechecks_id, "process_step_precheck_conditions.id": condition_id},
        {"$set": updates},
        return_document=True,
    )
    return _serialize(result) if result else None


async def remove_step_condition(
    db: AsyncIOMotorDatabase,
    prechecks_id: str,
    condition_id: str,
) -> Optional[dict]:
    """Remove a specific condition from a step's conditions array."""
    result = await db[STEP_COLLECTION].find_one_and_update(
        {"id": prechecks_id},
        {"$pull": {"process_step_precheck_conditions": {"id": condition_id}}},
        return_document=True,
    )
    return _serialize(result) if result else None


async def delete_step_prechecks(
    db: AsyncIOMotorDatabase,
    prechecks_id: str,
) -> bool:
    """Delete a ProcessStepPrechecks container by ID."""
    result = await db[STEP_COLLECTION].delete_one({"id": prechecks_id})
    return result.deleted_count == 1


async def delete_step_prechecks_by_step_id(
    db: AsyncIOMotorDatabase,
    step_id: str,
) -> bool:
    """Delete the step prechecks container that belongs to a step (cascade delete)."""
    result = await db[STEP_COLLECTION].delete_one({"process_step_id": step_id})
    return result.deleted_count == 1
