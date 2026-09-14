"""
MQTT Connection Pool
=====================

Pools MQTT client connections for IoT / sensor integrations.

Uses paho-mqtt (already in requirements.txt) with an asyncio event loop
bridge.  Each pooled client maintains a persistent connection to the broker.

Supported brokers:
    - Eclipse Mosquitto
    - HiveMQ
    - AWS IoT Core (MQTT over TLS)
    - Azure IoT Hub
    - Any MQTT 3.1.1 / 5.0 compatible broker

Usage::

    pool = MQTTConnectionPool(
        broker="mqtt.example.com",
        port=1883,
        username="user",
        password="pass",
        pool_size=3,
    )
    await pool.initialize()

    async with pool.connection() as client:
        client.publish("sensors/temperature", "22.5")
"""

import asyncio
import logging
from typing import Any, Optional

from .pool import ConnectionPool

logger = logging.getLogger(__name__)


class MQTTConnectionPool(ConnectionPool):
    """
    Connection pool for MQTT brokers via paho-mqtt.

    Parameters
    ----------
    broker:     Broker hostname or IP.
    port:       Broker port (default 1883; use 8883 for TLS).
    username:   MQTT username (optional).
    password:   MQTT password (optional).
    tls:        Set True to enable TLS.
    client_id_prefix: Prefix for auto-generated client IDs.
    """

    def __init__(
        self,
        broker: str,
        port: int = 1883,
        username: Optional[str] = None,
        password: Optional[str] = None,
        tls: bool = False,
        client_id_prefix: str = "monkeypatched-adapter",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.broker = broker
        self.port = port
        self.username = username
        self.password = password
        self.tls = tls
        self.client_id_prefix = client_id_prefix
        self._client_counter = 0

    async def create_connection(self) -> Any:
        """Create and connect a new paho-mqtt client."""
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:
            raise ImportError(
                "paho-mqtt is required for MQTTConnectionPool. Install with: pip install paho-mqtt"
            ) from exc

        self._client_counter += 1
        client_id = f"{self.client_id_prefix}-{self._client_counter}"

        client = mqtt.Client(client_id=client_id)

        if self.username:
            client.username_pw_set(self.username, self.password)

        if self.tls:
            client.tls_set()

        # Connect synchronously (paho is callback-based)
        connected_event = asyncio.Event()
        loop = asyncio.get_event_loop()

        def on_connect(c, userdata, flags, rc):
            if rc == 0:
                loop.call_soon_threadsafe(connected_event.set)
            else:
                logger.error("MQTT connect failed with code %d", rc)

        client.on_connect = on_connect

        try:
            client.connect(self.broker, self.port, keepalive=60)
            client.loop_start()
            await asyncio.wait_for(connected_event.wait(), timeout=self.timeout)
        except asyncio.TimeoutError:
            client.loop_stop()
            raise ConnectionError(f"MQTT connection to {self.broker}:{self.port} timed out.")

        return client

    async def is_healthy(self, connection: Any) -> bool:
        """Return True if the MQTT client is still connected."""
        try:
            return connection.is_connected()
        except Exception:
            return False

    async def _close_connection(self, connection: Any) -> None:
        """Disconnect and stop the MQTT client loop."""
        try:
            connection.loop_stop()
            connection.disconnect()
        except Exception as exc:
            logger.debug("Error closing MQTT connection: %s", exc)
