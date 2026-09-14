from fastapi import APIRouter, HTTPException, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from typing import Optional
from datetime import datetime, timezone

from services.common.db import get_database
from services.common.auth import get_current_user

router = APIRouter()
COLLECTION = "families"


def _serialize(doc):
    if not doc:
        return None
    d = dict(doc)
    d.pop("_id", None)
    return d


@router.get("/")
async def list_families(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    total = await db[COLLECTION].count_documents({})
    cursor = (
        db[COLLECTION]
        .find({})
        .sort("name", 1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    results = [_serialize(d) async for d in cursor]
    return {"total": total, "page": page, "page_size": page_size, "results": results}


@router.get("/{family_id}")
async def get_family(
    family_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    doc = await db[COLLECTION].find_one({"id": family_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Not found")
    return _serialize(doc)


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_family(
    data: dict,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    data["created_at"] = datetime.now(timezone.utc)
    data["updated_at"] = datetime.now(timezone.utc)
    await db[COLLECTION].insert_one(data)
    return _serialize(data)


@router.patch("/{family_id}")
async def update_family(
    family_id: str,
    data: dict,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    data["updated_at"] = datetime.now(timezone.utc)
    result = await db[COLLECTION].find_one_and_update(
        {"id": family_id}, {"$set": data}, return_document=True
    )
    if not result:
        raise HTTPException(status_code=404, detail="Not found")
    return _serialize(result)


@router.delete("/{family_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_family(
    family_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    await db[COLLECTION].delete_one({"id": family_id})
