"""Execute route — full workload execution with explicit action semantics.

POST /api/v1/agentos/execute
  Classify the question, select a workload, and execute it end-to-end.
  Semantically distinct from /plan: /execute commits work; /plan only selects.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.monkey_brain.kernel.models.plan import PlanResponse
from src.monkey_brain.kernel.execute.models import ExecutionMode
from src.monkey_brain.kernel.cognitive_runtime import CognitiveRuntime
from src.monkey_brain.kernel.plan.goals.intent_ir import (
    IntentIRRejected,
    decode_and_validate_ir,
)
from src.monkey_brain.kernel.learn.telemetry.telemetry import (
    profile_start,
    profile_end,
    profile_add,
)
from src.monkey_brain.runtime.routers import get_mongo_client
from src.monkey_brain.api.dependencies import (
    require_permission,
    sanitize_and_check_governance,
    record_request_audit,
    RequestRejected,
)
from src.monkey_brain.api.idempotency import idempotent
from src.monkey_brain.api.helpers.run_helpers import (
    build_citations,
    get_cognitive_runtime,
)
from src.monkey_brain.kernel.execute.grounding import assess as assess_grounding
from src.monkey_brain.kernel.execute.grounding import enforce as enforce_grounding
from src.monkey_brain.kernel.models.execute import ExecuteResponse
from src.monkey_brain.kernel.models.graph import canonical_graph_envelope
from src.monkey_brain.kernel.plan.goals.run_store import get_run_store
from src.monkey_brain.kernel.temporal_graph import (
    assert_same_topology,
    clone_graph_snapshot,
)
from src.introspection.lemon import get_lemon

logger = logging.getLogger("agentos.execute")

router = APIRouter()


def _get_graph_store(request: Request) -> Any:
    return getattr(request.app.state, "_graph_store", None) or getattr(request.app.state, "graph_store", None)


def _envelope(graph: Any) -> dict[str, Any]:
    return canonical_graph_envelope(graph)


def _edge_endpoints(edge: Any) -> tuple[str, str]:
    """(prerequisite, dependent) for a graph edge. `to` depends on `from`."""
    if not isinstance(edge, dict):
        return "", ""
    src = str(edge.get("from") or edge.get("src") or "")
    dst = str(edge.get("to") or edge.get("dst") or "")
    return src, dst


def _graph_to_plan_steps(graph: Any) -> list[dict[str, Any]]:
    """Derive plan steps from the planner-produced execution graph.

    Steps are emitted in dependency order — by the planner's execution_order
    layers when present, else by a topological sort of the edges. The runtime
    executes steps in list order, so emitting them in node-declaration order
    would let a node run before the node it depends on.
    """
    if graph is None:
        return []
    if hasattr(graph, "to_dict"):
        graph = graph.to_dict()
    elif hasattr(graph, "nodes") and hasattr(graph, "edges"):
        graph = {
            "nodes": list(getattr(graph, "nodes", [])),
            "edges": list(getattr(graph, "edges", [])),
            "metadata": dict(getattr(graph, "metadata", {})),
        }

    nodes = [n.to_dict() if hasattr(n, "to_dict") else n for n in graph.get("nodes", [])]
    nodes = [n for n in nodes if isinstance(n, dict)]
    by_id = {str(n.get("id", "")): n for n in nodes}

    edges = graph.get("edges", [])
    deps: dict[str, list[str]] = {nid: [] for nid in by_id}
    for edge in edges:
        src, dst = _edge_endpoints(edge)
        if src in by_id and dst in by_id:
            deps[dst].append(src)

    ordered = _order_nodes(nodes, by_id, deps, graph.get("execution_order", []))

    steps: list[dict[str, Any]] = []
    for node in ordered:
        node_id = str(node.get("id", ""))
        agent_name = node.get("agent", "")
        step_name = node.get("label") or node.get("name") or agent_name or node_id
        steps.append(
            {
                "step_id": node_id,
                "name": step_name,
                "capability_name": agent_name or node.get("capability_name", "") or step_name,
                "agent": agent_name,
                "step_type": "agent",
                "dependencies": list(deps.get(node_id, [])),
                "metadata": node.get("props", node.get("metadata", {})),
            }
        )
    return steps


def _order_nodes(
    nodes: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
    deps: dict[str, list[str]],
    execution_order: Any,
) -> list[dict[str, Any]]:
    """Order nodes so every node follows its dependencies.

    Prefers the planner's execution_order layering; falls back to Kahn's
    algorithm over the edges. On a cycle, or when the layering doesn't cover
    every node, falls back to declaration order and logs — never silently
    drops or duplicates a node.
    """
    # 1. Planner-provided layering, when it covers every node exactly once.
    layered: list[str] = []
    for layer in execution_order or []:
        if isinstance(layer, list):
            layered.extend(str(n) for n in layer)
        elif layer:
            layered.append(str(layer))
    if layered and sorted(layered) == sorted(by_id):
        return [by_id[nid] for nid in layered]

    # 2. Kahn's algorithm, stable in declaration order.
    indeg = {nid: len(deps.get(nid, [])) for nid in by_id}
    dependents: dict[str, list[str]] = {nid: [] for nid in by_id}
    for nid, prereqs in deps.items():
        for p in prereqs:
            dependents[p].append(nid)

    declared = [str(n.get("id", "")) for n in nodes]
    ready = [nid for nid in declared if indeg[nid] == 0]
    out: list[str] = []
    while ready:
        nid = ready.pop(0)
        out.append(nid)
        for d in dependents[nid]:
            indeg[d] -= 1
            if indeg[d] == 0:
                ready.append(d)

    if len(out) != len(by_id):
        logger.error(
            "[execute] execution graph has a dependency cycle (%d of %d nodes ordered) — "
            "falling back to declaration order; steps may run before their dependencies",
            len(out),
            len(by_id),
        )
        return nodes
    return [by_id[nid] for nid in out]


async def _recover_planner_graph(run_id: str, graph_store: Any) -> tuple[dict | None, str | None]:
    """Recover a run's planner-produced execution graph, one source at a time.

    Order: RunStore (process-local, wiped on restart) → PlanStore (durable graph.json +
    Mongo) → Neo4j (legacy, reconstructed from run_id-tagged nodes). Returns
    (graph, source) where source is "run_store" | "plan_store" | "neo4j", or (None, None)
    when every source is empty.

    Extracted because /execute, /execute/stream, and /replay each re-implemented this exact
    cascade. Three copies meant a change to the recovery order (or a new source) had to be
    made in three places and kept in sync by hand — which is precisely how the streaming
    twin kept drifting out of parity with /execute. One definition now.
    """
    graph = get_run_store().get_graph(run_id)
    if graph is not None:
        return graph, "run_store"

    from src.monkey_brain.persistence.plan_store import get_plan_store

    graph = get_plan_store().load_local(run_id)
    if graph is not None:
        return graph, "plan_store"

    if graph_store is not None:
        graph = await graph_store.get_graph_by_run_id(run_id)
        if graph is not None:
            return graph, "neo4j"

    return None, None


# executes the selected plan and returns the result, requires perm-execute-action permission
# when a plan is being executed this is the expoitation stage that means furing this stage the policy is looked up and the plan is executed and the results are returned to the user,
# the policy weights are not updated during this stage, the policy weights are only updated during the learning stage which is after the execution stage, so the policy weights are
# not updated during the execution stage
@router.post("/execute", response_model=ExecuteResponse)
@idempotent("execute.action")
async def execute_action(
    payload: PlanResponse,
    request: Request,
    mongo_client: Any = Depends(get_mongo_client),
    user_id: str = Depends(require_permission("perm-execute-action")),
    graph_store: Any = Depends(_get_graph_store),
    runtime: CognitiveRuntime = Depends(get_cognitive_runtime),
) -> Any:
    """POST /execute — full workload execution for action-oriented requests.

    Runs Intent IR → validation → preconditions → execution (GoalExecutor,
    the runtime). /execute never classifies natural language — it requires
    payload.intent_ir from a prior /plan call and executes exactly that,
    rejecting outright (400) if it's missing, malformed, unsupported-version,
    or fails signature verification. Requires perm-execute-action.
    """
    # payload IS the PlanResponse from /plan — reuse its run_id so the two
    # phases correlate under one trace.
    run_id = payload.run_id
    logger.info("run=%r [execute] user=%r question=%r", run_id, user_id, payload.question[:100])

    # 1. Input validation + 2. Governance check (shared with /simulate,
    # /compare, /execute/stream — see dependencies.py::sanitize_and_check_governance)
    try:
        payload.question = await sanitize_and_check_governance(
            payload.question,
            user_id,
            "execute",
            extra_context={"run_id": run_id},
        )
    except RequestRejected as e:
        return JSONResponse(
            status_code=e.status_code,
            content={"error": e.error_code, "detail": e.detail},
        )

    # 3. Audit: record execution request
    record_request_audit(
        user_id,
        "execute",
        "execute_requested",
        {"run_id": run_id, "question": payload.question[:200]},
    )

    lemon = get_lemon()

    if lemon:
        lemon.start_trace(
            name="api.execute",
            trace_id=run_id,
            mode=ExecutionMode.EXECUTE.value,
            user=user_id,
            run_id=run_id,
            question_preview=payload.question[:80],
        )
        lemon.counter("api.execute.request")

    if not payload.intent_ir:
        if lemon:
            lemon.counter("api.execute.missing_intent_ir")
            lemon.finish_trace()
        return JSONResponse(
            status_code=400,
            content={
                "error": "Missing intent_ir — /execute requires a prior /plan call's intent_ir",
                "question": payload.question,
                "user_id": user_id,
                "run_id": run_id,
            },
        )

    # 4. IR validation: /execute is the only route that runs a plan, so it is the only route that must validate the IR.
    # /plan does not validate the IR because it is not executing it, and /replay does not validate the IR because it is
    # re-running a previously validated plan.
    # 5. Decode + validate the IntentIR (schema, shape, signature). Same decode+validate
    # sequence as /execute/stream, /simulate and /compare — shared in decode_and_validate_ir
    # — but /execute renders the rejection as its own JSONResponse with metrics/trace teardown.
    try:
        ir = decode_and_validate_ir(payload.intent_ir)
    except IntentIRRejected as e:
        if e.kind == "malformed":
            logger.warning("run=%r [execute] malformed intent_ir: %s", run_id, e.detail)
            error_body = f"Malformed intent_ir: {e.detail}"
            counter = "api.execute.malformed_intent_ir"
            content = {
                "error": error_body,
                "question": payload.question,
                "user_id": user_id,
                "run_id": run_id,
            }
        else:
            logger.warning(
                "run=%r [execute] intent_ir validation failed: %s",
                run_id,
                "; ".join(e.detail),
            )
            counter = "api.execute.invalid_intent_ir"
            content = {
                "error": "intent_ir validation failed",
                "detail": e.detail,
                "question": payload.question,
                "user_id": user_id,
                "run_id": run_id,
            }
        if lemon:
            lemon.counter(counter)
            lemon.finish_trace()
        return JSONResponse(status_code=400, content=content)

    # 6. Only run a plan that was compiled for THIS runtime. The stored target is
    # server-side and authoritative (the IntentIR signature does not cover it).
    stored_target = get_run_store().get_target(run_id) or payload.target
    if stored_target != "execute":
        if lemon:
            lemon.counter("api.execute.wrong_target")
            lemon.finish_trace()
        return JSONResponse(
            status_code=400,
            content={
                "error": f"Plan was compiled with target={stored_target!r} — /execute requires target=execute",
                "question": payload.question,
                "user_id": user_id,
                "run_id": run_id,
            },
        )

    # 7. Build the execution context for this run. This includes the runtime, user_id, and any other relevant information needed to execute the plan.
    context = runtime.build_execution_runtime(ir, ExecutionMode.EXECUTE, user_id=user_id)

    profile_start()
    t0 = time.monotonic()

    planner_graph, source = await _recover_planner_graph(run_id, graph_store)
    if source in ("plan_store", "neo4j"):
        # RunStore is the primary (no counter). A recovery from a durable source means the
        # process restarted or RunStore evicted the run — worth logging + counting.
        logger.info(
            "run=%r [execute] recovered execution graph from %s (RunStore miss)",
            run_id,
            source,
        )
        if lemon:
            lemon.counter(f"api.execute.execution_graph_recovered_{'planstore' if source == 'plan_store' else 'neo4j'}")

    if planner_graph is None:
        if lemon:
            lemon.counter("api.execute.missing_execution_graph")
            lemon.finish_trace()
        return JSONResponse(
            status_code=500,
            content={
                "error": "Missing planner execution graph",
                "detail": "Execution requires the planner-produced ExecutionGraph on runtime.execution_graph",
                "question": payload.question,
                "user_id": user_id,
                "run_id": run_id,
            },
        )

    # 8. clone the planner graph to avoid mutating the original graph in the runtime, and set the run_id in the metadata for tracking.
    execution_graph = clone_graph_snapshot(planner_graph)
    execution_graph.setdefault("metadata", {})["run_id"] = run_id
    execution_graph["metadata"]["goal_id"] = run_id
    runtime.execution_graph = execution_graph

    # 8.1 Compile the Broca graph through the canonical plan compiler, then
    # derive executable steps from the compiled ExecutionGraph.
    from src.monkey_brain.kernel.pipeline.plan_compiler import (
        compile_broca_graph,
        execution_graph_to_plan_steps,
    )

    compile_outcome = compile_broca_graph(
        execution_graph,
        plan_id=run_id,
        actor_id=user_id,
        goal_id=run_id,
    )
    if not compile_outcome.ok:
        if lemon:
            lemon.counter("api.execute.compile_rejected")
            lemon.finish_trace()
        return JSONResponse(
            status_code=400,
            content={
                "error": "Plan rejected at compile",
                "violations": list(compile_outcome.violations),
                "question": payload.question,
                "user_id": user_id,
                "run_id": run_id,
            },
        )

    execution_graph = compile_outcome.execution_graph.to_dict()
    runtime.execution_graph = execution_graph
    execute_steps = execution_graph_to_plan_steps(compile_outcome.execution_graph)

    # 8.2 If there are no executable steps, return an error response. This can happen if the planner produced an empty graph or if all the steps were filtered out due to preconditions or other constraints.
    if not execute_steps:
        if lemon:
            lemon.counter("api.execute.empty_execution_graph")
            lemon.finish_trace()
        return JSONResponse(
            status_code=500,
            content={
                "error": "Planner execution graph had no executable steps",
                "detail": "Execution requires the planner-produced ExecutionGraph to contain nodes",
                "question": payload.question,
                "user_id": user_id,
                "run_id": run_id,
            },
        )

    # 8.3 Execute the validated IntentIR via the CognitiveRuntime, passing context.
    try:
        answer, semantic_hits, graph_paths, llm_answered = await runtime.execute_cognitive_workload(
            context,
            mongo_client,
            plan_steps=execute_steps,
        )
        elapsed_ms = (time.monotonic() - t0) * 1000
        profile_add("execute", ms=int(elapsed_ms))

        profile_entries = profile_end()

        # 8.4 persist discovered graph paths so entities accumulate across executions
        if graph_store and graph_paths:
            try:
                # GraphStore.add_query_paths is implemented — it merges discovered
                # nodes/relationships into Neo4j so entities accumulate across executions.
                await graph_store.add_query_paths(graph_paths)
            except Exception as gse:
                logger.warning("[execute] graph_store.add_query_paths failed: %s", gse)

        # 8.5 Record telemetry metrics for the execution, including latency, semantic hits, graph paths, and node states. This helps monitor the performance and success of executions over time.
        if lemon:
            lemon.counter("api.execute.success")
            lemon.histogram("api.execute.latency_ms", elapsed_ms, user=user_id)
            lemon.histogram("api.execute.semantic_hits", float(len(semantic_hits)))
            lemon.histogram("api.execute.graph_paths", float(len(graph_paths)))
            exec_nodes = execution_graph.get("nodes", []) if isinstance(execution_graph, dict) else []
            completed = sum(1 for n in exec_nodes if n.get("state") == "complete")
            failed = sum(1 for n in exec_nodes if n.get("state") == "failed")
            running = sum(1 for n in exec_nodes if n.get("state") == "running")
            unimplemented = sum(1 for n in exec_nodes if n.get("state") == "unimplemented")
            lemon.gauge("exec.completed", float(completed))
            lemon.gauge("exec.failed", float(failed))
            lemon.gauge("exec.running", float(running))
            lemon.gauge("exec.unimplemented", float(unimplemented))
            for node in exec_nodes:
                agent = node.get("agent", node.get("name", ""))
                state = node.get("state", "unknown")
                if agent:
                    lemon.observe_pipeline_step(
                        pipeline_id=run_id,
                        step_name=node.get("id", ""),
                        capability=agent,
                        status="ok" if state == "complete" else "error",
                        latency_ms=elapsed_ms / max(len(exec_nodes), 1),
                        agent_type=agent,
                    )
            lemon.info(
                f"execute completed in {elapsed_ms:.0f}ms llm_answered={llm_answered}",
                component="api.execute",
                user=user_id,
                hits=len(semantic_hits),
            )

        # 8.6. Sign the execution graph and record audit
        try:
            from src.monkey_brain.kernel.execute.graph import sign_graph_dict

            sign_graph_dict(execution_graph)
        except Exception as exc:
            logger.error(
                "run=%r [execute] failed to sign execution graph — proceeding unsigned: %s",
                run_id,
                exc,
            )
            if lemon:
                lemon.counter("api.execute.graph_signing_failed")

        try:
            from src.monkey_brain.kernel.audit import get_audit_log

            get_audit_log().record(
                runtime_id=user_id,
                event_type="execute",
                action="execute_completed",
                actor=user_id,
                details={
                    "run_id": run_id,
                    "llm_answered": llm_answered,
                    "elapsed_ms": round(elapsed_ms, 2),
                    "semantic_hits": len(semantic_hits),
                },
            )
        except Exception as exc:
            logger.warning(
                "run=%r [execute] audit record (execute_completed) failed: %s",
                run_id,
                exc,
            )

        # 8.7. Return the execution response
        assert_same_topology(planner_graph, execution_graph, context="/execute")
        graph_envelope = _envelope(execution_graph)

        get_run_store().store_graph(run_id, graph_envelope)

        # The result tuple drops the workload's success flag, so a run where a step FAILED and
        # could not be compensated still returned 200 with llm_answered=true and the failure
        # buried inside the answer string ("Agent: action [failed]"). Read the recorded outcome
        # back and state it explicitly, so a client can actually detect a failed execution.
        outcome = get_run_store().get_outcome(run_id) or {}
        succeeded = bool(outcome.get("success", True))
        failed_steps = list(outcome.get("failed_steps") or [])
        if not succeeded:
            logger.warning("run=%r [execute] completed with failed steps: %s", run_id, failed_steps)

        # Is there anything behind this answer? Retrieval hits, graph paths, or an agent that
        # declared its source. With none of the three, a fluent answer is the model's prior
        # dressed up as fact — say so, and in strict mode withhold it rather than assert it.

        # 8.8  Build citations from semantic hits and any step citations recorded in the outcome. Assess grounding of the answer based on the semantic hits, graph paths, citations, and whether the LLM answered. Enforce grounding on the answer if necessary.
        citations = build_citations(semantic_hits) + list(outcome.get("step_citations") or [])
        grounding = assess_grounding(
            answer=answer,
            semantic_hits=semantic_hits,
            graph_paths=graph_paths,
            citations=citations,
            llm_answered=llm_answered,
        )

        # 8.9 Enforce grounding on the answer if necessary. This may modify the answer to ensure it is grounded in the available evidence.
        answer = enforce_grounding(answer, grounding, run_id)

        # 8.10 Return the ExecuteResponse with all relevant information, including the run_id, question, answer, semantic hits, graph paths, citations, grounding information, success status, failed steps, user_id, elapsed time, metadata, and the execution graph envelope.
        return ExecuteResponse(
            run_id=run_id,
            question=payload.question,
            answer=answer,
            semantic_hits=semantic_hits,
            graph_paths=graph_paths,
            citations=citations,
            llm_answered=llm_answered,
            grounded=grounding["grounded"],
            grounding=grounding,
            success=succeeded,
            failed_steps=failed_steps,
            user_id=user_id,
            elapsed_ms=round(elapsed_ms, 2),
            metadata={
                "profile": [entry.to_dict() for entry in profile_entries],
                "success": succeeded,
                "failed_steps": failed_steps,
            },
            graph=graph_envelope,
            graph_id=graph_envelope["graph_id"],
            graph_type=graph_envelope["graph_type"],
            timestamp=graph_envelope["timestamp"],
            nodes=graph_envelope["nodes"],
            edges=graph_envelope["edges"],
            execution_order=graph_envelope["execution_order"],
            annotations=graph_envelope["annotations"],
            state=graph_envelope["state"],
        )

    except Exception as e:
        logger.error("run=%r [execute] user=%r failed: %s", run_id, user_id, e)
        if lemon:
            lemon.counter("api.execute.error")
            lemon.error(str(e), component="api.execute", user=user_id)
        partial_profile = profile_end()
        return JSONResponse(
            status_code=500,
            content={
                "error": "Execution failed",
                "detail": str(e),
                "question": payload.question,
                "user_id": user_id,
                "metadata": {"profile": [entry.to_dict() for entry in partial_profile]},
            },
        )
    finally:
        if lemon:
            lemon.finish_trace()


# TODO: doesnt really belong here, but this is the only route that needs to replay a run_id and it needs to be shared across all three runtimes (Cognitive/Simulation/Comparator) so it is here for now.
@router.post("/replay/{run_id}")
@idempotent("execute.replay_action")
async def replay_action(
    run_id: str,
    request: Request,
    mongo_client: Any = Depends(get_mongo_client),
    user_id: str = Depends(require_permission("perm-execute-action")),
    runtime: CognitiveRuntime = Depends(get_cognitive_runtime),
) -> Any:
    """POST /replay/{run_id} — shared across all three runtimes.

    Looks up which runtime (Cognitive/Simulation/Comparator) originally
    compiled `run_id` and dispatches back to that runtime's Run stage with
    the exact stored IntentIR — never re-classifies the original question.
    404 if no plan was stored for this run_id (process-local store — not
    durable across restarts).

    Response shape depends on which runtime compiled the run: target="execute"
    returns ExecuteResponse; target="simulate"/"compare" return that runtime's
    own result dict (SimulationRuntime/ComparatorRuntime.run()'s return value)
    wrapped with run_id/target/elapsed_ms.
    """
    from src.monkey_brain.kernel.plan.goals.run_store import (
        get_run_store,
        replay as replay_run,
    )

    logger.info("run=%r [replay] user=%r requested", run_id, user_id)
    store = get_run_store()
    if not store.has(run_id):
        return JSONResponse(
            status_code=404,
            content={
                "error": f"No stored plan for run_id {run_id!r}",
                "user_id": user_id,
                "run_id": run_id,
            },
        )

    # Replay re-executes real work, so it takes the same governance/audit gates
    # as /execute. The question is NOT re-read from the client — it comes from
    # the stored IntentIR, so there is nothing to sanitize.
    stored_ir = store.get(run_id)
    question = (stored_ir.execution_metadata.get("question", "") if stored_ir else "") or ""

    try:
        from src.monkey_brain.kernel.governance import get_governance_engine

        gov_result = await get_governance_engine().evaluate(
            user_id,
            "execute",
            {"question": question[:200], "run_id": run_id, "replay": True},
        )
        if not gov_result.get("allowed"):
            return JSONResponse(
                status_code=403,
                content={
                    "error": "governance_denied",
                    "detail": gov_result.get("reason"),
                    "run_id": run_id,
                },
            )
    except ImportError:
        logger.warning(
            "run=%r [execute/replay] governance module not available — skipping check",
            run_id,
        )
    except Exception as exc:
        # Was `except: pass` — a governance ENGINE error let the replay through unchecked.
        # Fail closed and surface it, matching the main /execute governance handling.
        logger.error(
            "run=%r [execute/replay] governance check failed — denying replay: %s",
            run_id,
            exc,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": "governance_error",
                "detail": "Governance check failed",
                "run_id": run_id,
            },
        )

    try:
        from src.monkey_brain.kernel.audit import get_audit_log

        get_audit_log().record(
            runtime_id=user_id,
            event_type="execute",
            action="replay_requested",
            actor=user_id,
            details={"run_id": run_id, "question": question[:200]},
        )
    except Exception as exc:
        logger.warning(
            "run=%r [execute/replay] audit record (replay_requested) failed: %s",
            run_id,
            exc,
        )

    target = store.get_target(run_id)
    t0 = time.monotonic()

    if target == "execute":
        # Re-run the plan that was stored for this run. Passing no steps meant the
        # executor had nothing to run and refused (it never resolves a workload of
        # its own), so every replay came back with "No planner workload was
        # provided" as its ANSWER — at HTTP 200, with success implied.
        # Same recovery cascade as /execute (now also tries Neo4j, which the old inline
        # version here did not — strictly more robust and consistent).
        graph_store = getattr(request.app.state, "_graph_store", None) or getattr(
            request.app.state, "graph_store", None
        )
        planner_graph, _ = await _recover_planner_graph(run_id, graph_store)
        replay_steps = _graph_to_plan_steps(planner_graph)
        if not replay_steps:
            return JSONResponse(
                status_code=500,
                content={
                    "error": "Missing planner execution graph",
                    "detail": f"No execution graph stored for run_id {run_id!r} — nothing to replay",
                    "user_id": user_id,
                    "run_id": run_id,
                },
            )

        result = await replay_run(run_id, mongo_client, runtime=runtime, plan_steps=replay_steps)
        elapsed_ms = (time.monotonic() - t0) * 1000

        answer, semantic_hits, graph_paths, llm_answered = result

        # Same outcome contract as /execute: a replay whose steps failed must not
        # report success. This returned the default success=True unconditionally.
        outcome = store.get_outcome(run_id) or {}
        succeeded = bool(outcome.get("success", True))
        failed_steps = list(outcome.get("failed_steps") or [])
        if not succeeded:
            logger.warning("run=%r [replay] completed with failed steps: %s", run_id, failed_steps)

        citations = build_citations(semantic_hits) + list(outcome.get("step_citations") or [])
        grounding = assess_grounding(
            answer=answer,
            semantic_hits=semantic_hits,
            graph_paths=graph_paths,
            citations=citations,
            llm_answered=llm_answered,
        )
        answer = enforce_grounding(answer, grounding, run_id)

        # The replayed graph, not _envelope(None) — the response used to carry an
        # empty graph even though the run's graph was right there in the store.
        graph_envelope = _envelope(
            runtime.execution_graph if isinstance(getattr(runtime, "execution_graph", None), dict) else planner_graph
        )

        return ExecuteResponse(
            run_id=run_id,
            question=question,
            answer=answer,
            semantic_hits=semantic_hits,
            graph_paths=graph_paths,
            citations=citations,
            llm_answered=llm_answered,
            grounded=grounding["grounded"],
            grounding=grounding,
            success=succeeded,
            failed_steps=failed_steps,
            user_id=user_id,
            elapsed_ms=round(elapsed_ms, 2),
            metadata={
                "replay": True,
                "success": succeeded,
                "failed_steps": failed_steps,
            },
            graph=graph_envelope,
            graph_id=graph_envelope["graph_id"],
            graph_type=graph_envelope["graph_type"],
            timestamp=graph_envelope["timestamp"],
            nodes=graph_envelope["nodes"],
            edges=graph_envelope["edges"],
            execution_order=graph_envelope["execution_order"],
            annotations=graph_envelope["annotations"],
            state=graph_envelope["state"],
        )

    # simulate / compare: dispatch to the BOOTED runtimes (not bare ones built inside
    # run_store), and give the compare path its plan_steps — its cognitive half executes
    # for real and refuses to run without them.
    #
    # Resolved from app.state here rather than as route dependencies: an execute-target
    # replay must not fail merely because the simulation/comparator runtimes are absent.
    # run_store.replay falls back to constructing its own if these are None.
    replay_steps = _graph_to_plan_steps(store.get_graph(run_id))
    result = await replay_run(
        run_id,
        mongo_client,
        runtime=runtime,
        sim_runtime=getattr(request.app.state, "simulation_runtime", None),
        comparator=getattr(request.app.state, "comparator_runtime", None),
        plan_steps=replay_steps,
    )
    elapsed_ms = (time.monotonic() - t0) * 1000

    return {
        "run_id": run_id,
        "target": target,
        "user_id": user_id,
        "elapsed_ms": round(elapsed_ms, 2),
        "result": result,
    }


# ── SSE Streaming Execute ──────────────────────────────────────────────────────


def _sse_event(event: str, data: dict | str) -> str:
    """Format a Server-Sent Event."""
    payload = json.dumps(data) if isinstance(data, dict) else str(data)
    return f"event: {event}\ndata: {payload}\n\n"


def _sse_fatal(error: str) -> str:
    """An error event plus the terminal `done`.

    Clients block waiting for `done`; without it they hang and then fall back
    to re-POSTing /execute, running the whole workload a second time.
    """
    return _sse_event("error", {"error": error}) + _sse_event("done", {"error": error, "failed": True, "answer": ""})


@router.post("/execute/stream")
@idempotent("execute.execute_stream")
async def execute_stream(
    payload: PlanResponse,
    mongo_client: Any = Depends(get_mongo_client),
    user_id: str = Depends(require_permission("perm-execute-action")),
    graph_store: Any = Depends(_get_graph_store),
    runtime: CognitiveRuntime = Depends(get_cognitive_runtime),
):
    """POST /execute/stream — SSE streaming execution with live progress events.

    Same semantics as /execute but streams layer/step events as they happen.
    """
    from src.monkey_brain.kernel.models.graph import canonical_graph_envelope
    from src.monkey_brain.kernel.plan.goals.run_store import get_run_store
    from src.monkey_brain.kernel.temporal_graph import clone_graph_snapshot

    run_id = payload.run_id
    logger.info("run=%r [execute/stream] user=%r", run_id, user_id)

    def _fatal_stream(error: str) -> StreamingResponse:
        async def error_gen():
            yield _sse_fatal(error)

        return StreamingResponse(error_gen(), media_type="text/event-stream")

    # Same gates as execute_action — this endpoint commits the same work, so it
    # must not be the one path that skips them. Until now /execute/stream ran
    # with NO input sanitization, NO governance check, NO audit record, and NO
    # intent_ir validation: a tampered intent_ir that /execute rejects with 400
    # (signature verification) executed here without complaint. Shared logic
    # with /simulate, /compare, execute_action — see
    # dependencies.py::sanitize_and_check_governance.
    try:
        payload.question = await sanitize_and_check_governance(
            payload.question,
            user_id,
            "execute",
            extra_context={"run_id": run_id},
        )
    except RequestRejected as e:
        return _fatal_stream(f"{e.error_code}: {e.detail}")

    record_request_audit(
        user_id,
        "execute",
        "execute_requested",
        {"run_id": run_id, "question": payload.question[:200], "stream": True},
    )

    # Same decode+validate as /execute (shared in decode_and_validate_ir), rendered as an
    # SSE fatal frame. Previously this twin caught a broader exception set on from_dict than
    # /execute did, so the two could disagree on what counted as malformed.
    try:
        ir = decode_and_validate_ir(payload.intent_ir)
    except IntentIRRejected as e:
        if e.kind == "invalid":
            logger.warning(
                "run=%r [execute/stream] intent_ir validation failed: %s",
                run_id,
                "; ".join(e.detail),
            )
            return _fatal_stream("intent_ir validation failed: " + "; ".join(e.detail))
        return _fatal_stream(str(e.detail) or "malformed intent_ir")

    # Only run a plan compiled for THIS runtime — the same guard /execute, /simulate and
    # /compare carry. It was added to all three and missed here, so the streaming twin of
    # /execute would happily execute a plan compiled with target="simulate"/"compare".
    # The stored target is server-side and authoritative (the IntentIR signature does not
    # cover it).
    stored_target = get_run_store().get_target(run_id) or payload.target
    if stored_target != "execute":
        logger.warning("run=%r [execute/stream] wrong target=%r", run_id, stored_target)
        return _fatal_stream(
            f"Plan was compiled with target={stored_target!r} — /execute/stream requires target=execute"
        )

    context = runtime.build_execution_runtime(ir, ExecutionMode.EXECUTE, user_id=user_id)

    planner_graph, source = await _recover_planner_graph(run_id, graph_store)
    if source in ("plan_store", "neo4j"):
        logger.info(
            "run=%r [execute/stream] recovered execution graph from %s (RunStore miss)",
            run_id,
            source,
        )
    if planner_graph is None:
        return _fatal_stream("Missing planner execution graph")

    # Same singleton hazard as execute_action — stamp and keep a local handle.
    execution_graph = clone_graph_snapshot(planner_graph)
    execution_graph.setdefault("metadata", {})["run_id"] = run_id
    runtime.execution_graph = execution_graph
    execute_steps = _graph_to_plan_steps(execution_graph)
    if not execute_steps:
        # Parity with execute_action — running a workload with zero steps
        # "succeeds" instantly and streams a done event for work never done.
        return _fatal_stream("Planner execution graph had no executable steps")

    graph_envelope = canonical_graph_envelope(execution_graph)
    nodes = graph_envelope.get("nodes", [])
    edges = graph_envelope.get("edges", [])
    execution_order = graph_envelope.get("execution_order", [])

    node_map = {n.get("id", ""): n for n in nodes}

    async def event_stream():
        t0 = time.monotonic()

        yield _sse_event(
            "init",
            {
                "graph_id": graph_envelope.get("graph_id", ""),
                "nodes": [{"id": n.get("id"), "name": n.get("name") or n.get("agent", "")} for n in nodes],
                "edges": edges,
                "execution_order": execution_order,
            },
        )

        try:
            answer, semantic_hits, graph_paths, llm_answered = await runtime.execute_cognitive_workload(
                context,
                mongo_client,
                plan_steps=execute_steps,
            )
        except Exception as e:
            logger.error("run=%r [execute/stream] failed: %s", run_id, e)
            # Terminal `done` so the client finalizes instead of hanging and
            # re-POSTing /execute — the workload already ran.
            yield _sse_fatal(str(e) or type(e).__name__)
            return

        elapsed_ms = (time.monotonic() - t0) * 1000

        # Since execution already completed, replay the state for the monitor
        # by reading the graph state that was set during execution. Read the
        # local graph, not runtime.execution_graph — a concurrent request may
        # have replaced the singleton's slot while we awaited above.
        if isinstance(execution_graph, dict):
            # Re-envelope so node states set during execution (failed /
            # unimplemented / complete) reach the client — the pre-execution
            # envelope holds a stale state dict.
            final_envelope = canonical_graph_envelope(execution_graph)
            final_nodes = final_envelope.get("nodes", [])
        else:
            final_envelope = graph_envelope
            final_nodes = nodes

        # Walk through execution_order and emit events with simulated timing
        total_ms = elapsed_ms
        step_count = len(execute_steps)
        avg_step_ms = total_ms / max(step_count, 1)

        for li, layer in enumerate(execution_order):
            yield _sse_event("layer_start", {"layer": li})

            for nid in layer:
                ns = node_map.get(nid, {})
                name = ns.get("name") or ns.get("agent", nid)

                yield _sse_event(
                    "step_start",
                    {
                        "node_id": nid,
                        "layer": li,
                        "output": f"Executing {name}...",
                    },
                )

                # Find the step result for this node
                node_state = {}
                for fn in final_nodes:
                    if fn.get("id") == nid:
                        node_state = fn
                        break

                state = node_state.get("state", "pending")
                is_complete = state == "complete"
                step_latency = avg_step_ms * (0.5 + (hash(nid) % 100) / 200)

                # An unresolved capability is not a failure — nothing ran.
                # Collapsing it to [failed] hides why the run was partial.
                label = {"complete": "ok"}.get(state, state)

                yield _sse_event(
                    "step_complete",
                    {
                        "node_id": nid,
                        "success": is_complete,
                        "state": state,
                        "latency_ms": round(step_latency, 1),
                        "output": f"{name}: [{label}]",
                    },
                )

            yield _sse_event("layer_complete", {"layer": li})

        # Same grounding contract as the non-streaming path — a streaming client must not be
        # the one consumer that still receives an unmarked hallucination.
        _sse_outcome = get_run_store().get_outcome(run_id) or {}
        _sse_citations = build_citations(semantic_hits) + list(_sse_outcome.get("step_citations") or [])
        _sse_grounding = assess_grounding(
            answer=answer,
            semantic_hits=semantic_hits,
            graph_paths=graph_paths,
            citations=_sse_citations,
            llm_answered=llm_answered,
        )
        answer = enforce_grounding(answer, _sse_grounding, run_id)

        yield _sse_event(
            "done",
            {
                "answer": answer,
                "elapsed_ms": round(elapsed_ms, 2),
                "llm_answered": llm_answered,
                "grounded": _sse_grounding["grounded"],
                "grounding": _sse_grounding,
                "success": bool(_sse_outcome.get("success", True)),
                "failed_steps": list(_sse_outcome.get("failed_steps") or []),
                "graph_id": final_envelope.get("graph_id", "") or graph_envelope.get("graph_id", ""),
                "nodes": final_nodes,
                "edges": edges,
                "execution_order": execution_order,
                "state": final_envelope.get("state", {}),
                "semantic_hits": semantic_hits,
                "graph_paths": graph_paths,
            },
        )

    return StreamingResponse(event_stream(), media_type="text/event-stream")
