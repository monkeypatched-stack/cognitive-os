from __future__ import annotations

import asyncio
import socket
from typing import Any
from urllib.parse import urlparse

import httpx

from services.process_definitions.models.process_definition import (
    ProcessDefinitionCanvasBackendCall,
)
from services.process_definitions.node_services.models import (
    NodeExecutionResult,
    config_value,
    listener_registration_response,
    payload_message,
    render_template,
)


async def execute_http_node(
    node_type: str | None,
    backend: ProcessDefinitionCanvasBackendCall | None,
    config: dict,
    payload: dict[str, Any],
) -> NodeExecutionResult:
    if node_type == "http in":
        return NodeExecutionResult(
            True,
            None,
            {
                "status": "registered",
                "method": config_value(config, "method", "POST"),
                "path": config.get("path"),
                "message": "HTTP input nodes are registered as canvas node listeners; callers should post to this node's incoming webhook endpoint.",
            },
        )
    if node_type == "http response":
        status_code = int(config_value(config, "status_code", 200))
        headers = (
            config.get("headers") if isinstance(config.get("headers"), dict) else {}
        )
        body_template = str(config.get("body_template") or "")
        body = render_template(body_template, payload) if body_template else payload
        return NodeExecutionResult(
            True,
            status_code,
            {
                "status": "response",
                "status_code": status_code,
                "headers": headers,
                "body": body,
            },
        )
    if node_type != "http request":
        return NodeExecutionResult(True, None, {"status": "queued"})
    url = (backend.url if backend else None) or config.get("url")
    if not url:
        return NodeExecutionResult(False, None, None, "HTTP request URL is required")
    method = (
        (backend.method if backend else None) or config.get("method") or "GET"
    ).upper()
    headers = dict((backend.headers if backend else None) or {})
    query_params = (
        config.get("query") if isinstance(config.get("query"), dict) else None
    )
    body_template = str(config.get("body_template") or "")
    body = render_template(body_template, payload) if body_template else payload
    timeout_seconds = (
        config.get("timeout_seconds")
        if isinstance(config.get("timeout_seconds"), (int, float))
        else 20.0
    )
    try:
        async with httpx.AsyncClient(timeout=float(timeout_seconds)) as client:
            response = await client.request(
                method,
                url,
                json=None if method == "GET" else body,
                headers=headers,
                params=query_params,
            )
        try:
            response_body = response.json()
        except Exception:
            response_body = response.text
        return NodeExecutionResult(
            response.is_success,
            response.status_code,
            response_body,
            None if response.is_success else response.text,
        )
    except httpx.HTTPError as exc:
        return NodeExecutionResult(False, None, None, str(exc))


async def execute_mqtt_node(
    node_type: str | None, config: dict, payload: dict[str, Any]
) -> NodeExecutionResult:
    if node_type == "mqtt in":
        return NodeExecutionResult(
            True, None, listener_registration_response(node_type, config)
        )
    if node_type != "mqtt out":
        return NodeExecutionResult(True, None, {"status": "queued"})
    try:
        import paho.mqtt.client as mqtt
    except Exception:
        return NodeExecutionResult(
            False, None, None, "paho-mqtt is not installed; cannot publish MQTT message"
        )
    broker = urlparse(str(config_value(config, "broker_url", "mqtt://localhost:1883")))
    host = str(config_value(config, "host", broker.hostname or "localhost"))
    port = int(config_value(config, "port", broker.port or 1883))
    topic = str(config_value(config, "topic", ""))
    if not topic:
        return NodeExecutionResult(False, None, None, "MQTT topic is required")
    client_id = str(config_value(config, "client_id", ""))
    username = config_value(config, "username")
    password = config_value(config, "password")
    qos = int(config_value(config, "qos", 0))
    retain = bool(config.get("retain") or False)
    message = payload_message(payload, config, "payload_template")
    client = mqtt.Client(client_id=client_id or "")
    if username:
        client.username_pw_set(str(username), str(password or ""))
    client.connect(host, port, keepalive=30)
    result = client.publish(topic, message, qos=qos, retain=retain)
    result.wait_for_publish(timeout=float(config_value(config, "timeout_seconds", 20)))
    client.disconnect()
    ok = result.rc == mqtt.MQTT_ERR_SUCCESS
    return NodeExecutionResult(
        ok,
        None,
        {"topic": topic, "published": ok, "rc": result.rc},
        None if ok else f"MQTT publish failed rc={result.rc}",
    )


async def execute_websocket_node(
    node_type: str | None, config: dict, payload: dict[str, Any]
) -> NodeExecutionResult:
    if node_type == "websocket in":
        return NodeExecutionResult(
            True, None, listener_registration_response(node_type, config)
        )
    if node_type != "websocket out":
        return NodeExecutionResult(True, None, {"status": "queued"})
    try:
        import websockets
    except Exception:
        return NodeExecutionResult(
            False,
            None,
            None,
            "websockets is not installed; cannot send WebSocket message",
        )
    url = str(config_value(config, "url", ""))
    if not url:
        return NodeExecutionResult(False, None, None, "WebSocket URL is required")
    headers = config.get("headers") if isinstance(config.get("headers"), dict) else {}
    if config.get("client_key"):
        headers = {**headers, "x-client-key": str(config["client_key"])}
    if config.get("client_secret"):
        headers = {**headers, "x-client-secret": str(config["client_secret"])}
    message = payload_message(payload, config)
    timeout_seconds = float(config_value(config, "timeout_seconds", 20))
    async with asyncio.timeout(timeout_seconds):
        async with websockets.connect(url, extra_headers=headers) as websocket:
            await websocket.send(message)
    return NodeExecutionResult(True, None, {"url": url, "sent": True})


async def execute_tcp_udp_node(
    node_type: str | None, config: dict, payload: dict[str, Any]
) -> NodeExecutionResult:
    if node_type in {"tcp in", "udp in"}:
        return NodeExecutionResult(
            True, None, listener_registration_response(node_type, config)
        )
    host = str(config_value(config, "host", "localhost"))
    port = config_value(config, "port")
    if port is None:
        return NodeExecutionResult(False, None, None, f"{node_type} port is required")
    port = int(port)
    encoding = str(config_value(config, "encoding", "utf-8"))
    message = payload_message(payload, config).encode(encoding)
    timeout_seconds = float(config_value(config, "timeout_seconds", 20))
    if node_type == "tcp out":
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout_seconds
        )
        writer.write(message)
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        return NodeExecutionResult(
            True, None, {"host": host, "port": port, "bytes_sent": len(message)}
        )
    if node_type == "udp out":
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout_seconds)
            sent = sock.sendto(message, (host, port))
        return NodeExecutionResult(
            True, None, {"host": host, "port": port, "bytes_sent": sent}
        )
    return NodeExecutionResult(True, None, {"status": "queued"})
