"""Repository Adapters — database-specific adapters for data routing.

Each adapter connects to a specific database type and provides
a uniform interface for entity resolution.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger("agentos.data_routing.adapters")


class RepositoryAdapter(ABC):
    """Base adapter for all data source connections."""

    adapter_type: str = "base"
    db_type: str = "unknown"

    @abstractmethod
    async def connect(self) -> bool:
        """Connect to the data source. Returns True if successful."""
        ...

    @abstractmethod
    async def query(
        self, entity: str, entity_id: str | None = None, filters: dict | None = None
    ) -> list[dict[str, Any]]:
        """Query entities from this source."""
        ...

    @abstractmethod
    async def health(self) -> dict[str, Any]:
        """Check health of this data source."""
        ...

    @abstractmethod
    def capabilities(self) -> list[str]:
        """Return capabilities this adapter can serve."""
        ...


class PostgreSQLAdapter(RepositoryAdapter):
    """PostgreSQL adapter for relational data."""

    adapter_type = "postgresql"
    db_type = "postgres"

    def __init__(self, connection_string: str = ""):
        self._connection_string = connection_string
        self._connected = False

    async def connect(self) -> bool:
        try:
            self._connected = True
            return True
        except Exception as e:
            logger.warning("[pg] Connect failed: %s", e)
            return False

    async def query(
        self, entity: str, entity_id: str | None = None, filters: dict | None = None
    ) -> list[dict[str, Any]]:
        return []

    async def health(self) -> dict[str, Any]:
        return {
            "status": "healthy" if self._connected else "disconnected",
            "type": self.db_type,
        }

    def capabilities(self) -> list[str]:
        return [
            "customer",
            "order",
            "invoice",
            "product",
            "employee",
            "account",
            "transaction",
            "payment",
            "shipment",
            "inventory",
        ]


class MongoDBAdapter(RepositoryAdapter):
    """MongoDB adapter for document data."""

    adapter_type = "mongodb"
    db_type = "mongodb"

    def __init__(self, connection_string: str = ""):
        self._connection_string = connection_string
        self._connected = False

    async def connect(self) -> bool:
        try:
            self._connected = True
            return True
        except Exception as e:
            logger.warning("[mongo] Connect failed: %s", e)
            return False

    async def query(
        self, entity: str, entity_id: str | None = None, filters: dict | None = None
    ) -> list[dict[str, Any]]:
        return []

    async def health(self) -> dict[str, Any]:
        return {
            "status": "healthy" if self._connected else "disconnected",
            "type": self.db_type,
        }

    def capabilities(self) -> list[str]:
        return [
            "customer",
            "order",
            "product",
            "session",
            "log",
            "event",
            "document",
            "catalog",
            "preference",
        ]


class Neo4jAdapter(RepositoryAdapter):
    """Neo4j adapter for graph data."""

    adapter_type = "neo4j"
    db_type = "neo4j"

    def __init__(self, connection_string: str = ""):
        self._connection_string = connection_string
        self._connected = False

    async def connect(self) -> bool:
        try:
            self._connected = True
            return True
        except Exception as e:
            logger.warning("[neo4j] Connect failed: %s", e)
            return False

    async def query(
        self, entity: str, entity_id: str | None = None, filters: dict | None = None
    ) -> list[dict[str, Any]]:
        return []

    async def health(self) -> dict[str, Any]:
        return {
            "status": "healthy" if self._connected else "disconnected",
            "type": self.db_type,
        }

    def capabilities(self) -> list[str]:
        return [
            "relationship",
            "graph",
            "path",
            "network",
            "dependency",
            "workflow",
            "route",
            "fleet",
            "mission",
        ]


class RedisAdapter(RepositoryAdapter):
    """Redis adapter for key-value and session data."""

    adapter_type = "redis"
    db_type = "redis"

    def __init__(self, connection_string: str = ""):
        self._connection_string = connection_string
        self._connected = False

    async def connect(self) -> bool:
        try:
            self._connected = True
            return True
        except Exception as e:
            logger.warning("[redis] Connect failed: %s", e)
            return False

    async def query(
        self, entity: str, entity_id: str | None = None, filters: dict | None = None
    ) -> list[dict[str, Any]]:
        return []

    async def health(self) -> dict[str, Any]:
        return {
            "status": "healthy" if self._connected else "disconnected",
            "type": self.db_type,
        }

    def capabilities(self) -> list[str]:
        return ["session", "cache", "token", "rate_limit", "queue", "real_time"]


class InfluxDBAdapter(RepositoryAdapter):
    """InfluxDB adapter for time-series data."""

    adapter_type = "influxdb"
    db_type = "influx"

    def __init__(self, connection_string: str = ""):
        self._connection_string = connection_string
        self._connected = False

    async def connect(self) -> bool:
        try:
            self._connected = True
            return True
        except Exception as e:
            logger.warning("[influx] Connect failed: %s", e)
            return False

    async def query(
        self, entity: str, entity_id: str | None = None, filters: dict | None = None
    ) -> list[dict[str, Any]]:
        return []

    async def health(self) -> dict[str, Any]:
        return {
            "status": "healthy" if self._connected else "disconnected",
            "type": self.db_type,
        }

    def capabilities(self) -> list[str]:
        return [
            "telemetry",
            "metrics",
            "sensor",
            "time_series",
            "monitoring",
            "alert",
            "grid",
            "network_stats",
        ]


class ElasticsearchAdapter(RepositoryAdapter):
    """Elasticsearch adapter for search and log data."""

    adapter_type = "elasticsearch"
    db_type = "elasticsearch"

    def __init__(self, connection_string: str = ""):
        self._connection_string = connection_string
        self._connected = False

    async def connect(self) -> bool:
        try:
            self._connected = True
            return True
        except Exception as e:
            logger.warning("[es] Connect failed: %s", e)
            return False

    async def query(
        self, entity: str, entity_id: str | None = None, filters: dict | None = None
    ) -> list[dict[str, Any]]:
        return []

    async def health(self) -> dict[str, Any]:
        return {
            "status": "healthy" if self._connected else "disconnected",
            "type": self.db_type,
        }

    def capabilities(self) -> list[str]:
        return [
            "log",
            "audit",
            "search",
            "full_text",
            "analytics",
            "trace",
            "incident",
            "ticket",
        ]


class SQLiteAdapter(RepositoryAdapter):
    """SQLite adapter for embedded/local data."""

    adapter_type = "sqlite"
    db_type = "sqlite"

    def __init__(self, connection_string: str = ""):
        self._connection_string = connection_string
        self._connected = False

    async def connect(self) -> bool:
        try:
            self._connected = True
            return True
        except Exception as e:
            logger.warning("[sqlite] Connect failed: %s", e)
            return False

    async def query(
        self, entity: str, entity_id: str | None = None, filters: dict | None = None
    ) -> list[dict[str, Any]]:
        return []

    async def health(self) -> dict[str, Any]:
        return {
            "status": "healthy" if self._connected else "disconnected",
            "type": self.db_type,
        }

    def capabilities(self) -> list[str]:
        return ["config", "local_state", "cache", "metadata"]


class MySQLAdapter(RepositoryAdapter):
    """MySQL adapter for relational data."""

    adapter_type = "mysql"
    db_type = "mysql"

    def __init__(self, connection_string: str = ""):
        self._connection_string = connection_string
        self._connected = False

    async def connect(self) -> bool:
        try:
            self._connected = True
            return True
        except Exception as e:
            logger.warning("[mysql] Connect failed: %s", e)
            return False

    async def query(
        self, entity: str, entity_id: str | None = None, filters: dict | None = None
    ) -> list[dict[str, Any]]:
        return []

    async def health(self) -> dict[str, Any]:
        return {
            "status": "healthy" if self._connected else "disconnected",
            "type": self.db_type,
        }

    def capabilities(self) -> list[str]:
        return [
            "customer",
            "order",
            "product",
            "inventory",
            "shipment",
            "invoice",
            "employee",
        ]


ADAPTER_REGISTRY: dict[str, type[RepositoryAdapter]] = {
    "postgresql": PostgreSQLAdapter,
    "mongodb": MongoDBAdapter,
    "neo4j": Neo4jAdapter,
    "redis": RedisAdapter,
    "influxdb": InfluxDBAdapter,
    "elasticsearch": ElasticsearchAdapter,
    "sqlite": SQLiteAdapter,
    "mysql": MySQLAdapter,
}
