"""Engine — the Wolverine Process Execution Engine.

The engine orchestrates all process execution.
"""

from __future__ import annotations

import logging

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Coroutine

from src.monkey_brain.runtime.process import Process, ProcessState, ProcessPriority
from src.monkey_brain.runtime.scheduler import Scheduler
from src.monkey_brain.runtime.worker_pool import WorkerPool
from src.monkey_brain.runtime.message_bus import MessageBus
from src.monkey_brain.runtime.supervisor import Supervisor
from src.monkey_brain.runtime.resource_manager import ResourceManager

logger = logging.getLogger("monkey_brain.runtime.engine")


@dataclass
class EngineConfig:
    """Configuration for the execution engine."""

    worker_pool_size: int = 4
    scheduler_policy: str = "priority"
    max_processes: int = 1000
    enable_work_stealing: bool = True
    enable_supervision: bool = True


class Engine:
    """Wolverine Process Execution Engine.

    Orchestrates all process execution.
    """

    def __init__(self, config: EngineConfig | None = None):
        self._config = config or EngineConfig()
        self._scheduler = Scheduler(policy=self._config.scheduler_policy)
        self._worker_pool = WorkerPool(size=self._config.worker_pool_size)
        self._message_bus = MessageBus()
        self._supervisor = Supervisor()
        self._resource_manager = ResourceManager()
        self._processes: dict[str, Process] = {}
        self._running = False
        self._lemon = None

    def set_lemon(self, lemon) -> None:
        self._lemon = lemon
        self._scheduler.set_lemon(lemon)
        self._worker_pool.set_lemon(lemon)
        self._message_bus.set_lemon(lemon)
        self._supervisor.set_lemon(lemon)

    async def submit(
        self,
        name: str,
        coroutine: Coroutine,
        args: tuple = (),
        kwargs: dict[str, Any] | None = None,
        priority: ProcessPriority = ProcessPriority.NORMAL,
        parent_id: str | None = None,
    ) -> Process:
        """Submit a new process for execution."""
        process = Process(
            name=name,
            coroutine=coroutine,
            args=args,
            kwargs=kwargs or {},
            priority=priority,
            parent_id=parent_id,
        )

        self._processes[process.process_id] = process
        self._scheduler.submit(process)

        if self._config.enable_supervision:
            self._supervisor.add_child(process)

        if self._lemon:
            self._lemon.counter("wolverine.processes_created")

        return process

    async def execute_process(self, process: Process) -> Any:
        """Execute a single process."""
        process.state = ProcessState.RUNNING
        process.started_at = time.time()

        worker = self._worker_pool.acquire_worker()
        if worker:
            worker.current_process = process

        try:
            if asyncio.iscoroutinefunction(process.coroutine):
                result = await process.coroutine(*process.args, **process.kwargs)
            else:
                result = process.coroutine(*process.args, **process.kwargs)

            process.result = result
            process.state = ProcessState.COMPLETED
            process.completed_at = time.time()
            process.metrics.cpu_time_ms = process.elapsed_ms

            self._scheduler.complete(process)

            if worker:
                self._worker_pool.release_worker(worker)

            if self._lemon:
                self._lemon.counter("wolverine.processes_completed")

            return result

        except Exception as e:
            process.state = ProcessState.FAILED
            process.error = str(e)
            process.completed_at = time.time()

            self._scheduler.fail(process, str(e))

            if worker:
                self._worker_pool.release_worker(worker)

            # Handle failure with supervisor
            if self._config.enable_supervision:
                should_restart = await self._supervisor.handle_failure(process, str(e))
                if should_restart:
                    self._scheduler.submit(process)

            if self._lemon:
                self._lemon.counter("wolverine.processes_failed")

            raise

    async def execute_workload(
        self,
        steps: list[dict[str, Any]],
        state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a workload of steps as processes."""
        current_state = dict(state or {})
        results = []

        for step in steps:
            step_name = step.get("name", "step")
            capability = step.get("capability")

            if capability:
                process = await self.submit(
                    name=step_name,
                    coroutine=capability.execute,
                    args=(current_state,),
                )

                result = await self.execute_process(process)
                current_state.update(result)
                results.append({"step": step_name, "result": result})

        return {"state": current_state, "results": results}

    def get_process(self, process_id: str) -> Process | None:
        return self._processes.get(process_id)

    def get_all_processes(self) -> list[Process]:
        return list(self._processes.values())

    def summary(self) -> dict:
        return {
            "config": {
                "worker_pool_size": self._config.worker_pool_size,
                "scheduler_policy": self._config.scheduler_policy,
                "max_processes": self._config.max_processes,
            },
            "scheduler": self._scheduler.summary(),
            "worker_pool": self._worker_pool.summary(),
            "message_bus": self._message_bus.summary(),
            "supervisor": self._supervisor.summary(),
            "resource_manager": self._resource_manager.summary(),
            "total_processes": len(self._processes),
        }
