"""Scenario Builder — generates complete test scenarios.

Supports:
- Small Factory
- Medium Factory
- Enterprise Factory
- Failure Scenarios
- Maintenance Scenarios
- Quality Incidents
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from plasticity.seed.seed_data import SeedConfig, SeedGenerator


@dataclass
class Scenario:
    """A complete test scenario."""

    scenario_id: str = field(default_factory=lambda: f"scenario-{uuid4().hex[:8]}")
    name: str = ""
    description: str = ""
    config: SeedConfig = field(default_factory=SeedConfig)
    data: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class ScenarioBuilder:
    """Builds complete test scenarios.

    Generates deterministic datasets for:
    - Benchmarking
    - Development
    - Testing
    - Demonstrations
    """

    def __init__(self):
        self._scenarios: dict[str, Scenario] = {}

    def small_factory(self) -> Scenario:
        config = SeedConfig(
            plant_count=1,
            lines_per_plant=2,
            stages_per_line=3,
            workstations_per_stage=3,
            machines_per_workstation=1,
            workers_per_plant=10,
            batches_per_line=5,
        )
        return self._build_scenario("small_factory", "Small Factory", config)

    def medium_factory(self) -> Scenario:
        config = SeedConfig(
            plant_count=1,
            lines_per_plant=4,
            stages_per_line=4,
            workstations_per_stage=5,
            machines_per_workstation=2,
            workers_per_plant=30,
            batches_per_line=15,
        )
        return self._build_scenario("medium_factory", "Medium Factory", config)

    def enterprise_factory(self) -> Scenario:
        config = SeedConfig(
            plant_count=3,
            lines_per_plant=6,
            stages_per_line=5,
            workstations_per_stage=8,
            machines_per_workstation=3,
            workers_per_plant=100,
            batches_per_line=50,
        )
        return self._build_scenario("enterprise_factory", "Enterprise Factory", config)

    def failure_scenario(self) -> Scenario:
        scenario = self.medium_factory()
        scenario.name = "Failure Scenario"
        scenario.description = "Factory with equipment failures"
        scenario.events = [
            {
                "type": "equipment_failure",
                "machine_id": "MCH-001",
                "severity": "critical",
            },
            {
                "type": "batch_failure",
                "batch_id": "BAT-001",
                "reason": "quality_deviation",
            },
            {
                "type": "maintenance_overdue",
                "machine_id": "MCH-003",
                "days_overdue": 30,
            },
        ]
        return scenario

    def maintenance_scenario(self) -> Scenario:
        scenario = self.medium_factory()
        scenario.name = "Maintenance Scenario"
        scenario.description = "Factory with maintenance activities"
        scenario.events = [
            {"type": "pm_scheduled", "machine_id": "MCH-001", "pm_type": "preventive"},
            {
                "type": "calibration_due",
                "machine_id": "MCH-002",
                "due_date": "2026-07-01",
            },
            {"type": "work_order_created", "type": "maintenance", "priority": "high"},
        ]
        return scenario

    def quality_incident_scenario(self) -> Scenario:
        scenario = self.medium_factory()
        scenario.name = "Quality Incident"
        scenario.description = "Factory with quality deviations"
        scenario.events = [
            {"type": "deviation", "batch_id": "BAT-001", "severity": "major"},
            {"type": "capa_created", "deviation_id": "DEV-001", "type": "corrective"},
            {"type": "oos_result", "test": "dissolution", "result": 78.5, "spec": 80.0},
        ]
        return scenario

    def _build_scenario(
        self, name: str, description: str, config: SeedConfig
    ) -> Scenario:
        generator = SeedGenerator(config)
        data = generator.generate_full_hierarchy()

        scenario = Scenario(
            name=name,
            description=description,
            config=config,
            data=data,
            metadata=generator.summary(),
        )
        self._scenarios[scenario.scenario_id] = scenario
        return scenario

    def get_scenario(self, scenario_id: str) -> Scenario | None:
        return self._scenarios.get(scenario_id)

    def list_scenarios(self) -> list[Scenario]:
        return list(self._scenarios.values())
