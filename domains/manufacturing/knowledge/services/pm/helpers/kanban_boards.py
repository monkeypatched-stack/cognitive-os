from __future__ import annotations

from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.pm.models.kanban_board import (
    KanbanBoardCreate,
    KanbanBoardUpdate,
    KanbanCardCreate,
    KanbanCardMove,
    KanbanCardUpdate,
)

COLLECTION = "kanban_boards"


def _serialize(doc: dict) -> dict:
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


def _dump(data) -> dict:
    return data.model_dump(mode="json", exclude_unset=True)


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    query: dict = {}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(d) async for d in cursor], total


async def get_by_id(db: AsyncIOMotorDatabase, board_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"board_id": board_id})
    return _serialize(doc) if doc else None


async def get_by_workstation(
    db: AsyncIOMotorDatabase, workstation_id: str
) -> list[dict]:
    cursor = db[COLLECTION].find({"workstation_id": workstation_id})
    return [_serialize(d) async for d in cursor]


async def get_by_line(db: AsyncIOMotorDatabase, line_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"line_id": line_id})
    return [_serialize(d) async for d in cursor]


async def get_by_stage(db: AsyncIOMotorDatabase, stage_id: str) -> list[dict]:
    cursor = db[COLLECTION].find({"stage_id": stage_id})
    return [_serialize(d) async for d in cursor]


async def get_cards_by_status(
    db: AsyncIOMotorDatabase,
    board_id: str,
    status: str,
) -> list[dict]:
    board = await get_by_id(db, board_id)
    if not board:
        return []
    return [card for card in board.get("cards", []) if card.get("status") == status]


async def get_card_by_id(
    db: AsyncIOMotorDatabase,
    board_id: str,
    card_id: str,
) -> Optional[dict]:
    doc = await db[COLLECTION].find_one(
        {"board_id": board_id, "cards.card_id": card_id},
        {"cards.$": 1},
    )
    if not doc or not doc.get("cards"):
        return None
    return doc["cards"][0]


async def create(db: AsyncIOMotorDatabase, data: KanbanBoardCreate) -> dict:
    doc = _dump(data)
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update(
    db: AsyncIOMotorDatabase,
    board_id: str,
    data: KanbanBoardUpdate,
) -> Optional[dict]:
    fields = _dump(data)
    if not fields:
        return await get_by_id(db, board_id)
    result = await db[COLLECTION].find_one_and_update(
        {"board_id": board_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result) if result else None


async def delete(db: AsyncIOMotorDatabase, board_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"board_id": board_id})
    return result.deleted_count == 1


async def add_card(
    db: AsyncIOMotorDatabase,
    board_id: str,
    data: KanbanCardCreate,
) -> Optional[dict]:
    result = await db[COLLECTION].find_one_and_update(
        {"board_id": board_id},
        {
            "$push": {"cards": _dump(data)},
            "$set": {"updated_at": data.updated_at},
        },
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result) if result else None


async def update_card(
    db: AsyncIOMotorDatabase,
    board_id: str,
    card_id: str,
    data: KanbanCardUpdate,
) -> Optional[dict]:
    fields = {
        f"cards.$.{key}": value
        for key, value in _dump(data).items()
        if value is not None
    }
    if not fields:
        return await get_by_id(db, board_id)
    fields["updated_at"] = data.updated_at
    result = await db[COLLECTION].find_one_and_update(
        {"board_id": board_id, "cards.card_id": card_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result) if result else None


async def move_card(
    db: AsyncIOMotorDatabase,
    board_id: str,
    card_id: str,
    data: KanbanCardMove,
) -> Optional[dict]:
    fields = {
        "cards.$.status": data.status,
        "cards.$.updated_at": data.updated_at,
        "updated_at": data.updated_at,
    }
    if data.sort_order is not None:
        fields["cards.$.sort_order"] = data.sort_order
    if data.rejection_reason is not None:
        fields["cards.$.rejection_reason"] = data.rejection_reason
    if data.notes is not None:
        fields["cards.$.notes"] = data.notes

    result = await db[COLLECTION].find_one_and_update(
        {"board_id": board_id, "cards.card_id": card_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result) if result else None


async def remove_card(
    db: AsyncIOMotorDatabase,
    board_id: str,
    card_id: str,
) -> Optional[dict]:
    result = await db[COLLECTION].find_one_and_update(
        {"board_id": board_id},
        {"$pull": {"cards": {"card_id": card_id}}},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result) if result else None
