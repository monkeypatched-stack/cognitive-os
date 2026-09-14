import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId

EMBEDDING_DIMENSIONS = 64
EMBEDDING_MODEL = "local-hash-v1"
EXCLUDED_TEXT_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "embedding",
    "hashed_password",
    "password",
    "password_hash",
    "refresh_token",
    "secret",
    "token",
}


def _json_default(value: Any) -> str:
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _flatten_value(value: Any) -> str:
    if isinstance(value, dict):
        parts = []
        for key in sorted(value):
            if key.lower() in EXCLUDED_TEXT_KEYS:
                continue
            nested = _flatten_value(value[key])
            if nested:
                parts.append(f"{key}: {nested}")
        return ". ".join(parts)
    if isinstance(value, list):
        return "; ".join(filter(None, (_flatten_value(item) for item in value)))
    if value is None:
        return ""
    return str(value)


def embedding_text(collection: str, document: dict[str, Any]) -> str:
    body = _flatten_value(document)
    return f"Collection: {collection}. {body}".strip()


def _hash_embedding(text: str) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    tokens = text.lower().split()
    if not tokens:
        return vector

    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        for index, byte in enumerate(digest):
            slot = index % EMBEDDING_DIMENSIONS
            vector[slot] += (byte / 255.0) - 0.5

    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude == 0:
        return vector
    return [round(value / magnitude, 8) for value in vector]


def build_embedding(collection: str, document: dict[str, Any]) -> dict[str, Any]:
    source_document = {
        key: value
        for key, value in document.items()
        if key.lower() not in EXCLUDED_TEXT_KEYS
    }
    text = embedding_text(collection, source_document)
    source_hash = hashlib.sha256(
        json.dumps(source_document, sort_keys=True, default=_json_default).encode(
            "utf-8"
        )
    ).hexdigest()
    return {
        "text": text,
        "vector": _hash_embedding(text),
        "model": EMBEDDING_MODEL,
        "dimensions": EMBEDDING_DIMENSIONS,
        "source_hash": source_hash,
        "updated_at": datetime.now(timezone.utc),
    }


def enrich_document_embedding(
    collection: str, document: dict[str, Any]
) -> dict[str, Any]:
    enriched = dict(document)
    enriched["embedding"] = build_embedding(collection, enriched)
    return enriched
