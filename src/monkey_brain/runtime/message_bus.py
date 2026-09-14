"""Message Bus — inter-process communication.

Supports:
- Request/Response
- Event broadcasting
- Notification
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Callable

from src.monkey_brain.runtime.process import Message

logger = logging.getLogger(__name__)


class MessageBus:
    """Inter-process message bus.

    Supports:
    - Request/Response
    - Event broadcasting
    - Notification
    - Message history
    """

    def __init__(self):
        self._subscribers: dict[str, list[Callable]] = defaultdict(list)
        self._history: list[Message] = []
        self._lemon = None
        # Strong references to in-flight subscriber tasks. asyncio only keeps a WEAK reference
        # to a task, so a task with no strong ref can be garbage-collected mid-execution —
        # silently dropping the subscriber's work.
        self._tasks: set[asyncio.Task] = set()

    def set_lemon(self, lemon) -> None:
        self._lemon = lemon

    def _on_task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            # Async subscriber failures used to vanish entirely: the try/except below only
            # wrapped coroutine CREATION, never the coroutine's own execution.
            logger.error("Async subscriber failed: %s", exc, exc_info=exc)

    def _schedule(self, coro, callback: Callable) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # publish() is sync; called with no running loop, create_task() raised RuntimeError
            # which was swallowed at DEBUG — the coroutine was never scheduled and the message
            # was silently lost. Close the coroutine (avoids "never awaited") and say so.
            coro.close()
            logger.error(
                "MessageBus.publish() called with no running event loop; async subscriber %r was DROPPED",
                getattr(callback, "__name__", callback),
            )
            return
        task = loop.create_task(coro)
        self._tasks.add(task)  # strong ref → cannot be GC'd mid-flight
        task.add_done_callback(self._on_task_done)

    def publish(self, message: Message) -> None:
        """Publish a message to subscribers."""
        self._history.append(message)

        if len(self._history) > 10000:
            self._history = self._history[-5000:]

        # Notify subscribers
        for callback in self._subscribers.get(message.message_type, []):
            try:
                result = callback(message)
            except Exception:
                logger.exception(
                    "Subscriber callback failed: %r",
                    getattr(callback, "__name__", callback),
                )
                continue
            if asyncio.iscoroutine(result):
                self._schedule(result, callback)

        if self._lemon:
            self._lemon.counter("wolverine.messages_published", type=message.message_type)

    def subscribe(self, message_type: str, callback: Callable) -> None:
        """Subscribe to a message type."""
        self._subscribers[message_type].append(callback)

    def unsubscribe(self, message_type: str, callback: Callable) -> bool:
        """Unsubscribe from a message type."""
        if message_type in self._subscribers:
            try:
                self._subscribers[message_type].remove(callback)
                return True
            except ValueError:
                logger.debug("unsubscribe: suppressed exception", exc_info=True)
        return False

    def get_history(self, message_type: str | None = None, limit: int = 100) -> list[Message]:
        """Get message history."""
        if message_type:
            return [m for m in self._history if m.message_type == message_type][-limit:]
        return self._history[-limit:]

    def summary(self) -> dict:
        return {
            "total_messages": len(self._history),
            "subscribers": {k: len(v) for k, v in self._subscribers.items()},
        }
