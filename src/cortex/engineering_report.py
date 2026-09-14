"""engineering_report.py — Engineering telemetry, not CI status.

Instead of  OK / HEALED / FAILED
you get     DDD Score, Simulation Loss, Convergence, Knowledge Confidence, ...

This is engineering telemetry:

    ────────────────────────────────────────────
    Engineering Report — work-order    2026-07-05
    ────────────────────────────────────────────

    Architecture
    ────────────
    DDD Score            100
    Governance            97
    Compliance           100

    Quality
    ───────
    Tests Passed         128
    Tests Healed           1

    Repair
    ──────
    Iterations             2
    Files Patched          5
    Loss: 0.41 → 0.06

    Simulation
    ──────────
    Solver Mesh         PASS
    Counterexamples        0
    Prediction Loss     0.03

    Knowledge
    ─────────
    Knowledge Packs        7
    Confidence          0.94

    Overall Confidence   96.2 %
    ────────────────────────────────────────────

The report is buildable incrementally as pipeline stages complete.
Call `report.section(...)` from each stage, then `report.render()` at the end.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# RepairTelemetry — tracks convergence, not just pass/fail
# ---------------------------------------------------------------------------


@dataclass
class RepairTelemetry:
    iterations: int = 0
    files_patched: int = 0
    violations_found: int = 0
    violations_fixed: int = 0
    loss_initial: float = 0.0
    loss_final: float = 0.0
    converged: bool = False

    @property
    def success_rate(self) -> float:
        if self.violations_found == 0:
            return 1.0
        return round(self.violations_fixed / self.violations_found, 4)

    @property
    def loss_delta(self) -> float:
        return round(self.loss_initial - self.loss_final, 4)


# ---------------------------------------------------------------------------
# SimulationTelemetry — solver mesh + prediction loss trajectory
# ---------------------------------------------------------------------------


@dataclass
class SimulationTelemetry:
    rounds: int = 0
    solvers_run: list[str] = field(default_factory=list)
    counterexamples: int = 0
    prediction_loss_initial: float = 0.0
    prediction_loss_final: float = 0.0
    converged: bool = False
    loss_by_component: dict[str, float] = field(default_factory=dict)  # state/action/affordance/...

    @property
    def solver_mesh_pass(self) -> bool:
        return self.counterexamples == 0


# ---------------------------------------------------------------------------
# EngineeringReport — the top-level telemetry object
# ---------------------------------------------------------------------------


@dataclass
class EngineeringReport:
    """Aggregates telemetry from all pipeline stages into one structured report.

    Build incrementally:
        report = EngineeringReport(service="work-order")
        report.ddd_score = 100
        report.tests_passed = 128
        ...
        print(report.render())
    """

    service: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    # ── Architecture ─────────────────────────────────────────────────────────
    ddd_score: float | None = None  # 0–100
    governance_score: float | None = None  # 0–100
    compliance_score: float | None = None  # 0–100

    # ── Quality ──────────────────────────────────────────────────────────────
    tests_passed: int = 0
    tests_failed: int = 0
    tests_healed: int = 0
    files_generated: int = 0

    # ── Runtime ──────────────────────────────────────────────────────────────
    latency_ms: float | None = None
    memory_mb: float | None = None

    # ── Knowledge ────────────────────────────────────────────────────────────
    knowledge_packs: int = 0
    knowledge_confidence: float | None = None  # C_knowledge scalar
    knowledge_loss_vec: dict[str, float] = field(default_factory=dict)  # ⟨P,F,S,V,E,M,R⟩

    # ── Simulation ───────────────────────────────────────────────────────────
    simulation: SimulationTelemetry = field(default_factory=SimulationTelemetry)

    # ── Repair ───────────────────────────────────────────────────────────────
    repair: RepairTelemetry = field(default_factory=RepairTelemetry)

    # ── Overall ──────────────────────────────────────────────────────────────
    _overall_confidence: float | None = None  # set explicitly or auto-computed

    # ── Incremental builders ─────────────────────────────────────────────────

    def record_ddd(self, score: float) -> "EngineeringReport":
        self.ddd_score = round(score, 1)
        return self

    def record_governance(self, score: float) -> "EngineeringReport":
        self.governance_score = round(score, 1)
        return self

    def record_compliance(self, score: float) -> "EngineeringReport":
        self.compliance_score = round(score, 1)
        return self

    def record_tests(self, passed: int, failed: int = 0, healed: int = 0) -> "EngineeringReport":
        self.tests_passed = passed
        self.tests_failed = failed
        self.tests_healed = healed
        return self

    def record_runtime(self, latency_ms: float | None = None, memory_mb: float | None = None) -> "EngineeringReport":
        if latency_ms is not None:
            self.latency_ms = round(latency_ms, 1)
        if memory_mb is not None:
            self.memory_mb = round(memory_mb, 1)
        return self

    def record_knowledge(self, packs: int, confidence: float, loss_vec: dict | None = None) -> "EngineeringReport":
        self.knowledge_packs = packs
        self.knowledge_confidence = round(confidence, 4)
        if loss_vec:
            self.knowledge_loss_vec = loss_vec
        return self

    def record_simulation(self, sim: SimulationTelemetry) -> "EngineeringReport":
        self.simulation = sim
        return self

    def record_repair(self, rep: RepairTelemetry) -> "EngineeringReport":
        self.repair = rep
        return self

    def record_generation(self, files: int) -> "EngineeringReport":
        self.files_generated = files
        return self

    # ── Overall confidence ───────────────────────────────────────────────────

    @property
    def overall_confidence(self) -> float:
        if self._overall_confidence is not None:
            return round(self._overall_confidence, 3)

        scores: list[float] = []
        weights: list[float] = []

        def _add(val: float | None, w: float) -> None:
            if val is not None:
                scores.append(max(0.0, min(1.0, val / 100.0 if val > 1.0 else val)))
                weights.append(w)

        _add(self.ddd_score, 0.20)
        _add(self.governance_score, 0.12)
        _add(self.compliance_score, 0.12)

        total = self.tests_passed + self.tests_failed
        if total > 0:
            _add(self.tests_passed / total, 0.20)

        pred_loss = self.simulation.prediction_loss_final
        _add(1.0 - pred_loss, 0.16)

        _add(self.knowledge_confidence, 0.10)

        if not scores:
            return 0.0

        total_w = sum(weights)
        return round(sum(s * w for s, w in zip(scores, weights)) / total_w, 4)

    # ── Rendering ────────────────────────────────────────────────────────────

    def render(self, *, width: int = 44, color: bool = True) -> str:
        """Render the engineering report as a terminal-printable string."""
        divider = "─" * width
        title = f"Engineering Report — {self.service}    {self.timestamp}"

        rows: list[str] = []

        def div(label: str = "") -> None:
            rows.append(divider if not label else label)

        def blank() -> None:
            rows.append("")

        def section(name: str) -> None:
            blank()
            rows.append(name)
            rows.append("─" * len(name))

        def row(label: str, value: Any, unit: str = "", warn: bool = False) -> None:
            label_w = width - 14
            v = str(value)
            if unit:
                v = f"{v} {unit}"
            line = f"{label:<{label_w}}{v:>12}"
            if color and warn:
                line = f"\033[33m{line}\033[0m"
            rows.append(line)

        def row_pct(label: str, value: float | None, threshold: float = 90.0) -> None:
            if value is None:
                return
            row(label, f"{value:.1f}", warn=value < threshold)

        def loss_arrow(initial: float, final: float) -> str:
            if initial == 0.0 and final == 0.0:
                return "—"
            arrow = "↓" if final < initial else ("→" if final == initial else "↑")
            return f"{initial:.3f} {arrow} {final:.3f}"

        # Header
        div()
        rows.append(title)
        div()

        # Architecture
        if any(v is not None for v in (self.ddd_score, self.governance_score, self.compliance_score)):
            section("Architecture")
            row_pct("DDD Score", self.ddd_score)
            row_pct("Governance", self.governance_score)
            row_pct("Compliance", self.compliance_score)

        # Quality
        if self.tests_passed + self.tests_failed + self.files_generated > 0:
            section("Quality")
            if self.files_generated:
                row("Files Generated", self.files_generated)
            if self.tests_passed or self.tests_failed:
                row("Tests Passed", self.tests_passed, warn=(self.tests_failed > 0))
                if self.tests_failed:
                    row("Tests Failed", self.tests_failed, warn=True)
                if self.tests_healed:
                    row("Tests Healed", self.tests_healed)

        # Runtime
        if self.latency_ms is not None or self.memory_mb is not None:
            section("Runtime")
            if self.latency_ms is not None:
                row("Latency", f"{self.latency_ms:.0f}", "ms")
            if self.memory_mb is not None:
                row("Memory", f"{self.memory_mb:.0f}", "MB")

        # Repair
        if self.repair.iterations > 0 or self.repair.violations_found > 0:
            section("Repair")
            row("Iterations", self.repair.iterations)
            if self.repair.files_patched:
                row("Files Patched", self.repair.files_patched)
            if self.repair.violations_found:
                row("Violations Found", self.repair.violations_found)
                row(
                    "Violations Fixed",
                    self.repair.violations_fixed,
                    warn=(self.repair.violations_fixed < self.repair.violations_found),
                )
            if self.repair.success_rate < 1.0:
                row(
                    "Repair Success",
                    f"{self.repair.success_rate * 100:.0f}",
                    "%",
                    warn=self.repair.success_rate < 1.0,
                )
            if self.repair.loss_initial or self.repair.loss_final:
                row(
                    "Loss",
                    loss_arrow(self.repair.loss_initial, self.repair.loss_final),
                    warn=(self.repair.loss_final > 0.15),
                )

        # Simulation
        sim = self.simulation
        if sim.rounds or sim.counterexamples or sim.prediction_loss_final:
            section("Simulation")
            if sim.rounds:
                row("Rounds", sim.rounds)
            row(
                "Solver Mesh",
                "PASS" if sim.solver_mesh_pass else "FAIL",
                warn=(not sim.solver_mesh_pass),
            )
            row("Counterexamples", sim.counterexamples, warn=(sim.counterexamples > 0))
            if sim.prediction_loss_initial or sim.prediction_loss_final:
                row(
                    "Loss",
                    loss_arrow(sim.prediction_loss_initial, sim.prediction_loss_final),
                    warn=(sim.prediction_loss_final > 0.15),
                )
            if sim.loss_by_component:
                dominant = max(sim.loss_by_component, key=sim.loss_by_component.get)  # type: ignore[arg-type]
                row(f"  Dominant ({dominant})", f"{sim.loss_by_component[dominant]:.3f}")

        # Knowledge
        if self.knowledge_packs or self.knowledge_confidence is not None:
            section("Knowledge")
            if self.knowledge_packs:
                row("Knowledge Packs", self.knowledge_packs)
            if self.knowledge_confidence is not None:
                row(
                    "Confidence",
                    f"{self.knowledge_confidence:.2f}",
                    warn=(self.knowledge_confidence < 0.70),
                )
            if self.knowledge_loss_vec:
                weakest = min(self.knowledge_loss_vec, key=self.knowledge_loss_vec.get)  # type: ignore[arg-type]
                row(f"  Weakest ({weakest})", f"{self.knowledge_loss_vec[weakest]:.3f}")

        # Overall confidence
        blank()
        oc = self.overall_confidence * 100
        warn = oc < 80.0
        row("Overall Confidence", f"{oc:.1f}", "%", warn=warn)
        div()

        return "\n".join(rows)

    def to_dict(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "timestamp": self.timestamp,
            "architecture": {
                "ddd_score": self.ddd_score,
                "governance_score": self.governance_score,
                "compliance_score": self.compliance_score,
            },
            "quality": {
                "tests_passed": self.tests_passed,
                "tests_failed": self.tests_failed,
                "tests_healed": self.tests_healed,
                "files_generated": self.files_generated,
            },
            "runtime": {"latency_ms": self.latency_ms, "memory_mb": self.memory_mb},
            "knowledge": {
                "packs": self.knowledge_packs,
                "confidence": self.knowledge_confidence,
                "loss_vec": self.knowledge_loss_vec,
            },
            "simulation": {
                "rounds": self.simulation.rounds,
                "counterexamples": self.simulation.counterexamples,
                "prediction_loss_initial": self.simulation.prediction_loss_initial,
                "prediction_loss_final": self.simulation.prediction_loss_final,
                "loss_by_component": self.simulation.loss_by_component,
                "converged": self.simulation.converged,
            },
            "repair": {
                "iterations": self.repair.iterations,
                "files_patched": self.repair.files_patched,
                "violations_found": self.repair.violations_found,
                "violations_fixed": self.repair.violations_fixed,
                "success_rate": self.repair.success_rate,
                "loss_initial": self.repair.loss_initial,
                "loss_final": self.repair.loss_final,
                "converged": self.repair.converged,
            },
            "overall_confidence": self.overall_confidence,
        }
