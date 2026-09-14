"""
OPC-UA Connection Pool
=======================

Pools OPC-UA client sessions for industrial PLC / SCADA integrations.

Uses asyncua (opcua-asyncio) library.  Install with:
    pip install asyncua

Suitable for:
    - Siemens PLCs (S7-1500, etc.)
    - Allen-Bradley / Rockwell Automation
    - Beckhoff TwinCAT
    - Any OPC-UA compliant server

Usage::

    pool = OPCUAConnectionPool(
        endpoint="opc.tcp://plc.example.com:4840",
        username="opcuser",
        password="opcpass",
        pool_size=3,
    )
    await pool.initialize()

    async with pool.connection() as client:
        node = client.get_node("ns=2;i=1001")
        value = await node.read_value()
"""

import logging
from typing import Any, Optional

from .pool import ConnectionPool

logger = logging.getLogger(__name__)

# OPC-UA root node for basic connectivity ping
_ROOT_NODE_ID = "i=84"


class OPCUAConnectionPool(ConnectionPool):
    """
    Connection pool for OPC-UA servers via asyncua.

    Parameters
    ----------
    endpoint:   OPC-UA endpoint URL  (e.g. ``opc.tcp://plc:4840``).
    username:   OPC-UA user (optional; leave None for anonymous).
    password:   OPC-UA password (optional).
    security:   Security policy string (e.g. ``Basic256Sha256``).
    """

    def __init__(
        self,
        endpoint: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        security: Optional[str] = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.endpoint = endpoint
        self.username = username
        self.password = password
        self.security = security

    async def create_connection(self) -> Any:
        """Connect a new OPC-UA client session."""
        try:
            from asyncua import Client
        except ImportError as exc:
            raise ImportError("asyncua is required for OPCUAConnectionPool. Install with: pip install asyncua") from exc

        client = Client(url=self.endpoint, timeout=self.timeout)

        if self.username:
            client.set_user(self.username)
            client.set_password(self.password or "")

        if self.security:
            await client.set_security_string(self.security)

        await client.connect()
        logger.debug("OPC-UA session connected to %s.", self.endpoint)
        return client

    async def is_healthy(self, connection: Any) -> bool:
        """Ping the OPC-UA server by reading the root node."""
        try:
            node = connection.get_node(_ROOT_NODE_ID)
            await node.read_browse_name()
            return True
        except Exception:
            return False

    async def _close_connection(self, connection: Any) -> None:
        """Disconnect the OPC-UA client."""
        try:
            await connection.disconnect()
        except Exception as exc:
            logger.debug("Error closing OPC-UA connection: %s", exc)
