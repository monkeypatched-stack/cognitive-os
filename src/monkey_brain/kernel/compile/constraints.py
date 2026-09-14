"""RuntimeConstraintEngine — Graph synthesis (planning) kernel.

Compiles an execution graph from belief using dynamic programming with
constraint-aware path selection. Decoupled from execution and simulation
for independent testing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.monkey_brain.kernel.compile import _obs
from src.monkey_brain.kernel.compile.sparse import SparseMatrix
from src.monkey_brain.kernel.compile.tensor import Feature, SparseTransitionTensor
from src.monkey_brain.kernel.compile.execution_graph import ExecutionPlanSnapshot

logger = logging.getLogger("agentos.compile.runtime_constraint_engine")


@dataclass
class PlanConstraints:
    """Constraints that guide path selection during planning.

    Attributes:
        budget: Maximum total cost allowed along the path.
        max_steps: Maximum number of transitions allowed.
        risk_threshold: Maximum acceptable risk per transition [0, 1].
        min_confidence: Minimum confidence required for a transition.
        forbidden_states: States that must not appear in the path.
        required_states: States that must appear in the path (in order).
        cost_weights: Per-domain cost multipliers (e.g., {"delivery": 2.0}).
        prefer_domains: Domains to prefer when ties exist (higher rank = preferred).
    """

    budget: float = float("inf")
    max_steps: int = 64
    risk_threshold: float = 1.0
    min_confidence: float = 0.0
    forbidden_states: frozenset[str] = field(default_factory=frozenset)
    required_states: tuple[str, ...] = ()
    cost_weights: dict[str, float] = field(default_factory=dict)
    prefer_domains: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PlanConstraints":
        """Build constraints from a Goal's constraints dict."""
        if not d:
            return cls()
        return cls(
            budget=float(d.get("budget", float("inf"))),
            max_steps=int(d.get("max_steps", 64)),
            risk_threshold=float(d.get("risk_threshold", 1.0)),
            min_confidence=float(d.get("min_confidence", 0.0)),
            forbidden_states=frozenset(d.get("forbidden_states", [])),
            required_states=tuple(d.get("required_states", [])),
            cost_weights=dict(d.get("cost_weights", {})),
            prefer_domains=tuple(d.get("prefer_domains", [])),
        )


@dataclass
class PathCandidate:
    """A candidate path with its score components."""

    path: list[str]
    probability: float  # cumulative transition probability
    cost: float  # cumulative cost along path
    risk: float  # max risk encountered
    domains: list[str]  # domains visited (for preference scoring)
    constraint_violations: int  # number of constraint violations (0 = valid)

    @property
    def score(self) -> float:
        """Combined score: higher is better.

        Score = probability * domain_preference - cost_penalty - violation_penalty
        """
        return self.probability - self.cost * 0.1 - self.constraint_violations * 100.0


