"""Seed Data — deterministic domain data generation.

All data generated from existing domain models.
No direct database manipulation.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from typing import Any


@dataclass
class SeedConfig:
    """Configuration for data seeding."""

    seed: int = 42
    plant_count: int = 1
    lines_per_plant: int = 3
    stages_per_line: int = 4
    workstations_per_stage: int = 5
    machines_per_workstation: int = 2
    workers_per_plant: int = 20
    batches_per_line: int = 10
    work_orders_per_batch: int = 3
    sops_per_plant: int = 15

    def deterministic_hash(self) -> str:
        config_str = f"{self.seed}:{self.plant_count}:{self.lines_per_plant}"
        return hashlib.md5(config_str.encode()).hexdigest()[:8]


PLANT_NAMES = [
    "Tablet Manufacturing Plant",
    "Capsule Production Facility",
    "Injection Molding Center",
    "Packaging & Distribution Hub",
    "API Synthesis Plant",
    "Biotech Manufacturing Suite",
]

LINE_NAMES = [
    "Tablet Line A",
    "Tablet Line B",
    "Capsule Line A",
    "Capsule Line B",
    "Packaging Line 1",
    "Packaging Line 2",
]
STAGE_NAMES = [
    "Dispensing",
    "Granulation",
    "Compression",
    "Coating",
    "Packaging",
    "Inspection",
    "Blending",
    "Milling",
]
WORKER_ROLES = [
    "Operator",
    "Technician",
    "Engineer",
    "Supervisor",
    "QA Inspector",
    "Maintenance Tech",
]
MACHINE_TYPES = [
    "Tablet Press",
    "Coating Pan",
    "Blender",
    "Granulator",
    "Packaging Machine",
    "Inspection Camera",
]
BATCH_STATUSES = ["planned", "in_progress", "completed", "on_hold", "failed"]
WORK_ORDER_STATUSES = ["open", "assigned", "in_progress", "completed", "on_hold"]


class SeedGenerator:
    """Deterministic seed data generator.

    Generates realistic domain data from configuration.
    """

    def __init__(self, config: SeedConfig | None = None):
        self._config = config or SeedConfig()
        self._rng = random.Random(self._config.seed)

    @property
    def config(self) -> SeedConfig:
        return self._config

    def generate_plants(self) -> list[dict[str, Any]]:
        plants = []
        for i in range(self._config.plant_count):
            plants.append(
                {
                    "id": f"PLT-{i + 1:03d}",
                    "name": PLANT_NAMES[i % len(PLANT_NAMES)],
                    "status": "active",
                    "code": f"PLT{i + 1:03d}",
                }
            )
        return plants

    def generate_lines(self, plant_id: str) -> list[dict[str, Any]]:
        lines = []
        for i in range(self._config.lines_per_plant):
            lines.append(
                {
                    "id": f"{plant_id}-LINE-{i + 1:03d}",
                    "plant_id": plant_id,
                    "name": LINE_NAMES[i % len(LINE_NAMES)],
                    "status": "active",
                }
            )
        return lines

    def generate_stages(self, line_id: str) -> list[dict[str, Any]]:
        stages = []
        for i in range(self._config.stages_per_line):
            stages.append(
                {
                    "id": f"{line_id}-STG-{i + 1:03d}",
                    "line_id": line_id,
                    "name": STAGE_NAMES[i % len(STAGE_NAMES)],
                    "order": i + 1,
                    "status": "active",
                }
            )
        return stages

    def generate_workstations(self, stage_id: str) -> list[dict[str, Any]]:
        workstations = []
        for i in range(self._config.workstations_per_stage):
            workstations.append(
                {
                    "id": f"{stage_id}-WS-{i + 1:03d}",
                    "stage_id": stage_id,
                    "name": f"Workstation {i + 1}",
                    "status": "active",
                }
            )
        return workstations

    def generate_machines(self, workstation_id: str) -> list[dict[str, Any]]:
        machines = []
        for i in range(self._config.machines_per_workstation):
            machines.append(
                {
                    "id": f"{workstation_id}-MCH-{i + 1:03d}",
                    "workstation_id": workstation_id,
                    "name": f"{MACHINE_TYPES[i % len(MACHINE_TYPES)]} {i + 1}",
                    "type": MACHINE_TYPES[i % len(MACHINE_TYPES)],
                    "status": "operational",
                }
            )
        return machines

    def generate_workers(self, plant_id: str) -> list[dict[str, Any]]:
        workers = []
        for i in range(self._config.workers_per_plant):
            workers.append(
                {
                    "id": f"{plant_id}-WRK-{i + 1:03d}",
                    "plant_id": plant_id,
                    "name": f"Worker {i + 1}",
                    "role": WORKER_ROLES[i % len(WORKER_ROLES)],
                    "status": "active",
                }
            )
        return workers

    def generate_batches(self, line_id: str) -> list[dict[str, Any]]:
        batches = []
        for i in range(self._config.batches_per_line):
            batches.append(
                {
                    "id": f"{line_id}-BAT-{i + 1:03d}",
                    "line_id": line_id,
                    "batch_number": f"BATCH-{self._rng.randint(10000, 99999)}",
                    "status": self._rng.choice(BATCH_STATUSES),
                    "product": f"Product {self._rng.choice(['A', 'B', 'C'])}",
                }
            )
        return batches

    def generate_work_orders(self, batch_id: str) -> list[dict[str, Any]]:
        work_orders = []
        for i in range(self._config.work_orders_per_batch):
            work_orders.append(
                {
                    "id": f"{batch_id}-WO-{i + 1:03d}",
                    "batch_id": batch_id,
                    "title": f"Work Order {i + 1}",
                    "status": self._rng.choice(WORK_ORDER_STATUSES),
                    "type": self._rng.choice(["production", "maintenance", "quality"]),
                }
            )
        return work_orders

    def generate_full_hierarchy(self) -> dict[str, Any]:
        """Generate complete entity hierarchy."""
        plants = self.generate_plants()
        all_lines = []
        all_stages = []
        all_workstations = []
        all_machines = []
        all_workers = []
        all_batches = []
        all_work_orders = []

        for plant in plants:
            lines = self.generate_lines(plant["id"])
            all_lines.extend(lines)
            all_workers.extend(self.generate_workers(plant["id"]))

            for line in lines:
                stages = self.generate_stages(line["id"])
                all_stages.extend(stages)
                batches = self.generate_batches(line["id"])
                all_batches.extend(batches)

                for stage in stages:
                    workstations = self.generate_workstations(stage["id"])
                    all_workstations.extend(workstations)

                    for ws in workstations:
                        all_machines.extend(self.generate_machines(ws["id"]))

                for batch in batches:
                    all_work_orders.extend(self.generate_work_orders(batch["id"]))

        return {
            "plants": plants,
            "lines": all_lines,
            "stages": all_stages,
            "workstations": all_workstations,
            "machines": all_machines,
            "workers": all_workers,
            "batches": all_batches,
            "work_orders": all_work_orders,
        }

    def summary(self) -> dict:
        hierarchy = self.generate_full_hierarchy()
        return {
            "config_hash": self._config.deterministic_hash(),
            "entities": {k: len(v) for k, v in hierarchy.items()},
            "total": sum(len(v) for v in hierarchy.values()),
        }
