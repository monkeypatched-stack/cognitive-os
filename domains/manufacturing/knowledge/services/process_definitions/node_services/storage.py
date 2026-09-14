from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from services.process_definitions.node_services.models import (
    NodeExecutionResult,
    config_value,
)


async def execute_storage_node(
    node_type: str | None,
    config: dict,
    payload: dict[str, Any],
) -> NodeExecutionResult:
    path_value = str(config_value(config, "path", ""))
    if not path_value:
        return NodeExecutionResult(False, None, None, f"{node_type} path is required")
    path = Path(path_value).expanduser().resolve()
    if ".." in path_value:
        return NodeExecutionResult(False, None, None, "path traversal not allowed")
    encoding = str(config_value(config, "encoding", "utf-8"))
    if node_type == "watch":
        return NodeExecutionResult(
            True,
            None,
            {
                "status": "registered",
                "path": str(path),
                "recursive": bool(config.get("recursive") or False),
                "events": config.get("events") or [],
            },
        )
    if node_type == "file in":
        if not path.exists():
            return NodeExecutionResult(False, None, None, f"File '{path}' not found")
        mode = str(config_value(config, "mode", "text"))
        if mode == "bytes":
            value: Any = path.read_bytes().hex()
        else:
            text = path.read_text(encoding=encoding)
            value = json.loads(text) if mode == "json" else text
        return NodeExecutionResult(
            True, None, {"status": "read", "path": str(path), "payload": value}
        )
    if node_type == "file":
        if config.get("create_dirs", True):
            path.parent.mkdir(parents=True, exist_ok=True)
        value = payload.get("payload", payload)
        text = value if isinstance(value, str) else json.dumps(value)
        mode = "a" if config.get("append") else "w"
        with path.open(mode, encoding=encoding) as handle:
            handle.write(text)
        return NodeExecutionResult(
            True,
            None,
            {
                "status": "written",
                "path": str(path),
                "bytes": len(text.encode(encoding)),
            },
        )
    return NodeExecutionResult(True, None, {"status": "queued"})
