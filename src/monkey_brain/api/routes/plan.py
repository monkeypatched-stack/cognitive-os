"""Plan route — returns the execution graph without executing it.

POST /api/v1/agentos/plan
  Synthesize an execution graph via the planner, build IntentIR from it,
  and return the complete DAG. Nothing is executed. Use this to inspect
  what the runtime would do before committing to execution.

Flow: Question → Planner → Execution Graph → IntentIR
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from src.introspection.lemon import get_lemon
from src.monkey_brain.api.dependencies import require_permission
from src.monkey_brain.api.helpers.run_helpers import (
    _get_grounding_confidence,
    get_cognitive_runtime,
)
from src.monkey_brain.api.idempotency import idempotent
from src.monkey_brain.kernel.cognitive_runtime import CognitiveRuntime
from src.monkey_brain.kernel.config import PLANNING_CONFIDENCE_THRESHOLD
from src.monkey_brain.kernel.execute.models import ExecutionMode
from src.monkey_brain.kernel.execute.orchestration.routing import unsupported_response
from src.monkey_brain.kernel.learn.telemetry.telemetry import (
    profile_add,
    profile_end,
    profile_start,
)
from src.monkey_brain.kernel.models.plan import PlanRequest, PlanResponse
from src.monkey_brain.persistence.plan_store import get_plan_store
from src.monkey_brain.runtime.routers import get_mongo_client

logger = logging.getLogger("agentos.plan")

router = APIRouter()

# Overlap ratio at or above which the top-ranked prior graph is adopted
# outright — the drafted plan is replaced by the proven one. Below it the
# candidate stays advisory (attached to the response, never blocking).
MESH_REUSE_ADOPT_THRESHOLD = float(os.getenv("MESH_REUSE_ADOPT_THRESHOLD", "0.8"))


def _get_graph_store(request: Request) -> Any:
    return getattr(request.app.state, "_graph_store", None) or getattr(request.app.state, "graph_store", None)


def _available_agent_types() -> list[str]:
    """Return registered Broca agent types for the planner prompt."""
    try:
        from broca.registry import register_etass_agents

        return sorted(register_etass_agents())
    except Exception as exc:
        # Degrades planning quality (the prompt loses its agent list), so don't hide it —
        # return the empty default but say why.
        logger.warning(
            "[plan] could not load registered agent types for the planner prompt: %s",
            exc,
        )
        return []


def _normalize_execution_order(order: Any) -> list[list[str]]:
    """Preserve nested list structure for execution_order."""
    if not isinstance(order, list):
        return [[str(order)] if order is not None else []]
    result: list[list[str]] = []
    for item in order:
        if isinstance(item, list):
            result.append([str(n) for n in item])
        elif isinstance(item, str):
            result.append([item])
        elif item is not None:
            result.append([str(item)])
    return result


# This is the planning agent it uses LLM to creates multiple plans for the question a
# and returns the plan without executing it. each plan execution updates the policy weights
# based on the plan selected and the grounding confidence of the knowledge pack.
@router.post("/plan", response_model=PlanResponse)
@idempotent("plan.execute")
async def plan_execution(
    payload: PlanRequest,
    request: Request,
    mongo_client: Any = Depends(get_mongo_client),
    user_id: str = Depends(require_permission("perm-execute-plan")),
    runtime: CognitiveRuntime = Depends(get_cognitive_runtime),
    grounding_confidence: float = Depends(_get_grounding_confidence),
) -> Any:
    """POST /plan — select a workload for the question without executing it.

    Returns the workload_id, steps, and grounding_confidence the runtime computed.
    low_grounding=True means the knowledge pack had insufficient coverage at plan
    time — consider retrieving more data before committing to execution.
    Requires perm-execute-plan.

    1. Input validation via security module
    2. get the graph store from the request context; this is used to persist the execution graph and related data
    3. Governance check — validate against charter before planning; basically
       this is a policy check to see if the user is allowed to plan with the
       given question
    4. Audit: record plan request
    5. Telemetry: start a trace for the plan request if Lemon is available
    6. Planner synthesizes the execution graph, then IntentIR is built from the graph output.
        6.1 ── Question → Planner → Graph ──────────────────────────────
        6.2 ── Planner failed → unsupported_response ───────────────────
        6.3 ── Graph generated → Build IntentIR → Return Plan ──────
        6.4 ── Build IntentIR ────────────────────────────────────────────────
        6.5 ── Store in RunStore ───────────────────────────────────────────────
        6.6 ── Store in PlanStore -───────────────────────────────────────────────
        6.7 ── check the knowledge pack's grounding confidence and log a
              warning if it's below the threshold ──────────────────────
        6.8 ── log the plan selection and metrics to lemon if available ─
        6.9 ── Cache Capabilities and Agents used in the plan for future reuse. ─────
        6.10. ── A planner that produced no nodes produced no plan. -────────────────────────────────────────────
        6.11 ── Return the plan. ────────────────────────────────────────────
    """

    # 1. Input validation via security module
    try:
        from src.monkey_brain.kernel.security import sanitize_input

        # Sanitize the question to prevent injection attacks or malicious content
        payload.question = sanitize_input(payload.question)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": "invalid_input", "detail": str(e)})

    # 2. Generate run_id first (so it can tag any error below), then resolve the graph
    #    store from the request context — it persists the execution graph. A failure here
    #    is a real 500, not an unhandled exception leaking FastAPI's default error page.
    run_id = uuid4().hex
    try:
        graph_store = _get_graph_store(request)
    except Exception as exc:
        logger.error("run=%r [plan] failed to resolve graph store: %s", run_id, exc)
        return JSONResponse(
            status_code=500,
            content={
                "error": "graph_store_error",
                "detail": "Failed to access the graph store",
                "run_id": run_id,
            },
        )
    logger.info("run=%r [plan] user=%r question=%r", run_id, user_id, payload.question[:100])

    # 3. Governance check — validate against charter before planning (is this user allowed
    #    to plan this question?). Both get_governance_engine() and evaluate() run inside this
    #    try: an unexpected failure denies with 500, while ImportError (module not installed)
    #    skips the check rather than failing the request.
    try:
        from src.monkey_brain.kernel.governance import get_governance_engine

        gov = get_governance_engine()
        gov_result = await gov.evaluate(user_id, "plan", {"question": payload.question[:200]})

        if not gov_result.get("allowed"):
            logger.warning("run=%r [plan] governance denied: %s", run_id, gov_result.get("reason"))
            return JSONResponse(
                status_code=403,
                content={
                    "error": "governance_denied",
                    "detail": gov_result.get("reason"),
                },
            )
    except ImportError:
        logger.error("Governance module not available — denying plan")
        return JSONResponse(
            status_code=500,
            content={"error": "governance_error", "detail": "Governance unavailable"},
        )
    except Exception as exc:
        logger.error("Governance check failed — denying plan: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"error": "governance_error", "detail": "Governance check failed"},
        )

    # 4. Audit: record plan request. Best-effort observability — a failed audit write must
    #    not block planning, but it must not vanish silently either (it may be a compliance
    #    signal). Surface it loudly. (lemon isn't wired until step 5, so no counter here.)
    try:
        from src.monkey_brain.kernel.audit import get_audit_log

        get_audit_log().record(
            runtime_id=user_id,
            event_type="plan",
            action="plan_requested",
            actor=user_id,
            details={"question": payload.question[:200], "run_id": run_id},
        )
    except Exception as exc:
        logger.warning("run=%r [plan] audit record (plan_requested) failed: %s", run_id, exc)

    # 5. Telemetry: start a trace for the plan request if Lemon is available
    lemon = get_lemon()

    if lemon:
        lemon.start_trace(
            name="api.plan",
            trace_id=run_id,
            mode=ExecutionMode.PLANNING.value,
            user=user_id,
            run_id=run_id,
            question_preview=payload.question[:80],
        )
        lemon.counter("api.plan.request")

    profile_start()
    t0 = time.monotonic()

    # 6. Planner synthesizes the execution graph, then IntentIR is built from the graph output.
    #    Everything from here down runs inside ONE try/except: PlannerConvergenceError returns
    #    422 with the validation errors, and any other failure — planning, goal classification,
    #    IntentIR construction, RunStore/PlanStore persistence — returns a clean 500 (see the
    #    `except Exception` at the bottom of this function) with `finally: lemon.finish_trace()`.
    #    So each step below is already covered; no per-step try/except is needed.
    try:
        workload_id, steps = "", []
        execution_graph = None

        intent_dict, intent_ir_dict = None, None

        # The intent passed around is now the RAW question. The planning guidance lives where
        # it belongs: in the graph generator's own prompt (see GraphGeneratorAgent).
        prompt = payload.question

        # 6.1 ── Question → Planner → Graph ──────────────────────────────

        execution_graph = await runtime._plan(prompt, runtime.graph_manager.graph)
        workload_id = ""
        execution_graph_id = ""

        # 6.2 ── Planner failed → unsupported_response ───────────────────
        if not execution_graph.get("nodes"):
            answer, _, _, _ = unsupported_response()
            steps = []
            intent_dict = {
                "intent": "unknown",
                "confidence": 0.0,
                "workload_id": "unknown",
            }
            intent_ir_dict = None
            if execution_graph is None:
                execution_graph = {}
            execution_graph["graph_type"] = "unknown"
            logger.warning(
                "run=%r [plan] planner returned no nodes — defaulting to UNKNOWN plan.",
                run_id,
            )
        else:
            # 6.3 ── Graph generated → Build IntentIR → Return Plan ──────
            workload_id = execution_graph.get("workload_id", "")
            execution_graph_id = execution_graph.get("execution_graph_id", "")
            steps = execution_graph.get("nodes", [])

            from src.monkey_brain.kernel.plan.goals.goal import Goal, GoalSource
            from src.monkey_brain.kernel.plan.goals.goal_type import (
                classify_goal_type,
                is_mutating,
                mutating_verbs,
            )
            from src.monkey_brain.kernel.plan.goals.intent_ir import build_intent_ir

            execution_intent = {
                "intent": execution_graph.get("domain", "planned"),
                "confidence": 1.0,
                "workload_id": workload_id,
            }

            # classify the goal type based on the question and check for destructive verbs
            # why do we need to classify: id all questions hare the same downsteam pipeline,
            # this is good if you want metadata about the question and the goal type for
            # logging and auditing purposes. However it is not used for any downstream
            # processing or decision making in the current implementation

            goal_type, goal_confidence = classify_goal_type(payload.question)

            destructive_verbs = mutating_verbs(payload.question)

            if destructive_verbs and not is_mutating(goal_type):
                # The request reads as a question but names a destructive verb ("show me how
                # to delete the audit log"). Not reclassified — the leading verb is still what
                # was asked — but recorded, so a caller enforcing policy can see it.
                logger.warning(
                    "run=%r goal typed %s but names mutating verb(s) %s",
                    run_id,
                    goal_type.value,
                    ", ".join(destructive_verbs),
                )

            execution_goal = Goal(
                name=execution_graph.get("domain", "planned"),
                description=payload.question[:256],
                goal_type=goal_type,
                source=GoalSource.API,
                confidence=goal_confidence,
                metadata={
                    "question": payload.question,
                    "execution_graph_id": execution_graph_id,
                    "mutating": is_mutating(goal_type),
                    "mutating_verbs": destructive_verbs,
                },
            )

            # 6.4 ──  Build IntentIR
            ir = build_intent_ir(
                intent=execution_intent,
                goal=execution_goal,
                run_id=run_id,
                question=payload.question,
            )

            execution_graph.setdefault("graph_id", execution_graph_id or run_id)
            execution_graph.setdefault("graph_type", "execution")
            execution_graph.setdefault("state", {})
            execution_graph.setdefault("annotations", {})
            execution_graph.setdefault("metadata", {})
            execution_graph["metadata"].setdefault("graph_id", execution_graph["graph_id"])
            execution_graph["metadata"].setdefault("run_id", run_id)
            execution_graph["metadata"].setdefault("goal_id", run_id)

            from src.monkey_brain.kernel.pipeline.plan_compiler import (
                compile_broca_graph,
            )

            compile_outcome = compile_broca_graph(
                execution_graph,
                plan_id=run_id,
                actor_id=user_id or "",
                goal_id=run_id,
                goal=execution_goal.name,
            )
            if not compile_outcome.ok:
                logger.warning(
                    "run=%r [plan] compile rejected: %s",
                    run_id,
                    "; ".join(compile_outcome.violations),
                )
            else:
                execution_graph = compile_outcome.execution_graph.to_dict()
            execution_graph.setdefault("metadata", {})["goal_id"] = run_id
            # compile_broca_graph's to_dict() emits a flat list[str] here (node ids in
            # topological order), but CanonicalGraph (kernel/models/plan.py's
            # PlanResponse.graph) requires list[list[str]] -- confirmed live, this was
            # normalized into execution_graph["metadata"]["execution_order"] instead of
            # this top-level key, so `graph=execution_graph` below always failed
            # PlanResponse's own pydantic validation with a 500 on every /plan call.
            execution_graph["execution_order"] = _normalize_execution_order(execution_graph.get("execution_order"))
            execution_graph["metadata"].setdefault("execution_order", execution_graph["execution_order"])
            answer = json.dumps(execution_graph, indent=2, default=str)

            from src.monkey_brain.kernel.plan.goals.run_store import get_run_store

            # 6.5 ── Store in RunStore
            # this is telemetry for the plan execution and must be shown in lemon as tracked runs
            # why is this stored in the run store and not in the plan store? because the run
            # store is for tracking the runs and the plan store is for tracking the plans.

            get_run_store().store(run_id, ir, target=payload.target, graph_snapshot=execution_graph)

            # 6.6 ── Store in PlanStore
            # Durable persistence: save plan to local filesystem + Neo4j

            plan_store = get_plan_store()
            intent_ir_dict_for_store = ir.to_dict() if hasattr(ir, "to_dict") else None
            await plan_store.save(
                run_id=run_id,
                graph=execution_graph,
                intent_ir=intent_ir_dict_for_store,
                graph_store=graph_store,
            )

            intent_dict = {
                "intent": ir.intent_type,
                "confidence": ir.confidence,
                "workload_id": ir.parameters.get("workload_id", ""),
            }
            intent_ir_dict = ir.to_dict()

        elapsed_ms = (time.monotonic() - t0) * 1000

        # This just adds the elapsed time to the profile for the plan execution, so that
        # we can see how long it took to select a plan.
        profile_add("plan", ms=int(elapsed_ms))

        profile_entries = profile_end()

        # 6.7 Grounding gate. Two distinct things happen with knowledge here, and it is worth
        # keeping them apart:
        #
        #   1. AVAILABLE knowledge already shapes the plan. The planner retrieves this
        #      question's semantic-memory context and feeds it into agent discovery
        #      (GraphGeneratorAgent._discover_agents), so the graph reflects what is known —
        #      the plan genuinely changes with available knowledge, it is not a fixed template.
        #
        #   2. When available knowledge is INSUFFICIENT (grounding_confidence below the
        #      threshold), the plan is still only as good as what was known, so we flag it.
        #      We do NOT acquire more knowledge here: /plan is select-only ("nothing is
        #      executed"), and a synchronous web search would add unbounded latency to a
        #      planning call. Instead the gap is a first-class, machine-actionable signal on
        #      the response (`low_grounding` + a `remediation` hint) plus a metric, so a caller
        #      or an outer orchestration loop can acquire MORE knowledge out of band and
        #      re-plan (a re-plan then folds the newly acquired knowledge into #1) before
        #      committing to /execute. Acquiring new knowledge is CognitiveRuntime's job
        #      (_acquire_knowledge / POST /acquire), driven separately.
        low_grounding = grounding_confidence < PLANNING_CONFIDENCE_THRESHOLD
        remediation = "acquire_knowledge_and_replan" if low_grounding else None

        if low_grounding:
            logger.warning(
                "run=%r [plan] user=%r low grounding_confidence=%.3f — plan is speculative; "
                "caller should acquire more knowledge before /execute",
                run_id,
                user_id,
                grounding_confidence,
            )

        # 6.8 log the plan selection and metrics to lemon if available
        if lemon:
            lemon.counter("api.plan.success")
            lemon.histogram("api.plan.latency_ms", elapsed_ms, user=user_id)
            lemon.histogram("api.plan.grounding_confidence", grounding_confidence)
            if execution_graph:
                lemon.observe_intent(
                    raw_text=payload.question[:200],
                    classified_intent=execution_graph.get("domain", "planned"),
                    confidence=grounding_confidence,
                    goal_id=run_id,
                    trace_id=run_id,
                )
                graph_nodes = execution_graph.get("nodes", [])
                selected_agents = [n.get("agent", n.get("name", "")) for n in graph_nodes]
                rejected = execution_graph.get("rejected_agents", [])
                lemon.gauge("plan.graph_nodes", float(len(graph_nodes)))
                lemon.gauge("plan.graph_edges", float(len(execution_graph.get("edges", []))))
                depth = max((len(execution_graph.get("execution_order", [])), 1))
                lemon.gauge("plan.graph_depth", float(depth))
                lemon.counter("plan.agents_selected", increment=len(selected_agents))
                if rejected:
                    lemon.counter("plan.agents_rejected", increment=len(rejected))
            if low_grounding:
                lemon.counter("api.plan.low_grounding")
                lemon.warn(
                    f"low grounding_confidence={grounding_confidence:.3f} for plan",
                    component="api.plan",
                    user=user_id,
                    workload_id=workload_id,
                )
            else:
                lemon.info(
                    f"plan selected workload={workload_id!r} in {elapsed_ms:.0f}ms",
                    component="api.plan",
                    user=user_id,
                    workload_id=workload_id,
                    steps=len(steps),
                )

        # 6.9 Cache Capabilities and Agents used in the plan for future reuse.
        # This is a telemetry feature that tracks what capabilities and agents are used
        # during each execution run. It allows the system to suggest previously successful
        # plans for similar goals in the future, improving efficiency and reducing planning time.

        mesh_reuse: dict | None = None
        try:
            # get the nodes from the graph delta and extract the agent names or node names
            # to create a set of drafted agents/nodes
            drafted = sorted(
                {
                    str(n.get("agent") or n.get("name", ""))
                    for n in (execution_graph.get("nodes", []) if execution_graph else [])
                    if isinstance(n, dict) and (n.get("agent") or n.get("name"))
                }
            )
            if drafted:
                from src.monkey_brain.persistence.graph_store import (
                    get_graph_store_instance,
                )

                store = get_graph_store_instance()
                if store is not None and store.is_connected():
                    similar = await store.find_similar_execution_graphs(
                        drafted,
                        min_overlap=max(2, len(drafted) // 2),
                        exclude_run_id=run_id,
                    )
                    if similar:
                        best = similar[0]
                        # overlap ratio is the number of overlapping nodes divided by the
                        # number of drafted nodes, rounded to 3 decimal places
                        overlap_ratio = round(best["overlap"] / max(len(drafted), 1), 3)
                        mesh_reuse = {
                            "run_id": best["run_id"],
                            "goal": best["goal"],
                            "overlap": best["overlap"],
                            "overlap_ratio": overlap_ratio,
                            "mesh_pagerank": best.get("mesh_pagerank", 0.0),
                            "candidates": len(similar),
                            "adopted": False,
                        }
                        logger.info(
                            "run=%r [plan] mesh reuse candidate: run=%r overlap=%d/%d goal=%r",
                            run_id,
                            best["run_id"],
                            best["overlap"],
                            len(drafted),
                            str(best["goal"])[:60],
                        )
                        if lemon:
                            lemon.counter("api.plan.mesh_reuse_candidate")
                            lemon.histogram("plan.mesh_overlap_ratio", overlap_ratio)

                        # overlap ratio being greater than or equal to the threshold means
                        # that the drafted plan is similar enough to a prior plan that we
                        # can adopt the prior plan instead of the drafted plan. This is a
                        # form of plan reuse that can save time and resources.
                        if overlap_ratio >= MESH_REUSE_ADOPT_THRESHOLD:
                            # Prefer the durable local copy (full planner
                            # graph); fall back to Neo4j recovery.
                            prior_graph = get_plan_store().load_local(best["run_id"])

                            # if there is no reuse graph in the local plan store, then we need
                            # to get the graph from the graph store by run id. This is a
                            # fallback mechanism to ensure that we can still retrieve the
                            # prior graph even if it is not stored locally.
                            if not (prior_graph and prior_graph.get("nodes")):
                                prior_graph = await store.get_graph_by_run_id(best["run_id"])

                            # if there is a prior graph and it has nodes, then we can adopt
                            # the prior graph as the current plan.
                            if prior_graph and prior_graph.get("nodes"):
                                execution_graph["nodes"] = prior_graph["nodes"]
                                execution_graph["edges"] = prior_graph.get("edges", [])
                                prior_order = prior_graph.get("execution_order") or prior_graph.get("metadata", {}).get(
                                    "execution_order"
                                )
                                if prior_order:
                                    execution_graph["execution_order"] = prior_order
                                    execution_graph["metadata"]["execution_order"] = _normalize_execution_order(
                                        prior_order
                                    )
                                execution_graph["metadata"]["reused_from_run_id"] = best["run_id"]
                                steps = execution_graph["nodes"]
                                answer = json.dumps(execution_graph, indent=2, default=str)
                                mesh_reuse["adopted"] = True
                                # /execute reads this run's snapshot from the
                                # run store — re-store so the adopted graph is
                                # what actually runs, and re-save durably.
                                from src.monkey_brain.kernel.plan.goals.run_store import (
                                    get_run_store,
                                )

                                get_run_store().store(
                                    run_id,
                                    ir,
                                    target=payload.target,
                                    graph_snapshot=execution_graph,
                                )
                                await get_plan_store().save(
                                    run_id=run_id,
                                    graph=execution_graph,
                                    intent_ir=intent_ir_dict,
                                    graph_store=graph_store,
                                )
                                logger.info(
                                    "run=%r [plan] adopted prior graph from run=%r (overlap_ratio=%.3f)",
                                    run_id,
                                    best["run_id"],
                                    overlap_ratio,
                                )
                                if lemon:
                                    lemon.counter("api.plan.mesh_reuse_adopted")
        except Exception as e:
            # Mesh reuse is a pure optimization (adopt a proven prior graph). On failure the
            # freshly planned graph — already stored above — is returned unchanged, so this
            # never fails the request; but it was hidden at debug level, so a persistently
            # broken reuse path (e.g. a graph-store outage) looked like it was never tried.
            logger.warning(
                "run=%r [plan] mesh reuse check failed — returning the freshly planned graph: %s",
                run_id,
                e,
            )
            if lemon:
                lemon.counter("api.plan.mesh_reuse_failed")

        # 5. Sign the execution graph and record audit
        if execution_graph:
            try:
                from src.monkey_brain.kernel.execute.graph import sign_graph_dict

                sign_graph_dict(execution_graph)
            except Exception as exc:
                # The graph's tamper signature is defense-in-depth; the load-bearing
                # integrity is the IntentIR signature (built + signed earlier, and it would
                # have raised long before here if the key system were broken). So don't 500
                # — but signing failing at THIS point is genuinely unexpected, so log it at
                # error and count it rather than returning a silently-unsigned graph.
                logger.error(
                    "run=%r [plan] failed to sign execution graph — returning it unsigned: %s",
                    run_id,
                    exc,
                )
                if lemon:
                    lemon.counter("api.plan.graph_signing_failed")

        try:
            from src.monkey_brain.kernel.audit import get_audit_log

            node_count = len(execution_graph.get("nodes", [])) if execution_graph else 0
            edge_count = len(execution_graph.get("edges", [])) if execution_graph else 0
            get_audit_log().record(
                runtime_id=user_id,
                event_type="plan",
                action="plan_completed",
                actor=user_id,
                details={
                    "run_id": run_id,
                    "nodes": node_count,
                    "edges": edge_count,
                    "mesh_reuse": bool(mesh_reuse),
                },
            )
        except Exception as exc:
            logger.warning("run=%r [plan] audit record (plan_completed) failed: %s", run_id, exc)
            if lemon:
                lemon.counter("api.plan.audit_failed", stage="completed")

        # 6.10. A planner that produced no nodes produced no plan. PlanResponse
        #    dropped the unsupported-answer string on the floor (it declares no
        #    `answer` field), so this case came back as HTTP 200 with an empty
        #    graph and no stated reason — success implied. Say what happened.
        if not steps:
            if lemon:
                lemon.counter("api.plan.no_graph")
            return JSONResponse(
                status_code=422,
                content={
                    "error": "planner_produced_no_graph",
                    "detail": answer,
                    "run_id": run_id,
                    "question": payload.question,
                    "user_id": user_id,
                    "elapsed_ms": round(elapsed_ms, 2),
                    "grounding_confidence": round(grounding_confidence, 4),
                    "low_grounding": low_grounding,
                    "metadata": {"profile": [entry.to_dict() for entry in profile_entries]},
                },
            )

        # 6.11 Return the plan.
        logger.info(
            "run=%r [plan] Returning PlanResponse: nodes=%d edges=%d intent=%s",
            run_id,
            len(execution_graph.get("nodes", [])) if execution_graph else 0,
            len(execution_graph.get("edges", [])) if execution_graph else 0,
            intent_dict.get("intent", "None") if intent_dict else "None",
        )
        return PlanResponse(
            run_id=run_id,
            target=payload.target,
            question=payload.question,
            workload_id=workload_id,
            steps=steps,
            answer=answer,
            grounding_confidence=round(grounding_confidence, 4),
            low_grounding=low_grounding,
            user_id=user_id,
            elapsed_ms=round(elapsed_ms, 2),
            metadata={
                "profile": [entry.to_dict() for entry in profile_entries],
                "grounding": {
                    "confidence": round(grounding_confidence, 4),
                    "threshold": PLANNING_CONFIDENCE_THRESHOLD,
                    "low_grounding": low_grounding,
                    # Machine-actionable next step when grounding is low: acquire more
                    # knowledge (out of band) and re-plan before executing. None when
                    # grounding is adequate.
                    "remediation": remediation,
                },
                "graph": execution_graph,
                "mesh_reuse": mesh_reuse,
            },
            intent=intent_dict,
            intent_ir=intent_ir_dict,
            graph=execution_graph,
            graph_id=execution_graph.get("graph_id") if execution_graph else None,
            graph_type=execution_graph.get("graph_type") if execution_graph else None,
            timestamp=execution_graph.get("timestamp") if execution_graph else None,
            nodes=execution_graph.get("nodes") if execution_graph else None,
            edges=execution_graph.get("edges") if execution_graph else None,
            execution_order=(
                _normalize_execution_order(execution_graph.get("execution_order")) if execution_graph else None
            ),
            annotations=execution_graph.get("annotations") if execution_graph else None,
            state=execution_graph.get("state") if execution_graph else None,
        )

    except Exception as e:
        from broca.agents.graph_generator_agent import PlannerConvergenceError

        if isinstance(e, PlannerConvergenceError):
            logger.warning(
                "run=%r [plan] user=%r graph convergence failed after %d attempt(s): %s",
                run_id,
                user_id,
                e.attempts,
                ", ".join(err.code for err in e.errors),
            )
            if lemon:
                lemon.counter("api.plan.convergence_failed")
            elapsed_ms = (time.monotonic() - t0) * 1000
            profile_add("plan", ms=int(elapsed_ms))
            partial_profile = profile_end()
            # 6.12 If the planner failed to converge, return a 422 with the convergence errors
            return JSONResponse(
                status_code=422,
                content={
                    "error": "Graph validation failed after repair attempts",
                    "attempts": e.attempts,
                    "errors": [{"code": err.code, "message": err.message} for err in e.errors],
                    "run_id": run_id,
                    "question": payload.question,
                    "user_id": user_id,
                    "elapsed_ms": round(elapsed_ms, 2),
                    "grounding_confidence": round(grounding_confidence, 4),
                    "low_grounding": grounding_confidence < PLANNING_CONFIDENCE_THRESHOLD,
                    "metadata": {"profile": [entry.to_dict() for entry in partial_profile]},
                },
            )

        logger.error("run=%r [plan] user=%r failed: %s", run_id, user_id, e)
        if lemon:
            lemon.counter("api.plan.error")
            lemon.error(str(e), component="api.plan", user=user_id)
        partial_profile = profile_end()
        return JSONResponse(
            status_code=500,
            content={
                "error": "Plan selection failed",
                "detail": "An internal error occurred during plan generation",
                "metadata": {"profile": [entry.to_dict() for entry in partial_profile]},
            },
        )
    finally:
        if lemon:
            lemon.finish_trace()


@router.post("/acquire")
@idempotent("plan.acquire_knowledge_route")
async def acquire_knowledge_route(
    payload: PlanRequest,
    user_id: str = Depends(require_permission("perm-execute-plan")),
    runtime: CognitiveRuntime = Depends(get_cognitive_runtime),
) -> Any:
    """POST /acquire — the remediation for a low-grounding /plan.

    When /plan returns metadata.grounding.remediation == "acquire_knowledge_and_replan",
    call this with the same question, then re-plan. It queries every available knowledge
    source, folds what it finds into the grounding knowledge pack, and returns the
    grounding delta so the caller can decide whether re-planning is worthwhile.

    Deliberately separate from /plan: acquisition can incur web-search latency and MUST
    NOT block plan selection. Requires perm-execute-plan.
    """
    try:
        from src.monkey_brain.kernel.security import sanitize_input

        payload.question = sanitize_input(payload.question)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": "invalid_input", "detail": str(e)})

    from src.monkey_brain.kernel.cognitive_kernel import get_cognitive_kernel
    from src.monkey_brain.kernel.plan.knowledge_acquisition import acquire_knowledge

    try:
        result = await acquire_knowledge(
            payload.question,
            kernel=get_cognitive_kernel(),
            semantic_memory=getattr(runtime, "semantic_memory", None),
        )
    except Exception as e:
        logger.error("[acquire] user=%r failed: %s", user_id, e)
        return JSONResponse(
            status_code=500,
            content={
                "error": "Knowledge acquisition failed",
                "detail": str(e),
                "question": payload.question,
                "user_id": user_id,
            },
        )

    result["user_id"] = user_id
    return result
