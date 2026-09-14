from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.changeover.helpers.changeover_common import (
    create,
    delete,
    dump,
    get_all,
    get_by_id,
    serialize,
    update,
)
from services.changeover.models.changeover_events import (
    ChangeoverEventCreate,
    ChangeoverEventUpdate,
)
from services.changeover.models.changeover_tasks import (
    ChangeoverTaskCreate,
    ChangeoverTaskUpdate,
)

COLLECTION = "changeover_events"


async def get_all_events(
    db: AsyncIOMotorDatabase, page: int = 1, page_size: int = 20
) -> tuple[list[dict], int]:
    return await get_all(db, COLLECTION, page, page_size)


async def get_event_by_id(db: AsyncIOMotorDatabase, event_id: str) -> Optional[dict]:
    return await get_by_id(db, COLLECTION, event_id)


async def get_events_by_workstation(
    db: AsyncIOMotorDatabase, workstation_id: str
) -> list[dict]:
    records, _ = await get_all(
        db, COLLECTION, query={"workstation_id": workstation_id}, page_size=1000
    )
    return records


async def get_events_by_status(db: AsyncIOMotorDatabase, status: str) -> list[dict]:
    records, _ = await get_all(db, COLLECTION, query={"status": status}, page_size=1000)
    return records


async def create_event(db: AsyncIOMotorDatabase, data: ChangeoverEventCreate) -> dict:
    return await create(db, COLLECTION, data)


async def update_event(
    db: AsyncIOMotorDatabase, event_id: str, data: ChangeoverEventUpdate
) -> Optional[dict]:
    return await update(db, COLLECTION, event_id, data)


async def delete_event(db: AsyncIOMotorDatabase, event_id: str) -> bool:
    return await delete(db, COLLECTION, event_id)


async def add_event_task(
    db: AsyncIOMotorDatabase, event_id: str, data: ChangeoverTaskCreate
) -> Optional[dict]:
    result = await db[COLLECTION].find_one_and_update(
        {"id": event_id},
        {"$push": {"tasks": dump(data)}},
        return_document=ReturnDocument.AFTER,
    )
    return serialize(result) if result else None


async def update_event_task(
    db: AsyncIOMotorDatabase,
    event_id: str,
    task_id: str,
    data: ChangeoverTaskUpdate,
) -> Optional[dict]:
    fields = {
        f"tasks.$.{key}": value
        for key, value in dump(data).items()
        if value is not None
    }
    if not fields:
        return await get_event_by_id(db, event_id)
    result = await db[COLLECTION].find_one_and_update(
        {"id": event_id, "tasks.id": task_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    return serialize(result) if result else None
