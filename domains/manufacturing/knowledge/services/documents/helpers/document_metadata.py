import os
import hashlib
import io
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

from fastapi import UploadFile
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.common.embeddings import build_embedding
from src.monkey_brain.documents.document_ocr import (
    DocumentOcrError,
    extract_document_text_with_qwen_ocr,
)
from services.documents.models.document_metadata import (
    DocumentMetadataCreate,
    DocumentMetadataResponse,
    DocumentMetadataUpdate,
    serialize_document,
)

COLLECTION = "document_metadata"
DOCUMENT_EMBEDDINGS_COLLECTION = "document_embeddings"
GRAPH_RAG_DOCUMENT_CHUNKS_COLLECTION = "agentos_document_chunks"
LOCAL_UPLOAD_DIR = Path(os.getenv("DOCUMENT_UPLOAD_DIR", "uploads/documents"))
DOCUMENT_EMBEDDING_MAX_BYTES = int(
    os.getenv("DOCUMENT_EMBEDDING_MAX_BYTES", str(5 * 1024 * 1024))
)
DOCUMENT_EMBEDDING_TEXT_MAX_CHARS = int(
    os.getenv("DOCUMENT_EMBEDDING_TEXT_MAX_CHARS", "20000")
)
DOCUMENT_CHUNK_SIZE = int(os.getenv("DOCUMENT_CHUNK_SIZE", "1400"))
DOCUMENT_CHUNK_OVERLAP = int(os.getenv("DOCUMENT_CHUNK_OVERLAP", "180"))
S3_BUCKET = os.getenv("S3_DOCUMENT_BUCKET") or os.getenv("AWS_S3_BUCKET")
S3_PREFIX = os.getenv("S3_DOCUMENT_PREFIX", "documents").strip("/")
S3_REGION = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL")
S3_PUBLIC_BASE_URL = os.getenv("S3_PUBLIC_BASE_URL")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _serialize(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    return serialize_document(_coerce_legacy_document(doc))


def _coerce_legacy_document(doc: dict) -> dict:
    """Keep older metadata rows readable after the schema replacement."""
    coerced = dict(doc)
    now = _utc_now()

    coerced.setdefault(
        "document_name", coerced.get("document_title") or "Untitled Document"
    )
    coerced.setdefault("document_version", "1.0")
    coerced.setdefault("document_tags", coerced.get("tags") or [])
    coerced.setdefault(
        "document_url",
        coerced.get("s3_url")
        or coerced.get("local_path")
        or f"/api/v1/document-metadata/{coerced.get('document_id', 'unknown')}/view",
    )
    coerced.setdefault("document_status", coerced.get("status") or "Draft")
    coerced.setdefault(
        "document_owner", coerced.get("document_author") or "Unknown Owner"
    )
    coerced.setdefault(
        "document_preparer", coerced.get("created_by") or "Unknown Preparer"
    )
    coerced.setdefault("document_reviewer", "Unknown Reviewer")
    coerced.setdefault("document_approver", "Unknown Approver")
    coerced.setdefault("document_due_date", now)
    coerced.setdefault("created_by", coerced.get("document_author") or "system")
    coerced.setdefault("last_modified_by", coerced.get("document_author") or "system")
    coerced.setdefault("created_at", now)
    coerced.setdefault(
        "last_modified_at",
        coerced.get("updated_at") or coerced.get("created_at") or now,
    )

    return coerced


def _prepare(value):
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


def _as_response_doc(data: dict) -> dict:
    return _prepare(DocumentMetadataResponse(**data).model_dump(mode="python"))


def _document_url_from_storage(document_id: str, storage_fields: dict) -> str:
    if storage_fields.get("s3_url"):
        return storage_fields["s3_url"]
    if storage_fields.get("local_path"):
        return storage_fields["local_path"]
    return f"/api/v1/document-metadata/{document_id}/view"


def _safe_filename(filename: str) -> str:
    cleaned = Path(filename or "document").name
    return cleaned.replace("/", "_").replace("\\", "_")


def _looks_like_text(content: bytes) -> bool:
    if not content:
        return False
    sample = content[:2048]
    printable = sum(1 for byte in sample if byte in (9, 10, 13) or 32 <= byte <= 126)
    return printable / max(len(sample), 1) >= 0.85


def _decode_text(content: bytes) -> str:
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="ignore")


