from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.auth import require_permission
from services.common.db import get_database
from services.products.helpers import drug_research as crud
from services.products.models.drug_research import (
    DrugFormulationResearchCreate,
    DrugFormulationResearchRecord,
    DrugFormulationResearchUpdate,
    ProductSimilarityCanvasResponse,
    DrugResearchSearchResponse,
    PaginatedDrugResearchResponse,
)

router = APIRouter()


@router.get("/", response_model=PaginatedDrugResearchResponse)
async def list_drug_research_records(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    generic_name: str | None = None,
    dosage_form: str | None = None,
    therapeutic_class: str | None = None,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    query = {
        key: value
        for key, value in {
            "generic_name": generic_name,
            "dosage_form": dosage_form,
            "therapeutic_class": therapeutic_class,
        }.items()
        if value
    }
    records, total = await crud.get_all(db, page=page, page_size=page_size, query=query)
    return PaginatedDrugResearchResponse(
        total=total, page=page, page_size=page_size, results=records
    )


@router.get("/search", response_model=DrugResearchSearchResponse)
async def search_drug_research_records(
    q: str = Query(..., min_length=1),
    limit: int = Query(8, ge=1, le=25),
    include_documents: bool = True,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.search(db, q, limit=limit, include_documents=include_documents)


@router.post("/sync-elasticsearch")
async def sync_drug_research_elasticsearch(
    limit: int | None = Query(None, ge=1),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-products")),
):
    return await crud.sync_elasticsearch(db, limit=limit)


@router.get("/canvas", response_model=ProductSimilarityCanvasResponse)
async def product_similarity_canvas(
    q: str | None = Query(None, min_length=1),
    limit: int = Query(8, ge=1, le=25),
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    return await crud.product_similarity_canvas(db, q, limit=limit)


@router.post(
    "/",
    response_model=DrugFormulationResearchRecord,
    status_code=status.HTTP_201_CREATED,
)
async def upsert_drug_research_record(
    data: DrugFormulationResearchCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-create-products")),
):
    return await crud.upsert(db, data)


@router.get("/{research_product_id}", response_model=DrugFormulationResearchRecord)
async def get_drug_research_record(
    research_product_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-view-products")),
):
    record = await crud.get_by_id(db, research_product_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Drug research record '{research_product_id}' not found",
        )
    return record


@router.patch("/{research_product_id}", response_model=DrugFormulationResearchRecord)
async def update_drug_research_record(
    research_product_id: str,
    data: DrugFormulationResearchUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-update-products")),
):
    updated = await crud.update(db, research_product_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Drug research record '{research_product_id}' not found",
        )
    return updated


@router.delete("/{research_product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_drug_research_record(
    research_product_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
    _: dict = Depends(require_permission("perm-delete-products")),
):
    if not await crud.delete(db, research_product_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Drug research record '{research_product_id}' not found",
        )
