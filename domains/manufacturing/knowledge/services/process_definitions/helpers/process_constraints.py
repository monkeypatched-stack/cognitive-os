"""
helpers/constraints_helper.py
------------------------------
MongoDB CRUD helper for ProcessConstraints and ProcessStepConstraints models.
Mirrors the pattern established in the devices helper.

Collections
-----------
  process_constraints       → ProcessConstraints documents (one per process_definition)
  process_step_constraints  → ProcessStepConstraints documents (one per step)
"""

from datetime import datetime, timezone
from typing import Optional
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.process_definitions.models.process_constraints import (
    ProcessConstraintsCreate,
    ProcessConstraintsUpdate,
    ProcessStepConstraintsCreate,
    ProcessStepConstraintsUpdate,
    ConstraintCreate,
)

WORKFLOW_COLLECTION = "process_constraints"
STEP_COLLECTION = "process_step_constraints"


def _serialize(doc: dict) -> dict:
    """Strip MongoDB's internal _id before returning a document."""
    doc.pop("_id", None)
    return doc


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def _infer_process_definition_id_for_step(
    db: AsyncIOMotorDatabase,
    step_id: str,
) -> Optional[str]:
    """Find the process_definition that owns a step id from either normalized or embedded docs."""
    step_container = await db["process_steps"].find_one(
        {"steps.id": step_id},
        {"process_definition_id": 1},
    )
    if step_container and step_container.get("process_definition_id"):
        return step_container["process_definition_id"]

    process_definition = await db["process_definitions"].find_one(
        {"steps.steps.id": step_id},
        {"process_definition_id": 1, "id": 1},
    )
    if process_definition:
        return process_definition.get(
            "process_definition_id"
        ) or process_definition.get("id")
    return None


async def _sync_embedded_step_constraints(
    db: AsyncIOMotorDatabase,
    step_id: str,
    constraints_doc: dict,
) -> None:
    """Keep denormalized process_definition step snapshots in sync when present."""
    snapshot = constraints_doc.copy()
    snapshot.pop("_id", None)
    await db["process_steps"].update_many(
        {"steps.id": step_id},
        {"$set": {"steps.$.constraints": snapshot}},
    )
    await db["process_definitions"].update_many(
        {"steps.steps.id": step_id},
        {"$set": {"steps.steps.$.constraints": snapshot}},
    )


# ===========================================================================
# ProcessConstraints  (process_definition-level)
# ===========================================================================

# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    """Return a paginated list of all ProcessConstraints containers."""
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
    constraints_id: str,
) -> Optional[dict]:
    """Fetch a ProcessConstraints container by its id."""
    doc = await db[WORKFLOW_COLLECTION].find_one({"id": constraints_id})
    return _serialize(doc) if doc else None


async def get_by_process_definition_id(
    db: AsyncIOMotorDatabase,
    process_definition_id: str,
) -> Optional[dict]:
    """Fetch the constraints container that belongs to a given process_definition."""
    if not process_definition_id:
        return None
    doc = await db[WORKFLOW_COLLECTION].find_one(
        {"process_definition_id": process_definition_id}
    )
    return _serialize(doc) if doc else None


async def get_constraints_by_type(
    db: AsyncIOMotorDatabase,
    constraints_id: str,
    constraint_type: str,
) -> list[dict]:
    """Return all constraints within a container that match a given constraint_type."""
    doc = await db[WORKFLOW_COLLECTION].find_one({"id": constraints_id})
    if not doc:
        return []
    return [
        c
        for c in doc.get("constraints", [])
        if c.get("constraint_type") == constraint_type
    ]


async def get_constraints_by_severity(
    db: AsyncIOMotorDatabase,
    constraints_id: str,
    severity: str,
) -> list[dict]:
    """Return all constraints within a container that match a given severity."""
    doc = await db[WORKFLOW_COLLECTION].find_one({"id": constraints_id})
    if not doc:
        return []
    return [c for c in doc.get("constraints", []) if c.get("severity") == severity]


async def get_hard_constraints(
    db: AsyncIOMotorDatabase,
    constraints_id: str,
) -> list[dict]:
    """Return only hard constraints (is_hard_constraint=True) from a container."""
    doc = await db[WORKFLOW_COLLECTION].find_one({"id": constraints_id})
    if not doc:
        return []
    return [
        c for c in doc.get("constraints", []) if c.get("is_hard_constraint") is True
    ]


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


async def create(
    db: AsyncIOMotorDatabase,
    data: ProcessConstraintsCreate,
) -> dict:
    """Insert a new ProcessConstraints container and return it."""
    doc = data.model_dump()
    await db[WORKFLOW_COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    constraints_id: str,
    data: ProcessConstraintsUpdate,
) -> Optional[dict]:
    """
    Partially update a ProcessConstraints container.
    Only non-None fields are written.
    """
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        return await get_by_id(db, constraints_id)
    result = await db[WORKFLOW_COLLECTION].find_one_and_update(
        {"id": constraints_id},
        {"$set": fields},
        return_document=True,
    )
    return _serialize(result) if result else None


