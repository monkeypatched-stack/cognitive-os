"""simulate.py — AgentOS Simulation Routes

Takes a prompt and runs it through the world-model simulator to produce
the predicted (ideal) state, calculate loss against actual query results,
and store Bellman transition values for the planner.

Loss = 1 - (simulator's predicted entities matching query's actual answer).
Two independent reads of the same DB, measuring if they agree.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import time

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from src.monkey_brain.kernel.models.plan import PlanResponse
from src.monkey_brain.runtime.routers import get_mongo_client
from src.monkey_brain.api.dependencies import (
    require_permission,
    sanitize_and_check_governance,
    record_request_audit,
    RequestRejected,
)
from src.monkey_brain.kernel.execute.models import ExecutionMode
from src.monkey_brain.kernel.plan.goals.intent_ir import (
    IntentIRRejected,
    decode_and_validate_ir,
)
from src.monkey_brain.kernel.simulation_runtime import SimulationRuntime
from src.monkey_brain.kernel.comparator_runtime import ComparatorRuntime
from src.monkey_brain.api.helpers.run_helpers import (
    get_cognitive_runtime,
    get_simulation_runtime,
    get_comparator_runtime,
)
from src.introspection.lemon import get_lemon
from src.monkey_brain.kernel.models import (
    SimulateResponse,
    TransitionValuesResponse,
    BellmanCycleResponse,
)
from src.monkey_brain.api.gateway_models import CompareResponseGateway
from src.monkey_brain.kernel.models.graph import canonical_graph_envelope
from src.monkey_brain.api.helpers.simulate_helpers import (
    compare_predicted_vs_actual,
    resolve_workload_name,
    classify_and_setup_ctx,
    fetch_query_answer,
    store_bellman_transition,
)
from src.monkey_brain.api.common.db import get_db
from src.monkey_brain.api.idempotency import idempotent
from src.cortex.world_model_simulation import simulate, get_transition_values

logger = logging.getLogger(__name__)

router = APIRouter()

# Per-operation timeout in seconds for /compare's async operations.
COMPARE_TIMEOUT_SECONDS = float(__import__("os").getenv("COMPARE_TIMEOUT_SECONDS", "180.0"))
# Total timeout for the entire /compare pipeline (sim + exec + diff).
COMPARE_TOTAL_TIMEOUT = float(__import__("os").getenv("COMPARE_TOTAL_TIMEOUT", "600.0"))


def _envelope(graph: Any) -> dict[str, Any]:
    return canonical_graph_envelope(graph)


def _require_target(run_id: str, payload_target: str, expected: str) -> None:
    """Refuse a plan that was not compiled for this runtime.

    /plan records the target it compiled for; each Run endpoint must only run its own.
    Nothing checked this, so a plan compiled with target="execute" could be POSTed to
    /simulate — and RunStore.store_graph(..., target="simulate") would then silently
    REWRITE the stored target, so a later /replay of that run_id dispatched to the
    simulator instead of re-executing it. The stored target is server-side and
    authoritative; the payload's is not (the IntentIR signature does not cover it).
    """
    from src.monkey_brain.kernel.plan.goals.run_store import get_run_store

    stored = get_run_store().get_target(run_id)
    actual = stored or payload_target
    if actual != expected:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Plan for run_id {run_id!r} was compiled with target={actual!r} — "
                f"/{expected} requires a prior /plan?target={expected} call"
            ),
        )


def _reject_ir_http(e: IntentIRRejected) -> None:
    """Render an IntentIRRejected as the HTTP 400 that /simulate and /compare return.

    Preserves the two response shapes those routes already used: a plain string for a
    malformed payload, a structured {error, detail} body for validation failures.
    """
    if e.kind == "malformed":
        raise HTTPException(status_code=400, detail=f"Malformed intent_ir: {e.detail}")
    raise HTTPException(
        status_code=400,
        detail={"error": "intent_ir validation failed", "detail": e.detail},
    )


# Where the simulation actually runs (the solvers are not in this route — it only
# delegates). Tracing "where are the solvers, and whose are they":
#
#   /simulate  → runtime.run(ir, mongo_client)         (below, ~line 142)
#     → SimulationRuntime.run                          (kernel/simulation_runtime.py:100)
#         recovers the planner graph by run_id, then builds a SimulationPipeline and
#         calls pipeline.simulate(ir)
#       → SimulationPipeline.simulate                  (runtime/simulation_pipeline.py:133)
#           runs ALL solvers concurrently (_run_all_solvers), builds consensus, annotates
#       → SimulationPipeline._load_all_solvers         (runtime/simulation_pipeline.py:108)
#           LLMReasoning, KnowledgeBased, RuleBased + up to 8 adapters
#
# So: the simulation uses SimulationPipeline's OWN solvers, NOT the cognitive runtime's.
# They are independent (SimulationRuntime is a separate booted runtime). The only overlap
# is that the LLMReasoningSolver internally reuses GraphGeneratorAgent — but that is the
# solver's own business, not shared execution machinery.
@router.post("/simulate", response_model=SimulateResponse)
@idempotent("predict.run_simulation")
async def run_simulation(
    payload: PlanResponse,
    mongo_client=Depends(get_mongo_client),
    user_id: str = Depends(require_permission("perm-execute-simulate")),
    runtime: SimulationRuntime = Depends(get_simulation_runtime),
):
    """Run Cognitive Workload (SimulationRuntime): validate payload.intent_ir
    (compiled by a prior /plan?target=simulate call) and run the world-model
    simulator against it. Never reclassifies payload.question.
    """
    # 1. Input validation + 2. Governance check (shared with /compare,
    # /execute, /execute/stream — see dependencies.py::sanitize_and_check_governance)
    try:
        payload.question = await sanitize_and_check_governance(payload.question, user_id, "simulate")
    except RequestRejected as e:
        raise HTTPException(status_code=e.status_code, detail=f"{e.error_code}: {e.detail}")

    # 3. Audit
    record_request_audit(user_id, "simulate", "simulate_requested", {"question": payload.question[:200]})

    lemon = get_lemon()

    if not payload.intent_ir:
        raise HTTPException(
            status_code=400,
            detail="Missing intent_ir — /simulate requires a prior /plan?target=simulate call",
        )
    # Decode + validate shared with /execute, /compare, /execute/stream (was the #TODO
    # here): decode_and_validate_ir owns the sequence; _reject_ir_http renders it.
    try:
        ir = decode_and_validate_ir(payload.intent_ir)
    except IntentIRRejected as e:
        _reject_ir_http(e)

    # The plan was compiled for a specific target (simulate, execute, compare). Refuse a plan that was not compiled for this runtime. See _require_target() above.
    _require_target(ir.run_id, payload.target, "simulate")

    intent = ir.intent_type
    logger.info(
        "run=%r [simulate] intent=%r question=%r",
        ir.run_id,
        intent,
        payload.question[:80],
    )

    if lemon:
        lemon.start_trace(
            name="api.simulate",
            trace_id=ir.run_id,
            mode=ExecutionMode.SIMULATION.value,
            user=user_id,
            run_id=ir.run_id,
            intent=intent,
        )
        lemon.counter("api.simulate.request", intent=intent)

    t0 = time.monotonic()
    try:
        # SimulationRuntime.run() does the actual work: it recovers the planner graph by run_id, builds a SimulationPipeline,
        # and calls pipeline.simulate(ir). The pipeline runs ALL solvers concurrently (_run_all_solvers), builds consensus, and annotates the simulation graph. The solvers include LLMReasoning, KnowledgeBased, RuleBased, and up to 8 adapters. The simulation uses SimulationPipeline's OWN solvers, NOT the cognitive runtime's. They are independent (SimulationRuntime is a separate booted runtime). The only overlap is that the LLMReasoningSolver internally reuses GraphGeneratorAgent — but that is the solver's own business, not shared execution machinery.
        result = await runtime.run(ir, mongo_client)
        elapsed_ms = (time.monotonic() - t0) * 1000

        # SimulationRuntime.run reports failure as an {"error", "detail"} dict
        # (missing execution graph, IR validation). Flowing that into
        # SimulateResponse returned HTTP 200 with an empty graph, empty answer
        # and grounding 0.0 — a failed simulation dressed as a low-confidence
        # success. State the failure instead.
        if result.get("error"):
            logger.warning("run=%r [simulate] runtime failed: %s", ir.run_id, result.get("error"))
            if lemon:
                lemon.counter("api.simulate.runtime_error", intent=intent)
            return JSONResponse(
                status_code=500,
                content={
                    "error": result.get("error"),
                    "detail": result.get("detail"),
                    "run_id": ir.run_id,
                },
            )

        grounding_score = result.get("grounding_score", 0.0)
        low_grounding = grounding_score < 0.5
        needs_correction = bool(result.get("needs_correction") or low_grounding)
        simulation_graph = result.get("simulation_graph") or {}
        graph_metadata = simulation_graph.get("metadata", {}) if isinstance(simulation_graph, dict) else {}

        if lemon:
            lemon.counter("api.simulate.success", intent=intent)
            lemon.histogram("api.simulate.latency_ms", elapsed_ms)
            lemon.histogram("api.simulate.grounding_score", grounding_score, intent=intent)
            predictions = result.get("predictions", [])
            sim_nodes = simulation_graph.get("nodes", []) if isinstance(simulation_graph, dict) else []
            consensus = result.get("consensus", {})
            consensus_score = consensus.get("consensus_score", 0.0) if isinstance(consensus, dict) else 0.0
            lemon.gauge("sim.predictions", float(len(predictions)))
            lemon.gauge("sim.nodes", float(len(sim_nodes)))
            lemon.gauge("sim.consensus_score", consensus_score)
            lemon.histogram("sim.grounding_score", grounding_score)
            lemon.histogram("sim.latency_ms", elapsed_ms)
            if result.get("needs_correction"):
                lemon.counter("api.simulate.needs_correction", intent=intent)

        graph_envelope = _envelope(simulation_graph)

        from src.monkey_brain.kernel.plan.goals.run_store import get_run_store

        get_run_store().store_graph(ir.run_id, graph_envelope, target="simulate")

        # Audit: record simulation completion
        try:
            from src.monkey_brain.kernel.audit import get_audit_log

            get_audit_log().record(
                runtime_id=user_id,
                event_type="simulate",
                action="simulate_completed",
                actor=user_id,
                details={
                    "run_id": ir.run_id,
                    "grounding_score": grounding_score,
                    "elapsed_ms": round(elapsed_ms, 2),
                },
            )
        except Exception as exc:
            logger.warning(
                "run=%r [simulate] audit record (simulate_completed) failed: %s",
                ir.run_id,
                exc,
            )

        return SimulateResponse(
            run_id=ir.run_id,
            target="simulate",
            question=payload.question,
            # `or {}` — the result can carry execution_graph=None, and
            # .get("execution_graph", {}) keeps the None, crashing on .get below.
            workload_id=(result.get("execution_graph") or {}).get("graph_id", ""),
            steps=(result.get("execution_graph") or {}).get("nodes", []),
            answer=simulation_graph.get("metadata", {}).get("summary", {}).get("answer", result.get("answer", "")),
            grounding_confidence=grounding_score,
            grounding_score=grounding_score,
            low_grounding=low_grounding,
            needs_correction=needs_correction,
            simulation_id=result.get("simulation_id", ""),
            user_id=user_id,
            elapsed_ms=elapsed_ms,
            metadata={
                **graph_metadata,
                "simulation_id": result.get("simulation_id", ""),
                "simulation_graph": simulation_graph,
                "execution_graph": result.get("execution_graph"),
                "grounding_score": grounding_score,
                "feasibility_verdict": result.get("feasibility_verdict", "unknown"),
            },
            intent=result.get("intent"),
            context=result.get("context"),
            graph=graph_envelope,
            graph_id=graph_envelope["graph_id"],
            graph_type=graph_envelope["graph_type"],
            timestamp=graph_envelope["timestamp"],
            nodes=graph_envelope["nodes"],
            edges=graph_envelope["edges"],
            execution_order=graph_envelope["execution_order"],
            annotations=graph_envelope["annotations"],
            state=graph_envelope["state"],
            intent_ir=result.get("intent_ir"),
        )
    except Exception as e:
        # Bare re-raise gave the client an unstructured "Internal Server Error"
        # — /plan and /execute both return the structured shape below.
        logger.error("run=%r [simulate] user=%r failed: %s", ir.run_id, user_id, e)
        if lemon:
            lemon.counter("api.simulate.error", intent=intent)
            lemon.error(str(e), component="api.simulate")
        return JSONResponse(
            status_code=500,
            content={
                "error": "Simulation failed",
                "detail": "An internal error occurred during simulation",
                "run_id": ir.run_id,
            },
        )
    finally:
        if lemon:
            lemon.finish_trace()


@router.post("/compare", response_model=CompareResponseGateway)
@idempotent("predict.compare_sim_vs_query")
async def compare_sim_vs_query(
    payload: PlanResponse,
    mongo_client=Depends(get_mongo_client),
    # /compare does BOTH halves: it simulates AND it executes the workload for real
    # (execute_cognitive_workload in ExecutionMode.EXECUTE, live agents, real side
    # effects). It was gated on perm-execute-simulate ALONE, so a principal holding only
    # the dry-run permission could drive a full production execution through this route
    # and never present perm-execute-action — the permission whose entire job is to gate
    # committing work. Require both, since the endpoint genuinely does both.
    user_id: str = Depends(require_permission("perm-execute-action")),
    _simulate_perm: str = Depends(require_permission("perm-execute-simulate")),
    runtime: ComparatorRuntime = Depends(get_comparator_runtime),
    sim_runtime: SimulationRuntime = Depends(get_simulation_runtime),
    cog_runtime: Any = Depends(get_cognitive_runtime),
):
    """Run Cognitive Workload (ComparatorRuntime): validate payload.intent_ir
    (compiled by a prior /plan?target=compare call) and diff the simulator's
    predicted state against the actual query answer. Never reclassifies
    payload.question.

    Flow (inside ComparatorRuntime.run):
      1. Capture simulator's entity snapshot (predicted state)
      2. Run query to get actual answer
      3. Compare: does the query's answer agree with the simulator's data?
      4. Store Bellman transition with the computed loss
    """
    lemon = get_lemon()

    # Same input/governance/audit gates as /simulate and /execute — /compare
    # commits real execution (the cognitive side runs the workload for real),
    # so it must not be the one runtime endpoint that skips them. Shared
    # logic with /simulate, /execute, /execute/stream — see
    # dependencies.py::sanitize_and_check_governance.
    try:
        payload.question = await sanitize_and_check_governance(payload.question, user_id, "compare")
    except RequestRejected as e:
        raise HTTPException(status_code=e.status_code, detail=f"{e.error_code}: {e.detail}")

    record_request_audit(user_id, "compare", "compare_requested", {"question": payload.question[:200]})

    if not payload.intent_ir:
        raise HTTPException(
            status_code=400,
            detail="Missing intent_ir — /compare requires a prior /plan?target=compare call",
        )
    try:
        ir = decode_and_validate_ir(payload.intent_ir)
    except IntentIRRejected as e:
        _reject_ir_http(e)

    _require_target(ir.run_id, payload.target, "compare")

    if lemon:
        lemon.start_trace(
            name="api.compare",
            trace_id=ir.run_id,
            mode=ExecutionMode.SIMULATION.value,
            user=user_id,
            run_id=ir.run_id,
        )
        lemon.counter("api.compare.request")

    pipeline_start = time.monotonic()
    try:
        result = await asyncio.wait_for(
            _compare_pipeline(
                ir,
                payload,
                sim_runtime,
                cog_runtime,
                runtime,
                mongo_client,
                lemon,
                user_id,
            ),
            timeout=COMPARE_TOTAL_TIMEOUT,
        )
        # _compare_pipeline already returns a well-formed CompareResponseGateway
        # (status/loss/details) on success — this was previously discarded,
        # so the route fell through and implicitly returned None, which
        # FastAPI's response_model validation then rejected with a bare
        # 500 (ResponseValidationError: input should be a valid dictionary
        # or object, input=None). Confirmed live via a real /compare call.
        return result
    except asyncio.TimeoutError:
        elapsed = time.monotonic() - pipeline_start
        logger.error(
            "run=%r [compare] TOTAL timeout after %.1fs (limit %.1fs)",
            ir.run_id,
            elapsed,
            COMPARE_TOTAL_TIMEOUT,
        )
        if lemon:
            lemon.counter("api.compare.total_timeout")
        return JSONResponse(
            status_code=504,
            content={
                "error": "Comparison timed out",
                "detail": f"Total pipeline exceeded {COMPARE_TOTAL_TIMEOUT}s timeout",
                "run_id": ir.run_id,
            },
        )
    except Exception as e:
        logger.error("run=%r [compare] user=%r failed: %s", ir.run_id, user_id, e)
        if lemon:
            lemon.counter("api.compare.error")
            lemon.error(str(e), component="api.compare")
        return JSONResponse(
            status_code=500,
            content={
                "error": "Comparison failed",
                "detail": "An internal error occurred during comparison",
                "run_id": ir.run_id,
            },
        )


async def _compare_pipeline(ir, payload, sim_runtime, cog_runtime, runtime, mongo_client, lemon, user_id):
    """Inner compare pipeline — simulation + execution + diff."""
    from src.monkey_brain.kernel.plan.goals.run_store import get_run_store

    stored_graph = get_run_store().get_graph(ir.run_id)
    if stored_graph is None:
        from src.monkey_brain.persistence.plan_store import get_plan_store

        stored_graph = get_plan_store().load_local(ir.run_id)
    if not (stored_graph and stored_graph.get("nodes")):
        if lemon:
            lemon.counter("api.compare.missing_execution_graph")
        raise ValueError("Missing planner execution graph — compare requires the ExecutionGraph stored by run_id")

    from src.monkey_brain.api.routes.execute import _graph_to_plan_steps

    plan_steps = _graph_to_plan_steps(stored_graph)

    logger.info("run=%r [compare] starting simulation + execution in parallel...", ir.run_id)
    pipeline_start = time.monotonic()

    sim_task = asyncio.ensure_future(sim_runtime.run(ir, mongo_client))
    exec_task = asyncio.ensure_future(
        cog_runtime.execute_cognitive_workload(
            cog_runtime.build_execution_runtime(ir, ExecutionMode.EXECUTE, user_id=user_id),
            mongo_client,
            plan_steps=plan_steps,
        )
    )

    sim_result, exec_result = await asyncio.gather(sim_task, exec_task)

    pipeline_elapsed = time.monotonic() - pipeline_start
    logger.info("run=%r [compare] sim+exec done in %.1fs", ir.run_id, pipeline_elapsed)

    if sim_result.get("error"):
        raise RuntimeError(f"Simulation failed: {sim_result.get('error')}")

    plan_graph = dict(stored_graph)
    answer = exec_result[0]
    llm_answered = exec_result[3]
    total_nodes = len(plan_graph.get("nodes", []))
    exec_success = bool(llm_answered) and not str(answer).lower().startswith("error executing goal")
    completed_nodes = total_nodes if exec_success else 0
    exec_confidence = round(completed_nodes / max(total_nodes, 1), 4)
    execution_result = {
        "answer": answer,
        "semantic_hits": exec_result[1],
        "graph_paths": exec_result[2],
        "llm_answered": llm_answered,
        "state": {
            "answer": answer,
            "nodes_complete": completed_nodes,
            "total_nodes": total_nodes,
            "feasibility": "feasible" if exec_success else "infeasible",
        },
        "confidence": exec_confidence,
        "latency_ms": round(pipeline_elapsed * 1000, 2),
        "operations": [s.get("agent") or s.get("capability_name") or s.get("name", "") for s in plan_steps],
        "events": [],
        "artifacts": [],
        "nodes": plan_graph.get("nodes", []),
        "edges": plan_graph.get("edges", []),
        "execution_order": plan_graph.get("execution_order", []),
        "graph_id": plan_graph.get("graph_id", ""),
    }
    logger.info("run=%r [compare] starting comparison...", ir.run_id)
    result = await runtime.compare(sim_result.get("simulation_graph", {}), execution_result)
    result = result.to_dict() if hasattr(result, "to_dict") else result
    total_elapsed = time.monotonic() - pipeline_start
    logger.info(
        "run=%r [compare] done in %.1fs (sim+exec: %.1fs)",
        ir.run_id,
        total_elapsed,
        pipeline_elapsed,
    )
    result = result.to_dict() if hasattr(result, "to_dict") else result
    logger.info("run=%r [compare] question=%r", ir.run_id, payload.question[:60])

    if lemon:
        lemon.histogram("api.compare.topology_loss", float(result.get("topology_loss", 0.0)))
        lemon.histogram("api.compare.epistemic_loss", float(result.get("epistemic_loss", 0.0)))
        lemon.histogram("api.compare.world_loss", float(result.get("world_loss", 0.0)))
        lemon.histogram("api.compare.policy_loss", float(result.get("policy_loss", 0.0)))
        lemon.histogram("api.compare.actor_loss", float(result.get("actor_loss", 0.0)))
        lemon.counter("api.compare.success")

    return CompareResponseGateway(
        status="ok",
        loss=float(result.get("epistemic_loss", 0.0)),
        details=result,
    )


@router.post("/learn")
@idempotent("predict.learn")
async def learn(
    # These drive a nested `for batch: for epoch:` loop with NO await inside it, so the
    # whole thing runs to completion on the event loop. Unbounded, one request with
    # batch_size=1000000&epochs=1000000 asks for 10^12 iterations — measured at ~3µs
    # each, that is ~35 DAYS of a fully blocked event loop, i.e. the entire server, not
    # just the caller's request. Bound both; 100x100 = 10k iterations is ~30ms.
    batch_size: int = Query(default=10, ge=1, le=100),
    epochs: int = Query(default=5, ge=1, le=100),
    mongo_client=Depends(get_mongo_client),
    user_id: str = Depends(require_permission("perm-execute-simulate")),
    comparator: ComparatorRuntime = Depends(get_comparator_runtime),
):
    """POST /learn — iterative learning loop.

    Loads the latest execution and simulation graphs from the run store,
    computes topological loss between them, updates the Q-table via
    Bellman TD updates, and returns per-iteration metrics.
    """
    # Governance check
    try:
        from src.monkey_brain.kernel.governance import get_governance_engine

        gov = get_governance_engine()
        gov_result = await gov.evaluate(user_id, "learn", {"batch_size": batch_size, "epochs": epochs})
        if not gov_result.get("allowed"):
            raise HTTPException(status_code=403, detail=f"governance_denied: {gov_result.get('reason')}")
    except HTTPException:
        raise
    except ImportError:
        logger.error("Governance module not available — denying learn")
        raise HTTPException(status_code=500, detail="Governance unavailable")
    except Exception as exc:
        logger.error("Governance check failed — denying learn: %s", exc)
        raise HTTPException(status_code=500, detail="Governance check failed")

    # Audit
    try:
        from src.monkey_brain.kernel.audit import get_audit_log

        get_audit_log().record(
            runtime_id=user_id,
            event_type="learn",
            action="learn_requested",
            actor=user_id,
            details={"batch_size": batch_size, "epochs": epochs},
        )
    except Exception as exc:
        logger.warning("Audit recording failed for learn request: %s", exc)

    lemon = get_lemon()
    if lemon:
        lemon.start_trace(name="api.learn", user=user_id)
        lemon.counter("api.learn.request")

    t0 = time.monotonic()
    results = []
    converged = False

    # Wall-clock timeout: prevent slow iterations from blocking the event loop.
    LEARN_TIMEOUT_SECONDS = float(__import__("os").getenv("LEARN_TIMEOUT_SECONDS", "30.0"))

    try:
        from src.monkey_brain.kernel.graph_manager import GraphManager
        from src.monkey_brain.kernel.plan.goals.run_store import get_run_store
        from src.monkey_brain.kernel.temporal_graph import clone_graph_snapshot

        gm = GraphManager()
        gm.load()
        store = get_run_store()

        total_iterations = batch_size * epochs
        iteration = 0

        # The two sides of the diff. Topological loss means "did the shape the
        # simulator PREDICTED match the shape that actually RAN" — it needs two
        # different graphs. This used to take the single most recent snapshot of
        # any kind and use it as BOTH sides (gm.graph = plan_graph, then
        # sim_graph = canonical_graph_envelope(plan_graph)), so it diffed a graph
        # against a copy of itself: topological_loss was 0.0 by construction on
        # every run, and the Q-table was rewarded 1.0 for it. The learner could
        # not learn, because it never measured anything.
        exec_graph = store.latest_graph(target="execute")
        sim_graph = store.latest_graph(graph_type="simulation")
        have_both = bool(exec_graph and sim_graph)

        if exec_graph:
            gm.graph = clone_graph_snapshot(exec_graph)

        # Real measured loss from the last /compare, when one has run this
        # process. Hierarchical losses from the comparator:
        #   topology_loss: structural correctness
        #   epistemic_loss: knowledge correctness
        #   world_loss = topology + epistemic
        #   policy_loss: action quality
        #   actor_loss = world + policy
        last_cmp = getattr(comparator, "get_last_comparison", lambda: None)()
        topology_loss: float | None = None
        epistemic_loss_cmp: float | None = None
        world_loss: float | None = None
        policy_loss: float | None = None
        actor_loss: float | None = None
        if isinstance(last_cmp, dict):
            if "topology_loss" in last_cmp:
                topology_loss = max(0.0, min(1.0, float(last_cmp["topology_loss"])))
            if "epistemic_loss" in last_cmp:
                epistemic_loss_cmp = max(0.0, min(1.0, float(last_cmp["epistemic_loss"])))
            if "world_loss" in last_cmp:
                world_loss = max(0.0, min(1.0, float(last_cmp["world_loss"])))
            if "policy_loss" in last_cmp:
                policy_loss = max(0.0, min(1.0, float(last_cmp["policy_loss"])))
            if "actor_loss" in last_cmp:
                actor_loss = max(0.0, min(1.0, float(last_cmp["actor_loss"])))

        # Determine loss source for reporting
        if have_both and world_loss is not None:
            loss_source = "world+policy"
        elif have_both:
            loss_source = "topological"
        elif world_loss is not None:
            loss_source = "comparator"
        else:
            loss_source = "none"

        for batch in range(1, batch_size + 1):
            for epoch in range(1, epochs + 1):
                iteration += 1

                # Wall-clock timeout check
                if time.monotonic() - t0 > LEARN_TIMEOUT_SECONDS:
                    logger.warning(
                        "Learn loop timed out after %.1fs at iteration %d",
                        LEARN_TIMEOUT_SECONDS,
                        iteration,
                    )
                    converged = True
                    break

                if have_both:
                    # Real signal: the simulator's predicted shape vs the shape
                    # that actually executed.
                    topo_loss = gm._topological_loss(sim_graph)

                    # World loss: topology + epistemic from comparator, or local estimate
                    if world_loss is not None:
                        effective_world_loss = world_loss
                    else:
                        effective_world_loss = topo_loss

                    # Policy loss: from comparator
                    effective_policy_loss = policy_loss if policy_loss is not None else 0.0

                    # Actor loss: world + policy (canonical formula)
                    effective_actor_loss = min(1.0, effective_world_loss + effective_policy_loss)

                    # Per-agent credit assignment. Each agent is rewarded for ITS OWN
                    # execution outcome (did this node complete, fail, or go unimplemented) —
                    # NOT the whole graph's world-model accuracy. This fixes two things at once:
                    #   1. Coarse credit: previously every agent got the same reward, so a good
                    #      agent in a bad run was penalised and a failed agent free-rode on the
                    #      others. Now a completed node earns ~1.0 and a failed one ~0.0, so Q
                    #      converges to each agent's real quality.
                    #   2. Split leak: the old reward was 1 - world_loss, folding the WORLD
                    #      signal back into the POLICY reward the comparator split just
                    #      separated. Outcome-based reward is a pure policy signal; only the
                    #      fallback (no per-node outcome recorded) uses the run's actor quality.
                    node_states = gm.graph.get("state", {})
                    node_states = node_states if isinstance(node_states, dict) else {}
                    run_reward = max(0.0, 1.0 - effective_actor_loss)
                    # Reported run-level environment signal (per-agent rewards are applied in
                    # the loop below; this is the single value surfaced in the response/metrics).
                    environment_reward = run_reward
                    for node in gm.graph.get("nodes", []):
                        agent = node.get("agent", node.get("name", ""))
                        if not agent:
                            continue
                        entry = node_states.get(str(node.get("id", "")), {})
                        status = str(
                            node.get("state") or (entry.get("status") if isinstance(entry, dict) else "") or ""
                        )
                        if status == "complete":
                            agent_reward = 1.0
                        elif status in ("failed", "unimplemented"):
                            agent_reward = 0.0
                        else:
                            agent_reward = run_reward  # no per-node outcome — use run quality
                        gm.q_table.update_with_replay(agent, agent_reward)

                    gm._last_loss = effective_actor_loss

                    q_values = list(gm.q_table.snapshot().values())
                    avg_q = sum(q_values) / max(len(q_values), 1) if q_values else 0.5
                    # Uncertainty floors at zero — it never goes below.
                    policy_uncertainty = max(0.0, min(1.0, 1.0 - avg_q))
                    perplexity = max(0.0, effective_actor_loss + policy_uncertainty)
                else:
                    # No pair to diff. Use comparator losses if available.
                    topo_loss = None
                    effective_world_loss = world_loss if world_loss is not None else 0.0
                    effective_policy_loss = policy_loss if policy_loss is not None else 0.0
                    effective_actor_loss = actor_loss if actor_loss is not None else 0.0
                    environment_reward = 1.0 - effective_world_loss
                    q_values = list(gm.q_table.snapshot().values())
                    avg_q = sum(q_values) / max(len(q_values), 1) if q_values else 0.5
                    policy_uncertainty = max(0.0, min(1.0, 1.0 - avg_q))
                    perplexity = max(0.0, effective_actor_loss + policy_uncertainty)

                entry = {
                    "batch": batch,
                    "epoch": epoch,
                    "iteration": iteration,
                    "perplexity": round(perplexity, 4),
                    "topology_loss": (round(topo_loss, 4) if topo_loss is not None else None),
                    "topology_loss_cmp": (round(topology_loss, 4) if topology_loss is not None else None),
                    "epistemic_loss_cmp": (round(epistemic_loss_cmp, 4) if epistemic_loss_cmp is not None else None),
                    "world_loss": round(effective_world_loss, 4),
                    "policy_loss": round(effective_policy_loss, 4),
                    "actor_loss": round(effective_actor_loss, 4),
                    "environment_reward": round(environment_reward, 4),
                    "loss_source": loss_source,
                    "policy_uncertainty": round(policy_uncertainty, 4),
                    "q_value": round(avg_q, 4),
                    "has_graphs": have_both,
                }
                results.append(entry)

                if lemon:
                    lemon.histogram("api.learn.perplexity", perplexity)
                    lemon.histogram("api.learn.q_value", avg_q)
                    lemon.gauge("learn.world_loss", effective_world_loss)
                    lemon.gauge("learn.policy_loss", effective_policy_loss)
                    lemon.gauge("learn.actor_loss", effective_actor_loss)
                    lemon.gauge("learn.environment_reward", environment_reward)
                    if topo_loss is not None:
                        lemon.gauge("learn.topology_loss", topo_loss)
                    lemon.gauge("learn.bellman_q", avg_q)
                    if have_both:
                        for node in gm.graph.get("nodes", []):
                            agent = node.get("agent", node.get("name", ""))
                            if agent:
                                q_val = gm.q_table.get(agent, 0.5)
                                lemon.observe_learning(
                                    capability=agent,
                                    state_key=f"{agent}:{iteration}",
                                    q_before=q_val,
                                    q_after=q_val,
                                    reward=1.0 - effective_loss,
                                    exploration=False,
                                    episode=iteration,
                                )

                # Convergence means a loss we actually measured fell to zero. With
                # no signal at all (no graphs to diff, no prior /compare) perplexity
                # is 0.0 for want of a measurement, and this reported "converged" —
                # the loop's most optimistic possible answer, on no evidence.
                if perplexity <= 0.0 and (have_both or world_loss is not None or policy_loss is not None):
                    converged = True
                    break

            if converged:
                break

        gm.save()

        elapsed_ms = (time.monotonic() - t0) * 1000

        # Audit: record learning completion
        try:
            from src.monkey_brain.kernel.audit import get_audit_log

            get_audit_log().record(
                runtime_id=user_id,
                event_type="learn",
                action="learn_completed",
                actor=user_id,
                details={
                    "iterations": len(results),
                    "converged": converged,
                    "final_perplexity": results[-1]["perplexity"] if results else 0,
                    "elapsed_ms": round(elapsed_ms, 2),
                },
            )
        except Exception as exc:
            logger.warning("Audit recording failed for learn completion: %s", exc)

        if lemon:
            lemon.counter("api.learn.success")
            lemon.histogram("api.learn.elapsed_ms", elapsed_ms)
            lemon.finish_trace()

        return {
            "status": "converged" if converged else "max_iterations",
            "batch_size": batch_size,
            "epochs": epochs,
            "total_iterations": total_iterations,
            "iterations_completed": len(results),
            "loss_source": loss_source,
            "comparator_world_loss": world_loss,
            "comparator_policy_loss": policy_loss,
            # Was a structural loss measurable at all? Without both an execution
            # and a simulation graph in the run store there is nothing to diff,
            # and the Q-updates below rest on the comparator's loss alone.
            "measured_topological_loss": have_both,
            "final_perplexity": results[-1]["perplexity"] if results else 0.0,
            "final_q_value": results[-1]["q_value"] if results else 0.0,
            "elapsed_ms": round(elapsed_ms, 2),
            "results": results,
        }

    except Exception as e:
        # A failed learning run used to come back HTTP 200 with an "error" key —
        # every other runtime endpoint reports failure with a non-200 status.
        logger.error("Learning loop failed: %s", e)
        # Save Q-table on exception to preserve partial learning progress.
        if "gm" in locals():
            for _save_attempt in range(3):
                try:
                    gm.save()
                    break
                except Exception as save_exc:
                    if _save_attempt == 2:
                        logger.error("Failed to save Q-table after 3 attempts: %s", save_exc)
                    else:
                        logger.warning(
                            "Q-table save attempt %d failed, retrying: %s",
                            _save_attempt + 1,
                            save_exc,
                        )
                        time.sleep(0.1 * (_save_attempt + 1))
        if lemon:
            lemon.counter("api.learn.error")
            lemon.finish_trace()
        return JSONResponse(
            status_code=500,
            content={
                "error": "Learning loop failed",
                "detail": "An internal error occurred during learning",
                "iterations_completed": len(results),
                "results": results,
            },
        )


@router.get("/transitions", response_model=TransitionValuesResponse)
async def list_transition_values(
    action: str | None = None,
    # `limit` is passed straight to a Mongo .limit(): unbounded, it let a caller
    # ask for the entire transitions collection in one request, and a negative
    # value reaches pymongo with its own (quite different) meaning. Bound it.
    limit: int = Query(default=100, ge=1, le=1000),
    mongo_client=Depends(get_mongo_client),
    user_id: str = Depends(require_permission("perm-view-transitions")),
):
    """Retrieve Bellman transition values for the planner."""
    db = get_db(mongo_client)
    result = await get_transition_values(db, action=action, limit=limit)
    return TransitionValuesResponse(**result)


@router.post("/test-bellman-cycle", response_model=BellmanCycleResponse)
@idempotent("predict.test_bellman_cycle")
async def test_bellman_cycle(
    question: str = "How many machines are in the system?",
    mongo_client=Depends(get_mongo_client),
    user_id: str = Depends(require_permission("perm-execute-simulate")),
):
    """Run a sample workload through the full Bellman cycle and return results.

    Verifies: simulate → query → loss → transition store → transition lookup.
    """
    # Input validation — this reaches the simulator and a Mongo query.
    try:
        from src.monkey_brain.kernel.security import sanitize_input

        question = sanitize_input(question)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"invalid_input: {e}")

    db = get_db(mongo_client)
    if db is None:
        # The last two stages of the cycle this endpoint exists to verify (store
        # the transition, read it back) need the database. Without it the run
        # still returned 200 with transition.stored=false and empty bellman_values
        # — a verification that verified nothing, reported as a pass.
        raise HTTPException(
            status_code=503,
            detail="Bellman cycle cannot be verified without a database — transition store/lookup unavailable",
        )

    normalized, goal_obj, ctx = await classify_and_setup_ctx(question, None, db)
    sim_result = await simulate(normalized, context=ctx)
    predicted_state = sim_result.get("state_after") or sim_result.get("state_before") or {}

    query_answer = await fetch_query_answer(question, mongo_client)
    loss_result = compare_predicted_vs_actual(predicted_state, query_answer, question)
    workload_name = resolve_workload_name(sim_result, goal_obj)

    transition_stored = await store_bellman_transition(
        db,
        sim_result,
        question,
        predicted_state,
        query_answer,
        loss_result,
        workload_name,
    )

    bellman_values: dict[str, Any] = {}
    try:
        # get_transition_values takes `pipeline_name`, not `workload_name` — the old
        # kwarg raised TypeError into this except, so bellman_values was ALWAYS empty
        # and the "transition lookup" stage of the cycle never actually ran.
        tv = await get_transition_values(db, pipeline_name=workload_name, limit=10)
        bellman_values = tv.get("action_values", {})
    except Exception as e:
        logger.warning("[test-bellman-cycle] get_transition_values failed: %s", e)

    return BellmanCycleResponse(
        question=question,
        workload_name=workload_name,
        simulate={
            "simulation_id": sim_result.get("simulation_id"),
            "grounding_score": sim_result.get("grounding_score"),
            "feasibility_verdict": sim_result.get("feasibility_verdict"),
            "layers_activated": sim_result.get("layers_activated"),
            "entity_count": len(predicted_state.get("entities", {})),
        },
        query_answer_preview=query_answer[:500],
        loss=loss_result,
        transition={"stored": transition_stored, "workload_name": workload_name},
        bellman_values=bellman_values,
    )
