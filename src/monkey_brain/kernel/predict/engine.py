"""PredictEngine — coordinates SolverMesh to produce a PredictionReport.

Predict phase responsibilities (what this file does):
  - Translate ExecutionGraph state into solver-friendly problem dicts.
  - Dispatch to GraphSolver, SAT, Constraint, ModelChecker, Optimizer, JEPA, MCTS.
  - Aggregate SolverResults into a PredictedEPAState.
  - Return PredictionReport containing PredictedEPAState + solver_results + graph_analysis.

Predict does NOT:
  - Mutate the graph.
  - Execute repairs.
  - Update policies or Q-values.
  - Compute EPA Loss (that happens in Learn, after observation).
"""

from __future__ import annotations

import logging
from typing import Any

from src.monkey_brain.kernel.predict.report import PredictionReport, GraphAnalysis
from src.monkey_brain.kernel.predict.epa_state import PredictedEPAState

logger = logging.getLogger(__name__)


class PredictEngine:
    """Orchestrates SolverMesh → PredictionReport (with PredictedEPAState).

    Called at the PREDICT phase (before or after execution depending on cycle position).
    Returns a PredictionReport whose predicted_state is the expected execution outcome.
    The LEARN phase later pairs this with the observed state to compute the EPA loss.
    """

    def __init__(self, mesh: Any = None) -> None:
        if mesh is None:
            from src.monkey_brain.kernel.predict.solver_mesh import build_default_mesh

            mesh = build_default_mesh()
        self._mesh = mesh
        self._graph_solver = self._find_graph_solver()
        self._jepa_predictor = self._find_jepa_predictor()

    def _find_graph_solver(self) -> Any:
        from src.monkey_brain.kernel.predict.graph_solver.graph import GraphSolver

        for s in self._mesh._solvers:
            if isinstance(s, GraphSolver):
                return s
        return None

    def _find_jepa_predictor(self) -> Any:
        """Wrap JEPAWorldModel in a JEPAPredictor for graph-aware I/O."""
        try:
            from src.monkey_brain.kernel.predict.jepa.jepa import JEPAWorldModel
            from src.monkey_brain.kernel.predict.jepa.predictor import JEPAPredictor

            for s in self._mesh._solvers:
                if isinstance(s, JEPAWorldModel):
                    return JEPAPredictor(s)
        except Exception:
            logger.debug("_find_jepa_predictor: suppressed exception", exc_info=True)
        return None

    async def predict(
        self,
        graph: Any,
        *,
        goal: Any = None,
        mesh: dict | None = None,
    ) -> PredictionReport:
        """Run all solvers; produce PredictionReport with PredictedEPAState."""
        report = PredictionReport()

        # ── 1. Graph analysis (canonical — via GraphSolver) ───────────────────
        report.graph_analysis = self._graph_analysis(graph)

        # ── 2. Symbolic solvers — dispatch concurrently ───────────────────────
        problems = self._build_problems(graph, report.graph_analysis)
        results = await self._mesh.dispatch_all(problems)
        for r in results:
            if r is not None:
                report.solver_results[r.solver_name] = r

        # ── 3. JEPA — richer prediction with full EPA context ─────────────────
        # JEPAPredictor receives the graph + previous predicted state so it can
        # encode: S (node completion map), B (belief), A (affordances), G (goal), M (mesh)
        jepa_prediction = None
        if self._jepa_predictor is not None:
            try:
                jepa_prediction = self._jepa_predictor.predict(
                    graph,
                    epa_state=report.predicted_state,  # None first call, then carried forward
                    action="execute",
                    goal=goal,
                    mesh=mesh,
                )
            except Exception as e:
                logger.debug("[predict] JEPA predictor failed: %s", e)

        # ── 4. Aggregate symbolic + JEPA → PredictedEPAState ─────────────────
        report.predicted_state = self._aggregate_predicted_state(
            graph, report.graph_analysis, report.solver_results, jepa_prediction
        )

        # ── 5. Predicted constraints (from model checker + SAT) ───────────────
        report.predicted_constraints = self._extract_predicted_constraints(report.solver_results)

        # ── 6. Issues (supplementary, backward compat) + repair candidates ────
        report.issues = _derive_issues(report.graph_analysis)
        report.recommended_repairs = _derive_repairs(report)

        logger.debug(
            "[predict] %d solver results, %d issues, predicted_confidence=%.2f",
            len(report.solver_results),
            len(report.issues),
            report.predicted_state.B_hat.get("confidence", 0.0),
        )
        return report

    # ── private helpers ───────────────────────────────────────────────────────

    def _graph_analysis(self, graph: Any) -> GraphAnalysis:
        if self._graph_solver is None:
            return GraphAnalysis()
        gs = self._graph_solver
        return GraphAnalysis(
            failed_nodes=gs.find_failed_nodes(graph),
            unreachable_nodes=gs.find_unreachable_nodes(graph),
            cycles=gs.detect_cycles(graph),
            critical_path=gs.critical_path(graph),
        )

    def _build_problems(self, graph: Any, ga: GraphAnalysis) -> list[dict]:
        """Convert ExecutionGraph state into solver problem dicts."""
        from src.monkey_brain.kernel.execute.graph import NodeState

        nodes = graph.get_step_nodes()
        node_states = {n.id: graph.get_state(n.id) for n in nodes}

        # World state: node_id → completion probability (current known state)
        current_state = {
            n.id: (
                1.0
                if node_states[n.id] == NodeState.COMPLETE
                else 0.0
                if node_states[n.id] == NodeState.FAILED
                else 0.5
            )
            for n in nodes
        }

        # Adjacency dict for graph solver
        adj = {n.id: [] for n in nodes}
        for n in nodes:
            for e in graph.outgoing(n.id):
                if e.rel == "depends_on" and e.dst in adj:
                    adj[n.id].append(e.dst)

        return [
            # Graph: cycle + topo
            {"type": "cycle_check", "graph": adj, "query": {"check": "cycle_check"}},
            # Model check: execution invariants
            {
                "type": "model_check",
                "model": {
                    "node_count": len(nodes),
                    "failed_count": len(ga.failed_nodes),
                    "complete_count": sum(1 for s in node_states.values() if s == NodeState.COMPLETE),
                    "has_cycle": ga.has_cycle,
                },
                "invariants": [
                    {
                        "type": "range",
                        "variable": "failed_count",
                        "min": 0,
                        "max": len(nodes),
                    },
                ],
                "properties": [],
            },
            # Optimizer: estimate execution cost
            {
                "type": "optimization",
                "objective": "minimize",
                "variables": {n.id: {"target": 1.0} for n in nodes},
                "learning_rate": 0.1,
                "iterations": 10,
            },
            # JEPA / MCTS: predict future state
            {
                "type": "prediction",
                "current_state": current_state,
                "action": "execute",
            },
            # SAT: dependency satisfiability
            {
                "type": "satisfiability",
                "clauses": [],
                "variables": list(node_states.keys()),
            },
        ]

    def _aggregate_predicted_state(
        self,
        graph: Any,
        ga: GraphAnalysis,
        solver_results: dict[str, Any],
        jepa_prediction: Any = None,
    ) -> PredictedEPAState:
        """Aggregate symbolic solver results + JEPA into PredictedEPAState.

        Aggregation strategy:
          S_hat — symbolic graph analysis provides the prior (0/0.5/0.7/1.0);
                  JEPA refines PENDING nodes proportional to its confidence.
          B_hat — weighted blend: JEPA belief signal (if trained) + graph-derived confidence.
          A_hat — JEPA-predicted affordances (if trained), else graph labels of non-failed nodes.
          M_hat — model checker solution.
          goal_progress — JEPA predicted goal progress blended with graph-derived ratio.
          knowledge_expectation — optimizer + JEPA K signal.
        """
        from src.monkey_brain.kernel.execute.graph import NodeState

        nodes = graph.get_step_nodes()
        total = len(nodes)
        failed_set = set(ga.failed_nodes)
        unreachable_set = set(ga.unreachable_nodes)

        # ── S_hat: symbolic prior ─────────────────────────────────────────────
        S_hat: dict[str, float] = {}
        for node in nodes:
            state = graph.get_state(node.id)
            if state == NodeState.COMPLETE:
                S_hat[node.id] = 1.0
            elif node.id in failed_set:
                S_hat[node.id] = 0.0
            elif node.id in unreachable_set:
                S_hat[node.id] = 0.5
            else:
                S_hat[node.id] = 0.7  # optimistic prior for pending

        # ── Blend JEPA prediction into S_hat ─────────────────────────────────
        # JEPA only modulates non-terminal nodes; weight by its confidence.
        if jepa_prediction is not None and jepa_prediction.trained:
            w = jepa_prediction.prediction_confidence  # [0.25, 0.85]
            for nid, sym_val in S_hat.items():
                if sym_val in (0.0, 1.0):
                    continue  # terminal — keep symbolic
                jepa_val = jepa_prediction.predicted_world_state.get(nid)
                if jepa_val is not None:
                    S_hat[nid] = round(w * jepa_val + (1 - w) * sym_val, 4)
        elif jepa_prediction is None:
            # Fall back to generic SolverResult from JEPA solve() if available
            jepa_sr = solver_results.get("jepa")
            if jepa_sr is not None:
                jepa_state = jepa_sr.solution.get("predicted_state", {})
                for nid in S_hat:
                    if S_hat[nid] not in (0.0, 1.0) and nid in jepa_state:
                        if isinstance(jepa_state[nid], (int, float)):
                            S_hat[nid] = round(0.5 * float(jepa_state[nid]) + 0.5 * S_hat[nid], 4)

        # ── B_hat: predicted belief ───────────────────────────────────────────
        graph_confidence = sum(S_hat.values()) / total if total > 0 else 0.0
        if jepa_prediction is not None and jepa_prediction.trained:
            jepa_conf = jepa_prediction.predicted_beliefs.get("confidence", graph_confidence)
            w = jepa_prediction.prediction_confidence
            B_hat = {
                "confidence": round(w * jepa_conf + (1 - w) * graph_confidence, 4),
                "uncertainty": round(1.0 - (w * jepa_conf + (1 - w) * graph_confidence), 4),
            }
        else:
            B_hat = {
                "confidence": round(graph_confidence, 4),
                "uncertainty": round(1.0 - graph_confidence, 4),
            }

        # ── A_hat: predicted affordances ─────────────────────────────────────
        if jepa_prediction is not None and jepa_prediction.trained and jepa_prediction.predicted_actions:
            A_hat = jepa_prediction.predicted_actions
        else:
            A_hat = {node.label for node in nodes if S_hat.get(node.id, 0.0) >= 0.5}

        # ── M_hat: model checker solution ─────────────────────────────────────
        mc_result = solver_results.get("model_checker")
        M_hat: dict[str, Any] = mc_result.solution if mc_result is not None else {}

        # ── Goal progress ─────────────────────────────────────────────────────
        graph_goal_progress = sum(S_hat.values()) / total if total > 0 else 0.0
        if jepa_prediction is not None and jepa_prediction.trained:
            w = jepa_prediction.prediction_confidence
            goal_progress = round(
                w * jepa_prediction.predicted_goal_progress + (1 - w) * graph_goal_progress,
                4,
            )
        else:
            goal_progress = round(graph_goal_progress, 4)

        # ── Constraint status ─────────────────────────────────────────────────
        constraint_status = mc_result.solution.get("verified", True) if mc_result else True

        # ── Knowledge expectation ─────────────────────────────────────────────
        opt_result = solver_results.get("optimizer")
        knowledge_expectation = 0.5
        if opt_result is not None:
            knowledge_expectation = max(
                0.0,
                min(1.0, round(1.0 - abs(opt_result.solution.get("value", 0.5)), 3)),
            )
        if jepa_prediction is not None and jepa_prediction.trained:
            k_sig = jepa_prediction.component_signals.get("latent_signal_K", 0.0)
            knowledge_expectation = round(0.5 * knowledge_expectation + 0.5 * float((1.0 + k_sig) / 2.0), 4)

        # ── Solver contributions for diagnostics ─────────────────────────────
        contributions: dict[str, Any] = {name: r.proof for name, r in solver_results.items()}
        if jepa_prediction is not None:
            contributions["jepa_predictor"] = jepa_prediction.component_signals

        return PredictedEPAState(
            S_hat=S_hat,
            B_hat=B_hat,
            A_hat=A_hat,
            M_hat=M_hat,
            constraint_status=constraint_status,
            goal_progress=goal_progress,
            knowledge_expectation=knowledge_expectation,
            solver_contributions=contributions,
        )

    def _extract_predicted_constraints(self, solver_results: dict[str, Any]) -> dict[str, Any]:
        """Extract constraint prediction from model checker and SAT solver results."""
        constraints: dict[str, Any] = {}
        mc = solver_results.get("model_checker")
        if mc is not None:
            constraints["model_check_verified"] = mc.solution.get("verified", True)
            constraints["model_check_violations"] = mc.solution.get("violations", [])
        sat = solver_results.get("sat")
        if sat is not None:
            constraints["sat_satisfiable"] = sat.solution.get("satisfiable", True)
            constraints["sat_model"] = sat.solution.get("model", {})
        constraint_sr = solver_results.get("constraint")
        if constraint_sr is not None:
            constraints["constraint_feasible"] = constraint_sr.solution.get("feasible", True)
        return constraints


