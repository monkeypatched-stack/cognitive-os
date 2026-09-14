from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.procurement.models.sampling import (
    ChainOfCustodyEntry,
    ResamplingRecordCreate,
    ResamplingRecordResponse,
    ResamplingRecordUpdate,
    SampleCreate,
    SampleResponse,
    SampleStatus,
    SampleType,
    SampleUpdate,
    SamplingPlanCreate,
    SamplingPlanResponse,
    SamplingPlanUpdate,
    utc_now,
)

SAMPLING_PLANS_COLLECTION = "sampling_plans"
SAMPLES_COLLECTION = "samples"
RESAMPLING_RECORDS_COLLECTION = "resampling_records"


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


def _normalize_sampling_method(value: object) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower().replace("_", " ").replace("-", " ")
        aliases = {
            "aql": "AQL Standard",
            "aql standard": "AQL Standard",
            "fixed": "Fixed Quantity",
            "fixed quantity": "Fixed Quantity",
            "percentage": "Percentage Based",
            "percentage based": "Percentage Based",
            "skip lot": "Skip Lot",
            "zero defect": "Zero Defect",
        }
        if normalized in aliases:
            return aliases[normalized]
    return "AQL Standard"


def _normalize_risk(value: object) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        aliases = {"high": "High", "medium": "Medium", "med": "Medium", "low": "Low"}
        if normalized in aliases:
            return aliases[normalized]
    return "Medium"


def _normalize_aql(value: object) -> str:
    if isinstance(value, str):
        normalized = value.strip().upper()
        aliases = {
            "1": "I",
            "I": "I",
            "2": "II",
            "II": "II",
            "3": "III",
            "III": "III",
            "S1": "S-1",
            "S-1": "S-1",
            "S2": "S-2",
            "S-2": "S-2",
            "S3": "S-3",
            "S-3": "S-3",
            "S4": "S-4",
            "S-4": "S-4",
        }
        if normalized in aliases:
            return aliases[normalized]
    return "II"


def _normalize_plan_version(
    value: object, plan_code: str, created_at: object = None
) -> dict:
    version = dict(value) if isinstance(value, dict) else {}
    version.setdefault("version_number", str(version.get("version") or "1.0"))
    version.setdefault("effective_date", created_at or utc_now())
    version.setdefault("change_summary", f"Initial version for {plan_code}")
    version.setdefault("approved_by", "QA Manager")
    version.setdefault("status", "Approved")
    return version


def _normalize_sample_type(value: object) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower().replace("_", " ").replace("-", " ")
        aliases = {
            "retain": "Retain Sample",
            "retain sample": "Retain Sample",
            "lab": "Send-to-Lab Sample",
            "send to lab": "Send-to-Lab Sample",
            "send to lab sample": "Send-to-Lab Sample",
            "in house": "In-House Test Sample",
            "in house test sample": "In-House Test Sample",
        }
        if normalized in aliases:
            return aliases[normalized]
    return SampleType.IN_HOUSE.value


def _normalize_sample_status(value: object) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower().replace("_", " ").replace("-", " ")
        aliases = {
            "pending": "Pending Collection",
            "pending collection": "Pending Collection",
            "collected": "Collected",
            "in transit": "In Transit to Lab",
            "in transit to lab": "In Transit to Lab",
            "testing": "In Testing",
            "in testing": "In Testing",
            "results": "Results Received",
            "results received": "Results Received",
            "disposed": "Disposed",
        }
        if normalized in aliases:
            return aliases[normalized]
    return SampleStatus.PENDING.value