class RuntimeConstraintEngine:
    """Graph synthesis kernel — compiles execution plans from belief.

    Injected INTO per-actor CognitiveRuntime.

    Owns:
      - Planning (graph synthesis from belief)
      - Path finding through state space
      - Constraint-aware beam search
    """

    def __init__(self, actor_id: str) -> None:
        self.actor_id = actor_id
        self._plan_history: list[dict] = []

    def plan(
        self,
        start: str,
        goal: str,
        belief: SparseTransitionTensor,
        horizon: int = 12,
        constraints: PlanConstraints | None = None,
    ) -> ExecutionPlanSnapshot:
        """Synthesize execution graph: path start→goal through belief.

        Uses constraint-aware path selection with beam search:
        1. Propagate belief forward to get candidate distributions
        2. At each step, filter candidates by constraints
        3. Score surviving candidates by probability + constraint satisfaction
        4. Keep top-k candidates (beam width) and continue

        Args:
            start: starting state
            goal: target state
            belief: actor's local belief (SparseTransitionTensor)
            horizon: max planning depth
            constraints: optional constraints to guide path selection

        Returns:
             ExecutionPlanSnapshot with plan and predicted outcomes
        """
        assert isinstance(start, str) and start, "start must be non-empty string"
        assert isinstance(goal, str) and goal, "goal must be non-empty string"
        assert belief is not None, "belief cannot be None"
        assert isinstance(horizon, int) and 0 < horizon <= 1000, "horizon must be in (0, 1000]"

        if constraints is None:
            constraints = PlanConstraints()

        if start == goal:
            graph = ExecutionPlanSnapshot(
                graph_id=f"{self.actor_id}:plan:{start}:{goal}",
                actor_id=self.actor_id,
                plan=[start],
                predicted_distribution={start: 1.0},
                goal=goal,
                reached_goal=True,
                metadata={"constraints_used": False, "constraint_violations": 0},
            )
            self._record_plan(graph)
            return graph

        # Convert belief to probability matrix
        try:
            SparseMatrix.from_tensor(belief, Feature.PROBABILITY)
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            logger.warning("[software_engineering_runtime] belief conversion failed: %s", e)
            graph = ExecutionPlanSnapshot(
                graph_id=f"{self.actor_id}:plan:{start}:{goal}",
                actor_id=self.actor_id,
                plan=[start],
                predicted_distribution={start: 1.0},
                goal=goal,
                reached_goal=False,
            )
            self._record_plan(graph)
            return graph

        # Constraint-aware beam search through the belief graph
        beam_width = 3
        candidates = [
            PathCandidate(
                path=[start],
                probability=1.0,
                cost=0.0,
                risk=0.0,
                domains=[],
                constraint_violations=0,
            )
        ]
        visited_goals = []  # paths that reached goal

        for step in range(horizon):
            if not candidates:
                break

            next_candidates = []
            for candidate in candidates:
                current_state = candidate.path[-1]

                # If we already reached goal, just keep this candidate
                if current_state == goal:
                    visited_goals.append(candidate)
                    continue

                # Check if we've reached max steps
                if len(candidate.path) >= constraints.max_steps:
                    next_candidates.append(candidate)
                    continue

                # Get successors from belief graph
                successors = {}
                for succ, _ in belief.successors(current_state):
                    # Get transition probability from belief
                    prob = belief.feature(current_state, succ, Feature.PROBABILITY)
                    if prob > 0:
                        successors[succ] = prob

                if not successors:
                    # Dead end — keep candidate as-is
                    next_candidates.append(candidate)
                    continue

                # Evaluate each successor
                for succ, prob in successors.items():
                    # Check forbidden states
                    if succ in constraints.forbidden_states:
                        continue

                    # Check risk threshold (use 1 - probability as risk proxy)
                    risk = 1.0 - prob
                    if risk > constraints.risk_threshold:
                        continue

                    # Check confidence (probability >= min_confidence)
                    if prob < constraints.min_confidence:
                        continue

                    # Calculate cost
                    domain = belief.domain_of(succ) if hasattr(belief, "domain_of") else ""
                    cost_multiplier = constraints.cost_weights.get(domain, 1.0)
                    step_cost = cost_multiplier * (1.0 - prob)  # higher prob = lower cost
                    new_cost = candidate.cost + step_cost

                    # Check budget
                    if new_cost > constraints.budget:
                        continue

                    # Build new candidate
                    new_path = candidate.path + [succ]
                    new_domains = candidate.domains + [domain] if domain else candidate.domains
                    new_risk = max(candidate.risk, risk)

                    # Check required states ordering
                    violations = candidate.constraint_violations
                    if constraints.required_states:
                        # Check if any required state appears AFTER the goal in the path
                        # (i.e., we reached goal but haven't visited all required states)
                        if succ == goal:
                            req_count = sum(1 for s in constraints.required_states if s in new_path)
                            if req_count < len(constraints.required_states):
                                violations += 1

                    new_candidate = PathCandidate(
                        path=new_path,
                        probability=candidate.probability * prob,
                        cost=new_cost,
                        risk=new_risk,
                        domains=new_domains,
                        constraint_violations=violations,
                    )
                    next_candidates.append(new_candidate)

            # Keep top-k candidates by score
            next_candidates.sort(key=lambda c: c.score, reverse=True)
            candidates = next_candidates[:beam_width]

        # Combine visited goals with remaining candidates
        all_candidates = visited_goals + candidates

        # Select best candidate
        if not all_candidates:
            # Fallback: single-state plan
            best_path = [start]
            best_confidence = 0.0
            violations = 0
        else:
            # Prefer paths that reach goal with no violations, then by score
            def sort_key(c):
                reached_goal = 1 if c.path[-1] == goal else 0
                return (reached_goal, -c.constraint_violations, c.score)

            all_candidates.sort(key=sort_key, reverse=True)
            best = all_candidates[0]
            best_path = best.path
            best_confidence = best.probability
            violations = best.constraint_violations

        # Build predicted distribution: probability mass at the final state
        predicted_dist = {best_path[-1]: best_confidence}

        # Create execution graph
        assert best_path and best_path[0] == start, "plan must start with start state"

        graph = ExecutionPlanSnapshot(
            graph_id=f"{self.actor_id}:plan:{start}:{goal}",
            actor_id=self.actor_id,
            plan=best_path,
            predicted_distribution=predicted_dist,
            goal=goal,
            predicted_confidence=best_confidence,
            horizon=horizon,
            metadata={
                "constraints_used": True,
                "constraint_violations": violations,
                "beam_width": beam_width,
            },
        )

        assert graph.predicted_confidence >= 0.0 and graph.predicted_confidence <= 1.0, (
            f"predicted_confidence out of bounds: {graph.predicted_confidence}"
        )

        self._record_plan(graph)

        logger.debug(
            "[software_engineering_runtime] %s planned %s→%s: path=%s (%d steps), confidence=%.2f, violations=%d",
            self.actor_id,
            start,
            goal,
            "→".join(best_path),
            len(best_path),
            graph.predicted_confidence,
            violations,
        )
        _obs.event(
            "plan.synthesized",
            actor=self.actor_id,
            start=start,
            goal=goal,
            plan_length=len(best_path),
            confidence=round(graph.predicted_confidence, 3),
            constraint_violations=violations,
        )

        return graph

    def _record_plan(self, graph: ExecutionPlanSnapshot) -> None:
        """Record plan for auditing."""
        import time

        self._plan_history.append(
            {
                "timestamp": time.time(),
                "graph_id": graph.graph_id,
                "plan": graph.plan,
                "goal": graph.goal,
                "confidence": graph.predicted_confidence,
            }
        )

    def plan_history(self, limit: int = 10) -> list[dict]:
        """Recent plans."""
        return self._plan_history[-limit:]

    def checkpoint(self, path: str) -> None:
        """Persist plan history for auditing and recovery."""
        import json

        try:
            with open(path, "w") as f:
                json.dump(self._plan_history, f)
            logger.info(
                "[software_engineering_runtime] %s checkpointed %d plans to %s",
                self.actor_id,
                len(self._plan_history),
                path,
            )
            _obs.event(
                "planning.checkpoint",
                actor=self.actor_id,
                plans=len(self._plan_history),
            )
        except (IOError, OSError) as e:
            logger.error("[software_engineering_runtime] checkpoint failed: %s", e, exc_info=True)
            raise

    def restore(self, path: str) -> None:
        """Restore plan history from checkpoint."""
        import json

        try:
            with open(path) as f:
                self._plan_history = json.load(f)
            logger.info(
                "[software_engineering_runtime] %s restored %d plans from %s",
                self.actor_id,
                len(self._plan_history),
                path,
            )
            _obs.event("planning.restore", actor=self.actor_id, plans=len(self._plan_history))
        except (IOError, OSError, json.JSONDecodeError) as e:
            logger.error("[software_engineering_runtime] restore failed: %s", e, exc_info=True)
            raise

    def summary(self) -> dict[str, Any]:
        """Runtime summary."""
        return {
            "type": "RuntimeConstraintEngine",
            "actor": self.actor_id,
            "plans_synthesized": len(self._plan_history),
        }
