"""Seeder — deterministic data seeding framework.

Generates complete grounded datasets for:
- Benchmarking
- Development
- Testing
- Demonstrations
- Regression validation
"""

from __future__ import annotations

from typing import Any

from plasticity.seed.seed_data import SeedConfig, SeedGenerator
from plasticity.seed.scenario_builder import ScenarioBuilder, Scenario
from plasticity.seed.event_generator import EventGenerator, Event
from plasticity.seed.benchmark_generator import BenchmarkGenerator, BenchmarkEntry


class Seeder:
    """Deterministic data seeding framework.

    All data generated from existing domain models.
    No direct database manipulation.
    """

    def __init__(self, config: SeedConfig | None = None):
        self._config = config or SeedConfig()
        self._generator = SeedGenerator(self._config)
        self._scenarios = ScenarioBuilder()
        self._events = EventGenerator(self._config.seed)
        self._benchmarks = BenchmarkGenerator(self._config.seed)

    def seed_small(self) -> dict[str, Any]:
        """Seed a small factory."""
        scenario = self._scenarios.small_factory()
        events = self._events.generate_all_events(scenario.data)
        benchmarks = self._benchmarks.generate_all()

        return {
            "scenario": scenario.name,
            "data": scenario.data,
            "events": [e.__dict__ for e in events],
            "benchmarks": [b.__dict__ for b in benchmarks],
            "summary": {
                "entities": {k: len(v) for k, v in scenario.data.items()},
                "events": len(events),
                "benchmarks": len(benchmarks),
            },
        }

    def seed_medium(self) -> dict[str, Any]:
        """Seed a medium factory."""
        scenario = self._scenarios.medium_factory()
        events = self._events.generate_all_events(scenario.data)
        benchmarks = self._benchmarks.generate_all()

        return {
            "scenario": scenario.name,
            "data": scenario.data,
            "events": [e.__dict__ for e in events],
            "benchmarks": [b.__dict__ for b in benchmarks],
            "summary": {
                "entities": {k: len(v) for k, v in scenario.data.items()},
                "events": len(events),
                "benchmarks": len(benchmarks),
            },
        }

    def seed_enterprise(self) -> dict[str, Any]:
        """Seed an enterprise factory."""
        scenario = self._scenarios.enterprise_factory()
        events = self._events.generate_all_events(scenario.data)
        benchmarks = self._benchmarks.generate_all()

        return {
            "scenario": scenario.name,
            "data": scenario.data,
            "events": [e.__dict__ for e in events],
            "benchmarks": [b.__dict__ for b in benchmarks],
            "summary": {
                "entities": {k: len(v) for k, v in scenario.data.items()},
                "events": len(events),
                "benchmarks": len(benchmarks),
            },
        }

    def seed_failure(self) -> dict[str, Any]:
        """Seed a failure scenario."""
        scenario = self._scenarios.failure_scenario()
        events = self._events.generate_all_events(scenario.data)
        events.extend(
            [
                Event(
                    event_type=e["type"],
                    entity_type="event",
                    entity_id=e.get("machine_id", ""),
                    data=e,
                )
                for e in scenario.events
            ]
        )

        return {
            "scenario": scenario.name,
            "data": scenario.data,
            "events": [e.__dict__ for e in events],
            "summary": {
                "entities": {k: len(v) for k, v in scenario.data.items()},
                "events": len(events),
                "failure_events": len(scenario.events),
            },
        }

    def seed_benchmarks(self) -> dict[str, Any]:
        """Seed benchmark datasets only."""
        benchmarks = self._benchmarks.generate_all()
        return {
            "benchmarks": [b.__dict__ for b in benchmarks],
            "summary": self._benchmarks.summary(),
        }

    def summary(self) -> dict:
        return {
            "config": {
                "seed": self._config.seed,
                "plant_count": self._config.plant_count,
                "lines_per_plant": self._config.lines_per_plant,
            },
            "generator": self._generator.summary(),
        }
