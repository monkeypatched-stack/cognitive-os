"""Supervisor — process supervision trees.

Responsibilities:
- Restart failed processes
- Backoff strategies
- Escalation
- Health monitoring
- Shutdown
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from uuid import uuid4

from src.monkey_brain.runtime.process import Process, ProcessState


class RestartStrategy(str, Enum):
    ONE_FOR_ONE = "one_for_one"
    ONE_FOR_ALL = "one_for_all"
    REST_FOR_ONE = "rest_for_one"


class SupervisorState(str, Enum):
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"


@dataclass
class SupervisorConfig:
    """Configuration for a supervisor."""

    max_restarts: int = 3
    restart_period: float = 60.0  # seconds
    backoff_factor: float = 2.0
    max_backoff: float = 30.0
    restart_strategy: RestartStrategy = RestartStrategy.ONE_FOR_ONE


class Supervisor:
    """Supervises child processes.

    Responsibilities:
    - Restart failed processes
    - Backoff strategies
    - Escalation
    - Health monitoring
    """

    def __init__(self, supervisor_id: str | None = None, config: SupervisorConfig | None = None):
        self.supervisor_id = supervisor_id or f"sup-{uuid4().hex[:8]}"
        self._config = config or SupervisorConfig()
        self._children: dict[str, Process] = {}
        self._restart_counts: dict[str, int] = {}
        self._restart_times: dict[str, list[float]] = {}
        self._state = SupervisorState.RUNNING
        self._lemon = None

    def set_lemon(self, lemon) -> None:
        self._lemon = lemon

    def add_child(self, process: Process) -> None:
        """Add a child process to supervision."""
        self._children[process.process_id] = process
        self._restart_counts[process.process_id] = 0
        self._restart_times[process.process_id] = []

    def remove_child(self, process_id: str) -> None:
        """Remove a child from supervision."""
        self._children.pop(process_id, None)
        self._restart_counts.pop(process_id, None)
        self._restart_times.pop(process_id, None)

    async def handle_failure(self, process: Process, error: str) -> bool:
        """Handle a child process failure.

        Returns True if restart was attempted, False if escalation needed.
        """
        process_id = process.process_id
        restarts = self._restart_counts.get(process_id, 0)

        # Check restart limit
        if restarts >= self._config.max_restarts:
            if self._lemon:
                self._lemon.counter("wolverine.supervisor_escalation")
            return False

        # Check restart period
        now = time.time()
        restart_times = self._restart_times.get(process_id, [])
        recent_restarts = [t for t in restart_times if now - t < self._config.restart_period]

        if len(recent_restarts) >= self._config.max_restarts:
            if self._lemon:
                self._lemon.counter("wolverine.supervisor_escalation")
            return False

        # Calculate backoff
        backoff = min(
            self._config.backoff_factor**restarts,
            self._config.max_backoff,
        )

        # Record restart
        self._restart_counts[process_id] = restarts + 1
        self._restart_times[process_id].append(now)

        # Wait out the backoff before the process becomes eligible for
        # restart again — without this, a crash-looping process was
        # reset to CREATED (and resubmitted by the caller) immediately,
        # making backoff_factor/max_backoff pure dead configuration.
        if backoff > 0:
            await asyncio.sleep(backoff)

        # Reset process
        process.state = ProcessState.CREATED
        process.error = None
        process.result = None
        process.metrics.retries += 1

        if self._lemon:
            self._lemon.counter("wolverine.processes_restarted")

        return True

    def get_children(self) -> list[Process]:
        return list(self._children.values())

    def summary(self) -> dict:
        return {
            "supervisor_id": self.supervisor_id,
            "state": self._state.value,
            "children": len(self._children),
            "restarts": dict(self._restart_counts),
        }
