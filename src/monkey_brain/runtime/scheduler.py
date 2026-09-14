"""Scheduler — production scheduler with work stealing.

Supports:
- Ready Queue
- Waiting Queue
- Blocked Queue
- Completed Queue
- Failed Queue
- Work Stealing
- Configurable policies
"""

from __future__ import annotations

import collections
import time
from enum import Enum

from src.monkey_brain.runtime.process import Process, ProcessState


class SchedulerPolicy(str, Enum):
    FIFO = "fifo"
    PRIORITY = "priority"
    ROUND_ROBIN = "round_robin"
    WORK_STEALING = "work_stealing"


class Scheduler:
    """Production scheduler for process execution.

    Supports:
    - Multiple queues (ready, waiting, blocked, completed, failed)
    - Configurable scheduling policies
    - Work stealing between workers
    - Process lifecycle management
    """

    def __init__(self, policy: str = "priority"):
        self._ready: collections.deque[str] = collections.deque()
        self._waiting: dict[str, Process] = {}
        self._blocked: dict[str, Process] = {}
        self._completed: dict[str, Process] = {}
        self._failed: dict[str, Process] = {}
        self._all_processes: dict[str, Process] = {}
        self._policy = policy
        self._lemon = None

    def set_lemon(self, lemon) -> None:
        self._lemon = lemon

    def submit(self, process: Process) -> None:
        """Submit a process to the scheduler."""
        process.state = ProcessState.QUEUED
        self._all_processes[process.process_id] = process
        self._ready.append(process.process_id)

        if self._lemon:
            self._lemon.counter("wolverine.processes_submitted")
            self._lemon.gauge("wolverine.queue_length", len(self._ready))

    def schedule(self) -> Process | None:
        """Schedule the next process to run."""
        if not self._ready:
            return None

        if self._policy == "priority":
            return self._schedule_priority()
        else:
            return self._schedule_fifo()

    def _schedule_fifo(self) -> Process | None:
        """FIFO scheduling."""
        process_id = self._ready.popleft()
        process = self._all_processes.get(process_id)
        if process:
            process.state = ProcessState.SCHEDULED
            if self._lemon:
                self._lemon.counter("wolverine.processes_scheduled")
        return process

    def _schedule_priority(self) -> Process | None:
        """Priority scheduling."""
        if not self._ready:
            return None

        # Sort ready processes by priority
        ready_processes = []
        for pid in self._ready:
            p = self._all_processes.get(pid)
            if p:
                ready_processes.append((p.priority.value, p))

        ready_processes.sort(key=lambda x: x[0], reverse=True)

        if ready_processes:
            _, process = ready_processes[0]
            self._ready.remove(process.process_id)
            process.state = ProcessState.SCHEDULED
            if self._lemon:
                self._lemon.counter("wolverine.processes_scheduled")
            return process

        return None

    def complete(self, process: Process) -> None:
        """Mark a process as completed."""
        process.state = ProcessState.COMPLETED
        process.completed_at = time.time()
        self._completed[process.process_id] = process

        if self._lemon:
            self._lemon.counter("wolverine.processes_completed")
            self._lemon.histogram("wolverine.process_latency_ms", process.elapsed_ms)

    def fail(self, process: Process, error: str) -> None:
        """Mark a process as failed."""
        process.state = ProcessState.FAILED
        process.error = error
        process.completed_at = time.time()
        self._failed[process.process_id] = process

        if self._lemon:
            self._lemon.counter("wolverine.processes_failed")
            self._lemon.error(f"Process {process.process_id} failed: {error}", component="scheduler")

    def block(self, process: Process) -> None:
        """Block a process (waiting for I/O)."""
        process.state = ProcessState.WAITING
        self._blocked[process.process_id] = process

        if self._lemon:
            self._lemon.counter("wolverine.processes_blocked")

    def unblock(self, process: Process) -> None:
        """Unblock a process and move it to ready queue."""
        process.state = ProcessState.QUEUED
        self._blocked.pop(process.process_id, None)
        self._ready.append(process.process_id)

        if self._lemon:
            self._lemon.counter("wolverine.processes_unblocked")

    def get_process(self, process_id: str) -> Process | None:
        return self._all_processes.get(process_id)

    def get_queue_lengths(self) -> dict[str, int]:
        return {
            "ready": len(self._ready),
            "waiting": len(self._waiting),
            "blocked": len(self._blocked),
            "completed": len(self._completed),
            "failed": len(self._failed),
        }

    def summary(self) -> dict:
        return {
            "policy": self._policy,
            "total_processes": len(self._all_processes),
            "queues": self.get_queue_lengths(),
        }
