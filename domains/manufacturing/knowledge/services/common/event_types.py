import re
from typing import Any

DEFAULT_EVENT_TYPE = "generic"


def slugify_event_type(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or DEFAULT_EVENT_TYPE


def measurement_suffix(value: Any) -> str:
    return slugify_event_type(value).replace("-", "_")


def infer_event_type(event: dict[str, Any] | None) -> str:
    if not isinstance(event, dict):
        return DEFAULT_EVENT_TYPE

    payload = event.get("payload")
    payload_dict = payload if isinstance(payload, dict) else {}

    for key in ("event_type", "eventType", "category", "type"):
        value = event.get(key)
        if value:
            return slugify_event_type(value)

    for key in ("event_type", "eventType", "category", "type"):
        value = payload_dict.get(key)
        if value:
            return slugify_event_type(value)

    direction = event.get("direction") or payload_dict.get("direction")
    provider = event.get("provider") or payload_dict.get("provider")
    if direction and provider:
        return slugify_event_type(f"{provider}-{direction}")
    if direction:
        return slugify_event_type(direction)

    return DEFAULT_EVENT_TYPE


def configured_event_type_slugs(value: str) -> list[str]:
    return [
        slugify_event_type(event_type)
        for event_type in str(value or "").split(",")
        if event_type.strip()
    ]
