"""Graph-first simulation pipeline.

The simulator no longer treats solvers as competing question-answer engines.
It starts from an execution graph, clones that graph for each solver, gathers
solver contributions, and merges them into a single simulation graph.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

from src.monkey_brain.runtime.agent_middleware import (
    load_knowledge_context,
    _identity_from_state,
)

logger = logging.getLogger("agentos.simulation_pipeline")

# Per-solver timeout in seconds. A slow LLM call should not block the entire pipeline.
SOLVER_TIMEOUT_SECONDS = float(os.getenv("SOLVER_TIMEOUT_SECONDS", "30.0"))


@dataclass
class SolverContribution:
    solver_name: str
    annotations: dict[str, Any]
    predicted_state: dict[str, Any]
    confidence: float
    reasoning: str = ""
    latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "solver": self.solver_name,
            "annotations": self.annotations,
            "predicted_state": self.predicted_state,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "latency_ms": self.latency_ms,
        }


@dataclass
class ConsensusResult:
    simulation_graph: dict[str, Any]
    participating_solvers: list[str]
    rejected_solvers: list[str]
    consensus_score: float
    disagreement_score: float
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "simulation_graph": self.simulation_graph,
            "participating_solvers": self.participating_solvers,
            "rejected_solvers": self.rejected_solvers,
            "consensus_score": self.consensus_score,
            "disagreement_score": self.disagreement_score,
            "confidence": self.confidence,
        }


@dataclass
class SimulationResult:
    answer: str
    success: bool = True
    semantic_hits: list = field(default_factory=list)
    graph_paths: list = field(default_factory=list)
    llm_answered: bool = True
    capability: str = ""
    capability_group: str = ""
    implementation: str = ""
    predictions: list[SolverContribution] = field(default_factory=list)
    best_prediction: SolverContribution | None = None
    consensus: ConsensusResult | None = None
    grounding_score: float = 0.0
    feasibility_verdict: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)
    simulation_graph: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "success": self.success,
            "semantic_hits": self.semantic_hits,
            "graph_paths": self.graph_paths,
            "llm_answered": self.llm_answered,
            "capability": self.capability,
            "capability_group": self.capability_group,
            "implementation": self.implementation,
            "predictions": [p.to_dict() for p in self.predictions],
            "best_prediction": (self.best_prediction.to_dict() if self.best_prediction else None),
            "consensus": self.consensus.to_dict() if self.consensus else None,
            "grounding_score": self.grounding_score,
            "feasibility_verdict": self.feasibility_verdict,
            "metadata": self.metadata,
            "simulation_graph": self.simulation_graph,
        }


class SimulationPipeline:
    """Simulation pipeline that predicts the next state of the execution graph."""

    def __init__(self, execution_graph: dict[str, Any] | None = None):
        self.execution_graph = execution_graph or {}
        self.solvers = self._load_all_solvers()

    def _load_all_solvers(self) -> list:
        solvers: list[Any] = [
            LLMReasoningSolver(),
            KnowledgeBasedSolver(),
            RuleBasedSolver(),
        ]

        for adapter in (
            _GraphSolverAdapter,
            _SATSolverAdapter,
            _ConstraintSolverAdapter,
            _RuleEngineSolverAdapter,
            _ModelCheckerSolverAdapter,
            _OptimizerSolverAdapter,
            _MonteCarloSolverAdapter,
            _JEPAWorldModelAdapter,
        ):
            try:
                solvers.append(adapter())
            except Exception as exc:
                logger.debug("[simulation] %s unavailable: %s", adapter.__name__, exc)

        logger.info("[simulation] Loaded %d solvers", len(solvers))
        return solvers

    async def simulate(self, intent_ir: Any) -> SimulationResult:
        t0 = time.monotonic()

        # 1. Extract relevant metadata from the intent IR for logging and processing.
        question = intent_ir.execution_metadata.get("question", "")

        # 2. Capture capability information from the intent IR for reporting in the simulation result.
        capability = intent_ir.execution_metadata.get("capability", "")
        capability_group = intent_ir.execution_metadata.get("capability_group", "")

        # 3. Capture implementation details from the intent IR for reporting in the simulation result.
        implementation = intent_ir.execution_metadata.get("implementation", "")

        # 4. Convert the execution graph to a dictionary format for processing by solvers.
        execution_graph = self._to_graph_dict(self.execution_graph)

        # 5. Run all solvers concurrently to gather their contributions based on the execution graph.
        contributions = await self._run_all_solvers()

        # 6. Build a consensus from the contributions of all solvers, merging their predictions into a single simulation graph.
        consensus = self._build_consensus(execution_graph, contributions)

        # 7. Extract the simulation graph from the consensus result and select the best prediction based on confidence and agreement with the consensus.
        simulation_graph = consensus.simulation_graph

        # 8. Select the best prediction from the contributions based on confidence and agreement with the consensus.
        best = self._select_best(contributions, consensus)
        answer = (
            simulation_graph.get("metadata", {})
            .get("summary", {})
            .get("answer", f"Simulated execution for: {question}")
        )

        # 9. check convergence: if any solver has confidence > 0.0, the simulation is considered successful; otherwise, log a warning and mark the simulation as unsuccessful.
        solved = any(c.confidence > 0.0 for c in contributions)
        if not solved:
            logger.warning(
                "[simulation] every solver failed (%d) — simulation produced no prediction",
                len(contributions),
            )

        # 10. Return a SimulationResult object containing the answer, success status, capability information, solver contributions, best prediction, consensus result, grounding score, feasibility verdict, metadata (including latency and solver count), and the simulation graph.
        return SimulationResult(
            answer=answer,
            success=solved,
            capability=capability,
            capability_group=capability_group,
            implementation=implementation,
            predictions=contributions,
            best_prediction=best,
            consensus=consensus,
            grounding_score=consensus.confidence,
            feasibility_verdict=simulation_graph.get("metadata", {})
            .get("summary", {})
            .get("feasibility_verdict", "unknown"),
            metadata={
                "latency_ms": (time.monotonic() - t0) * 1000,
                "solver_count": len(contributions),
                "consensus_score": consensus.consensus_score,
                "disagreement_score": consensus.disagreement_score,
            },
            simulation_graph=simulation_graph,
        )

    async def _run_all_solvers(
        self,
    ) -> list[SolverContribution]:
        base_graph = self._to_graph_dict(self.execution_graph)

        async def _run_one(solver: Any) -> SolverContribution:
            try:
                # Each solver gets its own deep copy, so they share no state and are
                # independent — safe to run concurrently.
                return await asyncio.wait_for(
                    solver.solve(copy.deepcopy(base_graph)),
                    timeout=SOLVER_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "[simulation] Solver %s timed out after %.1fs",
                    solver.name,
                    SOLVER_TIMEOUT_SECONDS,
                )
                return SolverContribution(
                    solver_name=solver.name,
                    annotations={"error": "timeout"},
                    predicted_state={"error": "solver timed out"},
                    confidence=0.0,
                    reasoning=f"Solver timed out after {SOLVER_TIMEOUT_SECONDS}s",
                )
            except Exception as exc:
                logger.warning("[simulation] Solver %s failed: %s", solver.name, exc)
                return SolverContribution(
                    solver_name=solver.name,
                    annotations={"error": str(exc)},
                    predicted_state={"error": str(exc)},
                    confidence=0.0,
                    reasoning=f"Solver failed: {exc}",
                )

        return list(await asyncio.gather(*(_run_one(s) for s in self.solvers)))

    def _to_graph_dict(self, graph: Any) -> dict[str, Any]:
        if hasattr(graph, "to_dict"):
            return graph.to_dict()
        return dict(graph) if isinstance(graph, dict) else {"graph": graph}

    def _build_consensus(
        self,
        execution_graph: dict[str, Any],
        contributions: list[SolverContribution],
    ) -> ConsensusResult:
        # Rejection is decided against the full contribution set, then
        # enforced: a rejected solver's annotations, predicted state, and
        # score never enter the merged consensus graph. If every solver is
        # rejected, fall back to the full set — a degenerate low-confidence
        # consensus beats an empty simulation graph, and the scores stay
        # honest either way.
        rejected = [c.solver_name for c in contributions if c.confidence <= 0.1 or self._is_outlier(c, contributions)]
        accepted = [c for c in contributions if c.solver_name not in rejected] or list(contributions)

        node_annotations: dict[str, list[dict[str, Any]]] = {}
        graph_scores: dict[str, float] = {}
        node_agreement: dict[str, int] = {}
        for contribution in accepted:
            # 1. Score each solver's annotations to weight their contribution to the consensus.
            #  The score is based on the solver's confidence and the presence of node and edge annotations.
            graph_scores[contribution.solver_name] = self._score_annotations(contribution.annotations, contribution)
            for node_id in contribution.annotations.get("node_annotations", {}):
                node_agreement[node_id] = node_agreement.get(node_id, 0) + 1
                node_annotations.setdefault(node_id, []).append(contribution.annotations["node_annotations"][node_id])

        participating = [c.solver_name for c in accepted]

        # 2. Compute the overall consensus score based on the execution graph, accepted contributions, node annotations, and graph scores.
        #  The consensus score reflects the level of agreement among solvers and the quality of their contributions.
        consensus_score = self._consensus_score(execution_graph, accepted, node_annotations, graph_scores)
        disagreement_score = round(1.0 - consensus_score, 4)
        avg_confidence = sum(c.confidence for c in accepted) / max(len(accepted), 1)

        # 3. Annotate the simulation graph with the merged contributions, node annotations, graph scores, and consensus score. The simulation graph represents
        # the predicted next state of the execution graph based on the solvers' contributions.
        simulation_graph = self._annotate_graph(
            execution_graph, accepted, node_annotations, graph_scores, consensus_score
        )
        simulation_graph["metadata"]["rejected_solvers"] = list(rejected)

        # 4. Return a ConsensusResult object containing the simulation graph, participating solvers, rejected solvers, consensus score, disagreement score,
        #  and overall confidence. This result encapsulates the outcome of the simulation process and provides insights into the level of agreement among solvers.
        return ConsensusResult(
            simulation_graph=simulation_graph,
            participating_solvers=participating,
            rejected_solvers=rejected,
            consensus_score=round(consensus_score, 4),
            disagreement_score=disagreement_score,
            confidence=round((avg_confidence * 0.5) + (consensus_score * 0.5), 4),
        )

    def _annotate_graph(
        self,
        execution_graph: dict[str, Any],
        contributions: list[SolverContribution],
        node_annotations: dict[str, list[dict[str, Any]]],
        graph_scores: dict[str, float],
        consensus_score: float,
    ) -> dict[str, Any]:
        from src.monkey_brain.kernel.models.graph import canonical_graph_envelope

        # Create a canonical envelope of the execution graph to serve as the base for the simulation graph. This ensures that the simulation graph maintains the same structure and properties as the execution graph while allowing for the addition of solver contributions and annotations.
        simulation_graph = canonical_graph_envelope(execution_graph)

        # Simulation graph is a prediction of the next state of the execution graph, so it should be labeled as such.
        simulation_graph["graph_type"] = "simulation"
        source_metadata = dict(execution_graph.get("metadata", {})) if isinstance(execution_graph, dict) else {}
        simulation_graph["metadata"] = dict(simulation_graph.get("metadata", {}))
        simulation_graph["metadata"].setdefault("solver", "simulation")
        simulation_graph["metadata"]["temporal_model"] = "ExecutionGraph(tn) -> SimulationGraph(tn+1)"
        simulation_graph["metadata"]["source_graph_id"] = simulation_graph.get("graph_id")
        simulation_graph["metadata"]["source_timestamp"] = simulation_graph.get("timestamp")
        simulation_graph["metadata"]["graph_id"] = simulation_graph.get("graph_id")
        simulation_graph["metadata"]["timestamp"] = source_metadata.get("timestamp", simulation_graph.get("timestamp"))
        simulation_graph["metadata"]["solver_contributions"] = [c.to_dict() for c in contributions]
        simulation_graph["metadata"]["graph_scores"] = graph_scores
        simulation_graph["metadata"]["consensus_score"] = round(consensus_score, 4)
        simulation_graph["metadata"]["disagreement_score"] = round(1.0 - consensus_score, 4)
        simulation_graph["metadata"]["node_annotations"] = node_annotations
        simulation_graph["metadata"]["solver_states"] = {c.solver_name: c.predicted_state for c in contributions}

        # operations/events/artifacts/latency_ms are what ComparatorRuntime
        # diffs against the executed side (summary is its "expected" half) —
        # operations as the plan's agent names, matching the granularity the
        # execution side reports, so agreement is measurable rather than a
        # guaranteed shape mismatch.
        source_nodes = execution_graph.get("nodes", []) if isinstance(execution_graph, dict) else []
        expected_operations = [
            str(n.get("agent") or n.get("name") or n.get("id", ""))
            for n in source_nodes
            if isinstance(n, dict) and (n.get("agent") or n.get("name") or n.get("id"))
        ]

        # The summary section of the simulation graph metadata provides a concise overview of the simulation results, including the derived answer,
        # grounding score, feasibility verdict, predicted state, expected operations, and other relevant information. This summary is useful for quickly
        # assessing the outcome of the simulation and understanding the level of agreement among solvers.
        simulation_graph["metadata"]["summary"] = {
            "answer": self._derive_answer(contributions, execution_graph),
            "grounding_score": round(consensus_score, 3),
            "feasibility_verdict": (
                "feasible"
                if consensus_score >= 0.8
                else ("likely_feasible" if consensus_score >= 0.5 else "insufficient_data")
            ),
            "predicted_state": self._merge_predicted_state(contributions, execution_graph),
            "operations": expected_operations,
            "events": [],
            "artifacts": [],
            "latency_ms": 0.0,
            "graph_id": simulation_graph.get("graph_id"),
            "timestamp": simulation_graph.get("timestamp"),
        }
        return simulation_graph

    def _derive_answer(self, contributions: list[SolverContribution], execution_graph: dict[str, Any]) -> str:
        for contribution in contributions:
            answer = contribution.predicted_state.get("answer")
            if answer:
                return str(answer)
        nodes = (
            execution_graph.get("nodes", [])
            if isinstance(execution_graph, dict)
            else getattr(execution_graph, "nodes", [])
        )
        return f"Simulated execution graph with {len(nodes)} nodes"

    def _merge_predicted_state(
        self,
        contributions: list[SolverContribution],
        execution_graph: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # Canonical prediction that ComparatorRuntime can diff against the
        # executed side's comparable shape.  Solver-keyed internals are kept
        # under "solver_contributions" for inspection but never compared.
        total_nodes = len((execution_graph or {}).get("nodes", []))
        answer = ""
        for contribution in contributions:
            a = contribution.predicted_state.get("answer")
            if a:
                answer = str(a)
                break
        avg_conf = sum(c.confidence for c in contributions) / max(len(contributions), 1)
        return {
            "answer": answer,
            "nodes_complete": total_nodes,
            "total_nodes": total_nodes,
            "feasibility": (
                "feasible" if avg_conf >= 0.8 else "likely_feasible" if avg_conf >= 0.5 else "insufficient_data"
            ),
        }

    def _score_annotations(self, annotations: dict[str, Any], contribution: SolverContribution) -> float:
        score = float(contribution.confidence)
        score += 0.1 if annotations.get("node_annotations") else 0.0
        score += 0.1 if annotations.get("edge_annotations") else 0.0
        score += 0.1 if annotations.get("diagnostics") else 0.0
        return min(score, 1.0)

    def _consensus_score(
        self,
        execution_graph: dict[str, Any],
        contributions: list[SolverContribution],
        node_annotations: dict[str, list[dict[str, Any]]],
        graph_scores: dict[str, float],
    ) -> float:
        if not contributions:
            return 0.0
        nodes = (
            execution_graph.get("nodes", [])
            if isinstance(execution_graph, dict)
            else getattr(execution_graph, "nodes", [])
        )
        annotation_coverage = min(len(node_annotations) / max(len(nodes), 1), 1.0) if nodes else 0.0
        solver_agreement = self._solver_agreement(graph_scores)
        predicted_state_agreement = self._predicted_state_agreement(contributions)
        return min(
            (annotation_coverage * 0.25) + (solver_agreement * 0.35) + (predicted_state_agreement * 0.4),
            1.0,
        )

    def _solver_agreement(self, graph_scores: dict[str, float]) -> float:
        """Confidence-weighted solver agreement.

        High-confidence solvers (e.g. database-backed, knowledge-grounded)
        contribute more to the agreement score than low-confidence ones
        (e.g. LLM-only predictions).
        """
        if not graph_scores:
            return 0.0
        total_weight = sum(graph_scores.values())
        if total_weight <= 0:
            return 0.0
        # Weighted average: each solver's score contributes proportionally to its confidence
        weighted_avg = sum(s * s for s in graph_scores.values()) / total_weight
        spread = max(graph_scores.values()) - min(graph_scores.values()) if len(graph_scores) > 1 else 0.0
        return max(0.0, min((weighted_avg * 0.7) + ((1.0 - spread) * 0.3), 1.0))

    def _predicted_state_agreement(self, contributions: list[SolverContribution]) -> float:
        """Confidence-weighted agreement between solvers' predicted states.

        High-confidence solvers' predictions carry more weight when
        determining if solvers agree.
        """
        if len(contributions) < 2:
            return 1.0 if contributions else 0.0
        total_weight = sum(c.confidence for c in contributions)
        if total_weight <= 0:
            return 0.0
        weighted_matches = 0.0
        for idx, left in enumerate(contributions):
            for right in contributions[idx + 1 :]:
                weight = (left.confidence + right.confidence) / 2.0
                if left.predicted_state == right.predicted_state:
                    weighted_matches += weight
        max_pairs_weight = sum(
            (contributions[i].confidence + contributions[j].confidence) / 2.0
            for i in range(len(contributions))
            for j in range(i + 1, len(contributions))
        )
        return weighted_matches / max(max_pairs_weight, 1e-6)

    def _is_outlier(self, contribution: SolverContribution, contributions: list[SolverContribution]) -> bool:
        if not contributions:
            return False
        avg = sum(c.confidence for c in contributions) / len(contributions)
        return abs(contribution.confidence - avg) > 0.45

    def _select_best(
        self, contributions: list[SolverContribution], consensus: ConsensusResult
    ) -> SolverContribution | None:
        if not contributions:
            return None
        # Rank only within the accepted pool — a rejected outlier must not
        # win "best prediction". Falls back to the full set if the pool is
        # empty (mirrors _build_consensus's degenerate-consensus fallback).
        pool = [c for c in contributions if c.solver_name in consensus.participating_solvers] or contributions
        canonical_score = consensus.consensus_score
        return max(
            pool,
            key=lambda c: (1.0 - abs(c.confidence - canonical_score), c.confidence),
        )


class LLMReasoningSolver:
    def __init__(self, name: str = "llm_reasoning"):
        self.name = name

    async def solve(self, graph: dict[str, Any]) -> SolverContribution:
        t0 = time.monotonic()
        try:
            from broca.agents.graph_generator_agent import GraphGeneratorAgent

            generator = GraphGeneratorAgent()
            if generator._llm is None:
                clone = self._annotate_graph(graph, {"answer": "LLM not available"}, 0.0, "No LLM available")
                return SolverContribution(
                    self.name,
                    clone,
                    {"answer": "LLM not available"},
                    0.0,
                    "No LLM available",
                    (time.monotonic() - t0) * 1000,
                )

            # Sanitize graph to prevent prompt injection — only include safe fields
            safe_graph = {
                "node_count": len(graph.get("nodes", [])),
                "edge_count": len(graph.get("edges", [])),
                "metadata": {
                    k: v for k, v in graph.get("metadata", {}).items() if k in ("capability", "question", "goal_type")
                },
            }
            prompt = (
                "Predict execution graph outcomes.\n"
                f"Execution Graph Summary: {safe_graph}\n"
                "Return JSON with answer, state_change, confidence."
            )
            response = await generator._llm.generate(prompt, system="You are a prediction engine.")

            import json
            import re

            match = re.search(r"\{.*\}", response, re.DOTALL)
            if match:
                try:
                    result = json.loads(match.group(0))
                    clone = self._annotate_graph(
                        graph,
                        result,
                        float(result.get("confidence", 0.7)),
                        result.get("state_change", ""),
                    )
                    return SolverContribution(
                        self.name,
                        clone,
                        result,
                        float(result.get("confidence", 0.7)),
                        result.get("state_change", ""),
                        (time.monotonic() - t0) * 1000,
                    )
                except json.JSONDecodeError:
                    logger.debug("solve: suppressed exception", exc_info=True)
            clone = self._annotate_graph(graph, {"answer": response[:500]}, 0.5, "Raw LLM response")
            return SolverContribution(
                self.name,
                clone,
                {"answer": response[:500]},
                0.5,
                "Raw LLM response",
                (time.monotonic() - t0) * 1000,
            )
        except Exception as exc:
            clone = self._annotate_graph(graph, {"error": str(exc)}, 0.0, f"Error: {exc}")
            return SolverContribution(
                self.name,
                clone,
                {"error": str(exc)},
                0.0,
                f"Error: {exc}",
                (time.monotonic() - t0) * 1000,
            )

    def _annotate_graph(
        self,
        graph: dict[str, Any],
        prediction: dict[str, Any],
        confidence: float,
        reasoning: str,
    ) -> dict[str, Any]:
        clone = copy.deepcopy(graph)
        clone.setdefault("metadata", {})
        clone["metadata"].setdefault("solver_predictions", {})
        clone["metadata"]["solver_predictions"][self.name] = {
            "prediction": prediction,
            "confidence": confidence,
            "reasoning": reasoning,
        }
        for node in clone.get("nodes", []):
            node.setdefault("annotations", {})
            node["annotations"][self.name] = {
                "prediction": prediction,
                "confidence": confidence,
                "reasoning": reasoning,
            }
        return clone


class KnowledgeBasedSolver:
    def __init__(self, name: str = "knowledge_based"):
        self.name = name

    async def solve(self, graph: dict[str, Any]) -> SolverContribution:
        t0 = time.monotonic()
        md = graph.get("metadata", {}) or {}
        question = str(md.get("question", ""))
        # scope knowledge retrieval to the caller identity carried on the graph metadata
        knowledge = load_knowledge_context(question, identity=_identity_from_state(md))
        confidence = float(knowledge.get("grounding_confidence", 0.0))
        sources = [s.get("pack") for s in knowledge.get("sources", []) if isinstance(s, dict)]
        predicted_state = {
            "knowledge_grounded": confidence > 0.5,
            "sources": sources,
            "context_length": len(knowledge.get("context", "")),
        }
        clone = self._annotate_graph(
            graph,
            predicted_state,
            confidence,
            f"Grounded in {len(sources)} knowledge packs",
        )
        return SolverContribution(
            self.name,
            clone,
            predicted_state,
            confidence,
            f"Grounded in {len(sources)} knowledge packs",
            (time.monotonic() - t0) * 1000,
        )

    def _annotate_graph(
        self,
        graph: dict[str, Any],
        prediction: dict[str, Any],
        confidence: float,
        reasoning: str,
    ) -> dict[str, Any]:
        clone = copy.deepcopy(graph)
        clone.setdefault("metadata", {})
        clone["metadata"].setdefault("solver_predictions", {})
        clone["metadata"]["solver_predictions"][self.name] = {
            "prediction": prediction,
            "confidence": confidence,
            "reasoning": reasoning,
        }
        return clone


class RuleBasedSolver:
    def __init__(self, name: str = "rule_based"):
        self.name = name
        self._rules = {
            "task.create": {"answer": "Task will be created", "confidence": 0.9},
            "task.update": {"answer": "Task will be updated", "confidence": 0.9},
            "task.delete": {"answer": "Task will be deleted", "confidence": 0.9},
            "task.list": {"answer": "Tasks will be listed", "confidence": 0.95},
            "blog.write": {"answer": "Blog post will be written", "confidence": 0.85},
            "blog.edit": {"answer": "Blog post will be edited", "confidence": 0.85},
            "calendar.schedule": {
                "answer": "Event will be scheduled",
                "confidence": 0.8,
            },
            "email.send": {"answer": "Email will be sent", "confidence": 0.85},
            "knowledge.search": {
                "answer": "Knowledge will be searched",
                "confidence": 0.9,
            },
        }

    async def solve(self, graph: dict[str, Any]) -> SolverContribution:
        t0 = time.monotonic()
        capability = graph.get("metadata", {}).get("capability", "unknown")
        rule = self._rules.get(
            capability,
            {"answer": f"Action will be performed for {capability}", "confidence": 0.5},
        )
        clone = self._annotate_graph(
            graph,
            rule,
            float(rule["confidence"]),
            f"Rule-based prediction for {capability}",
        )
        return SolverContribution(
            self.name,
            clone,
            rule,
            float(rule["confidence"]),
            f"Rule-based prediction for {capability}",
            (time.monotonic() - t0) * 1000,
        )

    def _annotate_graph(
        self,
        graph: dict[str, Any],
        prediction: dict[str, Any],
        confidence: float,
        reasoning: str,
    ) -> dict[str, Any]:
        clone = copy.deepcopy(graph)
        clone.setdefault("metadata", {})
        clone["metadata"].setdefault("solver_predictions", {})
        clone["metadata"]["solver_predictions"][self.name] = {
            "prediction": prediction,
            "confidence": confidence,
            "reasoning": reasoning,
        }
        return clone


class _GraphSolverAdapter:
    def __init__(self):
        self.name = "graph_solver"
        try:
            from src.monkey_brain.kernel.predict.graph_solver.graph import GraphSolver

            self._solver = GraphSolver()
        except Exception as exc:
            logger.warning("[simulation] GraphSolver unavailable: %s", exc)
            self._solver = None

    async def solve(self, graph: dict[str, Any]) -> SolverContribution:
        t0 = time.monotonic()
        if self._solver is None:
            clone = self._annotate_graph(graph, {}, 0.0, "Not available")
            return SolverContribution(
                self.name,
                clone,
                {},
                0.0,
                "Not available",
                (time.monotonic() - t0) * 1000,
            )
        result = await self._solver.solve(graph)
        prediction = result.solution if hasattr(result, "solution") else {}
        confidence = result.confidence if hasattr(result, "confidence") else 0.5
        reasoning = result.proof if hasattr(result, "proof") else ""
        clone = self._annotate_graph(graph, prediction, confidence, reasoning)
        return SolverContribution(
            self.name,
            clone,
            prediction,
            confidence,
            reasoning,
            (time.monotonic() - t0) * 1000,
        )

    def _annotate_graph(
        self,
        graph: dict[str, Any],
        prediction: dict[str, Any],
        confidence: float,
        reasoning: str,
    ) -> dict[str, Any]:
        clone = copy.deepcopy(graph)
        clone.setdefault("metadata", {})
        clone["metadata"].setdefault("solver_predictions", {})
        clone["metadata"]["solver_predictions"][self.name] = {
            "prediction": prediction,
            "confidence": confidence,
            "reasoning": reasoning,
        }
        for node in clone.get("nodes", []):
            node.setdefault("annotations", {})
            node["annotations"][self.name] = {
                "prediction": prediction,
                "confidence": confidence,
                "reasoning": reasoning,
            }
        return clone


class _SATSolverAdapter(_GraphSolverAdapter):
    def __init__(self):
        self.name = "sat_solver"
        try:
            from src.monkey_brain.kernel.predict.constraint.sat import SATSolver

            self._solver = SATSolver()
        except Exception as exc:
            logger.warning("[simulation] SATSolver unavailable: %s", exc)
            self._solver = None


class _ConstraintSolverAdapter(_GraphSolverAdapter):
    def __init__(self):
        self.name = "constraint_solver"
        try:
            from src.monkey_brain.kernel.predict.constraint.constraint import (
                ConstraintSolver,
            )

            self._solver = ConstraintSolver()
        except Exception as exc:
            logger.warning("[simulation] ConstraintSolver unavailable: %s", exc)
            self._solver = None


class _RuleEngineSolverAdapter(_GraphSolverAdapter):
    def __init__(self):
        self.name = "rule_engine"
        try:
            from src.monkey_brain.kernel.predict.constraint.rules import (
                RuleEngineSolver,
            )

            self._solver = RuleEngineSolver()
        except Exception as exc:
            logger.warning("[simulation] RuleEngineSolver unavailable: %s", exc)
            self._solver = None


class _ModelCheckerSolverAdapter(_GraphSolverAdapter):
    def __init__(self):
        self.name = "model_checker"
        try:
            from src.monkey_brain.kernel.predict.model_checker.model_checker import (
                ModelCheckerSolver,
            )

            self._solver = ModelCheckerSolver()
        except Exception as exc:
            logger.warning("[simulation] ModelCheckerSolver unavailable: %s", exc)
            self._solver = None


class _OptimizerSolverAdapter(_GraphSolverAdapter):
    def __init__(self):
        self.name = "optimizer"
        try:
            from src.monkey_brain.kernel.predict.optimizer.optimizer import (
                OptimizerSolver,
            )

            self._solver = OptimizerSolver()
        except Exception as exc:
            logger.warning("[simulation] OptimizerSolver unavailable: %s", exc)
            self._solver = None


class _MonteCarloSolverAdapter(_GraphSolverAdapter):
    def __init__(self):
        self.name = "monte_carlo"
        try:
            from src.monkey_brain.kernel.predict.mcts.monte_carlo import (
                MonteCarloSolver,
            )

            self._solver = MonteCarloSolver()
        except Exception as exc:
            logger.warning("[simulation] MonteCarloSolver unavailable: %s", exc)
            self._solver = None


class _JEPAWorldModelAdapter(_GraphSolverAdapter):
    def __init__(self):
        self.name = "jepa_world_model"
        try:
            from src.monkey_brain.kernel.predict.jepa.jepa import JEPAWorldModel

            self._solver = JEPAWorldModel()
        except Exception as exc:
            logger.warning("[simulation] JEPAWorldModel unavailable: %s", exc)
            self._solver = None
