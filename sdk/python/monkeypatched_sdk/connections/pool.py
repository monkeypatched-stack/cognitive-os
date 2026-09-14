"""
Connection Pool Base
====================

Provides generic async connection pooling:

    - Fixed pool size
    - Acquire / release with timeout
    - Automatic health validation before returning connections
    - Graceful shutdown

Subclass ConnectionPool and implement:
    create_connection() – create one connection
    is_healthy()        – test a connection
    _close_connection() – close a connection (optional, override if needed)
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from ..contracts.exceptions import ConnectionPoolExhausted

logger = logging.getLogger(__name__)


class ConnectionPool(ABC):
    """
    Generic async connection pool.

    Parameters
    ----------
    pool_size:  Maximum number of pooled connections.
    timeout:    Seconds to wait for a free connection before raising
                ConnectionPoolExhausted.
    """

    def __init__(self, pool_size: int = 10, timeout: int = 30) -> None:
        self.pool_size = pool_size
        self.timeout = timeout
        self._available: asyncio.Queue = asyncio.Queue(maxsize=pool_size)
        self._in_use: Dict[int, Any] = {}
        self._initialized = False

    # ------------------------------------------------------------------
    # Subclass contract
    # ------------------------------------------------------------------

    @abstractmethod
    async def create_connection(self) -> Any:
        """Create and return one new connection."""

    @abstractmethod
    async def is_healthy(self, connection: Any) -> bool:
        """Return True if ``connection`` is still usable."""

    async def _close_connection(self, connection: Any) -> None:
        """Close a connection. Override if the connection needs async close."""
        try:
            if hasattr(connection, "aclose"):
                await connection.aclose()
            elif hasattr(connection, "close"):
                result = connection.close()
                if asyncio.iscoroutine(result):
                    await result
        except Exception as exc:
            logger.debug("Error closing connection: %s", exc)

    # ------------------------------------------------------------------
    # Pool lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Fill the pool with pool_size connections."""
        if self._initialized:
            return
        logger.debug(
            "%s: initialising pool with %d connection(s).",
            self.__class__.__name__,
            self.pool_size,
        )
        errors = 0
        for _ in range(self.pool_size):
            try:
                conn = await self.create_connection()
                await self._available.put(conn)
            except Exception as exc:
                errors += 1
                logger.warning(
                    "%s: failed to create connection during init: %s",
                    self.__class__.__name__,
                    exc,
                )
        if errors == self.pool_size:
            raise ConnectionPoolExhausted(f"{self.__class__.__name__}: could not create any connections.")
        self._initialized = True
        logger.debug(
            "%s: pool ready (%d/%d connections).",
            self.__class__.__name__,
            self._available.qsize(),
            self.pool_size,
        )

    async def shutdown(self) -> None:
        """Close all pooled connections."""
        # Close idle connections
        while not self._available.empty():
            try:
                conn = self._available.get_nowait()
                await self._close_connection(conn)
            except asyncio.QueueEmpty:
                break
            except Exception as exc:
                logger.debug("Error closing idle connection: %s", exc)

        # Close in-use connections (force close on shutdown)
        for conn in list(self._in_use.values()):
            try:
                await self._close_connection(conn)
            except Exception as exc:
                logger.debug("Error closing in-use connection: %s", exc)
        self._in_use.clear()
        self._initialized = False
        logger.debug("%s: pool shut down.", self.__class__.__name__)

    # ------------------------------------------------------------------
    # Acquire / release
    # ------------------------------------------------------------------

    async def acquire(self) -> Any:
        """
        Check out a connection from the pool.

        Raises ConnectionPoolExhausted after ``timeout`` seconds.
        """
        try:
            conn = await asyncio.wait_for(self._available.get(), timeout=self.timeout)
        except asyncio.TimeoutError:
            raise ConnectionPoolExhausted(
                f"{self.__class__.__name__}: no available connection "
                f"within {self.timeout}s (pool_size={self.pool_size}, "
                f"in_use={len(self._in_use)})."
            )
        self._in_use[id(conn)] = conn
        return conn

    async def release(self, connection: Any) -> None:
        """
        Return a connection to the pool.

        If the connection is unhealthy it is replaced with a fresh one.
        """
        conn_id = id(connection)
        self._in_use.pop(conn_id, None)

        try:
            if await self.is_healthy(connection):
                await self._available.put(connection)
            else:
                logger.debug("%s: replacing unhealthy connection.", self.__class__.__name__)
                await self._close_connection(connection)
                try:
                    replacement = await self.create_connection()
                    await self._available.put(replacement)
                except Exception as exc:
                    logger.warning(
                        "%s: could not create replacement connection: %s",
                        self.__class__.__name__,
                        exc,
                    )
        except Exception as exc:
            logger.warning("%s: error during release: %s", self.__class__.__name__, exc)

    # ------------------------------------------------------------------
    # Context manager shortcut
    # ------------------------------------------------------------------

    def connection(self) -> "_PooledConnection":
        """
        Async context manager that acquires and auto-releases::

            async with pool.connection() as conn:
                result = await conn.get("/endpoint")
        """
        return _PooledConnection(self)

    @property
    def available_count(self) -> int:
        return self._available.qsize()

    @property
    def in_use_count(self) -> int:
        return len(self._in_use)


class _PooledConnection:
    """Async context manager returned by ConnectionPool.connection()."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool
        self._conn: Optional[Any] = None

    async def __aenter__(self) -> Any:
        self._conn = await self._pool.acquire()
        return self._conn

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._conn is not None:
            await self._pool.release(self._conn)