def _extract_pdf_text(content: bytes) -> str:
    try:
        from pypdf import PdfReader
    except Exception:
        return ""

    try:
        reader = PdfReader(io.BytesIO(content))
        pages = []
        for page in reader.pages:
            text = page.extract_text() or ""
            if text.strip():
                pages.append(text)
        return "\n\n".join(pages)
    except Exception:
        return ""


def _extract_upload_text(
    content: bytes, content_type: str | None, filename: str | None
) -> tuple[str, str]:
    content_type_normalized = (content_type or "").split(";")[0].strip().lower()
    suffix = Path(filename or "").suffix.lower()

    if content_type_normalized == "application/pdf" or suffix in {".pdf", ".pfd"}:
        text = _extract_pdf_text(content)
        return text[:DOCUMENT_EMBEDDING_TEXT_MAX_CHARS], "pdf"

    text_extensions = {
        ".csv",
        ".htm",
        ".html",
        ".json",
        ".log",
        ".md",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
    }
    if (
        content_type_normalized.startswith("text/")
        or suffix in text_extensions
        or _looks_like_text(content)
    ):
        text = _decode_text(content)
        return text[:DOCUMENT_EMBEDDING_TEXT_MAX_CHARS], "text"

    return "", "unsupported"


def _chunk_document_text(text: str) -> list[str]:
    normalized = "\n\n".join(part.strip() for part in text.splitlines() if part.strip())
    if not normalized:
        return []
    chunks = []
    start = 0
    while start < len(normalized):
        end = min(start + DOCUMENT_CHUNK_SIZE, len(normalized))
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(normalized):
            break
        start = max(end - DOCUMENT_CHUNK_OVERLAP, start + 1)
    return chunks


async def _replace_document_chunks(
    db: AsyncIOMotorDatabase,
    document: dict,
    storage_fields: dict,
    extracted_text: str,
    extraction_method: str,
) -> None:
    document_id = document["document_id"]
    await db[GRAPH_RAG_DOCUMENT_CHUNKS_COLLECTION].delete_many(
        {"document_id": document_id}
    )
    chunks = _chunk_document_text(extracted_text)
    if not chunks:
        return
    now = _utc_now()
    records = []
    for index, chunk in enumerate(chunks):
        record = {
            "document_id": document_id,
            "chunk_id": f"{document_id}-chunk-{index + 1}",
            "filename": storage_fields.get("file_name"),
            "document_name": document.get("document_name"),
            "content_type": storage_fields.get("content_type"),
            "extraction_method": extraction_method,
            "chunk_index": index,
            "text": chunk,
            "created_at": now,
            "updated_at": now,
        }
        record["embedding"] = build_embedding(
            GRAPH_RAG_DOCUMENT_CHUNKS_COLLECTION, record
        )
        records.append(record)
    await db[GRAPH_RAG_DOCUMENT_CHUNKS_COLLECTION].insert_many(records)
    await db[GRAPH_RAG_DOCUMENT_CHUNKS_COLLECTION].create_index("document_id")
    await db[GRAPH_RAG_DOCUMENT_CHUNKS_COLLECTION].create_index("chunk_id", unique=True)
    await db[GRAPH_RAG_DOCUMENT_CHUNKS_COLLECTION].create_index("filename")


async def _read_upload_bytes_for_embedding(file: UploadFile) -> tuple[bytes, bool]:
    await file.seek(0)
    content = await file.read(DOCUMENT_EMBEDDING_MAX_BYTES + 1)
    await file.seek(0)
    return (
        content[:DOCUMENT_EMBEDDING_MAX_BYTES],
        len(content) > DOCUMENT_EMBEDDING_MAX_BYTES,
    )


