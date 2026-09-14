"""Engineering Telemetry — quality metrics for autonomous engineering loops.

Replaces pass/fail with engineering quality:
    Architecture | Quality | Runtime | Knowledge | Simulation | Overall
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ArchitectureMetrics:
    ddd_score: float = 0.0
    governance: float = 0.0
    compliance: float = 0.0

    def to_dict(self) -> dict:
        return {
            "ddd_score": self.ddd_score,
            "governance": self.governance,
            "compliance": self.compliance,
        }


@dataclass
class QualityMetrics:
    tests_passed: int = 0
    tests_healed: int = 0
    tests_failed: int = 0
    files_generated: int = 0
    files_patched: int = 0

    @property
    def total_tests(self) -> int:
        return self.tests_passed + self.tests_healed + self.tests_failed

    @property
    def pass_rate(self) -> float:
        return self.tests_passed / max(self.total_tests, 1)

    def to_dict(self) -> dict:
        return {
            "tests_passed": self.tests_passed,
            "tests_healed": self.tests_healed,
            "tests_failed": self.tests_failed,
            "total": self.total_tests,
            "pass_rate": self.pass_rate,
            "files_generated": self.files_generated,
            "files_patched": self.files_patched,
        }


@dataclass
class RuntimeMetrics:
    latency_ms: float = 0.0
    memory_mb: float = 0.0

    def to_dict(self) -> dict:
        return {"latency_ms": self.latency_ms, "memory_mb": self.memory_mb}


@dataclass
class KnowledgeMetrics:
    knowledge_packs: int = 0
    confidence: float = 0.0
    confidence_dimensions: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "packs": self.knowledge_packs,
            "confidence": self.confidence,
            "dimensions": self.confidence_dimensions,
        }


@dataclass
class SimulationMetrics:
    solver_mesh: str = "PASS"
    counterexamples: int = 0
    prediction_loss: float = 0.0
    rounds: int = 0
    initial_loss: float = 0.0
    final_loss: float = 0.0

    @property
    def loss_reduction(self) -> float:
        if self.initial_loss == 0:
            return 0.0
        return (self.initial_loss - self.final_loss) / self.initial_loss

    def to_dict(self) -> dict:
        return {
            "solver_mesh": self.solver_mesh,
            "counterexamples": self.counterexamples,
            "prediction_loss": self.prediction_loss,
            "rounds": self.rounds,
            "initial_loss": self.initial_loss,
            "final_loss": self.final_loss,
            "loss_reduction": self.loss_reduction,
        }


@dataclass
class RepairMetrics:
    iterations: int = 0
    files_patched: int = 0
    success_rate: float = 0.0
    violations_found: int = 0
    violations_fixed: int = 0

    def to_dict(self) -> dict:
        return {
            "iterations": self.iterations,
            "files_patched": self.files_patched,
            "success_rate": self.success_rate,
            "violations_found": self.violations_found,
            "violations_fixed": self.violations_fixed,
        }


@dataclass
class EngineeringReport:
    """Full engineering telemetry report."""

    architecture: ArchitectureMetrics = field(default_factory=ArchitectureMetrics)
    quality: QualityMetrics = field(default_factory=QualityMetrics)
    runtime: RuntimeMetrics = field(default_factory=RuntimeMetrics)
    knowledge: KnowledgeMetrics = field(default_factory=KnowledgeMetrics)
    simulation: SimulationMetrics = field(default_factory=SimulationMetrics)
    repair: RepairMetrics = field(default_factory=RepairMetrics)

    @property
    def overall_confidence(self) -> float:
        """Weighted average of all metric scores."""
        scores = [
            (
                self.architecture.ddd_score
                + self.architecture.governance
                + self.architecture.compliance
            )
            / 300,
            self.quality.pass_rate,
            max(0, 1.0 - self.runtime.latency_ms / 5000),
            self.knowledge.confidence,
            max(0, 1.0 - self.simulation.prediction_loss),
            self.repair.success_rate,
        ]
        return sum(scores) / len(scores)

    def format(self) -> str:
        lines = [
            "─" * 50,
            "Engineering Report",
            "─" * 50,
            "",
            "Architecture",
            "─" * 20,
            f"  DDD Score            {self.architecture.ddd_score:.0f}",
            f"  Governance           {self.architecture.governance:.0f}",
            f"  Compliance          {self.architecture.compliance:.0f}",
            "",
            "Quality",
            "─" * 20,
            f"  Tests Passed        {self.quality.tests_passed}",
            f"  Tests Healed          {self.quality.tests_healed}",
            f"  Tests Failed         {self.quality.tests_failed}",
            f"  Files Generated      {self.quality.files_generated}",
            f"  Files Patched          {self.quality.files_patched}",
            "",
            "Runtime",
            "─" * 20,
            f"  Latency            {self.runtime.latency_ms:.0f} ms",
            f"  Memory            {self.runtime.memory_mb:.0f} MB",
            "",
            "Knowledge",
            "─" * 20,
            f"  Knowledge Packs     {self.knowledge.knowledge_packs}",
            f"  Confidence         {self.knowledge.confidence:.2f}",
            "",
            "Simulation",
            "─" * 20,
            f"  Solver Mesh        {self.simulation.solver_mesh}",
            f"  Counterexamples       {self.simulation.counterexamples}",
            f"  Prediction Loss    {self.simulation.prediction_loss:.2f}",
            f"  Rounds              {self.simulation.rounds}",
            f"  Loss Reduction     {self.simulation.loss_reduction:.0%}",
            "",
            "Repair",
            "─" * 20,
            f"  Iterations           {self.repair.iterations}",
            f"  Files Patched          {self.repair.files_patched}",
            f"  Success Rate        {self.repair.success_rate:.0%}",
            f"  Violations Found     {self.repair.violations_found}",
            f"  Violations Fixed     {self.repair.violations_fixed}",
            "",
            "─" * 50,
            f"Overall Confidence   {self.overall_confidence:.1%}",
            "─" * 50,
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "architecture": self.architecture.to_dict(),
            "quality": self.quality.to_dict(),
            "runtime": self.runtime.to_dict(),
            "knowledge": self.knowledge.to_dict(),
            "simulation": self.simulation.to_dict(),
            "repair": self.repair.to_dict(),
            "overall_confidence": self.overall_confidence,
        }
