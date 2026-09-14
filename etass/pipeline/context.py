"""Pipeline execution context — shared state threaded through all 15 phases."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PhaseResult:
    phase: int
    name: str
    status: str  # success | failed | skipped | pending_human
    output: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    duration_ms: float = 0.0

    def ok(self) -> bool:
        return self.status == "success"


@dataclass
class PipelineContext:
    """Mutable state carried through all 15 phases."""

    # Inputs
    chart_source: str = ""
    chart_name: str = ""
    chart_version: str = "1.0.0"
    chart_domain: str = "pharmaceutical"
    monkeybrain_url: str = "http://localhost:8031"
    inside_monkeybrain: bool = (
        False  # True when called from a MonkeyBrain intent handler — Phase 10 skips its POST to avoid recursion
    )

    # Accumulated state (each phase adds to this)
    state: dict[str, Any] = field(default_factory=dict)
    phases: list[PhaseResult] = field(default_factory=list)
    start_time: float = field(default_factory=time.monotonic)

    def record(self, result: PhaseResult) -> None:
        self.phases.append(result)
        # Merge phase output into shared state
        self.state.update(result.output)

    def elapsed_ms(self) -> float:
        return (time.monotonic() - self.start_time) * 1000

    def failed_phases(self) -> list[PhaseResult]:
        return [p for p in self.phases if not p.ok()]

    def all_passed(self) -> bool:
        return all(p.ok() for p in self.phases)
