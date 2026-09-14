from datetime import datetime
from typing import Optional
from uuid import uuid4
from motor.motor_asyncio import AsyncIOMotorDatabase
from services.pm.models.calibration_point import (
    CalibrationPointCreate,
    CalibrationPointUpdate,
)

COLLECTION = "calibration_points"


def _serialize(doc: dict) -> dict:
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


# ── Read ──────────────────────────────────────────────────────────────────────


async def get_all(
    db: AsyncIOMotorDatabase,
    calibration_id: str,
) -> Optional[list[dict]]:
    doc = await db[COLLECTION].find_one(
        {"calibration_id": calibration_id},
        {"calibration_points": 1, "_id": 0},
    )
    if doc is None:
        return None
    return [_serialize(p) for p in doc.get("calibration_points", [])]


async def get_by_id(
    db: AsyncIOMotorDatabase,
    calibration_id: str,
    point_id: str,
) -> Optional[dict]:
    doc = await db[COLLECTION].find_one(
        {"calibration_id": calibration_id},
        {"calibration_points": 1, "_id": 0},
    )
    if doc is None:
        return None
    for point in doc.get("calibration_points", []):
        if point.get("point_id") == point_id:
            return _serialize(point)
    return None


# ── Create ────────────────────────────────────────────────────────────────────


async def create(
    db: AsyncIOMotorDatabase,
    calibration_id: str,
    data: CalibrationPointCreate,
) -> Optional[dict]:
    point = data.model_dump()
    point["point_id"] = str(uuid4())
    point["created_at"] = datetime.utcnow()
    point["updated_at"] = datetime.utcnow()

    result = await db[COLLECTION].find_one_and_update(
        {"calibration_id": calibration_id},
        {"$push": {"calibration_points": point}},
        return_document=True,
    )
    if result is None:
        return None
    return _serialize(point)


# ── Update ────────────────────────────────────────────────────────────────────


async def update(
    db: AsyncIOMotorDatabase,
    calibration_id: str,
    point_id: str,
    data: CalibrationPointUpdate,
) -> Optional[dict]:
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        return await get_by_id(db, calibration_id, point_id)

    fields["updated_at"] = datetime.utcnow()
    set_payload = {f"calibration_points.$.{k}": v for k, v in fields.items()}

    result = await db[COLLECTION].find_one_and_update(
        {"calibration_id": calibration_id, "calibration_points.point_id": point_id},
        {"$set": set_payload},
        return_document=True,
    )
    if result is None:
        return None

    for point in result.get("calibration_points", []):
        if point.get("point_id") == point_id:
            return _serialize(point)
    return None


# ── Delete ────────────────────────────────────────────────────────────────────


async def delete(
    db: AsyncIOMotorDatabase,
    calibration_id: str,
    point_id: str,
) -> bool:
    result = await db[COLLECTION].update_one(
        {"calibration_id": calibration_id},
        {"$pull": {"calibration_points": {"point_id": point_id}}},
    )
    return result.modified_count == 1
