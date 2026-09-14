from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from services.changeover.helpers.changeover_common import (
    create,
    delete,
    get_all,
    get_by_id,
    update,
)
from services.changeover.models.changeover_kpis import (
    ChangeoverKPICreate,
    ChangeoverKPIUpdate,
)

COLLECTION = "changeover_kpis"


async def get_all_kpis(
    db: AsyncIOMotorDatabase, page: int = 1, page_size: int = 20
) -> tuple[list[dict], int]:
    return await get_all(db, COLLECTION, page, page_size)


async def get_kpi_by_id(db: AsyncIOMotorDatabase, kpi_id: str) -> Optional[dict]:
    return await get_by_id(db, COLLECTION, kpi_id)


async def get_kpis_by_workstation(
    db: AsyncIOMotorDatabase, workstation_id: str
) -> list[dict]:
    records, _ = await get_all(
        db, COLLECTION, query={"workstation_id": workstation_id}, page_size=1000
    )
    return records


async def create_kpi(db: AsyncIOMotorDatabase, data: ChangeoverKPICreate) -> dict:
    return await create(db, COLLECTION, data)


async def update_kpi(
    db: AsyncIOMotorDatabase, kpi_id: str, data: ChangeoverKPIUpdate
) -> Optional[dict]:
    return await update(db, COLLECTION, kpi_id, data)


async def delete_kpi(db: AsyncIOMotorDatabase, kpi_id: str) -> bool:
    return await delete(db, COLLECTION, kpi_id)