async def _upsert_document_embedding(
    db: AsyncIOMotorDatabase,
    document: dict,
    file: UploadFile,
    storage_fields: dict,
) -> None:
    content, bytes_truncated = await _read_upload_bytes_for_embedding(file)
    ocr_model = None
    ocr_pages: list[int] = []
    try:
        extraction = await extract_document_text_with_qwen_ocr(
            storage_fields.get("file_name") or file.filename or "document",
            storage_fields.get("content_type") or file.content_type,
            content,
        )
        extracted_text = extraction.text[:DOCUMENT_EMBEDDING_TEXT_MAX_CHARS]
        extraction_method = extraction.extraction_method
        ocr_model = extraction.ocr_model
        ocr_pages = extraction.ocr_pages
    except DocumentOcrError:
        extracted_text, extraction_method = _extract_upload_text(
            content,
            storage_fields.get("content_type") or file.content_type,
            storage_fields.get("file_name") or file.filename,
        )
    now = _utc_now()
    document_id = document["document_id"]
    source = {
        "document_id": document_id,
        "document_name": document.get("document_name"),
        "document_type": document.get("document_type"),
        "document_version": document.get("document_version"),
        "document_tags": document.get("document_tags"),
        "document_description": document.get("document_description"),
        "file_name": storage_fields.get("file_name"),
        "content_type": storage_fields.get("content_type"),
        "file_size": storage_fields.get("file_size"),
        "extracted_text": extracted_text,
    }
    embedding_record = {
        "embedding_id": f"document-embedding-{document_id}",
        "document_id": document_id,
        "document_name": document.get("document_name"),
        "document_type": document.get("document_type"),
        "document_version": document.get("document_version"),
        "document_url": document.get("document_url"),
        "document_tags": document.get("document_tags") or [],
        "file_name": storage_fields.get("file_name"),
        "content_type": storage_fields.get("content_type"),
        "file_size": storage_fields.get("file_size"),
        "storage_backend": storage_fields.get("storage_backend"),
        "local_path": storage_fields.get("local_path"),
        "s3_bucket": storage_fields.get("s3_bucket"),
        "s3_key": storage_fields.get("s3_key"),
        "source_bytes_sha256": hashlib.sha256(content).hexdigest(),
        "bytes_indexed": len(content),
        "bytes_truncated": bytes_truncated,
        "extraction_method": extraction_method,
        "ocr_model": ocr_model,
        "ocr_pages": ocr_pages,
        "extracted_text": extracted_text,
        "text_truncated": len(extracted_text) >= DOCUMENT_EMBEDDING_TEXT_MAX_CHARS,
        "created_at": now,
        "updated_at": now,
        "embedding": build_embedding(DOCUMENT_EMBEDDINGS_COLLECTION, source),
    }
    await db[DOCUMENT_EMBEDDINGS_COLLECTION].replace_one(
        {"document_id": document_id},
        embedding_record,
        upsert=True,
    )
    await _replace_document_chunks(
        db, document, storage_fields, extracted_text, extraction_method
    )


def _s3_client():
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("boto3 is not installed") from exc
    return boto3.client("s3", region_name=S3_REGION, endpoint_url=S3_ENDPOINT_URL)


def generate_s3_view_url(
    doc: dict, expires_in: int = 3600, *, as_attachment: bool = False
) -> Optional[str]:
    bucket = doc.get("s3_bucket") or S3_BUCKET
    key = doc.get("s3_key")
    document_url = doc.get("document_url")
    if not key and isinstance(document_url, str):
        if document_url.startswith("s3://"):
            _, _, bucket_and_key = document_url.partition("s3://")
            parsed_bucket, _, parsed_key = bucket_and_key.partition("/")
            bucket = bucket or parsed_bucket
            key = parsed_key
        elif not document_url.startswith(("http://", "https://", "file://", "/")):
            key = document_url
    if not bucket or not key:
        return None
    try:
        params = {"Bucket": bucket, "Key": key}
        if as_attachment:
            filename = (
                doc.get("file_name")
                or Path(str(key)).name
                or doc.get("document_id")
                or "document"
            )
            safe_name = Path(str(filename)).name.replace('"', "")
            params["ResponseContentDisposition"] = f'attachment; filename="{safe_name}"'
        else:
            params["ResponseContentDisposition"] = "inline"
        return _s3_client().generate_presigned_url(
            "get_object", Params=params, ExpiresIn=expires_in
        )
    except Exception:
        return doc.get("s3_url")


