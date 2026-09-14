import json
from typing import Any

from bson import ObjectId
from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from pymongo import ReturnDocument

from services.common.db import get_database
from services.common.embeddings import EMBEDDING_MODEL


def empty_paginated_response(page: int, page_size: int) -> dict:
    return {
        "total": 0,
        "page": page,
        "page_size": page_size,
        "results": [],
    }


def empty_collection_router(paths: list[str]) -> APIRouter:
    router = APIRouter()

    async def list_empty(
        page: int = Query(1, ge=1),
        page_size: int = Query(20, ge=1),
    ) -> dict:
        return empty_paginated_response(page, page_size)

    for path in paths:
        router.add_api_route(path, list_empty, methods=["GET"])
        router.add_api_route(
            f"{path}/", list_empty, methods=["GET"], include_in_schema=False
        )

    return router


def _collection_name(path: str) -> str:
    return path.rstrip("/").split("/")[-1].replace("-", "_")


def _singular_collection_name(collection: str) -> str:
    return collection[:-1] if collection.endswith("s") else collection


def _id_field(collection: str) -> str:
    return f"{_singular_collection_name(collection)}_id"


def _serialize(doc: dict | None) -> dict | None:
    if doc is None:
        return None
    serialized = dict(doc)
    if isinstance(serialized.get("_id"), ObjectId):
        serialized["_id"] = str(serialized["_id"])
    return serialized


async def _read_uploaded_json(file: UploadFile) -> Any:
    content_type = (
        file.content_type.split(";", 1)[0].lower() if file.content_type else None
    )
    if content_type and content_type not in {
        "application/json",
        "text/json",
        "application/octet-stream",
    }:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Upload must be a JSON file.",
        )

    content = await file.read()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded JSON file is empty.",
        )

    try:
        return json.loads(content.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file must be UTF-8 JSON.",
        ) from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid JSON: {exc.msg}"
        ) from exc


def _documents_from_json(payload: Any) -> list[dict]:
    documents = payload if isinstance(payload, list) else [payload]
    if not documents:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="JSON array must contain at least one object.",
        )
    if not all(isinstance(document, dict) for document in documents):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded JSON must be an object or an array of objects.",
        )
    return documents


def collection_router(paths: list[str]) -> APIRouter:
    router = APIRouter()

    for path in paths:
        collection = _collection_name(path)
        id_field = _id_field(collection)

        async def list_records(
            page: int = Query(1, ge=1),
            page_size: int = Query(20, ge=1),
            db=Depends(get_database),
            *,
            collection: str = collection,
        ) -> dict:
            total = await db[collection].count_documents({})
            cursor = (
                db[collection].find({}).skip((page - 1) * page_size).limit(page_size)
            )
            return {
                "total": total,
                "page": page,
                "page_size": page_size,
                "results": [_serialize(doc) async for doc in cursor],
            }

        async def get_record(
            record_id: str,
            db=Depends(get_database),
            *,
            collection: str = collection,
            id_field: str = id_field,
        ) -> dict:
            doc = await db[collection].find_one(
                {
                    "$or": [
                        {id_field: record_id},
                        {"id": record_id},
                        {"group_id": record_id},
                    ]
                }
            )
            if not doc:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Record not found"
                )
            return _serialize(doc)

        async def create_record(
            payload: dict[str, Any] = Body(default_factory=dict),
            db=Depends(get_database),
            *,
            collection: str = collection,
            id_field: str = id_field,
        ) -> dict:
            doc = dict(payload)
            record_id = str(
                doc.get(id_field) or doc.get("id") or doc.get("group_id") or ObjectId()
            )
            doc.setdefault(id_field, record_id)
            doc.setdefault("id", record_id)
            doc.setdefault("group_id", record_id)
            existing = await db[collection].find_one(
                {
                    "$or": [
                        {id_field: record_id},
                        {"id": record_id},
                        {"group_id": record_id},
                    ]
                }
            )
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="Record already exists"
                )
            await db[collection].insert_one(doc)
            return _serialize(doc)

        async def upload_json(
            file: UploadFile = File(...),
            db=Depends(get_database),
            *,
            collection: str = collection,
        ) -> dict:
            documents = _documents_from_json(await _read_uploaded_json(file))

            if len(documents) == 1:
                result = await db[collection].insert_one(documents[0])
                inserted_ids = [str(result.inserted_id)]
            else:
                result = await db[collection].insert_many(documents)
                inserted_ids = [str(inserted_id) for inserted_id in result.inserted_ids]

            return {
                "collection": collection,
                "inserted_count": len(inserted_ids),
                "inserted_ids": inserted_ids,
                "embedding_model": EMBEDDING_MODEL,
                "embeddings_stored_in_documents": True,
            }

        async def update_record(
            record_id: str,
            payload: dict[str, Any] = Body(default_factory=dict),
            db=Depends(get_database),
            *,
            collection: str = collection,
            id_field: str = id_field,
        ) -> dict:
            fields = dict(payload)
            fields.pop("_id", None)
            fields.setdefault(id_field, record_id)
            fields.setdefault("id", record_id)
            fields.setdefault("group_id", record_id)
            doc = await db[collection].find_one_and_update(
                {
                    "$or": [
                        {id_field: record_id},
                        {"id": record_id},
                        {"group_id": record_id},
                    ]
                },
                {"$set": fields},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
            return _serialize(doc)

        async def delete_record(
            record_id: str,
            db=Depends(get_database),
            *,
            collection: str = collection,
            id_field: str = id_field,
        ) -> None:
            result = await db[collection].delete_one(
                {
                    "$or": [
                        {id_field: record_id},
                        {"id": record_id},
                        {"group_id": record_id},
                    ]
                }
            )
            if result.deleted_count != 1:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Record not found"
                )

        router.add_api_route(
            path, list_records, methods=["GET"], name=f"list_{collection}"
        )
        router.add_api_route(
            f"{path}/",
            list_records,
            methods=["GET"],
            include_in_schema=False,
            name=f"list_{collection}_slash",
        )
        router.add_api_route(
            path,
            create_record,
            methods=["POST"],
            status_code=status.HTTP_201_CREATED,
            name=f"create_{collection}",
        )
        router.add_api_route(
            f"{path}/",
            create_record,
            methods=["POST"],
            status_code=status.HTTP_201_CREATED,
            include_in_schema=False,
            name=f"create_{collection}_slash",
        )
        router.add_api_route(
            f"{path}/upload-json",
            upload_json,
            methods=["POST"],
            status_code=status.HTTP_201_CREATED,
            name=f"upload_{collection}_json",
        )
        router.add_api_route(
            f"{path}/upload-json/",
            upload_json,
            methods=["POST"],
            status_code=status.HTTP_201_CREATED,
            include_in_schema=False,
            name=f"upload_{collection}_json_slash",
        )
        router.add_api_route(
            f"{path}/{{record_id}}",
            get_record,
            methods=["GET"],
            name=f"get_{collection}",
        )
        router.add_api_route(
            f"{path}/{{record_id}}",
            update_record,
            methods=["PATCH"],
            name=f"update_{collection}",
        )
        router.add_api_route(
            f"{path}/{{record_id}}",
            delete_record,
            methods=["DELETE"],
            status_code=status.HTTP_204_NO_CONTENT,
            name=f"delete_{collection}",
        )

    return router
