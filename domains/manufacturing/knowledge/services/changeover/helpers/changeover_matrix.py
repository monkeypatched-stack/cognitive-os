from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from services.changeover.helpers.changeover_common import (
    create,
    delete,
    get_all,
    get_by_id,
    update,
)
from services.changeover.models.changeover_matrix import (
    ChangeoverMatrixEntryCreate,
    ChangeoverMatrixEntryResponse,
    ChangeoverMatrixEntryUpdate,
)

COLLECTION = "changeover_matrix"


def _normalize_matrix_record(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None

    record = dict(doc)
    record.pop("_id", None)
    entry_id = str(record.get("id") or record.get("entry_id") or "CHANGEOVER-MATRIX")
    record["id"] = entry_id
    record.setdefault(
        "workstation_id", str(record.get("workstation") or "UNKNOWN-WORKSTATION")
    )
    record["from_product_id"] = str(
        record.get("from_product_id")
        or record.get("from_product")
        or "UNKNOWN-FROM-PRODUCT"
    )
    record["to_product_id"] = str(
        record.get("to_product_id")
        or record.get("to_product")
        or record["from_product_id"]
    )
    record["standard_minutes"] = int(
        record.get("standard_minutes")
        or record.get("target_minutes")
        or record.get("duration_minutes")
        or 1
    )
    record["requires_cleaning"] = bool(record.get("requires_cleaning") or False)
    record["requires_tooling"] = bool(record.get("requires_tooling") or False)
    record["min_operators"] = int(record.get("min_operators") or 1)
    best = record.get("best_achieved_minutes")
    if best is not None and int(best) > record["standard_minutes"]:
        record["best_achieved_minutes"] = record["standard_minutes"]

    return ChangeoverMatrixEntryResponse.model_validate(record).model_dump()


async def get_all_matrix(
    db: AsyncIOMotorDatabase, page: int = 1, page_size: int = 20
) -> tuple[list[dict], int]:
    records, total = await get_all(db, COLLECTION, page, page_size)
    return [_normalize_matrix_record(record) for record in records], total


async def get_matrix_by_id(db: AsyncIOMotorDatabase, entry_id: str) -> Optional[dict]:
    return _normalize_matrix_record(await get_by_id(db, COLLECTION, entry_id))


async def get_matrix_by_workstation(
    db: AsyncIOMotorDatabase, workstation_id: str
) -> list[dict]:
    records, _ = await get_all(
        db, COLLECTION, query={"workstation_id": workstation_id}, page_size=1000
    )
    return [_normalize_matrix_record(record) for record in records]


async def create_matrix(
    db: AsyncIOMotorDatabase, data: ChangeoverMatrixEntryCreate
) -> dict:
    return _normalize_matrix_record(await create(db, COLLECTION, data))


async def update_matrix(
    db: AsyncIOMotorDatabase, entry_id: str, data: ChangeoverMatrixEntryUpdate
) -> Optional[dict]:
    return _normalize_matrix_record(await update(db, COLLECTION, entry_id, data))


async def delete_matrix(db: AsyncIOMotorDatabase, entry_id: str) -> bool:
    return await delete(db, COLLECTION, entry_id)
