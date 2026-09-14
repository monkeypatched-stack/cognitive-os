from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from services.procurement.models.quality_control import (
    InspectionChecklistCreate,
    InspectionChecklistUpdate,
    MaterialSpecificationCreate,
    MaterialSpecificationUpdate,
    NonConformanceReportCreate,
    NonConformanceReportUpdate,
    QCInspectionCreate,
    QCInspectionUpdate,
    ReInspectionRecordCreate,
    ReInspectionRecordUpdate,
)

MATERIAL_SPECS_COLLECTION = "material_specifications"
INSPECTION_CHECKLISTS_COLLECTION = "inspection_checklists"
QC_INSPECTIONS_COLLECTION = "qc_inspections"
REINSPECTIONS_COLLECTION = "reinspection_records"
NCR_COLLECTION = "non_conformance_reports"


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


async def _get_all(
    db: AsyncIOMotorDatabase, collection: str, page: int, page_size: int
):
    query: dict = {}
    total = await db[collection].count_documents(query)
    cursor = db[collection].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(doc) async for doc in cursor], total


async def _get_by_id(
    db: AsyncIOMotorDatabase, collection: str, id_field: str, value: str
):
    return _serialize(await db[collection].find_one({id_field: value}))


async def _get_many(db: AsyncIOMotorDatabase, collection: str, query: dict):
    cursor = db[collection].find(query)
    return [_serialize(doc) async for doc in cursor]


async def _create(db: AsyncIOMotorDatabase, collection: str, data) -> dict:
    doc = _prepare(data.model_dump())
    await db[collection].insert_one(doc)
    return _serialize(doc)


async def _update(
    db: AsyncIOMotorDatabase, collection: str, id_field: str, value: str, data
):
    fields = _prepare(data.model_dump(exclude_unset=True))
    if not fields:
        return await _get_by_id(db, collection, id_field, value)
    result = await db[collection].find_one_and_update(
        {id_field: value},
        {"$set": fields},
        return_document=ReturnDocument.AFTER,
    )
    return _serialize(result)


async def _delete(
    db: AsyncIOMotorDatabase, collection: str, id_field: str, value: str
) -> bool:
    result = await db[collection].delete_one({id_field: value})
    return result.deleted_count == 1


async def get_all_material_specs(db, page=1, page_size=20):
    return await _get_all(db, MATERIAL_SPECS_COLLECTION, page, page_size)


async def get_material_spec_by_id(db, spec_id: str):
    return await _get_by_id(db, MATERIAL_SPECS_COLLECTION, "spec_id", spec_id)


async def get_material_spec_by_code(db, spec_code: str):
    return await _get_by_id(db, MATERIAL_SPECS_COLLECTION, "spec_code", spec_code)


async def get_material_specs_by_material(db, material_code: str):
    return await _get_many(
        db, MATERIAL_SPECS_COLLECTION, {"material_code": material_code}
    )


async def create_material_spec(db, data: MaterialSpecificationCreate):
    return await _create(db, MATERIAL_SPECS_COLLECTION, data)


async def update_material_spec(db, spec_id: str, data: MaterialSpecificationUpdate):
    return await _update(db, MATERIAL_SPECS_COLLECTION, "spec_id", spec_id, data)


async def delete_material_spec(db, spec_id: str):
    return await _delete(db, MATERIAL_SPECS_COLLECTION, "spec_id", spec_id)


async def get_all_inspection_checklists(db, page=1, page_size=20):
    return await _get_all(db, INSPECTION_CHECKLISTS_COLLECTION, page, page_size)


async def get_inspection_checklist_by_id(db, checklist_id: str):
    return await _get_by_id(
        db, INSPECTION_CHECKLISTS_COLLECTION, "checklist_id", checklist_id
    )


async def get_inspection_checklists_by_material(db, material_code: str):
    return await _get_many(
        db, INSPECTION_CHECKLISTS_COLLECTION, {"material_code": material_code}
    )


async def get_inspection_checklists_by_type(db, inspection_type: str):
    return await _get_many(
        db, INSPECTION_CHECKLISTS_COLLECTION, {"inspection_type": inspection_type}
    )


