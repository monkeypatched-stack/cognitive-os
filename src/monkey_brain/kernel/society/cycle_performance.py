"""Runtime Performance Audit — cycle/actor-level timing instrumentation.

Measurement-only: nothing here changes control flow or behavior. These
dataclasses are built once per PlanetaryRuntime._run_cycle() call
(kernel/society/integration.py), populated from:

  - time.perf_counter() wraps added directly around _run_cycle()'s own
    stages (scheduler/geo-tick, perturbation queue drain + world
    reconciliation, Deja Vu, cleanup);
  - PlanetaryRuntime._cycle_actor_timing_ms (per-actor total wall time,
    written by _tick_present_actor);
  - ActorRuntimeState.last_tick_result.stage_timings_ms (the finer
    per-stage breakdown — grounding/prompt-build/LLM-call/parse/etc. —
    threaded up from kernel/pipeline/llm_planner.py and
    kernel/pipeline/belief_runtime.py through
    kernel/compile/cognitive_actor.py::_CognitiveTickResult).

Exposed via PlanetaryRuntime._last_cycle_report / GET
/planet/performance-report for inspection; no code anywhere branches on
these values.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ActorPerformanceReport:
    """One actor's tick, broken into the stages a Runtime Performance
    Audit needs to separate LLM-inference latency from everything else."""

    actor_id: str
    actor_name: str
    total_ms: float
    belief_updated: bool = False
    ticked: bool = True
    """False if this actor was scheduled but tick_one_actor() returned a
    falsy/None result (not found, inactive, or belief fusion failed
    before the cognitive engine ever ran)."""

    observe_ms: float = 0.0
    believe_ms: float = 0.0
    grounding_ms: float = 0.0
    prompt_build_ms: float = 0.0
    llm_call_ms: float = 0.0
    llm_call_count: int = 0
    response_parse_ms: float = 0.0
    planning_total_ms: float = 0.0
    predict_ms: float = 0.0
    decide_ms: float = 0.0
    act_ms: float = 0.0
    perturbation_publish_ms: float = 0.0
    observe_outcome_ms: float = 0.0
    compare_ms: float = 0.0
    learn_ms: float = 0.0
    compile_phi_ms: float = 0.0
    commit_ms: float = 0.0

    @property
    def accounted_ms(self) -> float:
        """Sum of every sub-stage this report separately measured.
        planning_total_ms is deliberately excluded -- it's a cross-check
        total that DUPLICATES grounding_ms + prompt_build_ms +
        llm_call_ms + response_parse_ms, not an additional cost."""
        return (
            self.observe_ms
            + self.believe_ms
            + self.grounding_ms
            + self.prompt_build_ms
            + self.llm_call_ms
            + self.response_parse_ms
            + self.predict_ms
            + self.decide_ms
            + self.act_ms
            + self.observe_outcome_ms
            + self.compare_ms
            + self.learn_ms
            + self.compile_phi_ms
            + self.commit_ms
        )

    @property
    def unaccounted_ms(self) -> float:
        """total_ms minus everything separately measured -- scheduling/
        await overhead between stages, plus tick_one_actor's own
        get_observation()/belief_fusion.fuse() work, which runs before
        the cognitive engine's stages start and was never independently
        instrumented (see integration.py::_tick_present_actor)."""
        return max(0.0, self.total_ms - self.accounted_ms)

    def format(self) -> str:
        lines = [f"Actor: {self.actor_name}"]

        def row(label: str, ms: float) -> str:
            dots = "." * max(1, 26 - len(label))
            return f"{label}{dots}{ms:>10.1f} ms"

        lines.append(row("Observe", self.observe_ms))
        lines.append(row("Belief Retrieval", self.believe_ms))
        lines.append(row("Grounding", self.grounding_ms))
        lines.append(row("Prompt Build", self.prompt_build_ms))
        lines.append(row(f"LLM ({self.llm_call_count} call(s))", self.llm_call_ms))
        lines.append(row("Response Parse", self.response_parse_ms))
        lines.append(row("Predict", self.predict_ms))
        lines.append(row("Decide", self.decide_ms))
        lines.append(row("Act", self.act_ms))
        lines.append(row("Perturbation Publish", self.perturbation_publish_ms))
        lines.append(row("Observe Outcome", self.observe_outcome_ms))
        lines.append(row("Compare", self.compare_ms))
        lines.append(row("Learn", self.learn_ms))
        lines.append(row("Compile Phi", self.compile_phi_ms))
        lines.append(row("Commit", self.commit_ms))
        lines.append(row("Unaccounted", self.unaccounted_ms))
        lines.append("-" * 38)
        lines.append(row("Total", self.total_ms))
        return "\n".join(lines)

    @classmethod
    def from_stage_timings(
        cls,
        actor_id: str,
        actor_name: str,
        total_ms: float,
        stage_timings_ms: dict,
        *,
        belief_updated: bool = False,
        ticked: bool = True,
    ) -> "ActorPerformanceReport":
        t = stage_timings_ms or {}
        return cls(
            actor_id=actor_id,
            actor_name=actor_name,
            total_ms=round(total_ms, 3),
            belief_updated=belief_updated,
            ticked=ticked,
            observe_ms=t.get("observe", 0.0),
            believe_ms=t.get("believe", 0.0),
            grounding_ms=t.get("grounding_ms", 0.0),
            prompt_build_ms=t.get("prompt_build_ms", 0.0),
            llm_call_ms=t.get("llm_call_ms", 0.0),
            llm_call_count=int(t.get("llm_call_count", 0)),
            response_parse_ms=t.get("response_parse_ms", 0.0),
            planning_total_ms=t.get("planning_total_ms", 0.0),
            predict_ms=t.get("predict", 0.0),
            decide_ms=t.get("decide", 0.0),
            act_ms=t.get("execute", 0.0),
            perturbation_publish_ms=t.get("perturbation_publish_ms", 0.0),
            observe_outcome_ms=t.get("observe_outcome", 0.0),
            compare_ms=t.get("compare", 0.0),
            learn_ms=t.get("learn", 0.0),
            compile_phi_ms=t.get("compile_phi", 0.0),
            commit_ms=t.get("commit", 0.0),
        )


@dataclass(frozen=True)
class CyclePerformanceReport:
    """One _run_cycle() call's full timing breakdown."""

    cycle_number: int
    total_ms: float
    scheduler_ms: float
    """GeographicEntityRuntime(...).tick() -- actor discovery + serial
    per-actor dispatch. Includes every actor's total_ms (actors are
    ticked FROM WITHIN this call, not alongside it)."""
    perturbation_queue_ms: float
    """PerturbationQueue.drain() + WorldEvent reconciliation."""
    world_reconciliation_ms: float
    """Simulated world-noise perturb() + movement-perturbation pass."""
    deja_vu_ms: float
    cleanup_ms: float
    """Post-tick observation/belief-update ContextEvent publish loop."""
    actors: tuple[ActorPerformanceReport, ...] = field(default_factory=tuple)

    @property
    def actors_scheduled(self) -> int:
        return len(self.actors)

    @property
    def actors_ticked(self) -> int:
        return sum(1 for a in self.actors if a.ticked)

    @property
    def actors_changed(self) -> int:
        return sum(1 for a in self.actors if a.belief_updated)

    @property
    def llm_calls(self) -> int:
        return sum(a.llm_call_count for a in self.actors)

    @property
    def total_llm_ms(self) -> float:
        return sum(a.llm_call_ms for a in self.actors)

    @property
    def total_actor_ms(self) -> float:
        return sum(a.total_ms for a in self.actors)

    @property
    def runtime_overhead_ms(self) -> float:
        """Total cycle time minus total LLM time -- everything that ISN'T
        waiting on the external model: scheduling, grounding, prompt
        construction, parsing, execution, learning, world reconciliation,
        Deja Vu, cleanup, and unaccounted per-actor overhead."""
        return max(0.0, self.total_ms - self.total_llm_ms)

    def format_summary(self) -> str:
        lines = [
            "Planetary Cycle",
            "",
            f"Actors Scheduled: {self.actors_scheduled}",
            f"Actors Executed: {self.actors_ticked}",
            f"Actors Actually Changed: {self.actors_changed}",
            f"LLM Calls: {self.llm_calls}",
            "",
        ]

        def row(label: str, ms: float) -> str:
            dots = "." * max(1, 30 - len(label))
            return f"{label}{dots}{ms:>12.1f} ms"

        lines.append(row("Scheduler", self.scheduler_ms))
        lines.append(row("Perturbation Queue", self.perturbation_queue_ms))
        lines.append(row("World Reconciliation", self.world_reconciliation_ms))
        lines.append(row("Deja Vu", self.deja_vu_ms))
        lines.append(row("Cleanup", self.cleanup_ms))
        lines.append(row("Runtime Overhead", self.runtime_overhead_ms))
        lines.append(row("Total LLM Time", self.total_llm_ms))
        lines.append(row("Total Cycle", self.total_ms))
        return "\n".join(lines)
