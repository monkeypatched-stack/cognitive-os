"""Worker Pool — manages worker threads for process execution.

Supports:
- Work stealing
- Thread reuse
- Resource limits
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from src.monkey_brain.runtime.process import Process


@dataclass
class Worker:
    """A worker thread."""

    worker_id: str = field(default_factory=lambda: f"worker-{uuid4().hex[:8]}")
    current_process: Process | None = None
    busy: bool = False
    tasks_completed: int = 0
    total_cpu_time_ms: float = 0.0

    @property
    def utilization(self) -> float:
        if self.total_cpu_time_ms == 0:
            return 0.0
        return 1.0 if self.busy else 0.0


class WorkerPool:
    """Manages worker threads for process execution.

    Supports:
    - Work stealing between idle workers
    - Thread reuse
    - Resource limits
    """

    def __init__(self, size: int = 4):
        self._size = size
        self._workers: dict[str, Worker] = {}
        self._idle_workers: list[str] = []
        self._lemon = None

        # Initialize workers
        for _ in range(size):
            worker = Worker()
            self._workers[worker.worker_id] = worker
            self._idle_workers.append(worker.worker_id)

    def set_lemon(self, lemon) -> None:
        self._lemon = lemon

    def acquire_worker(self) -> Worker | None:
        """Acquire an idle worker."""
        if self._idle_workers:
            worker_id = self._idle_workers.pop(0)
            worker = self._workers[worker_id]
            worker.busy = True
            if self._lemon:
                self._lemon.counter("wolverine.workers_acquired")
            return worker
        return None

    def release_worker(self, worker: Worker) -> None:
        """Release a worker back to the idle pool."""
        worker.busy = False
        worker.current_process = None
        self._idle_workers.append(worker.worker_id)
        if self._lemon:
            self._lemon.counter("wolverine.workers_released")

    def steal_work(self) -> tuple[Worker, Process] | None:
        """Steal work from a busy worker."""
        # Find a busy worker with work
        for worker in self._workers.values():
            if worker.busy and worker.current_process:
                # Steal the process
                process = worker.current_process
                worker.current_process = None
                worker.busy = False
                self._idle_workers.append(worker.worker_id)

                if self._lemon:
                    self._lemon.counter("wolverine.work_stolen")

                return worker, process

        return None

    def get_worker(self, worker_id: str) -> Worker | None:
        return self._workers.get(worker_id)

    def get_all_workers(self) -> list[Worker]:
        return list(self._workers.values())

    def summary(self) -> dict:
        busy = sum(1 for w in self._workers.values() if w.busy)
        return {
            "size": self._size,
            "idle": len(self._idle_workers),
            "busy": busy,
            "utilization": round(busy / self._size, 2) if self._size else 0,
        }