async def create_inspection_checklist(db, data: InspectionChecklistCreate):
    return await _create(db, INSPECTION_CHECKLISTS_COLLECTION, data)


async def update_inspection_checklist(
    db, checklist_id: str, data: InspectionChecklistUpdate
):
    return await _update(
        db, INSPECTION_CHECKLISTS_COLLECTION, "checklist_id", checklist_id, data
    )


async def delete_inspection_checklist(db, checklist_id: str):
    return await _delete(
        db, INSPECTION_CHECKLISTS_COLLECTION, "checklist_id", checklist_id
    )


async def get_all_qc_inspections(db, page=1, page_size=20):
    return await _get_all(db, QC_INSPECTIONS_COLLECTION, page, page_size)


async def get_qc_inspection_by_id(db, inspection_id: str):
    return await _get_by_id(
        db, QC_INSPECTIONS_COLLECTION, "inspection_id", inspection_id
    )


async def get_qc_inspection_by_code(db, inspection_code: str):
    return await _get_by_id(
        db, QC_INSPECTIONS_COLLECTION, "inspection_code", inspection_code
    )


async def get_qc_inspections_by_gr(db, gr_id: str):
    return await _get_many(db, QC_INSPECTIONS_COLLECTION, {"gr_id": gr_id})


async def get_qc_inspections_by_status(db, status: str):
    return await _get_many(db, QC_INSPECTIONS_COLLECTION, {"status": status})


async def create_qc_inspection(db, data: QCInspectionCreate):
    return await _create(db, QC_INSPECTIONS_COLLECTION, data)


async def update_qc_inspection(db, inspection_id: str, data: QCInspectionUpdate):
    return await _update(
        db, QC_INSPECTIONS_COLLECTION, "inspection_id", inspection_id, data
    )


async def delete_qc_inspection(db, inspection_id: str):
    return await _delete(db, QC_INSPECTIONS_COLLECTION, "inspection_id", inspection_id)


async def get_all_reinspections(db, page=1, page_size=20):
    return await _get_all(db, REINSPECTIONS_COLLECTION, page, page_size)


async def get_reinspection_by_id(db, reinspection_id: str):
    return await _get_by_id(
        db, REINSPECTIONS_COLLECTION, "reinspection_id", reinspection_id
    )


async def get_reinspections_by_original(db, original_inspection_id: str):
    return await _get_many(
        db, REINSPECTIONS_COLLECTION, {"original_inspection_id": original_inspection_id}
    )


async def create_reinspection(db, data: ReInspectionRecordCreate):
    return await _create(db, REINSPECTIONS_COLLECTION, data)


async def update_reinspection(db, reinspection_id: str, data: ReInspectionRecordUpdate):
    return await _update(
        db, REINSPECTIONS_COLLECTION, "reinspection_id", reinspection_id, data
    )


async def delete_reinspection(db, reinspection_id: str):
    return await _delete(
        db, REINSPECTIONS_COLLECTION, "reinspection_id", reinspection_id
    )


async def get_all_ncrs(db, page=1, page_size=20):
    return await _get_all(db, NCR_COLLECTION, page, page_size)


async def get_ncr_by_id(db, ncr_id: str):
    return await _get_by_id(db, NCR_COLLECTION, "ncr_id", ncr_id)


async def get_ncr_by_number(db, ncr_number: str):
    return await _get_by_id(db, NCR_COLLECTION, "ncr_number", ncr_number)


async def get_ncrs_by_gr(db, gr_id: str):
    return await _get_many(db, NCR_COLLECTION, {"gr_id": gr_id})


async def get_ncrs_by_status(db, status: str):
    return await _get_many(db, NCR_COLLECTION, {"status": status})


async def create_ncr(db, data: NonConformanceReportCreate):
    return await _create(db, NCR_COLLECTION, data)


async def update_ncr(db, ncr_id: str, data: NonConformanceReportUpdate):
    return await _update(db, NCR_COLLECTION, "ncr_id", ncr_id, data)


async def delete_ncr(db, ncr_id: str):
    return await _delete(db, NCR_COLLECTION, "ncr_id", ncr_id)