async def _save_locally(file: UploadFile, document_id: str) -> dict:
    LOCAL_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = _safe_filename(file.filename or f"{document_id}.bin")
    local_path = LOCAL_UPLOAD_DIR / f"{document_id}-{safe_name}"
    size = 0
    with local_path.open("wb") as output:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            output.write(chunk)
    return {
        "storage_backend": "local",
        "file_name": safe_name,
        "content_type": file.content_type,
        "file_size": size,
        "local_path": str(local_path),
        "s3_bucket": None,
        "s3_key": None,
        "s3_url": None,
    }


async def store_upload(file: UploadFile, document_id: str) -> dict:
    safe_name = _safe_filename(file.filename or f"{document_id}.bin")
    s3_error: Optional[str] = None

    if S3_BUCKET:
        key = (
            f"{S3_PREFIX}/{document_id}/{safe_name}"
            if S3_PREFIX
            else f"{document_id}/{safe_name}"
        )
        try:
            await file.seek(0)
            client = _s3_client()
            extra_args = {}
            if file.content_type:
                extra_args["ContentType"] = file.content_type
            if extra_args:
                client.upload_fileobj(file.file, S3_BUCKET, key, ExtraArgs=extra_args)
            else:
                client.upload_fileobj(file.file, S3_BUCKET, key)
            await file.seek(0)
            contents = await file.read()
            s3_url = (
                f"{S3_PUBLIC_BASE_URL.rstrip('/')}/{key}"
                if S3_PUBLIC_BASE_URL
                else f"s3://{S3_BUCKET}/{key}"
            )
            return {
                "storage_backend": "s3",
                "file_name": safe_name,
                "content_type": file.content_type,
                "file_size": len(contents),
                "s3_bucket": S3_BUCKET,
                "s3_key": key,
                "s3_url": s3_url,
                "local_path": None,
                "storage_error": None,
            }
        except Exception as exc:
            s3_error = str(exc)

    await file.seek(0)
    local = await _save_locally(file, document_id)
    local["storage_error"] = s3_error
    return local