async def add_constraint(
    db: AsyncIOMotorDatabase,
    constraints_id: str,
    constraint: ConstraintCreate,
) -> Optional[dict]:
    """Append a new Constraint to the constraints array."""
    result = await db[WORKFLOW_COLLECTION].find_one_and_update(
        {"id": constraints_id},
        {"$push": {"constraints": constraint.model_dump()}},
        return_document=True,
    )
    return _serialize(result) if result else None


async def update_constraint(
    db: AsyncIOMotorDatabase,
    constraints_id: str,
    constraint_id: str,
    fields: dict,
) -> Optional[dict]:
    """
    Update specific fields of a Constraint inside the constraints array.
    Uses the positional $ operator to target the matched element.
    Pass only the fields you want to change in the `fields` dict.
    """
    updates = {f"constraints.$.{k}": v for k, v in fields.items()}
    result = await db[WORKFLOW_COLLECTION].find_one_and_update(
        {"id": constraints_id, "constraints.id": constraint_id},
        {"$set": updates},
        return_document=True,
    )
    return _serialize(result) if result else None


async def remove_constraint(
    db: AsyncIOMotorDatabase,
    constraints_id: str,
    constraint_id: str,
) -> Optional[dict]:
    """Remove a specific constraint from the constraints array."""
    result = await db[WORKFLOW_COLLECTION].find_one_and_update(
        {"id": constraints_id},
        {"$pull": {"constraints": {"id": constraint_id}}},
        return_document=True,
    )
    return _serialize(result) if result else None


async def delete(
    db: AsyncIOMotorDatabase,
    constraints_id: str,
) -> bool:
    """Delete a ProcessConstraints container by ID."""
    result = await db[WORKFLOW_COLLECTION].delete_one({"id": constraints_id})
    return result.deleted_count == 1


async def delete_by_process_definition_id(
    db: AsyncIOMotorDatabase,
    process_definition_id: str,
) -> bool:
    """Delete the constraints container that belongs to a process_definition (cascade delete)."""
    result = await db[WORKFLOW_COLLECTION].delete_one(
        {"process_definition_id": process_definition_id}
    )
    return result.deleted_count == 1


# ===========================================================================
# ProcessStepConstraints  (step-level)
# ===========================================================================

# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


async def get_step_constraints_by_id(
    db: AsyncIOMotorDatabase,
    constraints_id: str,
) -> Optional[dict]:
    """Fetch a ProcessStepConstraints container by its id."""
    doc = await db[STEP_COLLECTION].find_one({"id": constraints_id})
    return _serialize(doc) if doc else None


async def get_step_constraints_by_step_id(
    db: AsyncIOMotorDatabase,
    step_id: str,
) -> Optional[dict]:
    """Fetch the constraints container that belongs to a given process_definition step."""
    if not step_id:
        return None
    doc = await db[STEP_COLLECTION].find_one({"process_step_id": step_id})
    return _serialize(doc) if doc else None


async def get_step_constraints_by_process_definition_id(
    db: AsyncIOMotorDatabase,
    process_definition_id: str,
) -> list[dict]:
    """Return all step-level constraints containers that share the same process_definition_id."""
    if not process_definition_id:
        return []
    cursor = db[STEP_COLLECTION].find({"process_definition_id": process_definition_id})
    return [_serialize(d) async for d in cursor]


async def get_step_constraints_by_type(
    db: AsyncIOMotorDatabase,
    constraints_id: str,
    constraint_type: str,
) -> list[dict]:
    """Return all step-level constraints that match a given constraint_type."""
    doc = await db[STEP_COLLECTION].find_one({"id": constraints_id})
    if not doc:
        return []
    return [
        c
        for c in doc.get("constraints", [])
        if c.get("constraint_type") == constraint_type
    ]


async def get_step_hard_constraints(
    db: AsyncIOMotorDatabase,
    constraints_id: str,
) -> list[dict]:
    """Return only hard constraints from a step constraints container."""
    doc = await db[STEP_COLLECTION].find_one({"id": constraints_id})
    if not doc:
        return []
    return [
        c for c in doc.get("constraints", []) if c.get("is_hard_constraint") is True
    ]


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


async def create_step_constraints(
    db: AsyncIOMotorDatabase,
    data: ProcessStepConstraintsCreate,
) -> dict:
    """Insert a new ProcessStepConstraints container and return it."""
    doc = data.model_dump()
    await db[STEP_COLLECTION].insert_one(doc)
    return _serialize(doc)


