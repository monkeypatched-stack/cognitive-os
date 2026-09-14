"""
MQTT Sensor Adapter – Reference Implementation
================================================

Pattern: MQTT subscription + event-driven data ingestion

Capability:  ingest_sensor_telemetry
System:      Any MQTT broker (Mosquitto, HiveMQ, AWS IoT Core, etc.)

Demonstrates:
    - MQTTConnectionPool for persistent broker connections
    - Subscribe-and-forward pattern (MQTT → platform event bus)
    - Parsing and normalising raw sensor payloads
    - Handling continuous data streams as discrete capability calls
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from monkeypatched_sdk import (
    AdapterContext,
    AdapterResponse,
    CapabilityAdapter,
    MQTTConnectionPool,
    capability_adapter,
)
from monkeypatched_sdk.contracts.exceptions import AdapterInitializationError

logger = logging.getLogger(__name__)


@capability_adapter(
    capability_id="ingest_sensor_telemetry",
    version="1.0.0",
    tags=["mqtt", "iot", "sensors", "telemetry"],
)
class MQTTSensorAdapter(CapabilityAdapter):
    """
    Receives sensor telemetry from an MQTT broker.

    This adapter operates in two modes:

    1. **Subscribe mode** (background): subscribes to configured topics
       and forwards incoming messages as platform events.

    2. **On-demand mode** (execute): waits for the next message on a
       topic and returns it as an AdapterResponse.

    Configuration (config.yaml)
    ---------------------------
    adapters:
      ingest_sensor_telemetry:
        enabled: true
        endpoint: mqtt.example.com
        credentials:
          username: ${MQTT_USER}
          password: ${MQTT_PASS}
        timeout: 30
        extra:
          port: 1883
          topics:
            - sensors/+/temperature
            - sensors/+/pressure
            - sensors/+/vibration
          qos: 1
    """

    def __init__(self, adapter_id: str, capability_id: str) -> None:
        super().__init__(adapter_id, capability_id)
        self._pool: Optional[MQTTConnectionPool] = None
        self._subscriber_client: Optional[Any] = None
        self._message_buffer: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._topics: List[str] = []
        self._qos: int = 1

    async def initialize(self) -> None:
        broker = self.config.get("endpoint")
        if not broker:
            raise AdapterInitializationError("MQTTSensorAdapter requires 'endpoint' (broker hostname).")

        credentials = self.config.get("credentials", {})
        port = int(self.config.get("port", 1883))
        self._topics = self.config.get("topics", ["sensors/#"])
        self._qos = int(self.config.get("qos", 1))

        self._pool = MQTTConnectionPool(
            broker=broker,
            port=port,
            username=credentials.get("username"),
            password=credentials.get("password"),
            pool_size=2,
            timeout=int(self.config.get("timeout", 30)),
        )
        await self._pool.initialize()

        # Set up a dedicated subscriber client
        await self._setup_subscriber()
        self.log(
            "info",
            "MQTTSensorAdapter initialized.",
            broker=broker,
            topics=self._topics,
        )

    async def _setup_subscriber(self) -> None:
        """Configure the background subscriber client."""
        self._subscriber_client = await self._pool.acquire()

        loop = asyncio.get_event_loop()

        def on_message(client, userdata, message):
            try:
                payload = message.payload.decode("utf-8")
                record = {
                    "topic": message.topic,
                    "payload": payload,
                    "qos": message.qos,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self._handle_message(record)))
            except Exception as exc:
                logger.warning("MQTT message handling error: %s", exc)

        self._subscriber_client.on_message = on_message

        for topic in self._topics:
            self._subscriber_client.subscribe(topic, qos=self._qos)
            self.log("debug", f"Subscribed to MQTT topic: {topic}")

    async def _handle_message(self, record: Dict[str, Any]) -> None:
        """Buffer the message and forward as a platform event."""
        try:
            self._message_buffer.put_nowait(record)
        except asyncio.QueueFull:
            logger.warning("MQTT message buffer full; dropping oldest message.")
            try:
                self._message_buffer.get_nowait()
                self._message_buffer.put_nowait(record)
            except Exception:
                pass

        # Forward to platform event bus
        await self.emit_event(
            "sensor_telemetry_received",
            {
                "topic": record["topic"],
                "payload": record["payload"],
                "timestamp": record["timestamp"],
            },
        )
        await self.emit_metric("mqtt_messages_received", 1.0)

    async def execute(
        self,
        context: AdapterContext,
        inputs: Dict[str, Any],
    ) -> AdapterResponse:
        """
        Wait for and return the next sensor message on a topic.

        Required inputs
        ---------------
        topic   : str   – MQTT topic to read from (supports + and # wildcards)
        timeout : float – seconds to wait for a message (default 10)

        Returns
        -------
        {
            "topic": "sensors/device-001/temperature",
            "value": 23.4,          # parsed if JSON payload
            "raw_payload": "...",   # original string
            "timestamp": "...",
        }
        """
        topic_filter = inputs.get("topic")
        wait_timeout = float(inputs.get("timeout", 10))

        try:
            # Wait for a matching message from the buffer
            deadline = asyncio.get_event_loop().time() + wait_timeout

            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    return AdapterResponse.fail(f"No message received on '{topic_filter}' within {wait_timeout}s.")

                try:
                    record = await asyncio.wait_for(self._message_buffer.get(), timeout=remaining)
                except asyncio.TimeoutError:
                    return AdapterResponse.fail(f"No message received on '{topic_filter}' within {wait_timeout}s.")

                # Topic filter matching
                if topic_filter and not self._topic_matches(record["topic"], topic_filter):
                    # Put it back (simple re-queue)
                    await self._message_buffer.put(record)
                    await asyncio.sleep(0.1)
                    continue

                # Parse JSON payload if possible
                value = None
                raw = record["payload"]
                try:
                    parsed = json.loads(raw)
                    value = parsed if not isinstance(parsed, dict) else parsed
                except json.JSONDecodeError:
                    value = raw  # raw string value

                return AdapterResponse.ok(
                    {
                        "topic": record["topic"],
                        "value": value,
                        "raw_payload": raw,
                        "timestamp": record["timestamp"],
                    }
                )

        except Exception as exc:
            self.log("error", f"MQTT execute failed: {exc}")
            return AdapterResponse.fail(str(exc))

    @staticmethod
    def _topic_matches(topic: str, pattern: str) -> bool:
        """Simple MQTT topic wildcard matching (+ single, # multi)."""
        if pattern == "#":
            return True
        topic_parts = topic.split("/")
        pattern_parts = pattern.split("/")
        if "#" in pattern_parts:
            idx = pattern_parts.index("#")
            return topic_parts[:idx] == pattern_parts[:idx]
        if len(topic_parts) != len(pattern_parts):
            return False
        return all(p == "+" or p == t for t, p in zip(topic_parts, pattern_parts))

    async def health_check(self) -> bool:
        """Check MQTT broker connectivity."""
        if self._subscriber_client is None:
            return False
        try:
            return self._subscriber_client.is_connected()
        except Exception:
            return False

    async def shutdown(self) -> None:
        """Unsubscribe and shut down the connection pool."""
        if self._subscriber_client is not None:
            for topic in self._topics:
                try:
                    self._subscriber_client.unsubscribe(topic)
                except Exception:
                    pass
            await self._pool.release(self._subscriber_client)

        if self._pool is not None:
            await self._pool.shutdown()

        self.log("info", "MQTTSensorAdapter shut down.")