async def get_all(
    db: AsyncIOMotorDatabase,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    query: dict = {}
    total = await db[COLLECTION].count_documents(query)
    cursor = db[COLLECTION].find(query).skip((page - 1) * page_size).limit(page_size)
    return [_serialize(doc) async for doc in cursor], total


async def count(
    db: AsyncIOMotorDatabase,
    *,
    document_type: Optional[str] = None,
    document_owner: Optional[str] = None,
    document_department: Optional[str] = None,
    document_project: Optional[str] = None,
    document_status: Optional[str] = None,
    tag: Optional[str] = None,
) -> int:
    query: dict = {}
    if document_type:
        query["document_type"] = document_type
    if document_owner:
        query["document_owner"] = document_owner
    if document_department:
        query["document_department"] = document_department
    if document_project:
        query["document_project"] = document_project
    if document_status:
        query["document_status"] = document_status
    if tag:
        query["document_tags"] = tag
    return await db[COLLECTION].count_documents(query)


async def get_by_id(db: AsyncIOMotorDatabase, document_id: str) -> Optional[dict]:
    return _serialize(await db[COLLECTION].find_one({"document_id": document_id}))


async def get_by_type(db: AsyncIOMotorDatabase, document_type: str) -> list[dict]:
    cursor = db[COLLECTION].find({"document_type": document_type})
    return [_serialize(doc) async for doc in cursor]


async def get_by_owner(db: AsyncIOMotorDatabase, document_owner: str) -> list[dict]:
    cursor = db[COLLECTION].find({"document_owner": document_owner})
    return [_serialize(doc) async for doc in cursor]


async def get_by_department(
    db: AsyncIOMotorDatabase, document_department: str
) -> list[dict]:
    cursor = db[COLLECTION].find({"document_department": document_department})
    return [_serialize(doc) async for doc in cursor]


async def get_by_project(db: AsyncIOMotorDatabase, document_project: str) -> list[dict]:
    cursor = db[COLLECTION].find({"document_project": document_project})
    return [_serialize(doc) async for doc in cursor]


async def get_by_tag(db: AsyncIOMotorDatabase, tag: str) -> list[dict]:
    cursor = db[COLLECTION].find({"document_tags": tag})
    return [_serialize(doc) async for doc in cursor]


async def get_by_status(db: AsyncIOMotorDatabase, document_status: str) -> list[dict]:
    cursor = db[COLLECTION].find({"document_status": document_status})
    return [_serialize(doc) async for doc in cursor]


async def create(db: AsyncIOMotorDatabase, data: DocumentMetadataCreate) -> dict:
    now = _utc_now()
    payload = data.model_dump(mode="python")
    document_id = payload.get("document_id") or f"document-{uuid4().hex[:12]}"
    doc = _as_response_doc(
        {
            **payload,
            "document_id": document_id,
            "created_at": payload.get("created_at") or now,
            "last_modified_at": payload.get("last_modified_at") or now,
        }
    )
    await db[COLLECTION].insert_one(doc)
    return _serialize(doc)


async def create_with_file(
    db: AsyncIOMotorDatabase,
    data: DocumentMetadataCreate,
    file: UploadFile,
) -> dict:
    now = _utc_now()
    payload = data.model_dump(mode="python")
    document_id = payload.get("document_id") or f"document-{uuid4().hex[:12]}"
    storage_fields = await store_upload(file, document_id)
    doc = _as_response_doc(
        {
            **payload,
            **storage_fields,
            "document_id": document_id,
            "document_url": _document_url_from_storage(document_id, storage_fields),
            "created_at": payload.get("created_at") or now,
            "last_modified_at": payload.get("last_modified_at") or now,
        }
    )
    await db[COLLECTION].insert_one(doc)
    await _upsert_document_embedding(db, doc, file, storage_fields)
    return _serialize(doc)


async def attach_file(
    db: AsyncIOMotorDatabase,
    document_id: str,
    file: UploadFile,
) -> Optional[dict]:
    existing = await get_by_id(db, document_id)
    if not existing:
        return None
    storage_fields = await store_upload(file, document_id)
    storage_document_url = _document_url_from_storage(document_id, storage_fields)
    doc = _as_response_doc(
        {
            **existing,
            **storage_fields,
            "document_id": document_id,
            "document_url": storage_document_url,
            "last_modified_at": _utc_now(),
        }
    )
    result = await db[COLLECTION].find_one_and_update(
        {"document_id": document_id},
        {"$set": doc},
        return_document=True,
    )
    await _upsert_document_embedding(db, doc, file, storage_fields)
    return _serialize(result)


async def update(
    db: AsyncIOMotorDatabase,
    document_id: str,
    data: DocumentMetadataUpdate,
) -> Optional[dict]:
    existing = await get_by_id(db, document_id)
    if not existing:
        return None
    fields = data.model_dump(mode="python", exclude_unset=True)
    if not fields:
        return existing
    merged = {
        **existing,
        **fields,
        "document_id": document_id,
        "last_modified_at": fields.get("last_modified_at") or _utc_now(),
    }
    doc = _as_response_doc(merged)
    result = await db[COLLECTION].find_one_and_update(
        {"document_id": document_id},
        {"$set": doc},
        return_document=True,
    )
    return _serialize(result)


async def delete(db: AsyncIOMotorDatabase, document_id: str) -> bool:
    result = await db[COLLECTION].delete_one({"document_id": document_id})
    return result.deleted_count == 1
