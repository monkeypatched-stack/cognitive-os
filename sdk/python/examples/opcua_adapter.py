"""
OPC-UA Adapter – Reference Implementation
==========================================

Pattern: OPC-UA connection pool (industrial protocol)

Capability:  read_sensor_value
System:      Any OPC-UA server (PLCs, SCADA, historians)

Demonstrates:
    - OPCUAConnectionPool for efficient session reuse
    - Reading node values by node ID
    - Batch reads for multiple tags
    - Industrial data type handling
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from monkeypatched_sdk import (
    AdapterContext,
    AdapterResponse,
    CapabilityAdapter,
    OPCUAConnectionPool,
    capability_adapter,
)
from monkeypatched_sdk.contracts.exceptions import AdapterInitializationError


@capability_adapter(
    capability_id="read_sensor_value",
    version="1.0.0",
    tags=["opc-ua", "industrial", "plc", "scada", "sensors"],
)
class OPCUASensorAdapter(CapabilityAdapter):
    """
    Reads sensor values from an OPC-UA server.

    Configuration (config.yaml)
    ---------------------------
    adapters:
      read_sensor_value:
        enabled: true
        endpoint: opc.tcp://plc.example.com:4840
        credentials:
          username: ${OPC_USER}
          password: ${OPC_PASS}
        timeout: 30
        extra:
          namespace_index: 2
          pool_size: 3
    """

    def __init__(self, adapter_id: str, capability_id: str) -> None:
        super().__init__(adapter_id, capability_id)
        self._pool: Optional[OPCUAConnectionPool] = None

    async def initialize(self) -> None:
        endpoint = self.config.get("endpoint")
        if not endpoint:
            raise AdapterInitializationError("OPCUASensorAdapter requires 'endpoint' in configuration.")

        credentials = self.config.get("credentials", {})
        pool_size = int(self.config.get("pool_size", 3))

        self._pool = OPCUAConnectionPool(
            endpoint=endpoint,
            username=credentials.get("username"),
            password=credentials.get("password"),
            pool_size=pool_size,
            timeout=int(self.config.get("timeout", 30)),
        )
        await self._pool.initialize()
        self.log("info", "OPCUASensorAdapter initialized.", endpoint=endpoint)

    async def execute(
        self,
        context: AdapterContext,
        inputs: Dict[str, Any],
    ) -> AdapterResponse:
        """
        Read one or more OPC-UA node values.

        Single node
        -----------
        inputs = {"node_id": "ns=2;i=1001"}

        Batch read
        ----------
        inputs = {"node_ids": ["ns=2;i=1001", "ns=2;i=1002", "ns=2;i=1003"]}

        Returns
        -------
        Single:  {"value": 22.5, "node_id": "...", "timestamp": "..."}
        Batch:   {"readings": [{"node_id": "...", "value": ..., "timestamp": "..."}]}
        """
        node_id: Optional[str] = inputs.get("node_id")
        node_ids: Optional[List[str]] = inputs.get("node_ids")

        if not node_id and not node_ids:
            return AdapterResponse.fail("Provide either 'node_id' (single) or 'node_ids' (batch).")

        if self._pool is None:
            return AdapterResponse.fail("Connection pool not initialized.")

        try:
            async with await self.telemetry.start_span("opcua_read") as span:
                async with self._pool.connection() as client:
                    if node_id:
                        # Single read
                        span.set_tag("node_id", node_id)
                        value = await self._read_node(client, node_id)
                        await self.emit_metric("opcua_reads", 1.0, tags={"node": node_id})
                        return AdapterResponse.ok(
                            {
                                "node_id": node_id,
                                "value": value,
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            }
                        )

                    else:
                        # Batch read
                        span.set_tag("node_count", len(node_ids))
                        readings = []
                        for nid in node_ids:
                            try:
                                val = await self._read_node(client, nid)
                                readings.append(
                                    {
                                        "node_id": nid,
                                        "value": val,
                                        "timestamp": datetime.now(timezone.utc).isoformat(),
                                        "error": None,
                                    }
                                )
                            except Exception as exc:
                                readings.append(
                                    {
                                        "node_id": nid,
                                        "value": None,
                                        "timestamp": datetime.now(timezone.utc).isoformat(),
                                        "error": str(exc),
                                    }
                                )

                        await self.emit_metric("opcua_batch_reads", float(len(node_ids)))
                        return AdapterResponse.ok({"readings": readings})

        except Exception as exc:
            self.log("error", f"OPC-UA read failed: {exc}")
            return AdapterResponse.fail(str(exc))

    @staticmethod
    async def _read_node(client: Any, node_id: str) -> Any:
        """Read a single OPC-UA node value."""
        node = client.get_node(node_id)
        value = await node.read_value()
        # Return Python primitive (float, int, bool, str)
        if hasattr(value, "Value"):
            return value.Value
        return value

    async def health_check(self) -> bool:
        """Ping the OPC-UA server by reading the server root node."""
        if self._pool is None:
            return False
        try:
            async with self._pool.connection() as client:
                root = client.get_node("i=84")
                await root.read_browse_name()
                return True
        except Exception:
            return False

    async def shutdown(self) -> None:
        """Shutdown the OPC-UA connection pool."""
        if self._pool is not None:
            await self._pool.shutdown()
        self.log("info", "OPCUASensorAdapter shut down.")
