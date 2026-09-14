from __future__ import annotations

from typing import Any

from services.process_definitions.node_services.models import (
    NodeExecutionResult,
    config_value,
    nested_value,
    set_nested_value,
)


async def execute_sequence_node(
    node_type: str | None,
    config: dict,
    payload: dict[str, Any],
) -> NodeExecutionResult:
    message = dict(payload)
    prop = str(config_value(config, "property", "payload"))
    value = nested_value(message, prop)
    if node_type == "split":
        if isinstance(value, str):
            separator = str(config.get("separator") or "")
            items = list(value) if separator == "" else value.split(separator)
        elif isinstance(value, dict):
            items = [{"key": key, "value": item} for key, item in value.items()]
        elif isinstance(value, list):
            items = value
        else:
            items = [] if value is None else [value]
        messages = []
        for index, item in enumerate(items):
            part = dict(message)
            set_nested_value(part, prop, item)
            if config.get("include_parts", True):
                part["parts"] = {
                    "id": payload.get("_msgid") or payload.get("id"),
                    "index": index,
                    "count": len(items),
                }
            messages.append(part)
        return NodeExecutionResult(
            True,
            None,
            {"status": "split", "messages": messages, "count": len(messages)},
        )
    if node_type == "join":
        messages = (
            payload.get("messages")
            if isinstance(payload.get("messages"), list)
            else payload.get("payload")
        )
        if not isinstance(messages, list):
            messages = [payload]
        values = []
        for item in messages:
            values.append(nested_value(item, prop) if isinstance(item, dict) else item)
        mode = str(config_value(config, "mode", "array"))
        if mode == "string":
            joined = str(config.get("separator") or "").join(
                str(item) for item in values
            )
        elif mode == "object":
            joined = {}
            key_prop = str(config_value(config, "key_property", "topic"))
            for index, item in enumerate(messages):
                key = nested_value(item, key_prop) if isinstance(item, dict) else index
                joined[str(key)] = values[index]
        else:
            joined = values
        set_nested_value(message, prop, joined)
        return NodeExecutionResult(
            True, None, {"status": "joined", "message": message, "count": len(values)}
        )
    if node_type == "sort":
        if not isinstance(value, list):
            return NodeExecutionResult(
                False,
                None,
                None,
                "Sort node requires a list at the configured property",
            )
        key_path = str(config.get("key") or "")
        numeric = bool(config.get("numeric") or False)
        reverse = (
            str(config_value(config, "direction", "ascending")).lower() == "descending"
        )

        def sort_key(item):
            raw = (
                nested_value(item, key_path)
                if key_path and isinstance(item, dict)
                else item
            )
            if numeric:
                try:
                    return float(raw)
                except (TypeError, ValueError):
                    return 0
            return "" if raw is None else str(raw)

        sorted_items = sorted(value, key=sort_key, reverse=reverse)
        set_nested_value(message, prop, sorted_items)
        return NodeExecutionResult(
            True,
            None,
            {"status": "sorted", "message": message, "count": len(sorted_items)},
        )
    if node_type == "batch":
        if not isinstance(value, list):
            return NodeExecutionResult(
                False,
                None,
                None,
                "Batch node requires a list at the configured property",
            )
        size = max(1, int(config_value(config, "size", 10)))
        overlap = max(0, min(int(config_value(config, "overlap", 0)), size - 1))
        step = max(1, size - overlap)
        messages = []
        for index, start in enumerate(range(0, len(value), step)):
            batch = value[start : start + size]
            if not batch:
                continue
            part = dict(message)
            set_nested_value(part, prop, batch)
            if config.get("include_parts", True):
                part["parts"] = {"index": index, "start": start, "size": len(batch)}
            messages.append(part)
        return NodeExecutionResult(
            True,
            None,
            {"status": "batched", "messages": messages, "count": len(messages)},
        )
    return NodeExecutionResult(True, None, {"status": "queued", "message": message})
