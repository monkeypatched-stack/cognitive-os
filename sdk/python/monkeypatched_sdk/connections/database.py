"""
Database Connection Pool
=========================

Generic database connection pool supporting multiple backends:

    - PostgreSQL via asyncpg
    - MongoDB via motor (already in requirements.txt)
    - Neo4j via neo4j-driver (already in requirements.txt)
    - Any asyncio-compatible database driver

Usage (PostgreSQL)::

    pool = DatabaseConnectionPool(
        connection_string="postgresql://user:pass@host:5432/dbname",
        db_type="postgres",
        pool_size=10,
    )
    await pool.initialize()

    async with pool.connection() as conn:
        rows = await conn.fetch("SELECT * FROM work_orders WHERE status = $1", "open")

Usage (MongoDB)::

    pool = DatabaseConnectionPool(
        connection_string="mongodb://host:27017",
        db_type="mongo",
        database_name="mydb",
    )
    await pool.initialize()

    async with pool.connection() as db:
        docs = await db.work_orders.find({"status": "open"}).to_list(100)
"""

import logging
from typing import Any, Optional

from .pool import ConnectionPool

logger = logging.getLogger(__name__)


class DatabaseConnectionPool(ConnectionPool):
    """
    Database connection pool with multi-backend support.

    Parameters
    ----------
    connection_string:  Driver-specific connection string.
    db_type:            ``"postgres"`` | ``"mongo"`` | ``"neo4j"``
                        (default: auto-detected from connection_string).
    database_name:      Database/schema name (required for MongoDB).
    """

    def __init__(
        self,
        connection_string: str,
        db_type: Optional[str] = None,
        database_name: Optional[str] = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.connection_string = connection_string
        self.db_type = db_type or self._detect_type(connection_string)
        self.database_name = database_name

    @staticmethod
    def _detect_type(connection_string: str) -> str:
        """Auto-detect the database type from the connection string."""
        cs = connection_string.lower()
        if cs.startswith("postgresql") or cs.startswith("postgres"):
            return "postgres"
        if cs.startswith("mongodb"):
            return "mongo"
        if cs.startswith("bolt") or cs.startswith("neo4j"):
            return "neo4j"
        return "unknown"

    async def create_connection(self) -> Any:
        """Create a database connection based on db_type."""
        if self.db_type == "postgres":
            return await self._create_postgres()
        if self.db_type == "mongo":
            return await self._create_mongo()
        if self.db_type == "neo4j":
            return await self._create_neo4j()
        raise ValueError(f"Unsupported db_type '{self.db_type}'. Supported: postgres, mongo, neo4j.")

    async def _create_postgres(self) -> Any:
        """Create an asyncpg connection."""
        try:
            import asyncpg
        except ImportError as exc:
            raise ImportError(
                "asyncpg is required for PostgreSQL connections. Install with: pip install asyncpg"
            ) from exc
        conn = await asyncpg.connect(self.connection_string)
        logger.debug("PostgreSQL connection established.")
        return conn

    async def _create_mongo(self) -> Any:
        """Create a Motor (async MongoDB) database handle."""
        try:
            import motor.motor_asyncio as motor
        except ImportError as exc:
            raise ImportError("motor is required for MongoDB connections. Install with: pip install motor") from exc
        client = motor.AsyncIOMotorClient(self.connection_string)
        db_name = self.database_name or "default"
        logger.debug("MongoDB connection established (db=%s).", db_name)
        return client[db_name]

    async def _create_neo4j(self) -> Any:
        """Create a Neo4j async session."""
        try:
            from neo4j import AsyncGraphDatabase
        except ImportError as exc:
            raise ImportError("neo4j is required for Neo4j connections. Install with: pip install neo4j") from exc
        driver = AsyncGraphDatabase.driver(self.connection_string)
        session = driver.session(database=self.database_name or "neo4j")
        logger.debug("Neo4j session established.")
        return session

    async def is_healthy(self, connection: Any) -> bool:
        """Test the connection with a lightweight query."""
        try:
            if self.db_type == "postgres":
                await connection.fetchval("SELECT 1")
                return True
            if self.db_type == "mongo":
                await connection.command("ping")
                return True
            if self.db_type == "neo4j":
                await connection.run("RETURN 1")
                return True
        except Exception:
            pass
        return False

    async def _close_connection(self, connection: Any) -> None:
        """Close the database connection."""
        try:
            if self.db_type == "postgres":
                await connection.close()
            elif self.db_type == "neo4j":
                await connection.close()
            # Motor clients are managed by the AsyncIOMotorClient (not closed per connection)
        except Exception as exc:
            logger.debug("Error closing database connection: %s", exc)
