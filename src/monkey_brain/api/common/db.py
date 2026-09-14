"""Shared database utilities for API route handlers."""

from __future__ import annotations

from typing import Any


def get_db(mongo_client: Any) -> Any:
    """Return the application database from a mongo client, or None if client is None."""
    if mongo_client is None:
        return None
    from services.common.config import settings

    return mongo_client[settings.DB_NAME]
