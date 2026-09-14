from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timezone
from typing import Any, Optional

import httpx
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel
from pymongo import ReturnDocument

from services.common.config import settings
from services.common.embeddings import build_embedding
from services.products.models.drug_research import (
    DrugAlternative,
    DrugFormulationResearchCreate,
    DrugFormulationResearchRecord,
    DrugFormulationResearchUpdate,
)
from services.products.models.product_common import utc_now

COLLECTION = "india_drug_formulation_research"
DOCUMENT_CHUNKS_COLLECTION = "agentos_document_chunks"


def _serialize(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


def _jsonable(value: Any, *, strip_embeddings: bool = True) -> Any:
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return datetime(
            value.year, value.month, value.day, tzinfo=timezone.utc
        ).isoformat()
    if isinstance(value, list):
        return [_jsonable(item, strip_embeddings=strip_embeddings) for item in value]
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if strip_embeddings and str(key) == "embedding":
                continue
            cleaned[str(key)] = _jsonable(item, strip_embeddings=strip_embeddings)
        return cleaned
    return value


def _prepare(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _prepare(value.model_dump())
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


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(_jsonable(value), sort_keys=True, default=str)


def _search_text(record: dict[str, Any]) -> str:
    fields = [
        record.get("research_product_id"),
        record.get("generic_name"),
        record.get("formulation_name"),
        record.get("brand_name"),
        record.get("manufacturer_name"),
        record.get("manufacturer_site"),
        record.get("source_authority"),
        record.get("approval_number"),
        record.get("dosage_form"),
        record.get("route"),
        record.get("strength"),
        record.get("composition"),
        record.get("therapeutic_class"),
        record.get("indications"),
        record.get("storage_requirements"),
        record.get("tags"),
        record.get("aliases"),
    ]
    return "\n".join(part for part in (_text(field) for field in fields) if part)


def _as_doc(data: dict[str, Any]) -> dict[str, Any]:
    record = DrugFormulationResearchRecord(**data).model_dump()
    record["embedding"] = build_embedding(COLLECTION, record)
    return _prepare(record)


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    length = min(len(left), len(right))
    dot = sum(left[index] * right[index] for index in range(length))
    left_mag = math.sqrt(sum(value * value for value in left[:length]))
    right_mag = math.sqrt(sum(value * value for value in right[:length]))
    if left_mag == 0 or right_mag == 0:
        return 0.0
    return dot / (left_mag * right_mag)


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    await db[COLLECTION].create_index("research_product_id", unique=True)
    await db[COLLECTION].create_index("generic_name")
    await db[COLLECTION].create_index("formulation_name")
    await db[COLLECTION].create_index("brand_name")
    await db[COLLECTION].create_index("therapeutic_class")
    await db[COLLECTION].create_index("dosage_form")
    await db[COLLECTION].create_index("aliases")


async def get_all(
    db: AsyncIOMotorDatabase,
    *,
    page: int = 1,
    page_size: int = 20,
    query: dict | None = None,
) -> tuple[list[dict], int]:
    query = query or {}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(doc) async for doc in cursor], total


async def get_by_id(
    db: AsyncIOMotorDatabase, research_product_id: str
) -> Optional[dict]:
    return _serialize(
        await db[COLLECTION].find_one({"research_product_id": research_product_id})
    )


async def upsert(
    db: AsyncIOMotorDatabase, data: DrugFormulationResearchCreate | dict[str, Any]
) -> dict:
    payload = data.model_dump() if isinstance(data, BaseModel) else dict(data)
    doc = _as_doc({**payload, "updated_at": utc_now()})
    await ensure_indexes(db)
    result = await db[COLLECTION].find_one_and_update(
        {"research_product_id": doc["research_product_id"]},
        {
            "$set": doc,
            "$setOnInsert": {"created_at": doc.get("created_at") or utc_now()},
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    saved = _serialize(result)
    await index_record(saved)
    return saved


async def update(
    db: AsyncIOMotorDatabase,
    research_product_id: str,
    data: DrugFormulationResearchUpdate,
) -> Optional[dict]:
    existing = await get_by_id(db, research_product_id)
    if not existing:
        return None
    fields = data.model_dump(exclude_unset=True)
    merged = {
        **existing,
        **fields,
        "research_product_id": research_product_id,
        "updated_at": utc_now(),
    }
    doc = _as_doc(merged)
    result = await db[COLLECTION].find_one_and_update(
        {"research_product_id": research_product_id},
        {"$set": doc},
        return_document=ReturnDocument.AFTER,
    )
    saved = _serialize(result)
    await index_record(saved)
    return saved


async def delete(db: AsyncIOMotorDatabase, research_product_id: str) -> bool:
    result = await db[COLLECTION].delete_one(
        {"research_product_id": research_product_id}
    )
    if result.deleted_count == 1:
        await delete_indexed_record(research_product_id)
        return True
    return False


def _es_headers(content_type: str = "application/json") -> dict[str, str]:
    headers = {"Content-Type": content_type}
    if settings.PRODUCT_RESEARCH_ELASTICSEARCH_API_KEY:
        headers["Authorization"] = (
            f"ApiKey {settings.PRODUCT_RESEARCH_ELASTICSEARCH_API_KEY}"
        )
    return headers


def _es_auth():
    if settings.PRODUCT_RESEARCH_ELASTICSEARCH_USERNAME:
        return (
            settings.PRODUCT_RESEARCH_ELASTICSEARCH_USERNAME,
            settings.PRODUCT_RESEARCH_ELASTICSEARCH_PASSWORD,
        )
    return None


def _es_enabled() -> bool:
    return bool(settings.PRODUCT_RESEARCH_ELASTICSEARCH_ENABLED)


async def ensure_elasticsearch_index() -> dict[str, Any]:
    if not _es_enabled():
        return {"enabled": False}
    async with httpx.AsyncClient(
        base_url=settings.PRODUCT_RESEARCH_ELASTICSEARCH_URL.rstrip("/"),
        auth=_es_auth(),
        headers=_es_headers(),
        timeout=20.0,
    ) as client:
        response = await client.head(
            f"/{settings.PRODUCT_RESEARCH_ELASTICSEARCH_INDEX}"
        )
        if response.status_code == 200:
            return {
                "enabled": True,
                "created": False,
                "index": settings.PRODUCT_RESEARCH_ELASTICSEARCH_INDEX,
            }
        if response.status_code not in {400, 404}:
            response.raise_for_status()
        mapping = {
            "settings": {"number_of_shards": 1, "number_of_replicas": 0},
            "mappings": {
                "properties": {
                    "research_product_id": {"type": "keyword"},
                    "generic_name": {
                        "type": "text",
                        "fields": {"keyword": {"type": "keyword"}},
                    },
                    "formulation_name": {"type": "text"},
                    "brand_name": {"type": "text"},
                    "manufacturer_name": {"type": "text"},
                    "country": {"type": "keyword"},
                    "source_authority": {"type": "keyword"},
                    "source_url": {"type": "keyword"},
                    "approval_number": {"type": "keyword"},
                    "approval_date": {"type": "date"},
                    "dosage_form": {"type": "keyword"},
                    "route": {"type": "keyword"},
                    "strength": {"type": "text"},
                    "composition": {"type": "text"},
                    "therapeutic_class": {"type": "keyword"},
                    "indications": {"type": "text"},
                    "regulatory_status": {"type": "keyword"},
                    "storage_requirements": {"type": "text"},
                    "aliases": {"type": "text"},
                    "search_text": {"type": "text"},
                }
            },
        }
        created = await client.put(
            f"/{settings.PRODUCT_RESEARCH_ELASTICSEARCH_INDEX}", json=mapping
        )
        created.raise_for_status()
        return {
            "enabled": True,
            "created": True,
            "index": settings.PRODUCT_RESEARCH_ELASTICSEARCH_INDEX,
        }


def _es_document(record: dict[str, Any]) -> dict[str, Any]:
    clean = _jsonable(record)
    return {
        **clean,
        "search_text": _search_text(record),
    }


async def index_record(record: dict[str, Any] | None) -> dict[str, Any]:
    if not record or not _es_enabled():
        return {"enabled": _es_enabled(), "indexed": False}
    await ensure_elasticsearch_index()
    async with httpx.AsyncClient(
        base_url=settings.PRODUCT_RESEARCH_ELASTICSEARCH_URL.rstrip("/"),
        auth=_es_auth(),
        headers=_es_headers(),
        timeout=20.0,
    ) as client:
        response = await client.put(
            f"/{settings.PRODUCT_RESEARCH_ELASTICSEARCH_INDEX}/_doc/{record['research_product_id']}",
            json=_es_document(record),
        )
        response.raise_for_status()
    return {
        "enabled": True,
        "indexed": True,
        "index": settings.PRODUCT_RESEARCH_ELASTICSEARCH_INDEX,
    }


async def delete_indexed_record(research_product_id: str) -> None:
    if not _es_enabled():
        return
    async with httpx.AsyncClient(
        base_url=settings.PRODUCT_RESEARCH_ELASTICSEARCH_URL.rstrip("/"),
        auth=_es_auth(),
        headers=_es_headers(),
        timeout=20.0,
    ) as client:
        response = await client.delete(
            f"/{settings.PRODUCT_RESEARCH_ELASTICSEARCH_INDEX}/_doc/{research_product_id}"
        )
        if response.status_code not in {200, 202, 404}:
            response.raise_for_status()


async def sync_elasticsearch(
    db: AsyncIOMotorDatabase, *, limit: int | None = None
) -> dict[str, Any]:
    if not _es_enabled():
        return {"enabled": False, "indexed": 0}
    await ensure_elasticsearch_index()
    cursor = db[COLLECTION].find({}).limit(int(limit or 0))
    lines: list[str] = []
    count = 0
    async for record in cursor:
        serialized = _serialize(record)
        action = {
            "index": {
                "_index": settings.PRODUCT_RESEARCH_ELASTICSEARCH_INDEX,
                "_id": serialized["research_product_id"],
            }
        }
        lines.append(json.dumps(action, separators=(",", ":")))
        lines.append(
            json.dumps(_es_document(serialized), default=str, separators=(",", ":"))
        )
        count += 1
    if not lines:
        return {
            "enabled": True,
            "indexed": 0,
            "index": settings.PRODUCT_RESEARCH_ELASTICSEARCH_INDEX,
        }
    async with httpx.AsyncClient(
        base_url=settings.PRODUCT_RESEARCH_ELASTICSEARCH_URL.rstrip("/"),
        auth=_es_auth(),
        timeout=30.0,
    ) as client:
        response = await client.post(
            "/_bulk",
            content="\n".join(lines) + "\n",
            headers=_es_headers("application/x-ndjson"),
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            failed = [
                item
                for item in payload.get("items", [])
                if (item.get("index") or {}).get("error")
            ][:5]
            raise RuntimeError(
                f"Product research Elasticsearch bulk index failed: {failed}"
            )
    return {
        "enabled": True,
        "indexed": count,
        "index": settings.PRODUCT_RESEARCH_ELASTICSEARCH_INDEX,
    }


async def elasticsearch_search(
    query: str, *, limit: int = 10
) -> tuple[list[str], dict[str, Any]]:
    if not _es_enabled():
        return [], {"enabled": False}
    payload = {
        "size": limit,
        "query": {
            "multi_match": {
                "query": query,
                "fields": [
                    "generic_name^5",
                    "formulation_name^4",
                    "brand_name^4",
                    "composition^3",
                    "aliases^3",
                    "therapeutic_class^2",
                    "indications^2",
                    "manufacturer_name",
                    "search_text",
                ],
                "fuzziness": "AUTO",
            }
        },
    }
    try:
        async with httpx.AsyncClient(
            base_url=settings.PRODUCT_RESEARCH_ELASTICSEARCH_URL.rstrip("/"),
            auth=_es_auth(),
            headers=_es_headers(),
            timeout=15.0,
        ) as client:
            response = await client.post(
                f"/{settings.PRODUCT_RESEARCH_ELASTICSEARCH_INDEX}/_search",
                json=payload,
            )
            if response.status_code == 404:
                return [], {
                    "enabled": True,
                    "available": False,
                    "index": settings.PRODUCT_RESEARCH_ELASTICSEARCH_INDEX,
                }
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        return [], {
            "enabled": True,
            "available": False,
            "error": str(exc),
            "index": settings.PRODUCT_RESEARCH_ELASTICSEARCH_INDEX,
        }
    hits = data.get("hits", {}).get("hits", [])
    ids = [
        str((hit.get("_source") or {}).get("research_product_id") or hit.get("_id"))
        for hit in hits
        if hit.get("_source") or hit.get("_id")
    ]
    return ids, {
        "enabled": True,
        "available": True,
        "index": settings.PRODUCT_RESEARCH_ELASTICSEARCH_INDEX,
        "hit_count": len(ids),
    }


def _regex_query(query: str) -> dict:
    tokens = [
        re.escape(token)
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9+.-]{2,}", query)[:8]
    ]
    if not tokens:
        return {}
    pattern = "|".join(tokens)
    fields = [
        "generic_name",
        "formulation_name",
        "brand_name",
        "composition",
        "aliases",
        "therapeutic_class",
        "indications",
    ]
    return {"$or": [{field: {"$regex": pattern, "$options": "i"}} for field in fields]}


def _display_name(record: dict[str, Any]) -> str:
    parts = [
        record.get("brand_name")
        or record.get("formulation_name")
        or record.get("generic_name")
    ]
    detail = " ".join(
        str(item)
        for item in [record.get("strength"), record.get("dosage_form")]
        if item
    )
    if detail:
        parts.append(f"({detail})")
    return " ".join(str(part) for part in parts if part)


def _alternative_reason(seed: dict[str, Any], candidate: dict[str, Any]) -> str:
    shared: list[str] = []
    if seed.get("therapeutic_class") and seed.get("therapeutic_class") == candidate.get(
        "therapeutic_class"
    ):
        shared.append(f"same therapeutic class: {seed['therapeutic_class']}")
    seed_composition = {str(item).lower() for item in seed.get("composition") or []}
    candidate_composition = {
        str(item).lower() for item in candidate.get("composition") or []
    }
    overlap = sorted(seed_composition & candidate_composition)
    if overlap:
        shared.append("shared composition: " + ", ".join(overlap[:3]))
    if seed.get("dosage_form") and seed.get("dosage_form") == candidate.get(
        "dosage_form"
    ):
        shared.append(f"same dosage form: {seed['dosage_form']}")
    return (
        "; ".join(shared)
        or "semantic similarity across name, formulation, indication, and source fields"
    )


def _alternative_from_record(
    seed: dict[str, Any], candidate: dict[str, Any], score: float
) -> DrugAlternative:
    return DrugAlternative(
        research_product_id=str(candidate.get("research_product_id")),
        display_name=_display_name(candidate),
        generic_name=str(candidate.get("generic_name") or ""),
        dosage_form=candidate.get("dosage_form"),
        route=candidate.get("route"),
        strength=candidate.get("strength"),
        manufacturer_name=candidate.get("manufacturer_name"),
        therapeutic_class=candidate.get("therapeutic_class"),
        score=round(score, 8),
        reason=_alternative_reason(seed, candidate),
    )


def _canvas_label(record: dict[str, Any]) -> str:
    return _display_name(record)


def _canvas_node_data(
    record: dict[str, Any],
    *,
    source: str,
    score: float | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    data = {
        "research_product_id": record.get("research_product_id"),
        "generic_name": record.get("generic_name"),
        "formulation_name": record.get("formulation_name"),
        "brand_name": record.get("brand_name"),
        "manufacturer_name": record.get("manufacturer_name"),
        "dosage_form": record.get("dosage_form"),
        "route": record.get("route"),
        "strength": record.get("strength"),
        "composition": record.get("composition") or [],
        "therapeutic_class": record.get("therapeutic_class"),
        "source_authority": record.get("source_authority"),
        "source_record_id": record.get("source_record_id"),
        "source_url": record.get("source_url"),
        "regulatory_status": record.get("regulatory_status"),
        "storage_requirements": record.get("storage_requirements"),
        "source": source,
    }
    if score is not None:
        data["score"] = round(float(score), 8)
    if reason:
        data["reason"] = reason
    return data


async def product_similarity_canvas(
    db: AsyncIOMotorDatabase, query: str | None = None, *, limit: int = 8
) -> dict[str, Any]:
    resolved_query = str(query or "").strip() or "India drug formulation alternatives"
    result = await search(db, resolved_query, limit=limit, include_documents=True)
    seeded_total = await db[COLLECTION].count_documents({})
    elastic = result.get("elasticsearch") or {}
    nodes: list[dict[str, Any]] = [
        {
            "id": "query",
            "type": "query",
            "label": resolved_query,
            "data": {
                "query": resolved_query,
                "source": "user_query",
                "mongo_collection": COLLECTION,
                "elasticsearch_index": settings.PRODUCT_RESEARCH_ELASTICSEARCH_INDEX,
            },
        }
    ]
    edges: list[dict[str, Any]] = []
    seen_nodes = {"query"}

    direct_matches = result.get("direct_matches") or []
    for index, record in enumerate(direct_matches[:limit], start=1):
        node_id = f"product:{record.get('research_product_id')}"
        if node_id not in seen_nodes:
            nodes.append(
                {
                    "id": node_id,
                    "type": "direct_match",
                    "label": _canvas_label(record),
                    "data": _canvas_node_data(
                        record,
                        source=(
                            "mongo_seeded_record_resolved_from_elasticsearch"
                            if elastic.get("available") and not elastic.get("fallback")
                            else "mongo_seeded_record_regex_fallback"
                        ),
                    ),
                }
            )
            seen_nodes.add(node_id)
        edges.append(
            {
                "id": f"query-direct-{index}",
                "source": "query",
                "target": node_id,
                "label": (
                    "ELASTICSEARCH_DIRECT_MATCH"
                    if elastic.get("available") and not elastic.get("fallback")
                    else "MONGO_REGEX_MATCH"
                ),
                "data": {
                    "rank": index,
                    "source": (
                        "elasticsearch"
                        if elastic.get("available") and not elastic.get("fallback")
                        else "mongo"
                    ),
                    "index": elastic.get("index")
                    or settings.PRODUCT_RESEARCH_ELASTICSEARCH_INDEX,
                },
            }
        )

    alternatives = result.get("alternatives") or []
    direct_anchor = (
        f"product:{direct_matches[0].get('research_product_id')}"
        if direct_matches
        else "query"
    )
    for index, item in enumerate(alternatives[:limit], start=1):
        node_id = f"alternative:{item.get('research_product_id')}"
        if node_id not in seen_nodes:
            nodes.append(
                {
                    "id": node_id,
                    "type": "alternative",
                    "label": str(
                        item.get("display_name") or item.get("research_product_id")
                    ),
                    "data": {
                        "research_product_id": item.get("research_product_id"),
                        "generic_name": item.get("generic_name"),
                        "dosage_form": item.get("dosage_form"),
                        "route": item.get("route"),
                        "strength": item.get("strength"),
                        "manufacturer_name": item.get("manufacturer_name"),
                        "therapeutic_class": item.get("therapeutic_class"),
                        "score": item.get("score"),
                        "reason": item.get("reason"),
                        "source": "mongo_seeded_embedding_similarity",
                    },
                }
            )
            seen_nodes.add(node_id)
        edges.append(
            {
                "id": f"similarity-{index}",
                "source": direct_anchor,
                "target": node_id,
                "label": "MONGO_SEMANTIC_SIMILARITY",
                "data": {
                    "rank": index,
                    "score": item.get("score"),
                    "reason": item.get("reason"),
                    "source": "mongo_embedding_vector",
                },
            }
        )

    for index, document in enumerate(result.get("document_matches") or [], start=1):
        document_id = str(
            document.get("chunk_id")
            or document.get("document_id")
            or f"document-{index}"
        )
        node_id = f"document:{document_id}"
        if node_id not in seen_nodes:
            nodes.append(
                {
                    "id": node_id,
                    "type": "document_match",
                    "label": str(
                        document.get("filename")
                        or document.get("document_id")
                        or document_id
                    ),
                    "data": {
                        **document,
                        "source": "mongo_uploaded_document_embedding",
                    },
                }
            )
            seen_nodes.add(node_id)
        edges.append(
            {
                "id": f"document-match-{index}",
                "source": "query",
                "target": node_id,
                "label": "DOCUMENT_SIMILARITY",
                "data": {
                    "rank": index,
                    "score": document.get("score"),
                    "source": "mongo_document_embedding",
                },
            }
        )

    return {
        "query": resolved_query,
        "data_sources": {
            "mongo": {
                "collection": COLLECTION,
                "seeded_records": seeded_total,
                "role": "record hydration and semantic similarity ranking",
            },
            "elasticsearch": {
                **elastic,
                "index": elastic.get("index")
                or settings.PRODUCT_RESEARCH_ELASTICSEARCH_INDEX,
                "role": "direct product/formulation search and ordering",
            },
        },
        "nodes": nodes,
        "edges": edges,
    }


async def semantic_alternatives(
    db: AsyncIOMotorDatabase, query: str, seeds: list[dict[str, Any]], *, limit: int = 8
) -> list[DrugAlternative]:
    query_vector = build_embedding("_query", {"question": query})["vector"]
    seed = (
        seeds[0]
        if seeds
        else {"composition": [], "therapeutic_class": None, "dosage_form": None}
    )
    seed_ids = {str(item.get("research_product_id")) for item in seeds}
    candidates: list[tuple[float, dict[str, Any]]] = []
    async for record in db[COLLECTION].find({"embedding.vector": {"$type": "array"}}):
        serialized = _serialize(record)
        if str(serialized.get("research_product_id")) in seed_ids:
            continue
        embedding = serialized.get("embedding") or {}
        score = _cosine_similarity(query_vector, embedding.get("vector") or [])
        if seed.get("therapeutic_class") and seed.get(
            "therapeutic_class"
        ) == serialized.get("therapeutic_class"):
            score += 0.08
        seed_composition = {str(item).lower() for item in seed.get("composition") or []}
        candidate_composition = {
            str(item).lower() for item in serialized.get("composition") or []
        }
        if seed_composition & candidate_composition:
            score += 0.12
        if seed.get("dosage_form") and seed.get("dosage_form") == serialized.get(
            "dosage_form"
        ):
            score += 0.04
        candidates.append((score, serialized))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [
        _alternative_from_record(seed, record, score)
        for score, record in candidates[:limit]
    ]


async def document_matches(
    db: AsyncIOMotorDatabase, query: str, *, limit: int = 5
) -> list[dict[str, Any]]:
    query_vector = build_embedding("_query", {"question": query})["vector"]
    matches: list[tuple[float, dict[str, Any]]] = []
    async for chunk in db[DOCUMENT_CHUNKS_COLLECTION].find(
        {"embedding.vector": {"$type": "array"}}
    ):
        embedding = chunk.get("embedding") or {}
        score = _cosine_similarity(query_vector, embedding.get("vector") or [])
        if score <= 0:
            continue
        matches.append((score, _serialize(chunk)))
    matches.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            "document_id": record.get("document_id"),
            "filename": record.get("filename"),
            "chunk_id": record.get("chunk_id"),
            "score": round(score, 8),
            "text_preview": str(record.get("text") or record.get("content") or "")[
                :500
            ],
        }
        for score, record in matches[:limit]
    ]


async def search(
    db: AsyncIOMotorDatabase,
    query: str,
    *,
    limit: int = 8,
    include_documents: bool = True,
) -> dict[str, Any]:
    await ensure_indexes(db)
    elastic_ids, elastic_status = await elasticsearch_search(query, limit=limit)
    direct_matches: list[dict[str, Any]] = []
    if elastic_ids:
        cursor = db[COLLECTION].find({"research_product_id": {"$in": elastic_ids}})
        by_id = {
            str(doc.get("research_product_id")): _serialize(doc) async for doc in cursor
        }
        direct_matches = [by_id[item] for item in elastic_ids if item in by_id]
    if not direct_matches:
        records, _ = await get_all(
            db, page=1, page_size=limit, query=_regex_query(query)
        )
        direct_matches = records
        elastic_status = {**elastic_status, "fallback": "mongo_regex"}
    alternatives = await semantic_alternatives(db, query, direct_matches, limit=limit)
    docs = (
        await document_matches(db, query, limit=min(limit, 5))
        if include_documents
        else []
    )
    return {
        "query": query,
        "elasticsearch": elastic_status,
        "direct_matches": direct_matches,
        "alternatives": [item.model_dump() for item in alternatives],
        "document_matches": docs,
    }