def _normalize_sampling_plan_record(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None

    record = _serialize(doc)
    plan_id = str(
        record.get("plan_id")
        or record.get("id")
        or record.get("plan_code")
        or "SAMPLING-PLAN"
    )
    plan_code = str(record.get("plan_code") or plan_id)
    record["plan_id"] = plan_id
    record["plan_code"] = plan_code
    record.setdefault("name", record.get("description") or f"{plan_code} Sampling Plan")
    record["risk_classification"] = _normalize_risk(
        record.get("risk_classification") or record.get("risk")
    )
    record["sampling_method"] = _normalize_sampling_method(
        record.get("sampling_method") or record.get("method")
    )

    if record["sampling_method"] == "AQL Standard":
        record["aql_level"] = _normalize_aql(record.get("aql_level"))
    elif record["sampling_method"] == "Fixed Quantity":
        record["fixed_sample_qty"] = int(
            record.get("fixed_sample_qty")
            or record.get("sample_qty")
            or record.get("min_sample_qty")
            or 1
        )
    elif record["sampling_method"] == "Percentage Based":
        record["percentage"] = float(record.get("percentage") or 1)
    elif record["sampling_method"] == "Skip Lot":
        record["skip_lot_frequency"] = int(record.get("skip_lot_frequency") or 1)

    record["current_version"] = _normalize_plan_version(
        record.get("current_version"),
        plan_code,
        record.get("created_at"),
    )
    history = record.get("history")
    record["history"] = history if isinstance(history, list) else []

    return SamplingPlanResponse.model_validate(record).model_dump()


def _normalize_sample_record(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None

    record = _serialize(doc)
    sample_id = str(
        record.get("sample_id")
        or record.get("id")
        or record.get("sample_code")
        or "SAMPLE"
    )
    record["sample_id"] = sample_id
    record["sample_code"] = str(record.get("sample_code") or sample_id)
    record["gr_id"] = str(record["gr_id"]) if record.get("gr_id") is not None else None
    record["gr_line_id"] = (
        str(record["gr_line_id"]) if record.get("gr_line_id") is not None else None
    )
    record["sampling_plan_id"] = (
        str(record["sampling_plan_id"])
        if record.get("sampling_plan_id") is not None
        else None
    )
    record["sample_type"] = _normalize_sample_type(
        record.get("sample_type") or record.get("type")
    )
    record["status"] = _normalize_sample_status(record.get("status"))
    record["chain_of_custody"] = (
        record.get("chain_of_custody")
        if isinstance(record.get("chain_of_custody"), list)
        else []
    )
    record["attachments"] = (
        record.get("attachments") if isinstance(record.get("attachments"), list) else []
    )
    return SampleResponse.model_validate(record).model_dump()


def _normalize_resampling_record(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None

    record = _serialize(doc)
    resample_id = str(record.get("resample_id") or record.get("id") or "RESAMPLE")
    record["resample_id"] = resample_id
    record["original_sample_id"] = (
        str(record["original_sample_id"])
        if record.get("original_sample_id") is not None
        else None
    )
    record["new_sample_id"] = (
        str(record["new_sample_id"])
        if record.get("new_sample_id") is not None
        else None
    )
    record["reason_code"] = str(
        record.get("reason_code") or record.get("reason") or "Resampling"
    )
    record["reason_detail"] = str(
        record.get("reason_detail") or record.get("reason") or "Resampling requested"
    )
    record["requested_by"] = str(record.get("requested_by") or "System")
    record["approved_by"] = str(record.get("approved_by") or "System")
    record["approved_at"] = (
        record.get("approved_at")
        or record.get("updated_at")
        or record.get("created_at")
        or utc_now()
    )
    return ResamplingRecordResponse.model_validate(record).model_dump()


async def get_all_sampling_plans(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    query: dict = {}
    total = await db[SAMPLING_PLANS_COLLECTION].count_documents(query)
    cursor = (
        db[SAMPLING_PLANS_COLLECTION]
        .find(query)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return [_normalize_sampling_plan_record(doc) async for doc in cursor], total


async def get_sampling_plan_by_id(
    db: AsyncIOMotorDatabase, plan_id: str
) -> Optional[dict]:
    return _normalize_sampling_plan_record(
        await db[SAMPLING_PLANS_COLLECTION].find_one({"plan_id": plan_id})
    )


async def get_sampling_plan_by_code(
    db: AsyncIOMotorDatabase, plan_code: str
) -> Optional[dict]:
    return _normalize_sampling_plan_record(
        await db[SAMPLING_PLANS_COLLECTION].find_one({"plan_code": plan_code})
    )


async def get_sampling_plans_by_material(
    db: AsyncIOMotorDatabase, material_code: str
) -> list[dict]:
    cursor = db[SAMPLING_PLANS_COLLECTION].find({"material_code": material_code})
    return [_normalize_sampling_plan_record(doc) async for doc in cursor]


async def get_sampling_plans_by_supplier(
    db: AsyncIOMotorDatabase, supplier_id: str
) -> list[dict]:
    cursor = db[SAMPLING_PLANS_COLLECTION].find({"supplier_id": supplier_id})
    return [_normalize_sampling_plan_record(doc) async for doc in cursor]


async def create_sampling_plan(
    db: AsyncIOMotorDatabase, data: SamplingPlanCreate
) -> dict:
    doc = _prepare(data.model_dump())
    await db[SAMPLING_PLANS_COLLECTION].insert_one(doc)
    return _normalize_sampling_plan_record(doc)


async def update_sampling_plan(
    db: AsyncIOMotorDatabase,
    plan_id: str,
    data: SamplingPlanUpdate,
) -> Optional[dict]:
    fields = _prepare(data.model_dump(exclude_unset=True))
    if not fields:
        return await get_sampling_plan_by_id(db, plan_id)
    result = await db[SAMPLING_PLANS_COLLECTION].find_one_and_update(
        {"plan_id": plan_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    return _normalize_sampling_plan_record(result)


async def delete_sampling_plan(db: AsyncIOMotorDatabase, plan_id: str) -> bool:
    result = await db[SAMPLING_PLANS_COLLECTION].delete_one({"plan_id": plan_id})
    return result.deleted_count == 1


async def get_all_samples(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    query: dict = {}
    total = await db[SAMPLES_COLLECTION].count_documents(query)
    cursor = (
        db[SAMPLES_COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    )
    return [_normalize_sample_record(doc) async for doc in cursor], total


async def get_sample_by_id(db: AsyncIOMotorDatabase, sample_id: str) -> Optional[dict]:
    return _normalize_sample_record(
        await db[SAMPLES_COLLECTION].find_one({"sample_id": sample_id})
    )


async def get_sample_by_code(
    db: AsyncIOMotorDatabase, sample_code: str
) -> Optional[dict]:
    return _normalize_sample_record(
        await db[SAMPLES_COLLECTION].find_one({"sample_code": sample_code})
    )


async def get_samples_by_gr_id(db: AsyncIOMotorDatabase, gr_id: str) -> list[dict]:
    cursor = db[SAMPLES_COLLECTION].find({"gr_id": gr_id})
    return [_normalize_sample_record(doc) async for doc in cursor]


async def get_samples_by_status(db: AsyncIOMotorDatabase, status: str) -> list[dict]:
    cursor = db[SAMPLES_COLLECTION].find({"status": status})
    return [_normalize_sample_record(doc) async for doc in cursor]


async def create_sample(db: AsyncIOMotorDatabase, data: SampleCreate) -> dict:
    doc = _prepare(data.model_dump())
    await db[SAMPLES_COLLECTION].insert_one(doc)
    return _normalize_sample_record(doc)


async def update_sample(
    db: AsyncIOMotorDatabase,
    sample_id: str,
    data: SampleUpdate,
) -> Optional[dict]:
    fields = _prepare(data.model_dump(exclude_unset=True))
    if not fields:
        return await get_sample_by_id(db, sample_id)
    result = await db[SAMPLES_COLLECTION].find_one_and_update(
        {"sample_id": sample_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    return _normalize_sample_record(result)


async def add_custody_entry(
    db: AsyncIOMotorDatabase,
    sample_id: str,
    entry: ChainOfCustodyEntry,
) -> Optional[dict]:
    result = await db[SAMPLES_COLLECTION].find_one_and_update(
        {"sample_id": sample_id},
        {"$push": {"chain_of_custody": _prepare(entry.model_dump())}},
        return_document=ReturnDocument.AFTER,
    )
    return _normalize_sample_record(result)


async def delete_sample(db: AsyncIOMotorDatabase, sample_id: str) -> bool:
    result = await db[SAMPLES_COLLECTION].delete_one({"sample_id": sample_id})
    return result.deleted_count == 1


async def get_all_resampling_records(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    query: dict = {}
    total = await db[RESAMPLING_RECORDS_COLLECTION].count_documents(query)
    cursor = (
        db[RESAMPLING_RECORDS_COLLECTION]
        .find(query)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return [_normalize_resampling_record(doc) async for doc in cursor], total


async def get_resampling_record_by_id(
    db: AsyncIOMotorDatabase,
    resample_id: str,
) -> Optional[dict]:
    return _normalize_resampling_record(
        await db[RESAMPLING_RECORDS_COLLECTION].find_one({"resample_id": resample_id})
    )


async def get_resampling_records_by_original_sample(
    db: AsyncIOMotorDatabase,
    original_sample_id: str,
) -> list[dict]:
    cursor = db[RESAMPLING_RECORDS_COLLECTION].find(
        {"original_sample_id": original_sample_id}
    )
    return [_normalize_resampling_record(doc) async for doc in cursor]


async def create_resampling_record(
    db: AsyncIOMotorDatabase,
    data: ResamplingRecordCreate,
) -> dict:
    doc = _prepare(data.model_dump())
    await db[RESAMPLING_RECORDS_COLLECTION].insert_one(doc)
    return _normalize_resampling_record(doc)


async def update_resampling_record(
    db: AsyncIOMotorDatabase,
    resample_id: str,
    data: ResamplingRecordUpdate,
) -> Optional[dict]:
    fields = _prepare(data.model_dump(exclude_unset=True))
    if not fields:
        return await get_resampling_record_by_id(db, resample_id)
    result = await db[RESAMPLING_RECORDS_COLLECTION].find_one_and_update(
        {"resample_id": resample_id},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    return _normalize_resampling_record(result)


async def delete_resampling_record(db: AsyncIOMotorDatabase, resample_id: str) -> bool:
    result = await db[RESAMPLING_RECORDS_COLLECTION].delete_one(
        {"resample_id": resample_id}
    )
    return result.deleted_count == 1