async def ensure_step_constraints_for_step(
    db: AsyncIOMotorDatabase,
    step_id: str,
    process_definition_id: Optional[str] = None,
) -> dict:
    """
    Return the step-level constraints container for a step, creating it if missing.
    Canvas-created nested constraint nodes may arrive before their parent record
    has been materialized in the normalized collection.
    """
    if not process_definition_id:
        process_definition_id = await _infer_process_definition_id_for_step(db, step_id)
    process_definition_id = process_definition_id or step_id
    now = _utc_now()
    result = await db[STEP_COLLECTION].find_one_and_update(
        {"process_step_id": step_id},
        {
            "$setOnInsert": {
                "id": f"constraints-{step_id}",
                "process_definition_id": process_definition_id,
                "process_step_id": step_id,
                "constraints": [],
                "created_at": now,
            },
            "$set": {"updated_at": now},
        },
        upsert=True,
        return_document=True,
    )
    serialized = _serialize(result)
    await _sync_embedded_step_constraints(db, step_id, serialized)
    return serialized


async def update_step_constraints(
    db: AsyncIOMotorDatabase,
    constraints_id: str,
    data: ProcessStepConstraintsUpdate,
) -> Optional[dict]:
    """Partially update a ProcessStepConstraints container."""
    fields = {k: v for k, v in data.model_dump().items() if v is not None}
    if not fields:
        return await get_step_constraints_by_id(db, constraints_id)
    result = await db[STEP_COLLECTION].find_one_and_update(
        {"id": constraints_id},
        {"$set": fields},
        return_document=True,
    )
    return _serialize(result) if result else None


async def upsert_step_constraint_by_step_id(
    db: AsyncIOMotorDatabase,
    step_id: str,
    constraint: ConstraintCreate,
) -> dict:
    """Create/update a constraint under the container owned by a process_definition step id."""
    container = await ensure_step_constraints_for_step(
        db,
        step_id,
        process_definition_id=constraint.process_definition_id,
    )
    constraints = list(container.get("constraints") or [])
    incoming = constraint.model_dump()
    now = _utc_now()
    replaced = False
    for index, existing in enumerate(constraints):
        if existing.get("id") == constraint.id:
            merged = {**existing, **incoming, "updated_at": now}
            constraints[index] = merged
            replaced = True
            break
    if not replaced:
        incoming["created_at"] = incoming.get("created_at") or now
        incoming["updated_at"] = incoming.get("updated_at") or now
        constraints.append(incoming)

    result = await db[STEP_COLLECTION].find_one_and_update(
        {"process_step_id": step_id},
        {"$set": {"constraints": constraints, "updated_at": now}},
        return_document=True,
    )
    serialized = _serialize(result)
    await _sync_embedded_step_constraints(db, step_id, serialized)
    return serialized


async def add_step_constraint(
    db: AsyncIOMotorDatabase,
    constraints_id: str,
    constraint: ConstraintCreate,
) -> Optional[dict]:
    """Append a new Constraint to a step's constraints array."""
    result = await db[STEP_COLLECTION].find_one_and_update(
        {"id": constraints_id},
        {"$push": {"constraints": constraint.model_dump()}},
        return_document=True,
    )
    return _serialize(result) if result else None


async def update_step_constraint(
    db: AsyncIOMotorDatabase,
    constraints_id: str,
    constraint_id: str,
    fields: dict,
) -> Optional[dict]:
    """Update specific fields of a constraint inside a step's constraints array."""
    updates = {f"constraints.$.{k}": v for k, v in fields.items()}
    result = await db[STEP_COLLECTION].find_one_and_update(
        {"id": constraints_id, "constraints.id": constraint_id},
        {"$set": updates},
        return_document=True,
    )
    return _serialize(result) if result else None


async def remove_step_constraint(
    db: AsyncIOMotorDatabase,
    constraints_id: str,
    constraint_id: str,
) -> Optional[dict]:
    """Remove a specific constraint from a step's constraints array."""
    result = await db[STEP_COLLECTION].find_one_and_update(
        {"id": constraints_id},
        {"$pull": {"constraints": {"id": constraint_id}}},
        return_document=True,
    )
    return _serialize(result) if result else None


async def delete_step_constraints(
    db: AsyncIOMotorDatabase,
    constraints_id: str,
) -> bool:
    """Delete a ProcessStepConstraints container by ID."""
    result = await db[STEP_COLLECTION].delete_one({"id": constraints_id})
    return result.deleted_count == 1


async def delete_step_constraints_by_step_id(
    db: AsyncIOMotorDatabase,
    step_id: str,
) -> bool:
    """Delete the constraints container that belongs to a step (cascade delete)."""
    result = await db[STEP_COLLECTION].delete_one({"process_step_id": step_id})
    return result.deleted_count == 1
