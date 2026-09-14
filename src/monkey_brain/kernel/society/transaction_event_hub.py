"""Transaction Event Hub — the WebSocket half of "stream negotiation
progress to both NATS and WebSockets" (Required Transaction Execution
Logic, step 9).

NATS publishing is handled directly by TransactionCoordinator (it already
owns a live nats_client via PlanetaryRuntime). WebSocket fan-out needs a
process-wide registry of connected clients keyed by transaction_id, since
the WS route (api/routes/ws.py) and the coordinator (deep in the kernel,
no Request/WebSocket object) are otherwise unreachable from each other.
A module-level singleton — the same pattern this codebase already uses
for get_backend()/get_executor()/get_somatic_compiler() — avoids plumbing
this through app.state and kernel boot for what is, structurally, just a
topic-based pub/sub registry.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("agentos.transaction_event_hub")


class TransactionEventHub:
    """In-process pub/sub: one topic (transaction_id) -> N subscribed
    WebSocket connections. A dead/erroring connection is dropped rather
    than allowed to break delivery to the rest of the topic's
    subscribers — mirrors this codebase's other non-fatal-fan-out
    patterns (e.g. PlanetaryRuntime's Redis publishes)."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[Any]] = {}

    def subscribe(self, transaction_id: str, websocket: Any) -> None:
        self._subscribers.setdefault(transaction_id, set()).add(websocket)

    def unsubscribe(self, transaction_id: str, websocket: Any) -> None:
        sockets = self._subscribers.get(transaction_id)
        if sockets is None:
            return
        sockets.discard(websocket)
        if not sockets:
            self._subscribers.pop(transaction_id, None)

    async def publish(self, transaction_id: str, event: dict[str, Any]) -> None:
        sockets = self._subscribers.get(transaction_id)
        if not sockets:
            return
        dead: list[Any] = []
        for websocket in sockets:
            try:
                await websocket.send_json(event)
            except Exception:
                logger.debug(
                    "TransactionEventHub.publish: dropping dead subscriber for %r",
                    transaction_id,
                    exc_info=True,
                )
                dead.append(websocket)
        for websocket in dead:
            sockets.discard(websocket)


_hub: TransactionEventHub | None = None


def get_transaction_event_hub() -> TransactionEventHub:
    global _hub
    if _hub is None:
        _hub = TransactionEventHub()
    return _hub
