"""Predict phase public API.

Architecture:
    ExecutionGraph
          │
          ▼
    PredictEngine
          │
          ▼
    SolverMesh  (GraphSolver, SAT, Constraint, ModelChecker, Optimizer, JEPA, MCTS)
          │
          ▼
    PredictedEPAState  ← the reference state for EPA loss computation
          │
          ▼  (after execution)
    ObservedEPAState   ← derived from epa_transition / ExecutionGraph states
          │
          ▼
    EPALossVector      ← prediction error: L_S, L_B, L_A, L_M, L_K, L_C, L_G
          │
          ▼
    PredictionReport   → Fix phase

Manual graph algorithms (Kahn's sort, DFS critical path, manual reachability)
are NOT in this module.  The canonical implementations live in
GraphSolver.find_failed_nodes / find_unreachable_nodes / detect_cycles / critical_path.

Public surface (backward compatible):
  - IssueKind    — StrEnum
  - Issue        — dataclass (supplementary diagnostics, not the primary signal)
  - analyze(graph) → list[Issue]    (sync wrapper, backward compat)
  - predict(graph) → PredictionReport  (async, full structured report with EPA loss)
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class IssueKind(StrEnum):
    FAILED_NODE = "failed_node"
    UNREACHABLE_NODE = "unreachable_node"
    CRITICAL_PATH_SLOW = "critical_path_slow"
    CYCLE_DETECTED = "cycle_detected"


@dataclass
class Issue:
    kind: IssueKind
    node_id: str
    label: str = ""
    detail: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ── Primary async API ─────────────────────────────────────────────────────────


async def predict(graph: Any) -> "PredictionReport":
    """Full structured prediction: SolverMesh → PredictedEPAState → PredictionReport.

    Returns a PredictionReport whose:
      - predicted_state  = PredictedEPAState from SolverMesh
      - observed_state   = None until report.set_observation(observed) is called
      - loss_vector      = None until observation is set
      - issues           = supplementary Issue list for backward compat
    """
    from src.monkey_brain.kernel.predict.engine import PredictEngine

    engine = PredictEngine()
    return await engine.predict(graph)


# ── Backward-compatible sync API ──────────────────────────────────────────────


def analyze(graph: Any) -> list[Issue]:
    """Sync entry point — returns list[Issue] from GraphSolver (no full EPA loop).

    Use predict() for the full PredictionReport with EPA loss.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(_run_predict_sync, graph)
                return future.result(timeout=30)
        else:
            return loop.run_until_complete(_predict_issues(graph))
    except Exception as e:
        logger.debug("[predict] engine error, falling back to GraphSolver: %s", e)
        return _fallback_analyze(graph)


async def _predict_issues(graph: Any) -> list[Issue]:
    report = await predict(graph)
    return report.issues


def _run_predict_sync(graph: Any) -> list[Issue]:
    return asyncio.run(_predict_issues(graph))


def _fallback_analyze(graph: Any) -> list[Issue]:
    """Direct GraphSolver fallback — no async, no mesh."""
    from src.monkey_brain.kernel.predict.graph_solver.graph import GraphSolver
    from src.monkey_brain.kernel.execute.graph import NodeState

    gs = GraphSolver()
    issues: list[Issue] = []

    for nid in gs.find_failed_nodes(graph):
        issues.append(Issue(kind=IssueKind.FAILED_NODE, node_id=nid))
    for nid in gs.find_unreachable_nodes(graph):
        issues.append(Issue(kind=IssueKind.UNREACHABLE_NODE, node_id=nid))
    cp = gs.critical_path(graph)
    failed = set(gs.find_failed_nodes(graph))
    for nid in cp:
        if nid in failed:
            issues.append(
                Issue(
                    kind=IssueKind.CRITICAL_PATH_SLOW,
                    node_id=nid,
                    metadata={"critical_path": cp},
                )
            )
    if gs.detect_cycles(graph):
        issues.append(Issue(kind=IssueKind.CYCLE_DETECTED, node_id="*"))
    return issues


# re-export for engine.py's _derive_issues
from src.monkey_brain.kernel.predict.report import PredictionReport  # noqa: E402, F401
