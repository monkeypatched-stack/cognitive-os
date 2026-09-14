import asyncio
import logging

from fastapi import WebSocket
from starlette.websockets import WebSocketState

log = logging.getLogger(__name__)
_connections: set[WebSocket] = set()
_lock = asyncio.Lock()


async def add_connection(websocket: WebSocket) -> None:
    async with _lock:
        _connections.add(websocket)


async def remove_connection(websocket: WebSocket) -> None:
    async with _lock:
        _connections.discard(websocket)


async def broadcast_event(event: dict) -> None:
    async with _lock:
        connections = list(_connections)

    stale_connections: list[WebSocket] = []
    for websocket in connections:
        if websocket.application_state != WebSocketState.CONNECTED:
            stale_connections.append(websocket)
            continue
        try:
            await websocket.send_json(event)
        except Exception:
            stale_connections.append(websocket)
            log.exception("Failed to send NATS event to websocket client")

    if stale_connections:
        async with _lock:
            for websocket in stale_connections:
                _connections.discard(websocket)
