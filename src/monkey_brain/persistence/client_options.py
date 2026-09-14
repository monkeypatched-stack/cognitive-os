"""Shared client options + safe logging for external datastores.

Two things every client here needs and several were missing:

1. **Explicit timeouts.** Drivers default generously (Motor's serverSelectionTimeoutMS is 30s)
   or not at all (redis-py's socket_timeout is None → a hung server blocks forever). With a
   slow or dead datastore, unbounded/long waits saturate request workers.

2. **Credential-safe logging.** Connection URLs conventionally carry credentials in the
   userinfo (``scheme://user:pass@host``). Logging them verbatim leaks secrets.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit


def redact_url(url: str) -> str:
    """Strip credentials from a connection URL so it is safe to log.

    ``mongodb://alice:s3cret@db:27017/x`` → ``mongodb://***@db:27017/x``
    """
    if not url:
        return ""
    try:
        parts = urlsplit(url)
        if not (parts.username or parts.password):
            return url
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        return urlunsplit((parts.scheme, f"***@{host}", parts.path, parts.query, parts.fragment))
    except Exception:
        return "<unparseable-url-redacted>"


def mongo_client_options() -> dict:
    """Explicit timeouts + pool bounds for Motor/PyMongo clients.

    Without these, a down Mongo stalls EVERY operation for the 30s default server-selection
    timeout before failing — which under load exhausts the worker pool.
    """
    return {
        "serverSelectionTimeoutMS": int(os.getenv("MONGO_SERVER_SELECTION_TIMEOUT_MS", "5000")),
        "connectTimeoutMS": int(os.getenv("MONGO_CONNECT_TIMEOUT_MS", "5000")),
        "socketTimeoutMS": int(os.getenv("MONGO_SOCKET_TIMEOUT_MS", "30000")),
        "maxPoolSize": int(os.getenv("MONGO_MAX_POOL_SIZE", "100")),
    }