# ── Issue derivation (supplementary, for backward compat) ────────────────────


def _derive_issues(ga: GraphAnalysis | None) -> list[Any]:
    if ga is None:
        return []
    from src.monkey_brain.kernel.predict.simulation import Issue, IssueKind

    issues: list[Any] = []
    for nid in ga.failed_nodes:
        issues.append(Issue(kind=IssueKind.FAILED_NODE, node_id=nid, detail="node state is FAILED"))
    for nid in ga.unreachable_nodes:
        issues.append(
            Issue(
                kind=IssueKind.UNREACHABLE_NODE,
                node_id=nid,
                detail="all upstream deps are FAILED",
            )
        )
    for nid in ga.critical_path:
        if nid in ga.failed_nodes:
            issues.append(
                Issue(
                    kind=IssueKind.CRITICAL_PATH_SLOW,
                    node_id=nid,
                    detail="failed node on critical path",
                    metadata={"critical_path": ga.critical_path},
                )
            )
    if ga.has_cycle:
        issues.append(
            Issue(
                kind=IssueKind.CYCLE_DETECTED,
                node_id="*",
                detail="cycle detected in dependency graph",
            )
        )
    return issues


def _derive_repairs(report: PredictionReport) -> list[str]:
    repairs = []
    if report.graph_analysis:
        for nid in report.graph_analysis.failed_nodes:
            repairs.append(f"self_heal:{nid}")
        for nid in report.graph_analysis.unreachable_nodes:
            repairs.append(f"skip:{nid}")
        if report.graph_analysis.has_cycle:
            repairs.append("break_cycle")
    return repairs
