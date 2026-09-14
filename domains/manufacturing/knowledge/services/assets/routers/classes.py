from fastapi import APIRouter, HTTPException, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from typing import Optional
from datetime import datetime, timezone

from services.common.db import get_database
from services.common.auth import get_current_user

router = APIRouter()
COLLECTION = "classes"


def _serialize(doc):
    if not doc:
        return None
    d = dict(doc)
    d.pop("_id", None)
    return d


@router.get("/")
async def list_classes(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1),
    family_id: Optional[str] = Query(None),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    query = {}
    if family_id:
        query["family_id"] = family_id
    total = await db[COLLECTION].count_documents(query)
    cursor = (
        db[COLLECTION]
        .find(query)
        .sort("name", 1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    results = [_serialize(d) async for d in cursor]
    return {"total": total, "page": page, "page_size": page_size, "results": results}


@router.get("/{class_id}")
async def get_class(
    class_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    doc = await db[COLLECTION].find_one({"id": class_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Not found")
    return _serialize(doc)


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_class(
    data: dict,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    data["created_at"] = datetime.now(timezone.utc)
    data["updated_at"] = datetime.now(timezone.utc)
    await db[COLLECTION].insert_one(data)
    return _serialize(data)


@router.patch("/{class_id}")
async def update_class(
    class_id: str,
    data: dict,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    data["updated_at"] = datetime.now(timezone.utc)
    result = await db[COLLECTION].find_one_and_update(
        {"id": class_id}, {"$set": data}, return_document=True
    )
    if not result:
        raise HTTPException(status_code=404, detail="Not found")
    return _serialize(result)


@router.delete("/{class_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_class(
    class_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(get_current_user),
):
    await db[COLLECTION].delete_one({"id": class_id})
