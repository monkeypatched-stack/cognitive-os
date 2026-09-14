"""adversarial_agents — disprove the design before it ships.

Simulation-only. Never invoked from the actual query path.

Solver Selection Constitution
------------------------------
The planner dispatches based on the **mathematical structure** of the problem,
not on the availability of an LLM.  Priority order (Rule 1, 13):

  O(1) rules → Graph algorithms → Constraint solvers → Model checkers
  → Coherence check → Monte Carlo → LLM reasoning

Problem Classification → Primary Solver → Secondary Solver (cross-validation)
-------------------------------------------------------------------------------
  graph_reachability   BFS/DFS (exact)           Monte Carlo (sanity check)
  graph_coverage       BFS coverage (exact)       LLM architecture review
  finite_state         Model checker (DFS)        Monte Carlo exploration
  constraint_sat       SAT/SMT (z3 / brute-force) LLM to explain conflicts
  architecture         Constraint propagation     LLM critique
  optimization         M/M/c queuing MC           LLM performance review
  statistical          Monte Carlo sampling       coherence distribution check
  semantic             LLM discovery              coherence concept grounding

Rules:
  Rule 2  — Never ask an LLM to solve mathematics. Use SAT/SMT/ILP/CSP.
  Rule 3  — Never ask an LLM to traverse graphs. Use BFS/DFS.
  Rule 4  — Use model checking for finite state machines.
  Rule 5  — Use Monte Carlo for huge state spaces only (exact search is preferred).
  Rule 7  — Use LLMs only for semantic problems (meaning, ambiguity, domain concepts).
  Rule 10 — Cross-validate critical assertions with ≥ 2 independent solvers.
  Rule 11 — LLMs generate hypotheses; solvers verify them.
  Rule 12 — Escalate solver strength when a weaker solver cannot decide.
  Rule 13 — Choose the least expensive solver that achieves required confidence.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections import deque
from dataclasses import dataclass, field
from itertools import product
from typing import Any, ClassVar

logger = logging.getLogger("agentos.adversarial")


# ---------------------------------------------------------------------------
# Solver Selection Constitution — encoded as a dispatch table
# ---------------------------------------------------------------------------


class ProblemType:
    """Problem classification: determines solver priority (Rules 1–14)."""

    GRAPH_REACHABILITY = "graph_reachability"  # BFS primary, MC cross-check
    GRAPH_COVERAGE = "graph_coverage"  # coverage analysis, LLM review
    FINITE_STATE = "finite_state"  # model checker, MC exploration
    CONSTRAINT_SAT = "constraint_sat"  # SAT/SMT primary, LLM explains
    ARCHITECTURE = "architecture"  # constraint propagation, LLM critique
    OPTIMIZATION = "optimization"  # M/M/c queuing, LLM trade-off
    STATISTICAL = "statistical"  # MC primary, coherence distribution check
    SEMANTIC = "semantic"  # LLM primary (only valid use), coherence grounds


# Problem type → (primary_solver, secondary_solver)
# Deterministic solvers always run before probabilistic or heuristic.
SOLVER_DISPATCH: dict[str, tuple[str, str]] = {
    ProblemType.GRAPH_REACHABILITY: ("graph", "monte_carlo"),
    ProblemType.GRAPH_COVERAGE: ("graph", "heuristic"),
    ProblemType.FINITE_STATE: ("model_checker", "monte_carlo"),
    ProblemType.CONSTRAINT_SAT: ("sat_smt", "heuristic"),
    ProblemType.ARCHITECTURE: ("constraint", "heuristic"),
    ProblemType.OPTIMIZATION: ("optimizer", "heuristic"),
    ProblemType.STATISTICAL: ("monte_carlo", "coherence"),
    ProblemType.SEMANTIC: ("heuristic", "coherence"),
}


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class AdversarialFinding:
    agent: str
    solver: str  # primary or secondary solver that produced this
    finding_type: str
    description: str
    witness: str  # concrete counter-example
    severity: str  # "critical" | "high" | "medium" | "low"
    confidence: float  # 0.0 – 1.0


@dataclass
class SimulationLoss:
    """Decomposed transition loss for the Epistemic Predictive Architecture (EPA).

    Transition function:
        f(E_t, G_t, a_t) = E_{t+1}

    where E_t = (S_t, B_t, A_t, M_t) and G_t = goal state (conditioning variable).

    EPA loss (4 mathematical terms):
        L_E = L_S + L_B + L_A + L_M

    Empirical decomposition (6 measurable terms):
        L_S (world)      = state
        L_B (belief)     = (knowledge + constraint) / 2
        L_A (affordance) = (affordance + action) / 2
        L_M (mesh)       = mesh  [initialised to 0; updated by MeshOptimizer]

    L_goal is NOT a loss term — G_t is a conditioning input to the transition.
    Goal progress is measured by GoalState.progress(world_state).

    Domain-agnostic: robotics, enterprise, manufacturing, OS, digital twins,
    financial markets, multi-agent systems. Only the encoding of S changes.
    """

    # ── 6 empirical terms (implementation decomposition) ─────────────────────
    state: float = 0.0  # L_state:     S_{t+1} prediction error
    action: float = 0.0  # L_action:    policy prediction (contributes to L_A)
    affordance: float = 0.0  # L_affordance: ΔA prediction error (contributes to L_A)
    constraint: float = 0.0  # L_constraint: invariant violations (contributes to L_B)
    goal: float = 0.0  # L_goal:      legacy term; prefer GoalState.progress()
    knowledge: float = 0.0  # L_knowledge: epistemic uncertainty (contributes to L_B)
    mesh: float = 0.0  # L_mesh:      agent organisation prediction error (NEW)
    total: float = 0.0

    # Weights (α…η sum to 1.0) — tune per deployment
    _ALPHA: ClassVar[float] = 0.25  # state
    _BETA: ClassVar[float] = 0.15  # action
    _GAMMA: ClassVar[float] = 0.20  # affordance
    _DELTA: ClassVar[float] = 0.15  # constraint
    _EPSILON: ClassVar[float] = 0.05  # goal (reduced — G_t now conditions the transition)
    _ZETA: ClassVar[float] = 0.12  # knowledge
    _ETA: ClassVar[float] = 0.08  # mesh (new term)

    # ── 4 EPA projections (mathematical formalism) ────────────────────────────

    @property
    def l_world(self) -> float:
        """L_S — world state prediction error."""
        return self.state

    @property
    def l_belief(self) -> float:
        """L_B — belief state prediction error.

        Combines knowledge (epistemic confidence) and constraint (consistency)
        since both measure how well the system's beliefs match reality.
        """
        return round((self.knowledge + self.constraint) / 2.0, 4)

    @property
    def l_affordance_combined(self) -> float:
        """L_A — affordance prediction error.

        Combines action (policy loss) and affordance (ΔA = A_{t+1} − A_t).
        """
        return round((self.action + self.affordance) / 2.0, 4)

    @property
    def l_mesh(self) -> float:
        """L_M — agent mesh prediction error."""
        return self.mesh

    @property
    def l_epa(self) -> float:
        """L_E = L_S + L_B + L_A + L_M (un-weighted EPA total)."""
        return round(self.l_world + self.l_belief + self.l_affordance_combined + self.l_mesh, 4)

    def compute_total(self) -> "SimulationLoss":
        self.total = round(
            self._ALPHA * self.state
            + self._BETA * self.action
            + self._GAMMA * self.affordance
            + self._DELTA * self.constraint
            + self._EPSILON * self.goal
            + self._ZETA * self.knowledge
            + self._ETA * self.mesh,
            4,
        )
        return self

    @classmethod
    def pool(cls, losses: list["SimulationLoss"]) -> "SimulationLoss":
        """Component-wise max — worst-case across all agents."""
        if not losses:
            return cls()
        return cls(
            state=max(l.state for l in losses),
            action=max(l.action for l in losses),
            affordance=max(l.affordance for l in losses),
            constraint=max(l.constraint for l in losses),
            goal=max(l.goal for l in losses),
            knowledge=max(l.knowledge for l in losses),
            mesh=max(l.mesh for l in losses),
        ).compute_total()

    def to_dict(self) -> dict[str, float]:
        return {
            "state": self.state,
            "action": self.action,
            "affordance": self.affordance,
            "constraint": self.constraint,
            "goal": self.goal,
            "knowledge": self.knowledge,
            "mesh": self.mesh,
            "total": self.total,
        }

    def to_epa_dict(self) -> dict[str, float]:
        """The 4-term EPA view: L_E = L_S + L_B + L_A + L_M."""
        return {
            "L_S": self.l_world,
            "L_B": self.l_belief,
            "L_A": self.l_affordance_combined,
            "L_M": self.l_mesh,
            "L_E": self.l_epa,
        }


@dataclass
class AdversarialResult:
    findings: list[AdversarialFinding] = field(default_factory=list)
    agents_run: list[str] = field(default_factory=list)
    solvers_run: list[str] = field(default_factory=list)
    confidence_delta: float = 0.0
    simulation_loss: SimulationLoss = field(default_factory=SimulationLoss)
    summary: str = ""


# ---------------------------------------------------------------------------
# Domain model
# ---------------------------------------------------------------------------

_STATUS_MACHINES: dict[str, dict[str, list[str]]] = {
    "pharmaceutical_machines": {
        "Operational": ["Maintenance", "Calibration", "Decommissioned"],
        "Maintenance": ["Operational", "Decommissioned"],
        "Calibration": ["Operational", "Maintenance"],
        "Decommissioned": [],
    },
    "industrial_lines": {
        "Running": ["Stopped", "Under Maintenance"],
        "Stopped": ["Running", "Under Maintenance"],
        "Under Maintenance": ["Running", "Stopped"],
    },
    "work_orders": {
        "Open": ["In Progress", "Cancelled"],
        "In Progress": ["Completed", "On Hold"],
        "On Hold": ["In Progress", "Cancelled"],
        "Completed": [],
        "Cancelled": [],
    },
    "production_batches": {
        "Planned": ["In Progress"],
        "In Progress": ["Under QA Review", "Failed"],
        "Under QA Review": ["Released", "Failed"],
        "Released": [],
        "Failed": [],
    },
}

# States that are *intended* to have no outgoing transitions.
_TERMINAL_STATES: frozenset[str] = frozenset(
    {
        "decommissioned",
        "released",
        "completed",
        "cancelled",
        "failed",
        "closed",
        "archived",
        "retired",
    }
)

# Cross-entity invariants: (type_A, bad_status_A, type_B, bad_status_B, description)
_CROSS_INVARIANTS: list[tuple[str, str, str, str, str]] = [
    (
        "production_batches",
        "In Progress",
        "pharmaceutical_machines",
        "Decommissioned",
        "Batch in production but its machine is decommissioned",
    ),
    (
        "production_batches",
        "Released",
        "industrial_lines",
        "Stopped",
        "Batch released while its production line is stopped",
    ),
    (
        "work_orders",
        "In Progress",
        "pharmaceutical_machines",
        "Maintenance",
        "Work order active while target machine is under maintenance",
    ),
]


# ---------------------------------------------------------------------------
# Solver: Heuristic (LLM) — Rule 7: semantic problems only
# ---------------------------------------------------------------------------


async def _llm_call(prompt: str, system: str, timeout: float = 60.0) -> str:
    try:
        import httpx

        payload = {
            "model": "qwen2.5-coder:7b",
            "prompt": f"{system}\n\n{prompt}",
            "stream": False,
            "options": {"temperature": 0.7, "num_predict": 512},
        }
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post("http://localhost:11434/api/generate", json=payload)
            if r.status_code == 200:
                return r.json().get("response", "")
    except Exception as exc:
        logger.debug("[adversarial] ollama call failed: %s", exc)
    try:
        import anthropic, os

        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if key:
            client = anthropic.Anthropic(api_key=key)
            msg = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text
    except Exception as exc:
        logger.debug("[adversarial] anthropic fallback failed: %s", exc)
    return ""


# ---------------------------------------------------------------------------
# Solver: Graph (BFS/DFS) — Rules 3, 4: reachability and coverage
# ---------------------------------------------------------------------------


def _bfs_reachable(entity_type: str) -> set[str]:
    """BFS from all source states (no incoming edges) — deterministic, complete."""
    machine = _STATUS_MACHINES.get(entity_type, {})
    if not machine:
        return set()
    all_states = set(machine)
    has_incoming = {t for targets in machine.values() for t in targets}
    sources = all_states - has_incoming or {next(iter(all_states))}
    visited: set[str] = set()
    q: deque[str] = deque(sources)
    while q:
        s = q.popleft()
        if s in visited:
            continue
        visited.add(s)
        q.extend(t for t in machine.get(s, []) if t not in visited)
    return visited


def _graph_unreachable(entity_type: str) -> set[str]:
    return set(_STATUS_MACHINES.get(entity_type, {})) - _bfs_reachable(entity_type)


def _graph_missing_transitions(world_state: dict[str, Any]) -> list[dict[str, Any]]:
    """Transitions defined in the machine but whose target state is never observed."""
    entities = world_state.get("entities", {})
    findings: list[dict[str, Any]] = []
    for etype, machine in _STATUS_MACHINES.items():
        if etype not in entities:
            continue
        observed = set(entities[etype].get("statuses", {}).keys())
        for src, targets in machine.items():
            for tgt in targets:
                if tgt not in observed:
                    findings.append(
                        {
                            "entity": etype,
                            "transition": f"{src} → {tgt}",
                            "target_state": tgt,
                            "description": (
                                f"{etype}: transition '{src} → {tgt}' may never fire — "
                                f"target state '{tgt}' has 0 observed records"
                            ),
                        }
                    )
    return findings


# ---------------------------------------------------------------------------
# Solver: Model checker — Rule 4: FSM deadlock / livelock detection
# ---------------------------------------------------------------------------


def _model_check_deadlocks() -> list[dict[str, Any]]:
    """
    Single-entity: sink states that are not intentional terminals.
    Multi-entity:  pairs where both entities sink simultaneously.
    Livelock:      DFS cycle detection.
    """
    findings: list[dict[str, Any]] = []

    for etype, machine in _STATUS_MACHINES.items():
        for state, transitions in machine.items():
            if not transitions and state.lower() not in _TERMINAL_STATES:
                findings.append(
                    {
                        "entity": etype,
                        "state": state,
                        "kind": "unintended_sink",
                        "description": (
                            f"{etype}: state '{state}' has no outgoing transitions "
                            f"and is not a recognised terminal — potential deadlock"
                        ),
                    }
                )

    for et_a, bad_a, et_b, bad_b, desc in _CROSS_INVARIANTS:
        a_exits = _STATUS_MACHINES.get(et_a, {}).get(bad_a, [])
        b_exits = _STATUS_MACHINES.get(et_b, {}).get(bad_b, [])
        if not a_exits and not b_exits:
            findings.append(
                {
                    "entity_a": et_a,
                    "state_a": bad_a,
                    "entity_b": et_b,
                    "state_b": bad_b,
                    "kind": "multi_entity_deadlock",
                    "description": (
                        f"Deadlock: {et_a}='{bad_a}' and {et_b}='{bad_b}' — "
                        f"neither can exit, invariant '{desc}' traps the system"
                    ),
                }
            )

    for etype, machine in _STATUS_MACHINES.items():
        visited: set[str] = set()
        rec_stack: set[str] = set()

        def _dfs_cycle(state: str) -> list[str] | None:
            visited.add(state)
            rec_stack.add(state)
            for nxt in machine.get(state, []):
                if nxt not in visited:
                    result = _dfs_cycle(nxt)
                    if result is not None:
                        return [state] + result
                elif nxt in rec_stack:
                    return [state, nxt]
            rec_stack.discard(state)
            return None

        for start in machine:
            if start not in visited:
                cycle = _dfs_cycle(start)
                if cycle:
                    findings.append(
                        {
                            "entity": etype,
                            "kind": "livelock_cycle",
                            "cycle": cycle,
                            "description": (
                                f"{etype}: state cycle {' → '.join(cycle)} — system can spin without making progress"
                            ),
                        }
                    )
                    break

    return findings


# ---------------------------------------------------------------------------
# Solver: SAT/SMT — Rule 2: contradictory constraints
# ---------------------------------------------------------------------------


def _sat_check() -> list[dict[str, Any]]:
    """Uses z3 when available; falls back to exhaustive brute-force."""
    try:
        return _z3_check()
    except ImportError:
        return _brute_force_sat()


def _z3_check() -> list[dict[str, Any]]:
    import z3

    findings: list[dict[str, Any]] = []
    state_lists: dict[str, list[str]] = {et: list(m) for et, m in _STATUS_MACHINES.items()}
    vars_: dict[str, Any] = {et: z3.Int(et) for et in state_lists}
    solver = z3.Solver()
    for et, states in state_lists.items():
        solver.add(vars_[et] >= 0, vars_[et] < len(states))
    constraint_exprs = []
    for et_a, bad_a, et_b, bad_b, desc in _CROSS_INVARIANTS:
        if et_a not in state_lists or et_b not in state_lists:
            continue
        ia = state_lists[et_a].index(bad_a) if bad_a in state_lists[et_a] else -1
        ib = state_lists[et_b].index(bad_b) if bad_b in state_lists[et_b] else -1
        if ia < 0 or ib < 0:
            continue
        expr = z3.Not(z3.And(vars_[et_a] == ia, vars_[et_b] == ib))
        constraint_exprs.append((desc, expr))
        solver.add(expr)
    result = solver.check()
    if result == z3.unsat:
        findings.append(
            {
                "kind": "over_constrained",
                "description": "SAT: all invariants combined leave no valid global state — system is over-constrained",
                "severity": "critical",
            }
        )
    else:
        for i in range(len(constraint_exprs)):
            for j in range(i + 1, len(constraint_exprs)):
                desc_a, _ = constraint_exprs[i]
                desc_b, _ = constraint_exprs[j]
                s2 = z3.Solver()
                for et, bad in [
                    (_CROSS_INVARIANTS[i][0], _CROSS_INVARIANTS[i][1]),
                    (_CROSS_INVARIANTS[i][2], _CROSS_INVARIANTS[i][3]),
                    (_CROSS_INVARIANTS[j][0], _CROSS_INVARIANTS[j][1]),
                    (_CROSS_INVARIANTS[j][2], _CROSS_INVARIANTS[j][3]),
                ]:
                    if et in state_lists and bad in state_lists[et]:
                        s2.add(vars_[et] == state_lists[et].index(bad))
                if s2.check() == z3.unsat:
                    findings.append(
                        {
                            "kind": "contradictory_pair",
                            "inv_a": desc_a,
                            "inv_b": desc_b,
                            "description": f"SAT: invariants cannot both be enforced — '{desc_a}' contradicts '{desc_b}'",
                            "severity": "high",
                        }
                    )
    return findings


def _brute_force_sat() -> list[dict[str, Any]]:
    """Exhaustive Cartesian product — fallback when z3 is absent."""
    findings: list[dict[str, Any]] = []
    state_lists = {et: list(m) for et, m in _STATUS_MACHINES.items()}
    ets = list(state_lists)
    states_product = list(product(*[state_lists[et] for et in ets]))
    valid_count = 0
    for combo in states_product:
        assignment = dict(zip(ets, combo))
        ok = all(
            not (assignment.get(et_a) == bad_a and assignment.get(et_b) == bad_b)
            for et_a, bad_a, et_b, bad_b, _ in _CROSS_INVARIANTS
        )
        if ok:
            valid_count += 1
    if valid_count == 0:
        findings.append(
            {
                "kind": "over_constrained",
                "description": (
                    f"Brute-force SAT ({len(states_product)} assignments checked): "
                    f"no valid global state exists — system is over-constrained"
                ),
                "severity": "critical",
            }
        )
    for i, (et_a_i, bad_a_i, et_b_i, bad_b_i, desc_i) in enumerate(_CROSS_INVARIANTS):
        for j, (et_a_j, bad_a_j, et_b_j, bad_b_j, desc_j) in enumerate(_CROSS_INVARIANTS):
            if j <= i:
                continue
            required = {
                et_a_i: bad_a_i,
                et_b_i: bad_b_i,
                et_a_j: bad_a_j,
                et_b_j: bad_b_j,
            }
            conflict = any(required.get(et) and required[et] != required.get(et) for et in required)
            if conflict:
                findings.append(
                    {
                        "kind": "contradictory_pair",
                        "inv_a": desc_i,
                        "inv_b": desc_j,
                        "description": f"Brute-force SAT: '{desc_i}' conflicts with '{desc_j}'",
                        "severity": "high",
                    }
                )
    return findings


# ---------------------------------------------------------------------------
# Solver: Constraint — Rule 8: aggregate and referential consistency
# ---------------------------------------------------------------------------


def _constraint_check(world_state: dict[str, Any]) -> list[dict[str, Any]]:
    """Constraint propagation over counts and cross-collection references. No external deps."""
    entities = world_state.get("entities", {})
    findings: list[dict[str, Any]] = []
    for col, info in entities.items():
        total = info.get("count", 0)
        statuses = info.get("statuses") or {}
        status_sum = sum(statuses.values())
        if total > 0 and status_sum != total:
            findings.append(
                {
                    "collection": col,
                    "kind": "count_mismatch",
                    "total": total,
                    "status_sum": status_sum,
                    "description": (
                        f"{col}: {total} total records but status counts sum to "
                        f"{status_sum} (gap={total - status_sum}) — data may be inconsistent"
                    ),
                }
            )
        machine = _STATUS_MACHINES.get(col, {})
        for state in machine:
            if total > 0 and state not in statuses:
                findings.append(
                    {
                        "collection": col,
                        "kind": "unobserved_state",
                        "state": state,
                        "description": (
                            f"{col}: state '{state}' is defined but has 0 observed "
                            f"records (total={total}) — dead branch or bootstrap gap"
                        ),
                    }
                )
    for et_a, _, et_b, _, desc in _CROSS_INVARIANTS:
        count_a = entities.get(et_a, {}).get("count", 0)
        count_b = entities.get(et_b, {}).get("count", 0)
        if count_a > 0 and count_b == 0:
            findings.append(
                {
                    "kind": "referential_gap",
                    "entity_a": et_a,
                    "count_a": count_a,
                    "entity_b": et_b,
                    "count_b": count_b,
                    "description": (
                        f"Referential gap: {count_a} {et_a} records but 0 {et_b} "
                        f"records — invariant '{desc}' can never be checked"
                    ),
                }
            )
    return findings


# ---------------------------------------------------------------------------
# L_knowledge — delegate to epistemic.py
#
# epistemic.EpistemicState owns the full K = {k_1,…,k_n} model including
# multimodal fusion, retrieval policy, and K_t → K_{t+1} evolution.
# Here we just compute the scalar L_knowledge for SimulationLoss injection.
# ---------------------------------------------------------------------------

# Actions that update epistemic state k_{t+1} — reduce L_knowledge proportionally
_EPISTEMIC_BENEFIT: dict[str, float] = {
    "test": 0.05,
    "verify": 0.06,
    "validate": 0.07,
    "simulate": 0.06,
    "audit": 0.08,
    "inspect": 0.05,
    "measure": 0.04,
    "compile": 0.03,
    "release": 0.02,
}


def _l_knowledge(world_state: dict[str, Any], action_verb: str | None = None) -> float:
    """L_knowledge = 1 − C_knowledge via EpistemicState multimodal fusion.

    Epistemic actions (test, verify, audit …) reduce the raw loss — the act of
    running them generates k_{t+1} confidence regardless of the result.
    Returns 0.0 when no knowledge context is present.
    """
    from cortex.epistemic import (
        EpistemicState,
    )  # local — avoids circular import at module level

    epistemic = EpistemicState.from_world_state(world_state)
    if not epistemic.items:
        return 0.0
    raw_loss = epistemic.knowledge_loss()
    benefit = _EPISTEMIC_BENEFIT.get(action_verb or "", 0.0)
    return round(max(0.0, raw_loss - benefit), 4)


# ---------------------------------------------------------------------------
# Solver: Coherence — generalized simulation loss over the transition function
#
# Models T:(s_t, k_t, a_t) → (s_{t+1}, A_{t+1}, k_{t+1})
#
# Six loss terms (domain-agnostic — applies to robotics, enterprise, OS, etc.):
#   L_state:     next world-state prediction error (embedding residual)
#   L_action:    next-action prediction error — are current states compatible?
#   L_affordance: action-space change error — does action expand/contract ΔA correctly?
#   L_constraint: invariant/DDD/safety violations
#   L_goal:      distance from goal state (terminal state proximity)
#   L_knowledge:  epistemic uncertainty of supporting knowledge packs
#
# No external deps — character n-gram hash as embedding, cosine similarity.
# ---------------------------------------------------------------------------

# Action model: verb → {req, next, gain, lose} where all sets contain state names / verb names
_ACTION_MODEL: dict[str, dict[str, set[str]]] = {
    "create": {
        "req": set(),
        "next": {"Open", "Planned"},
        "gain": {"assign", "cancel", "update", "schedule"},
        "lose": set(),
    },
    "assign": {
        "req": {"Open", "Planned"},
        "next": {"In Progress"},
        "gain": {"start", "complete", "reassign"},
        "lose": {"assign", "delete"},
    },
    "start": {
        "req": {"Planned", "Open", "Stopped", "On Hold"},
        "next": {"In Progress", "Running"},
        "gain": {"stop", "complete", "pause"},
        "lose": {"start", "delete"},
    },
    "complete": {
        "req": {"In Progress", "Running", "Calibration"},
        "next": {"Completed"},
        "gain": {"archive", "invoice", "review"},
        "lose": {"complete", "assign", "start", "stop"},
    },
    "cancel": {
        "req": {"Open", "Planned", "In Progress", "On Hold"},
        "next": {"Cancelled"},
        "gain": {"delete"},
        "lose": {"assign", "complete", "start"},
    },
    "stop": {
        "req": {"In Progress", "Running"},
        "next": {"Stopped", "On Hold"},
        "gain": {"start", "cancel"},
        "lose": {"stop", "complete"},
    },
    "release": {
        "req": {"Under QA Review"},
        "next": {"Released"},
        "gain": {"deploy", "archive"},
        "lose": {"release", "test"},
    },
    "maintain": {
        "req": {"Operational", "Running", "Stopped"},
        "next": {"Maintenance", "Under Maintenance"},
        "gain": {"repair", "calibrate", "restore"},
        "lose": {"maintain", "operate"},
    },
    "deploy": {
        "req": {"Released"},
        "next": {"Operational", "Running"},
        "gain": {"maintain", "monitor", "scale"},
        "lose": {"deploy", "release"},
    },
    "generate": {
        "req": set(),
        "next": set(),
        "gain": {"compile", "test", "deploy"},
        "lose": set(),
    },
    "seed": {
        "req": set(),
        "next": set(),
        "gain": {"query", "update", "delete"},
        "lose": set(),
    },
}

_TERMINAL_STATES = {
    "Completed",
    "Released",
    "Operational",
    "Closed",
    "Done",
    "Archived",
    "Deployed",
}
_INITIAL_STATES = {"Open", "Planned", "Draft", "Pending", "New", "Created"}


def _merge_capability_registry() -> None:
    """Merge CapabilityGraph action model into _ACTION_MODEL.

    Called lazily on first use.  Graph capabilities extend the static model
    without overwriting it — entries are unioned with existing verbs.
    Imports from cerebellum.graph (the canonical location).
    """
    try:
        from cerebellum.graph import CapabilityGraph

        # Module-level graph is populated by the application at startup.
        # Access via a well-known module attribute if set.
        import cerebellum.graph as _cg

        graph: CapabilityGraph | None = getattr(_cg, "_GLOBAL_GRAPH", None)
        if graph is None or not graph._descriptors:
            return
        registry_model = graph.to_action_model()
        for verb, entry in registry_model.items():
            if verb not in _ACTION_MODEL:
                _ACTION_MODEL[verb] = entry
            else:
                existing = _ACTION_MODEL[verb]
                existing["req"] = existing["req"] | entry["req"]
                existing["next"] = existing["next"] | entry["next"]
                existing["gain"] = existing["gain"] | entry["gain"]
                existing["lose"] = existing["lose"] | entry["lose"]
    except Exception as e:
        logger.debug("Graph merge skipped (not populated): %s", e)


def _extract_action_verb(prompt: str) -> str | None:
    _merge_capability_registry()
    p = prompt.lower()
    for verb in _ACTION_MODEL:
        if verb in p:
            return verb
    return None


def _dominant_state(info: dict) -> str | None:
    statuses = info.get("statuses") or {}
    if not statuses:
        return None
    return max(statuses, key=statuses.get)  # type: ignore[arg-type]


def _l_action(action_verb: str | None, entities: dict) -> float:
    """L_action: fraction of entities whose current state forbids the proposed action."""
    if action_verb is None:
        return 0.0
    model = _ACTION_MODEL.get(action_verb, {})
    required = model.get("req", set())
    if not required:
        return 0.0  # action valid from any state
    invalid = sum(1 for info in entities.values() if (dom := _dominant_state(info)) is not None and dom not in required)
    return round(invalid / max(len(entities), 1), 4)


def _l_affordance(action_verb: str | None, entities: dict) -> float:
    """L_affordance: error in predicted ΔA = A_{t+1} − A_t.

    For each entity, derive current affordances and projected affordances after
    the action, then measure how far the observed change is from the model's
    expected change (gain/lose sets).
    """
    if action_verb is None or action_verb not in _ACTION_MODEL:
        return 0.0

    model = _ACTION_MODEL[action_verb]
    expected_gain: set[str] = model.get("gain", set())
    expected_lose: set[str] = model.get("lose", set())

    # Current affordances across all entities (union)
    current_aff: set[str] = set()
    for info in entities.values():
        dom = _dominant_state(info)
        for verb, vm in _ACTION_MODEL.items():
            req = vm.get("req", set())
            if not req or (dom is not None and dom in req):
                current_aff.add(verb)

    # Project one step: replace dominant states with action's next states
    next_states = model.get("next", set())
    projected_aff: set[str] = set()
    if next_states:
        proj_dom = next(iter(next_states))
        for verb, vm in _ACTION_MODEL.items():
            req = vm.get("req", set())
            if not req or proj_dom in req:
                projected_aff.add(verb)
    else:
        projected_aff = current_aff.copy()

    actual_gain = projected_aff - current_aff
    actual_lose = current_aff - projected_aff

    gain_err = len(expected_gain.symmetric_difference(actual_gain)) / max(len(expected_gain | actual_gain), 1)
    lose_err = len(expected_lose.symmetric_difference(actual_lose)) / max(len(expected_lose | actual_lose), 1)
    return round((gain_err + lose_err) / 2, 4)


def _l_goal(entities: dict) -> float:
    """L_goal: distance from goal state — fraction of records in initial/stuck states."""
    terminal = initial = total = 0
    for info in entities.values():
        for state, cnt in (info.get("statuses") or {}).items():
            total += cnt
            if state in _TERMINAL_STATES:
                terminal += cnt
            elif state in _INITIAL_STATES:
                initial += cnt
    if total == 0:
        return 0.5
    completion = terminal / total
    stuck = initial / total
    return round(max(0.0, stuck * 0.6 + (1.0 - completion) * 0.4), 4)


def _jepa_check(world_state: dict[str, Any], prompt: str = "") -> tuple[list[dict[str, Any]], SimulationLoss]:
    """JEPA-based simulation loss check — delegates to coherence check.

    Returns (findings, SimulationLoss) matching the interface expected by mesh.py.
    """
    return _coherence_check(world_state, prompt)


def _coherence_check(world_state: dict[str, Any], prompt: str = "") -> tuple[list[dict[str, Any]], SimulationLoss]:
    """Generalized simulation loss over the transition function T:(s,a)→(s',A',C',R').

    Returns (findings, SimulationLoss) where SimulationLoss decomposes prediction
    error across five independent dimensions.  High total → world model is incoherent.
    """
    entities = world_state.get("entities", {})
    if len(entities) < 2:
        return [], SimulationLoss()

    _KNOWN_STATUSES = [
        "Operational",
        "Maintenance",
        "Calibration",
        "Decommissioned",
        "Running",
        "Stopped",
        "Under Maintenance",
        "In Progress",
        "Completed",
        "On Hold",
        "Open",
        "Cancelled",
        "Planned",
        "Under QA Review",
        "Released",
        "Failed",
    ]

    def _embed(info: dict) -> list[float]:
        total = max(info.get("count", 1), 1)
        statuses = info.get("statuses") or {}
        vec = [statuses.get(s, 0) / total for s in _KNOWN_STATUSES]
        vec.append(info.get("count", 0) / total)
        return vec

    def _cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(x * x for x in b) ** 0.5
        return dot / (na * nb + 1e-8)

    cols = list(entities)
    embeddings = {col: _embed(entities[col]) for col in cols}
    findings: list[dict[str, Any]] = []
    residuals: list[float] = []
    duplicate_pairs = 0
    total_pairs = max(len(cols) * (len(cols) - 1) // 2, 1)

    # 1. Semantic duplicates — cosine > 0.95
    for i, col_a in enumerate(cols):
        for col_b in cols[i + 1 :]:
            sim = _cosine(embeddings[col_a], embeddings[col_b])
            if sim > 0.95:
                duplicate_pairs += 1
                findings.append(
                    {
                        "kind": "semantic_duplicate",
                        "entity_a": col_a,
                        "entity_b": col_b,
                        "similarity": round(sim, 3),
                        "description": (
                            f"'{col_a}' and '{col_b}' have nearly identical "
                            f"status-distribution embeddings (cosine={sim:.3f})"
                        ),
                    }
                )

    # 2. Leave-one-out prediction residuals → L_state
    dim = len(next(iter(embeddings.values())))
    for col in cols:
        if len(cols) >= 3:
            others = [embeddings[c] for c in cols if c != col]
            mean_ctx = [sum(v[i] for v in others) / len(others) for i in range(dim)]
        else:
            other = embeddings[[c for c in cols if c != col][0]]
            mean_ctx = other
        residual = sum((t - m) ** 2 for t, m in zip(embeddings[col], mean_ctx)) ** 0.5
        residuals.append(residual)
        if residual > 0.40:
            findings.append(
                {
                    "kind": "high_prediction_error",
                    "entity": col,
                    "residual": round(residual, 3),
                    "description": (
                        f"'{col}' state distribution deviates from entity-space context (residual={residual:.3f})"
                    ),
                }
            )

    # ── Five-component SimulationLoss ─────────────────────────────────────────
    max_residual = (dim + 1) ** 0.5 or 1.0
    mean_pred_error = (sum(residuals) / len(residuals)) / max_residual if residuals else 0.0
    duplicate_density = duplicate_pairs / total_pairs

    action_verb = _extract_action_verb(prompt)

    loss = SimulationLoss(
        state=round(min(1.0, mean_pred_error + duplicate_density * 0.3), 4),
        action=_l_action(action_verb, entities),
        affordance=_l_affordance(action_verb, entities),
        constraint=round(
            min(
                1.0,
                duplicate_density * 0.7
                + (len([f for f in findings if f.get("kind") == "high_prediction_error"]) / max(len(entities), 1))
                * 0.3,
            ),
            4,
        ),
        goal=_l_goal(entities),
    ).compute_total()

    return findings, loss


# ---------------------------------------------------------------------------
# Solver: Optimizer — M/M/c queuing MC for performance trade-offs (Rule 5)
# ---------------------------------------------------------------------------


def _mc_performance(world_state: dict[str, Any], trials: int = 400) -> dict[str, Any]:
    """M/M/c queuing model with Monte Carlo perturbation."""
    entities = world_state.get("entities", {})

    def _count(col: str, status: str | None = None) -> int:
        info = entities.get(col, {})
        if status is None:
            return info.get("count", 0)
        return (info.get("statuses") or {}).get(status, 0)

    machines_total = _count("pharmaceutical_machines") or 1
    lines_total = _count("industrial_lines") or 1
    machines_active = _count("pharmaceutical_machines", "Operational")
    lines_active = _count("industrial_lines", "Running")
    demand = _count("work_orders", "In Progress") + _count("production_batches", "In Progress")

    capacity = machines_total + lines_total
    arrival_rate = demand / capacity
    service_rate = (machines_active + lines_active) / capacity

    utilisations: list[float] = []
    for _ in range(trials):
        ar = arrival_rate * random.uniform(0.6, 1.6)
        sr = max(service_rate * random.uniform(0.7, 1.3), 1e-6)
        utilisations.append(ar / sr)
    utilisations.sort()

    avg_u = sum(utilisations) / len(utilisations)
    p95_u = utilisations[int(0.95 * len(utilisations))]
    p99_u = utilisations[int(0.99 * len(utilisations))]

    bottleneck = None
    if machines_active < 0.5 * machines_total:
        bottleneck = "pharmaceutical_machines (< 50% operational)"
    elif lines_active < 0.5 * lines_total:
        bottleneck = "industrial_lines (< 50% running)"

    saturation_risk = "critical" if p99_u > 1.5 else "high" if p99_u > 1.0 else "medium" if p95_u > 0.85 else "low"
    return {
        "avg_utilisation": round(avg_u, 3),
        "p95_utilisation": round(p95_u, 3),
        "p99_utilisation": round(p99_u, 3),
        "machines_utilisation": round(machines_active / machines_total, 3),
        "lines_utilisation": round(lines_active / lines_total, 3),
        "bottleneck": bottleneck,
        "saturation_risk": saturation_risk,
        "trials": trials,
    }


# ---------------------------------------------------------------------------
# Solver: Monte Carlo — random walk + invariant sampling (Rule 5)
# ---------------------------------------------------------------------------


def _random_walk(entity_type: str, steps: int = 6) -> list[str]:
    machine = _STATUS_MACHINES.get(entity_type, {})
    if not machine:
        return []
    current = random.choice(list(machine))
    path = [current]
    for _ in range(steps):
        nexts = machine.get(current, [])
        if not nexts:
            break
        current = random.choice(nexts)
        path.append(current)
    return path


def _mc_invariant_violations(world_state: dict[str, Any], trials: int = 300) -> list[dict[str, Any]]:
    entities = world_state.get("entities", {})
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for _ in range(trials):
        scenario = {
            et: random.choice(list((info.get("statuses") or {}).keys() or list(_STATUS_MACHINES.get(et, {}).keys())))
            for et, info in entities.items()
            if (info.get("statuses") or _STATUS_MACHINES.get(et))
        }
        for et_a, bad_a, et_b, bad_b, desc in _CROSS_INVARIANTS:
            if scenario.get(et_a) == bad_a and scenario.get(et_b) == bad_b:
                if desc not in seen:
                    seen.add(desc)
                    unique.append({"invariant": desc, "scenario": {et_a: bad_a, et_b: bad_b}})
    return unique


# ---------------------------------------------------------------------------
# Dispatch layer — runs primary then secondary per SOLVER_DISPATCH (Rule 10)
# ---------------------------------------------------------------------------


async def _run_heuristic(
    agent: "_AdversarialAgent",
    prompt: str,
    world_state: dict[str, Any],
) -> list[AdversarialFinding]:
    """LLM secondary solver — semantic discovery only (Rule 7, Rule 11)."""
    entity_summary = _summarise_entities(world_state)
    raw = await _llm_call(
        f"System:\n{prompt}\n\nEntities:\n{entity_summary}\n\n{agent._heuristic_question}",
        agent._heuristic_system,
    )
    if raw and "NO_FINDING" not in raw and len(raw) > 30:
        return [
            AdversarialFinding(
                agent=agent.name,
                solver="heuristic",
                finding_type=agent.finding_type,
                description=f"LLM: {agent._heuristic_description}",
                witness=raw[:600],
                severity=_infer_severity(raw),
                confidence=0.55,
            )
        ]
    return []


def _run_mc_cross_validate(
    agent: "_AdversarialAgent",
    world_state: dict[str, Any],
) -> list[AdversarialFinding]:
    """MC cross-validation: random walks cross-check graph reachability (Rule 10)."""
    findings = []
    for etype in _STATUS_MACHINES:
        if etype not in world_state.get("entities", {}):
            continue
        unreachable = _graph_unreachable(etype)
        for _ in range(50):
            path = _random_walk(etype, steps=8)
            for state in path:
                if state in unreachable:
                    findings.append(
                        AdversarialFinding(
                            agent=agent.name,
                            solver="monte_carlo",
                            finding_type=agent.finding_type,
                            description=(
                                f"MC cross-validation: '{state}' reached via random walk "
                                f"but BFS marks it unreachable — state machine model may be incomplete"
                            ),
                            witness=f"entity={etype} path={path}",
                            severity="low",
                            confidence=0.40,
                        )
                    )
                    break
    return findings


def _run_coherence_secondary(
    agent: "_AdversarialAgent",
    prompt: str,
    world_state: dict[str, Any],
) -> tuple[list[AdversarialFinding], SimulationLoss]:
    """Coherence secondary: five-component simulation loss + embedding-space findings."""
    coherence_findings, sim_loss = _coherence_check(world_state, prompt)
    sev_map = {
        "semantic_duplicate": ("medium", 0.70),
        "high_prediction_error": ("medium", 0.65),
    }
    findings = [
        AdversarialFinding(
            agent=agent.name,
            solver="coherence",
            finding_type=agent.finding_type,
            description=f["description"],
            witness=json.dumps({k: v for k, v in f.items() if k != "description"}),
            severity=sev_map.get(f["kind"], ("low", 0.55))[0],
            confidence=sev_map.get(f["kind"], ("low", 0.55))[1],
        )
        for f in coherence_findings
    ]
    return findings, sim_loss


async def _dispatch_secondary(
    solver: str,
    agent: "_AdversarialAgent",
    prompt: str,
    world_state: dict[str, Any],
) -> tuple[list[AdversarialFinding], SimulationLoss]:
    """Returns (findings, SimulationLoss). SimulationLoss is zero for non-coherence solvers."""
    if solver == "heuristic":
        return await _run_heuristic(agent, prompt, world_state), SimulationLoss()
    elif solver == "monte_carlo":
        return _run_mc_cross_validate(agent, world_state), SimulationLoss()
    elif solver == "coherence":
        return _run_coherence_secondary(agent, prompt, world_state)
    return [], SimulationLoss()


# ---------------------------------------------------------------------------
# Base agent — template method pattern
# ---------------------------------------------------------------------------


class _AdversarialAgent:
    """Base for all adversarial agents.

    Subclasses declare:
      name                 str          unique identifier
      problem_type         str          ProblemType constant — drives solver dispatch
      finding_type         str          label for findings produced
      _heuristic_system    str          LLM system prompt (used when heuristic is secondary)
      _heuristic_question  str          question appended to the LLM prompt
      _heuristic_description str        short description for the LLM finding

    Subclasses implement:
      _primary_findings(world_state)  — deterministic primary solver.
      _structural_findings(world_state) — optional extra deterministic checks
                                          for agents whose primary is heuristic.
    """

    name: str
    problem_type: str
    finding_type: str
    _heuristic_system: str = ""
    _heuristic_question: str = ""
    _heuristic_description: str = ""

    async def run(self, prompt: str, world_state: dict[str, Any]) -> tuple[list[AdversarialFinding], SimulationLoss]:
        """Returns (findings, SimulationLoss). Loss is non-zero only when coherence check runs."""
        primary_solver, secondary_solver = SOLVER_DISPATCH[self.problem_type]
        findings: list[AdversarialFinding] = []

        if primary_solver == "heuristic":
            # Semantic problems: LLM is primary (Rule 7); coherence check grounds it (Rule 11)
            findings.extend(await _run_heuristic(self, prompt, world_state))
            findings.extend(self._structural_findings(world_state))
            return findings, SimulationLoss()
        else:
            # Deterministic-first: exact solver → cross-validate (Rules 1, 10, 12)
            findings.extend(self._primary_findings(world_state))
            secondary_findings, sim_loss = await _dispatch_secondary(secondary_solver, self, prompt, world_state)
            findings.extend(secondary_findings)
            return findings, sim_loss

    def _primary_findings(self, world_state: dict[str, Any]) -> list[AdversarialFinding]:
        return []

    def _structural_findings(self, world_state: dict[str, Any]) -> list[AdversarialFinding]:
        return []


# ---------------------------------------------------------------------------
# Concrete agents
# ---------------------------------------------------------------------------


class UnreachableStateFinder(_AdversarialAgent):
    name = "unreachable_state"
    problem_type = ProblemType.GRAPH_REACHABILITY
    finding_type = "unreachable_state"
    _heuristic_system = (
        "You are an adversarial design reviewer specialising in state reachability. "
        "Find ONE state (or combination) the system can NEVER reach given its transitions. "
        "If none: respond NO_FINDING"
    )
    _heuristic_question = "Find an unreachable state."
    _heuristic_description = "state identified as unreachable by design"

    def _primary_findings(self, world_state: dict[str, Any]) -> list[AdversarialFinding]:
        findings = []
        for etype in _STATUS_MACHINES:
            if etype not in world_state.get("entities", {}):
                continue
            orphans = _graph_unreachable(etype)
            if orphans:
                findings.append(
                    AdversarialFinding(
                        agent=self.name,
                        solver="graph",
                        finding_type=self.finding_type,
                        description=f"BFS: {etype} states unreachable from any source",
                        witness=f"entity={etype} unreachable={sorted(orphans)}",
                        severity="medium",
                        confidence=0.95,
                    )
                )
        return findings


class DeadlockFinder(_AdversarialAgent):
    name = "deadlock"
    problem_type = ProblemType.FINITE_STATE
    finding_type = "deadlock"
    _heuristic_system = (
        "You are an adversarial design reviewer specialising in deadlocks and livelocks. "
        "Find ONE scenario where two or more entities enter states from which neither can exit. "
        "If none: respond NO_FINDING"
    )
    _heuristic_question = "Find a deadlock."
    _heuristic_description = "deadlock scenario identified"

    def _primary_findings(self, world_state: dict[str, Any]) -> list[AdversarialFinding]:
        sev_map = {
            "unintended_sink": "high",
            "multi_entity_deadlock": "critical",
            "livelock_cycle": "medium",
        }
        return [
            AdversarialFinding(
                agent=self.name,
                solver="model_checker",
                finding_type=self.finding_type,
                description=d["description"],
                witness=json.dumps({k: v for k, v in d.items() if k != "description"}),
                severity=sev_map.get(d.get("kind", ""), "medium"),
                confidence=0.90,
            )
            for d in _model_check_deadlocks()
        ]


class ContradictoryRequirementsFinder(_AdversarialAgent):
    name = "contradictory_requirements"
    problem_type = ProblemType.CONSTRAINT_SAT
    finding_type = "contradictory_requirements"
    _heuristic_system = (
        "You are an adversarial requirements analyst. "
        "Find TWO requirements the system implies that logically contradict each other. "
        "If none: respond NO_FINDING"
    )
    _heuristic_question = "Find contradictory requirements."
    _heuristic_description = "contradictory requirements found"

    def _primary_findings(self, world_state: dict[str, Any]) -> list[AdversarialFinding]:
        return [
            AdversarialFinding(
                agent=self.name,
                solver="sat_smt",
                finding_type=self.finding_type,
                description=f["description"],
                witness=json.dumps({k: v for k, v in f.items() if k not in ("description", "severity")}),
                severity=f.get("severity", "high"),
                confidence=0.90,
            )
            for f in _sat_check()
        ]


class MissingTransitionFinder(_AdversarialAgent):
    name = "missing_transition"
    problem_type = ProblemType.GRAPH_COVERAGE
    finding_type = "missing_transition"
    _heuristic_system = (
        "You are an adversarial design reviewer. "
        "Find ONE important state transition the design omits — a real-world scenario "
        "that requires moving between states but no such edge is defined. "
        "If none: respond NO_FINDING"
    )
    _heuristic_question = "Find a missing transition."
    _heuristic_description = "missing state transition identified"

    def _primary_findings(self, world_state: dict[str, Any]) -> list[AdversarialFinding]:
        return [
            AdversarialFinding(
                agent=self.name,
                solver="graph",
                finding_type=self.finding_type,
                description=m["description"],
                witness=f"transition={m['transition']} target_state={m['target_state']}",
                severity="low",
                confidence=0.80,
            )
            for m in _graph_missing_transitions(world_state)[:4]
        ]


class AggregateConsistencyChecker(_AdversarialAgent):
    name = "aggregate_consistency"
    problem_type = ProblemType.ARCHITECTURE
    finding_type = "aggregate_inconsistency"
    _heuristic_system = (
        "You are an adversarial data modeller. "
        "Find ONE aggregate inconsistency: a scenario where the sum of parts does not "
        "equal the whole, or a parent entity can exist without required children. "
        "If none: respond NO_FINDING"
    )
    _heuristic_question = "Find an aggregate inconsistency."
    _heuristic_description = "aggregate inconsistency identified"

    def _primary_findings(self, world_state: dict[str, Any]) -> list[AdversarialFinding]:
        sev_map = {
            "count_mismatch": "high",
            "referential_gap": "high",
            "unobserved_state": "medium",
        }
        return [
            AdversarialFinding(
                agent=self.name,
                solver="constraint",
                finding_type=self.finding_type,
                description=v["description"],
                witness=json.dumps({k: v2 for k, v2 in v.items() if k != "description"}),
                severity=sev_map.get(v.get("kind", ""), "medium"),
                confidence=0.85,
            )
            for v in _constraint_check(world_state)
        ]


class PerformanceTradeoffFinder(_AdversarialAgent):
    name = "performance_tradeoff"
    problem_type = ProblemType.OPTIMIZATION
    finding_type = "performance_tradeoff"
    _heuristic_system = (
        "You are an adversarial performance analyst. "
        "Find ONE performance trade-off the design has not addressed — a scenario "
        "where optimising for one goal degrades another. "
        "If none: respond NO_FINDING"
    )
    _heuristic_question = "Find a performance trade-off."
    _heuristic_description = "performance trade-off identified"

    def _primary_findings(self, world_state: dict[str, Any]) -> list[AdversarialFinding]:
        perf = _mc_performance(world_state, trials=400)
        if perf["saturation_risk"] not in ("critical", "high"):
            return []
        return [
            AdversarialFinding(
                agent=self.name,
                solver="optimizer",
                finding_type=self.finding_type,
                description=(
                    f"MC queuing: saturation_risk={perf['saturation_risk']} — "
                    f"avg={perf['avg_utilisation']:.2f} p95={perf['p95_utilisation']:.2f} "
                    f"p99={perf['p99_utilisation']:.2f}"
                    + (f", bottleneck={perf['bottleneck']}" if perf["bottleneck"] else "")
                ),
                witness=json.dumps(perf),
                severity=perf["saturation_risk"],
                confidence=0.75,
            )
        ]


class BreakingUseCaseFinder(_AdversarialAgent):
    """Semantic agent — LLM is primary (Rule 7); coherence check grounds findings in entity space."""

    name = "breaking_use_case"
    problem_type = ProblemType.SEMANTIC
    finding_type = "breaking_use_case"
    _heuristic_system = (
        "You are an adversarial design reviewer. "
        "Find ONE concrete use case the design cannot handle — name actors, steps, and failure point. "
        "If none: respond NO_FINDING"
    )
    _heuristic_question = "Find a breaking use case."
    _heuristic_description = "use case the design cannot handle"


class InvariantViolationFinder(_AdversarialAgent):
    """Statistical agent — MC samples invariant violations; coherence check cross-validates distribution."""

    name = "invariant_violation"
    problem_type = ProblemType.STATISTICAL
    finding_type = "violated_invariant"
    _heuristic_system = (
        "You are an adversarial domain modeller. "
        "Find ONE domain invariant the system allows to be violated — explain the event sequence. "
        "If none: respond NO_FINDING"
    )
    _heuristic_question = "Find a violated invariant."
    _heuristic_description = "violable domain invariant found"

    def _primary_findings(self, world_state: dict[str, Any]) -> list[AdversarialFinding]:
        return [
            AdversarialFinding(
                agent=self.name,
                solver="monte_carlo",
                finding_type=self.finding_type,
                description=f"MC: cross-entity invariant can be violated — {v['invariant']}",
                witness=json.dumps(v["scenario"]),
                severity="high",
                confidence=0.80,
            )
            for v in _mc_invariant_violations(world_state, trials=400)[:2]
        ]


class MissingEntityFinder(_AdversarialAgent):
    """Semantic agent — LLM discovers; coherence check grounds by checking embedding-space gaps."""

    name = "missing_entity"
    problem_type = ProblemType.SEMANTIC
    finding_type = "missing_entity"
    _heuristic_system = (
        "You are an adversarial domain modeller. "
        "Find ONE important entity the design implicitly relies on but has not modelled. "
        "If none: respond NO_FINDING"
    )
    _heuristic_question = "Find a missing entity."
    _heuristic_description = "entity the design omits"

    def _structural_findings(self, world_state: dict[str, Any]) -> list[AdversarialFinding]:
        """Deterministic orphan check: if A references B but B is empty, B is missing."""
        entities = world_state.get("entities", {})
        findings = []
        for et_a, _, et_b, _, desc in _CROSS_INVARIANTS:
            if entities.get(et_a, {}).get("count", 0) > 0 and entities.get(et_b, {}).get("count", -1) == 0:
                findings.append(
                    AdversarialFinding(
                        agent=self.name,
                        solver="constraint",
                        finding_type=self.finding_type,
                        description=f"Constraint: invariant references {et_b} but collection is empty",
                        witness=f"{et_a}={entities[et_a]['count']} records, {et_b}=0 records",
                        severity="high",
                        confidence=0.70,
                    )
                )
        return findings


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

_AGENTS: list[_AdversarialAgent] = [
    UnreachableStateFinder(),
    DeadlockFinder(),
    ContradictoryRequirementsFinder(),
    MissingTransitionFinder(),
    AggregateConsistencyChecker(),
    PerformanceTradeoffFinder(),
    BreakingUseCaseFinder(),
    InvariantViolationFinder(),
    MissingEntityFinder(),
]

_ALL_SOLVERS = [
    "graph",
    "model_checker",
    "sat_smt",
    "constraint",
    "coherence",
    "optimizer",
    "monte_carlo",
    "heuristic",
]


async def run_adversarial_pass(
    prompt: str,
    world_state: dict[str, Any],
    *,
    timeout: float = 90.0,
) -> AdversarialResult:
    """Run all agents concurrently; aggregate findings and compute confidence delta."""
    result = AdversarialResult(
        agents_run=[a.name for a in _AGENTS],
        solvers_run=_ALL_SOLVERS,
    )

    async def _guarded(
        agent: _AdversarialAgent,
    ) -> tuple[list[AdversarialFinding], SimulationLoss]:
        try:
            return await asyncio.wait_for(agent.run(prompt, world_state), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("[adversarial] %s timed out", agent.name)
            return [], SimulationLoss()
        except Exception as exc:
            logger.warning("[adversarial] %s failed: %s", agent.name, exc)
            return [], SimulationLoss()

    per_agent = await asyncio.gather(*[_guarded(a) for a in _AGENTS])
    sim_losses: list[SimulationLoss] = []
    for findings, sim_loss in per_agent:
        result.findings.extend(findings)
        if sim_loss.total > 0.0:
            sim_losses.append(sim_loss)

    # Component-wise max across agents — worst-case over all dimensions
    result.simulation_loss = SimulationLoss.pool(sim_losses)

    # L_knowledge: epistemic uncertainty — world_state property, not per-agent.
    # Injected post-pool so it applies uniformly to all agent findings.
    action_verb = _extract_action_verb(prompt)
    result.simulation_loss.knowledge = _l_knowledge(world_state, action_verb)
    result.simulation_loss.compute_total()

    sev_weight = {"critical": 0.20, "high": 0.12, "medium": 0.07, "low": 0.03}
    clean_agents = sum(1 for findings, _ in per_agent if not findings)
    penalty = sum(sev_weight.get(f.severity, 0.05) * f.confidence for f in result.findings)
    bonus = clean_agents * 0.04
    result.confidence_delta = round(bonus - penalty, 4)

    if not result.findings:
        result.summary = f"All {len(_AGENTS)} adversarial agents found no flaws — design confidence +{bonus:.2f}"
    else:
        critical = sum(1 for f in result.findings if f.severity in ("critical", "high"))
        result.summary = (
            f"{len(result.findings)} finding(s) across {len(_AGENTS)} agents "
            f"({critical} high/critical). Confidence delta: {result.confidence_delta:+.3f}"
        )

    logger.info("[adversarial] %s", result.summary)
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _summarise_entities(world_state: dict[str, Any]) -> str:
    lines = []
    for col, info in list((world_state.get("entities") or {}).items())[:12]:
        statuses = info.get("statuses") or {}
        s = ", ".join(f"{s}:{n}" for s, n in list(statuses.items())[:4])
        lines.append(f"  {col}: {info.get('count', 0)} records  statuses=[{s}]")
    return "\n".join(lines) or "(no entity data)"


def _infer_severity(text: str) -> str:
    t = text.lower()
    if any(w in t for w in ("critical", "catastrophic", "data loss", "security", "corrupt")):
        return "critical"
    if any(
        w in t
        for w in (
            "cannot",
            "impossible",
            "breaks",
            "violation",
            "fail",
            "crash",
            "deadlock",
        )
    ):
        return "high"
    if any(w in t for w in ("unlikely", "edge case", "rare", "may", "could")):
        return "low"
    return "medium"
