import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from services.auth.helpers import nats_store
from services.common.config import settings

log = logging.getLogger("uvicorn.error")

_client = None
_loop: asyncio.AbstractEventLoop | None = None


def _topic_list() -> list[str]:
    return [
        topic.strip()
        for topic in str(settings.MQTT_TOPICS or "").split(",")
        if topic.strip()
    ]


def _sensor_id_from_topic(topic: str) -> str:
    parts = [part for part in str(topic or "").split("/") if part and part != "#"]
    return parts[-1] if parts else "unknown"


def _reading_from_message(topic: str, payload: bytes) -> dict[str, Any]:
    text = payload.decode("utf-8", errors="replace").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = {"value": text}
    if not isinstance(data, dict):
        data = {"value": data}

    sensor_id = str(
        data.get("sensor_id")
        or data.get("sensorId")
        or data.get("id")
        or _sensor_id_from_topic(topic)
    )
    reading = {
        **data,
        "sensor_id": sensor_id,
        "topic": topic,
        "source": "mqtt",
        "received_at": datetime.now(timezone.utc).isoformat(),
    }
    if "timestamp" not in reading:
        reading["timestamp"] = reading["received_at"]
    return reading


async def _publish_reading(reading: dict[str, Any]) -> None:
    subject = nats_store.sensor_subject(str(reading.get("sensor_id") or "unknown"))
    await nats_store.publish_event(
        json.dumps(reading, default=str).encode("utf-8"), subject=subject
    )
    log.info(
        "MQTT sensor reading published to NATS subject=%s sensor_id=%s",
        subject,
        reading.get("sensor_id"),
    )


def _on_connect(client, _userdata, _flags, reason_code, _properties=None):
    if reason_code != 0:
        log.warning("MQTT bridge failed to connect reason_code=%s", reason_code)
        return
    for topic in _topic_list():
        client.subscribe(topic)
        log.info("MQTT bridge subscribed topic=%s", topic)


def _on_message(_client, _userdata, message):
    if _loop is None:
        return
    reading = _reading_from_message(message.topic, message.payload)
    asyncio.run_coroutine_threadsafe(_publish_reading(reading), _loop)


async def start_mqtt_bridge() -> None:
    global _client, _loop
    if not settings.MQTT_ENABLED:
        log.info("MQTT bridge disabled")
        return
    if _client is not None:
        return

    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        log.warning("MQTT bridge disabled because paho-mqtt is not installed")
        return

    parsed = urlparse(settings.MQTT_BROKER_URL)
    host = parsed.hostname or "localhost"
    port = parsed.port or 1883
    _loop = asyncio.get_running_loop()
    _client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2, client_id=settings.MQTT_CLIENT_ID
    )
    if settings.MQTT_USERNAME:
        _client.username_pw_set(settings.MQTT_USERNAME, settings.MQTT_PASSWORD or None)
    _client.on_connect = _on_connect
    _client.on_message = _on_message
    _client.connect(host, port, keepalive=60)
    _client.loop_start()
    log.info(
        "MQTT bridge connecting broker=%s topics=%s",
        settings.MQTT_BROKER_URL,
        _topic_list(),
    )


async def stop_mqtt_bridge() -> None:
    global _client, _loop
    if _client is None:
        return
    _client.loop_stop()
    _client.disconnect()
    _client = None
    _loop = None
