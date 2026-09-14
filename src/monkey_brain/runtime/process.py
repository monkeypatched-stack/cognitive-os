"""Process — lightweight execution unit.

Every execution is a Process.
Processes communicate through messages.
Processes never directly manipulate another process's memory.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


class ProcessState(str, Enum):
    CREATED = "created"
    QUEUED = "queued"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    WAITING = "waiting"
    RESUMED = "resumed"
    COMPLETED = "completed"
    OBSERVED = "observed"
    DESTROYED = "destroyed"
    FAILED = "failed"


class ProcessPriority(int, Enum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


@dataclass
class Message:
    """A message between processes."""

    message_id: str = field(default_factory=lambda: f"msg-{uuid4().hex[:8]}")
    sender: str = ""
    receiver: str = ""
    message_type: str = "request"  # request | response | event | broadcast | notification
    payload: Any = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class ProcessMetrics:
    """Metrics for a process."""

    cpu_time_ms: float = 0.0
    memory_bytes: int = 0
    context_switches: int = 0
    messages_sent: int = 0
    messages_received: int = 0
    retries: int = 0


@dataclass
class Process:
    """A lightweight execution process.

    Every execution is a Process.
    Processes communicate through messages.
    """

    process_id: str = field(default_factory=lambda: f"proc-{uuid4().hex[:8]}")
    name: str = ""
    parent_id: str | None = None

    # State
    state: ProcessState = ProcessState.CREATED
    priority: ProcessPriority = ProcessPriority.NORMAL

    # Execution
    coroutine: Any = None
    args: tuple = ()
    kwargs: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: str | None = None

    # Context
    execution_context: dict[str, Any] = field(default_factory=dict)

    # Mailbox
    mailbox: list[Message] = field(default_factory=list)
    mailbox_max_size: int = 100

    # Metrics
    metrics: ProcessMetrics = field(default_factory=ProcessMetrics)

    # Timing
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None

    # Tracing
    trace_id: str | None = None

    # Metadata
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def elapsed_ms(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.completed_at or time.time()
        return (end - self.started_at) * 1000

    @property
    def is_terminal(self) -> bool:
        return self.state in (
            ProcessState.COMPLETED,
            ProcessState.FAILED,
            ProcessState.DESTROYED,
        )

    def send(self, message: Message) -> bool:
        """Send a message to this process's mailbox."""
        if len(self.mailbox) >= self.mailbox_max_size:
            return False
        self.mailbox.append(message)
        self.metrics.messages_received += 1
        return True

    def receive(self) -> Message | None:
        """Receive a message from the mailbox."""
        if self.mailbox:
            return self.mailbox.pop(0)
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "process_id": self.process_id,
            "name": self.name,
            "parent_id": self.parent_id,
            "state": self.state.value,
            "priority": self.priority.value,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "cpu_time_ms": round(self.metrics.cpu_time_ms, 2),
            "messages_sent": self.metrics.messages_sent,
            "messages_received": self.metrics.messages_received,
            "retries": self.metrics.retries,
            "error": self.error,
        }
