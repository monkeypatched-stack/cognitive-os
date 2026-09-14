"""Living World Explorer — status WebSocket.

WS /ws/status — application-shell scope only (Prompt 1): confirms a
real WebSocket connection exists end to end and pushes a periodic
heartbeat. No world/actor/event data flows over this yet — that's a
later prompt once there's something real to visualize.

No auth required, same precedent as /live /health /ready (api/main.py):
this channel carries no sensitive data, only a connection heartbeat, so
gating it behind the same permission system as the real data routes
would only break the shell's connection indicator for no security
benefit.
"""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger("agentos.ws")

router = APIRouter()

HEARTBEAT_INTERVAL_SECONDS = 15


@router.websocket("/ws/status")
async def status_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        await websocket.send_json({"type": "connected", "timestamp": time.time()})
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            await websocket.send_json({"type": "heartbeat", "timestamp": time.time()})
    except WebSocketDisconnect:
        logger.debug("status_socket: client disconnected")


@router.websocket("/ws/transactions/{transaction_id}")
async def transaction_socket(websocket: WebSocket, transaction_id: str) -> None:
    """Streams Required Transaction Execution Logic's negotiation events
    (transaction_started, negotiation_trace, step_completed,
    transaction_completed — see kernel/society/transaction.py) for one
    transaction_id in real time. Real data flowing here, unlike
    /ws/status above — subscribes to the same process-wide
    TransactionEventHub TransactionCoordinator publishes to, so this
    works regardless of which request handled the /transactions POST
    that started the transaction."""
    from src.monkey_brain.kernel.society.transaction_event_hub import (
        get_transaction_event_hub,
    )

    hub = get_transaction_event_hub()
    await websocket.accept()
    hub.subscribe(transaction_id, websocket)
    try:
        await websocket.send_json(
            {
                "type": "connected",
                "transaction_id": transaction_id,
                "timestamp": time.time(),
            }
        )
        while True:
            # No client->server messages expected; just keep the socket
            # open until the client disconnects. receive_text() blocks
            # without busy-polling and raises WebSocketDisconnect on close.
            await websocket.receive_text()
    except WebSocketDisconnect:
        logger.debug(
            "transaction_socket: client disconnected (transaction_id=%r)",
            transaction_id,
        )
    finally:
        hub.unsubscribe(transaction_id, websocket)
