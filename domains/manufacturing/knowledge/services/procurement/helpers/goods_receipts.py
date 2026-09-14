from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.procurement.models.goods_receipts import (
    GoodsReceiptCreate,
    GoodsReceiptResponse,
    GoodsReceiptUpdate,
    QuarantineHoldCreate,
    QuarantineHoldUpdate,
)

GOODS_RECEIPTS_COLLECTION = "goods_receipts"
QUARANTINE_HOLDS_COLLECTION = "quarantine_holds"


def _serialize(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


def _prepare(value):
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if isinstance(value, dict):
        return {key: _prepare(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_prepare(item) for item in value]
    return value


def _normalize_status(value: object) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower().replace("_", " ").replace("-", " ")
        aliases = {
            "draft": "Draft",
            "received": "Submitted",
            "submitted": "Submitted",
            "qc hold": "QC Hold",
            "hold": "QC Hold",
            "released": "Released",
            "accepted": "Released",
            "rejected": "Rejected",
            "partial": "Partial",
        }
        if normalized in aliases:
            return aliases[normalized]
    return "Draft"


def _normalize_purchase_order(value: object, record: dict) -> dict:
    po = dict(value) if isinstance(value, dict) else {}
    po_number = str(po.get("po_number") or record.get("po_number") or "PO-UNKNOWN")
    po.setdefault("po_number", po_number)
    po.setdefault(
        "po_date",
        str(record.get("po_date") or record.get("created_at") or "2026-06-04"),
    )
    po.setdefault("supplier_id", str(record.get("supplier_id") or "SUP-UNKNOWN"))
    po.setdefault(
        "supplier_name", str(record.get("supplier_name") or "Unassigned Supplier")
    )
    po.setdefault(
        "buyer_id",
        str(record.get("buyer_id") or record.get("plant_id") or "BUYER-UNKNOWN"),
    )
    po.setdefault(
        "buyer_name",
        str(record.get("buyer_name") or record.get("plant") or "Unassigned Buyer"),
    )
    po.setdefault("total_amount", float(record.get("total_amount") or 0))
    po.setdefault("currency", str(record.get("currency") or "INR"))
    po.setdefault("payment_terms", str(record.get("payment_terms") or "Unspecified"))
    po.setdefault(
        "delivery_date",
        str(
            record.get("delivery_date")
            or record.get("received_at")
            or record.get("created_at")
            or "2026-06-04"
        ),
    )
    po.setdefault(
        "shipping_address",
        str(record.get("shipping_address") or record.get("plant") or "Unspecified"),
    )
    po.setdefault(
        "billing_address",
        str(record.get("billing_address") or record.get("plant") or "Unspecified"),
    )
    po.setdefault("status", str(record.get("po_status") or "Open"))
    return po


def _normalize_line(value: object, record: dict, index: int) -> dict:
    line = dict(value) if isinstance(value, dict) else {}
    gr_id = str(record.get("gr_id") or record.get("id") or "GR")
    line.setdefault(
        "gr_line_id", str(line.get("line_id") or f"{gr_id}-LINE-{index:03d}")
    )
    line.setdefault(
        "po_line_id",
        str(
            line.get("po_line_id")
            or line.get("line_number")
            or f"{record.get('po', {}).get('po_number', 'PO')}-LINE-{index:03d}"
        ),
    )
    line.setdefault(
        "material_code",
        str(
            line.get("material_code")
            or record.get("material_code")
            or "MATERIAL-UNKNOWN"
        ),
    )
    line.setdefault(
        "received_qty",
        line.get("quantity") or line.get("qty") or record.get("received_qty") or 0,
    )
    line.setdefault(
        "unit_of_measure",
        line.get("uom") or record.get("unit_of_measure") or record.get("uom") or "Each",
    )
    return line


def _normalize_goods_receipt_record(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None

    record = _serialize(doc)
    gr_id = str(
        record.get("gr_id") or record.get("id") or record.get("gr_number") or "GR"
    )
    record["gr_id"] = gr_id
    record.setdefault("gr_number", gr_id)
    record["po"] = _normalize_purchase_order(record.get("po"), record)
    record.setdefault(
        "received_by",
        str(record.get("created_by") or record.get("receiver") or "Receiving Operator"),
    )
    record["status"] = _normalize_status(record.get("status"))

    lines = record.get("lines") if isinstance(record.get("lines"), list) else []
    record["lines"] = [
        _normalize_line(line, record, index)
        for index, line in enumerate(lines or [{}], start=1)
    ]
    if not isinstance(record.get("attachments"), list):
        record["attachments"] = []
    audit = record.get("audit") if isinstance(record.get("audit"), dict) else {}
    audit.setdefault(
        "created_at", record.get("created_at") or datetime.now(timezone.utc)
    )
    audit.setdefault(
        "updated_at", record.get("updated_at") or datetime.now(timezone.utc)
    )
    record["audit"] = audit

    return GoodsReceiptResponse.model_validate(record).model_dump()


async def get_all_goods_receipts(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    query: dict = {}
    total = await db[GOODS_RECEIPTS_COLLECTION].count_documents(query)
    cursor = (
        db[GOODS_RECEIPTS_COLLECTION]
        .find(query)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return [_normalize_goods_receipt_record(doc) async for doc in cursor], total


async def get_goods_receipt_by_id(
    db: AsyncIOMotorDatabase, gr_id: str
) -> Optional[dict]:
    return _normalize_goods_receipt_record(
        await db[GOODS_RECEIPTS_COLLECTION].find_one({"gr_id": gr_id})
    )


async def get_goods_receipt_by_number(
    db: AsyncIOMotorDatabase,
    gr_number: str,
) -> Optional[dict]:
    return _normalize_goods_receipt_record(
        await db[GOODS_RECEIPTS_COLLECTION].find_one({"gr_number": gr_number})
    )


async def get_goods_receipts_by_po_number(
    db: AsyncIOMotorDatabase,
    po_number: str,
) -> list[dict]:
    cursor = db[GOODS_RECEIPTS_COLLECTION].find({"po.po_number": po_number})
    return [_normalize_goods_receipt_record(doc) async for doc in cursor]


async def get_goods_receipts_by_status(
    db: AsyncIOMotorDatabase, status: str
) -> list[dict]:
    cursor = db[GOODS_RECEIPTS_COLLECTION].find({"status": status})
    return [_normalize_goods_receipt_record(doc) async for doc in cursor]


async def create_goods_receipt(
    db: AsyncIOMotorDatabase, data: GoodsReceiptCreate
) -> dict:
    doc = _prepare(data.model_dump())
    await db[GOODS_RECEIPTS_COLLECTION].insert_one(doc)
    return _normalize_goods_receipt_record(doc)


async def update_goods_receipt(
    db: AsyncIOMotorDatabase,
    gr_id: str,
    data: GoodsReceiptUpdate,
) -> Optional[dict]:
    fields = _prepare(data.model_dump(exclude_unset=True))
    if not fields:
        return await get_goods_receipt_by_id(db, gr_id)
    result = await db[GOODS_RECEIPTS_COLLECTION].find_one_and_update(
        {"gr_id": gr_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    return _normalize_goods_receipt_record(result)


async def delete_goods_receipt(db: AsyncIOMotorDatabase, gr_id: str) -> bool:
    result = await db[GOODS_RECEIPTS_COLLECTION].delete_one({"gr_id": gr_id})
    return result.deleted_count == 1


async def get_all_quarantine_holds(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    query: dict = {}
    total = await db[QUARANTINE_HOLDS_COLLECTION].count_documents(query)
    cursor = (
        db[QUARANTINE_HOLDS_COLLECTION]
        .find(query)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return [_serialize(doc) async for doc in cursor], total


async def get_quarantine_hold_by_id(
    db: AsyncIOMotorDatabase,
    hold_id: str,
) -> Optional[dict]:
    return _serialize(
        await db[QUARANTINE_HOLDS_COLLECTION].find_one({"hold_id": hold_id})
    )


async def get_quarantine_holds_by_gr_id(
    db: AsyncIOMotorDatabase,
    gr_id: str,
) -> list[dict]:
    cursor = db[QUARANTINE_HOLDS_COLLECTION].find({"gr_id": gr_id})
    return [_serialize(doc) async for doc in cursor]


async def get_active_quarantine_holds(db: AsyncIOMotorDatabase) -> list[dict]:
    cursor = db[QUARANTINE_HOLDS_COLLECTION].find({"is_active": True})
    return [_serialize(doc) async for doc in cursor]


async def create_quarantine_hold(
    db: AsyncIOMotorDatabase, data: QuarantineHoldCreate
) -> dict:
    doc = _prepare(data.model_dump())
    await db[QUARANTINE_HOLDS_COLLECTION].insert_one(doc)
    return _serialize(doc)


async def update_quarantine_hold(
    db: AsyncIOMotorDatabase,
    hold_id: str,
    data: QuarantineHoldUpdate,
) -> Optional[dict]:
    fields = _prepare(data.model_dump(exclude_unset=True))
    if not fields:
        return await get_quarantine_hold_by_id(db, hold_id)
    result = await db[QUARANTINE_HOLDS_COLLECTION].find_one_and_update(
        {"hold_id": hold_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result)


async def delete_quarantine_hold(db: AsyncIOMotorDatabase, hold_id: str) -> bool:
    result = await db[QUARANTINE_HOLDS_COLLECTION].delete_one({"hold_id": hold_id})
    return result.deleted_count == 1
