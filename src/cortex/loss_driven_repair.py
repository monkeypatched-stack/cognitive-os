"""loss_driven_repair.py — The repair loop as an optimization problem.

Instead of:
    test_failed → fix

It becomes:
    loss = 0.42
    identify dominant loss term
    select solver matched to that term
    generate targeted repair
    recompute loss
    repeat until convergence

The loop terminates when:
    loss < convergence_threshold   (converged)
    iterations >= max_iterations   (budget exhausted)
    delta < stagnation_epsilon     (not improving — bail out)

Dominant loss term → solver selection:

    state       →  jepa           (world-state embedding anomalies)
    action      →  model_checker  (state machine transition validity)
    affordance  →  graph          (action-space reachability)
    constraint  →  sat_smt        (invariant satisfiability)
    goal        →  heuristic      (LLM goal-alignment reasoning)
    knowledge   →  retrieval      (fetch knowledge with higher confidence)

This is consistent with the Solver Selection Constitution in adversarial_agents.py.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("agentos.repair")


# ---------------------------------------------------------------------------
# Loss term → solver / repair strategy mapping
# ---------------------------------------------------------------------------

# Which solver targets each dominant loss component
_LOSS_TO_SOLVER: dict[str, str] = {
    "state": "jepa",
    "action": "model_checker",
    "affordance": "graph",
    "constraint": "sat_smt",
    "goal": "heuristic",
    "knowledge": "retrieval",
}

# Repair prompt templates — targeted at the dominant loss term
_REPAIR_TEMPLATES: dict[str, str] = {
    "state": (
        "The world-state prediction error is high (L_state={loss:.3f}). "
        "Identify which entities have inconsistent or duplicate status distributions "
        "and propose a minimal structural fix that resolves the embedding anomaly."
    ),
    "action": (
        "The action prediction error is high (L_action={loss:.3f}). "
        "Identify transitions in the design where the proposed action is incompatible "
        "with the current entity state machines and fix the state model."
    ),
    "affordance": (
        "The affordance prediction error is high (L_affordance={loss:.3f}). "
        "The design does not correctly model how taking an action changes the "
        "available future action space (ΔA). Fix the state machine so that actions "
        "correctly open and close downstream affordances."
    ),
    "constraint": (
        "Constraint violations detected (L_constraint={loss:.3f}). "
        "Identify the invariants being violated and propose a patch that satisfies "
        "all domain constraints, DDD rules, and safety properties."
    ),
    "goal": (
        "Goal proximity loss is high (L_goal={loss:.3f}). "
        "Too many entities are stuck in initial states. "
        "Propose process improvements or missing lifecycle transitions that move "
        "entities toward their terminal states."
    ),
    "knowledge": (
        "Epistemic confidence is low (L_knowledge={loss:.3f}). "
        "The knowledge supporting this decision has insufficient provenance, "
        "freshness, or validation. Identify which knowledge packs are weakest "
        "and propose what should be retrieved or re-verified."
    ),
}


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class RepairIteration:
    """One step in the loss-driven repair loop."""

    iteration: int
    dominant_term: str  # which SimulationLoss component drove this repair
    solver_selected: str  # solver chosen to address the dominant term
    repair_prompt: str  # the targeted repair instruction sent to LLM
    loss_before: dict[str, float]
    loss_after: dict[str, float]
    delta: float  # loss_before.total - loss_after.total (positive = improving)
    converging: bool


@dataclass
class RepairResult:
    """Full result of the loss-driven repair loop."""

    converged: bool
    iterations: list[RepairIteration]
    loss_initial: float
    loss_final: float
    final_prompt: str
    dominant_term_history: list[str]  # which term drove each iteration
    stagnated: bool = False  # true if loop stopped due to no improvement

    @property
    def total_delta(self) -> float:
        return round(self.loss_initial - self.loss_final, 4)

    @property
    def convergence_rate(self) -> float:
        """Fraction of loss eliminated across all iterations."""
        if self.loss_initial == 0:
            return 1.0
        return round(self.total_delta / self.loss_initial, 4)

    def to_telemetry(self) -> dict[str, Any]:
        return {
            "converged": self.converged,
            "stagnated": self.stagnated,
            "iterations": len(self.iterations),
            "loss_initial": self.loss_initial,
            "loss_final": self.loss_final,
            "total_delta": self.total_delta,
            "convergence_rate": self.convergence_rate,
            "dominant_term_history": self.dominant_term_history,
            "per_iteration": [
                {
                    "iteration": it.iteration,
                    "dominant_term": it.dominant_term,
                    "solver": it.solver_selected,
                    "loss_before": it.loss_before.get("total", 0),
                    "loss_after": it.loss_after.get("total", 0),
                    "delta": it.delta,
                }
                for it in self.iterations
            ],
        }


# ---------------------------------------------------------------------------
# Dominant loss term identification
# ---------------------------------------------------------------------------


def identify_dominant(loss_components: dict[str, float]) -> str:
    """Return the name of the highest non-total loss component."""
    candidates = {k: v for k, v in loss_components.items() if k != "total" and v > 0}
    if not candidates:
        return "constraint"  # safe fallback
    return max(candidates, key=candidates.get)  # type: ignore[arg-type]


def build_repair_prompt(dominant_term: str, loss_val: float, base_prompt: str) -> str:
    """Construct a targeted repair prompt focused on the dominant loss component."""
    template = _REPAIR_TEMPLATES.get(dominant_term, _REPAIR_TEMPLATES["constraint"])
    prefix = template.format(loss=loss_val)
    return f"{prefix}\n\nOriginal design context:\n{base_prompt}"


# ---------------------------------------------------------------------------
# LossDrivenRepairLoop
# ---------------------------------------------------------------------------


class LossDrivenRepairLoop:
    """Iteratively optimises a design against SimulationLoss until convergence.

    Each iteration:
      1. Identify the dominant loss term (highest component)
      2. Select the solver matched to that term
      3. Generate a targeted repair prompt
      4. Run adversarial pass to compute new loss
      5. Check convergence / stagnation

    Usage:
        loop = LossDrivenRepairLoop()
        result = await loop.run(prompt, world_state)
        report.record_repair(result.to_repair_telemetry())
    """

    def __init__(
        self,
        convergence_threshold: float = 0.10,
        stagnation_epsilon: float = 0.005,
        max_iterations: int = 10,
        timeout_per_round: float = 90.0,
    ) -> None:
        self.convergence_threshold = convergence_threshold
        self.stagnation_epsilon = stagnation_epsilon
        self.max_iterations = max_iterations
        self.timeout_per_round = timeout_per_round

    async def run(
        self,
        prompt: str,
        world_state: dict[str, Any],
    ) -> RepairResult:
        """Run the loss-driven repair loop.

        Requires adversarial_agents and world_model_simulation to be importable.
        Can run standalone (outside the G→A→R loop) for targeted repair.
        """
        from cortex.adversarial_agents import run_adversarial_pass

        iterations: list[RepairIteration] = []
        dominant_history: list[str] = []
        current_prompt = prompt

        # Initial loss
        initial_adv = await run_adversarial_pass(current_prompt, world_state, timeout=self.timeout_per_round)
        current_loss = initial_adv.simulation_loss.to_dict()
        loss_initial = current_loss.get("total", 0.0)

        logger.info("[repair] initial loss=%.4f", loss_initial)

        for i in range(1, self.max_iterations + 1):
            total = current_loss.get("total", 0.0)

            if total < self.convergence_threshold:
                logger.info("[repair] converged at iteration %d, loss=%.4f", i, total)
                break

            dominant = identify_dominant(current_loss)
            solver = _LOSS_TO_SOLVER.get(dominant, "heuristic")
            dominant_history.append(dominant)

            logger.info(
                "[repair] iter %d — dominant=%s, solver=%s, loss=%.4f",
                i,
                dominant,
                solver,
                total,
            )

            # Build targeted repair prompt
            repair_prompt = build_repair_prompt(dominant, current_loss.get(dominant, total), current_prompt)

            # Run adversarial pass with the repair prompt
            try:
                adv = await asyncio.wait_for(
                    run_adversarial_pass(repair_prompt, world_state, timeout=self.timeout_per_round),
                    timeout=self.timeout_per_round * 1.2,
                )
                new_loss = adv.simulation_loss.to_dict()
            except (asyncio.TimeoutError, Exception) as exc:
                logger.warning("[repair] iter %d failed: %s", i, exc)
                new_loss = current_loss  # no change

            new_total = new_loss.get("total", total)
            delta = round(total - new_total, 4)

            iterations.append(
                RepairIteration(
                    iteration=i,
                    dominant_term=dominant,
                    solver_selected=solver,
                    repair_prompt=repair_prompt,
                    loss_before=current_loss,
                    loss_after=new_loss,
                    delta=delta,
                    converging=(delta > 0),
                )
            )

            # Stagnation check
            if delta < self.stagnation_epsilon and i > 2:
                logger.info("[repair] stagnated at iteration %d (delta=%.5f)", i, delta)
                return RepairResult(
                    converged=False,
                    stagnated=True,
                    iterations=iterations,
                    loss_initial=loss_initial,
                    loss_final=new_total,
                    final_prompt=current_prompt,
                    dominant_term_history=dominant_history,
                )

            current_loss = new_loss
            # Use the repair prompt as the next prompt only if it improved things
            if delta > 0:
                current_prompt = repair_prompt

        loss_final = current_loss.get("total", 0.0)
        converged = loss_final < self.convergence_threshold

        return RepairResult(
            converged=converged,
            stagnated=False,
            iterations=iterations,
            loss_initial=loss_initial,
            loss_final=loss_final,
            final_prompt=current_prompt,
            dominant_term_history=dominant_history,
        )

    def to_repair_telemetry(self, result: RepairResult) -> "RepairTelemetry":
        from cortex.engineering_report import RepairTelemetry

        return RepairTelemetry(
            iterations=len(result.iterations),
            files_patched=sum(1 for it in result.iterations if it.delta > 0),
            violations_found=len([it for it in result.iterations if not it.converging]),
            violations_fixed=len([it for it in result.iterations if it.converging]),
            loss_initial=round(result.loss_initial, 4),
            loss_final=round(result.loss_final, 4),
            converged=result.converged,
        )
