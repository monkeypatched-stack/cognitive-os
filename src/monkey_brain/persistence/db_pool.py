"""MongoDB Connection Pool Manager.

Manages database connections for persistence layer.
Singleton pattern ensures single pool across application.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("agentos.persistence.db_pool")

_db_pool: Any = None


class DBPool:
    """MongoDB connection pool manager."""

    def __init__(self, connection_string: str | None = None):
        """Initialize MongoDB connection.

        Args:
            connection_string: MongoDB connection string
                              (defaults to DATABASE_URL env var)
        """
        self.connection_string = connection_string or os.getenv("DATABASE_URL") or self._mongodb_url_fallback()
        self._client = None
        self._db = None

    @staticmethod
    def _mongodb_url_fallback() -> str:
        """Gap Remediation audit finding: this codebase has two genuinely
        different, pre-existing Mongo env-var conventions — this class's
        own DATABASE_URL (with a database path baked in, since connect()
        below calls get_database() with no explicit name and relies on
        the connection string's own path component), and MONGODB_URL
        (used by the main API's own boot/health-check code and
        docker-compose.yml/deploy/k8s/configmap.yaml, typically WITHOUT
        a database path). Confirmed live during Deployment Conformance
        testing: a container given only MONGODB_URL failed to connect
        here (ActorStateStore/belief persistence silently disabled)
        while the rest of the app connected fine via its own, different
        Mongo client. MONGODB_URL is now accepted as a fallback, with
        DB_NAME (the same env var docker-compose.yml/configmap.yaml
        already set for the unrelated domain microservices) appended
        when the URL has no database path of its own — never overriding
        an explicit DATABASE_URL, so any deployment that already sets it
        is completely unaffected."""
        mongodb_url = os.getenv("MONGODB_URL", "").strip()
        if not mongodb_url:
            return "mongodb://localhost:27017/cognitive_platform"
        # A bare "mongodb://host:port" (no path, or just "/") has no
        # database component pymongo's get_database() can resolve.
        path = mongodb_url.split("://", 1)[-1].split("?", 1)[0].split("/", 1)
        has_db_path = len(path) > 1 and path[1].strip("/")
        if has_db_path:
            return mongodb_url
        db_name = os.getenv("DB_NAME", "cognitive_platform")
        base = mongodb_url.split("?", 1)
        query = f"?{base[1]}" if len(base) > 1 else ""
        return f"{base[0].rstrip('/')}/{db_name}{query}"

    def connect(self) -> None:
        """Establish MongoDB connection."""
        try:
            from pymongo import MongoClient

            self._client = MongoClient(
                self.connection_string,
                connectTimeoutMS=5000,
                serverSelectionTimeoutMS=5000,
                maxPoolSize=20,
                minPoolSize=1,
            )
            # Verify connection
            self._client.admin.command("ping")
            self._db = self._client.get_database()
            logger.info("[db_pool] Connected to MongoDB: %s", self.connection_string)
        except ImportError:
            logger.error("[db_pool] pymongo not installed")
            raise
        except Exception as e:
            logger.error("[db_pool] Connection failed: %s", e)
            raise

    def get_db(self) -> Any:
        """Get MongoDB database instance.

        Args:
            tenant_id: Tenant for query filtering (stored in documents)

        Returns:
            MongoDB database object
        """
        if self._db is None:
            self.connect()
        return self._db

    def get_collection(self, collection_name: str, tenant_id: str | None = None) -> Any:
        """Get MongoDB collection with optional tenant context.

        Args:
            collection_name: Name of collection
            tenant_id: Tenant ID (stored in documents for filtering)

        Returns:
            MongoDB collection object
        """
        db = self.get_db()
        collection = db[collection_name]

        # Store tenant_id for use in queries (documents include tenant_id field)
        if tenant_id:
            collection._tenant_id = tenant_id
            logger.debug("[db_pool] Collection context set for tenant: %s", tenant_id)

        return collection

    class CursorContext:
        """Context manager for MongoDB operations (mimics cursor interface)."""

        def __init__(self, db: Any, tenant_id: str | None = None):
            self.db = db
            self.tenant_id = tenant_id
            self.operations = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

        def execute(self, operation: str, params: tuple | None = None) -> None:
            """Store operation (MongoDB doesn't use SQL execute)."""
            self.operations.append((operation, params))

    def cursor(self, tenant_id: str | None = None) -> Any:
        """Get a MongoDB context manager (mimics cursor interface).

        Args:
            tenant_id: Tenant for this connection

        Usage:
            with db_pool.cursor(tenant_id="org_alpha") as cur:
                collection = db_pool.get_collection("actor_state", tenant_id)
        """
        if self._db is None:
            self.connect()

        return self.CursorContext(self._db, tenant_id)

    def commit(self) -> None:
        """No-op for MongoDB (transactions auto-committed)."""
        logger.debug("[db_pool] MongoDB operations committed")

    def close(self) -> None:
        """Close MongoDB connection."""
        if self._client:
            self._client.close()
            logger.info("[db_pool] MongoDB connection closed")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


def get_db_pool(connection_string: str | None = None) -> DBPool:
    """Get or create singleton database pool.

    Args:
        connection_string: Connection string (only used on first call)

    Returns:
        DBPool instance
    """
    global _db_pool
    if _db_pool is None:
        _db_pool = DBPool(connection_string)
        _db_pool.connect()
    return _db_pool


def reset_db_pool() -> None:
    """Reset singleton pool (for testing)."""
    global _db_pool
    if _db_pool:
        _db_pool.close()
    _db_pool = None
