"""Prompt API routes.

Prompt execution is deliberately a thin adapter over ``PlanetaryRuntime``.
The planetary runtime owns actor/society/geography resolution, recursive
traversal, context/world updates, and actor coordination.  Keeping that
boundary here prevents HTTP requests from creating a second execution path.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, is_dataclass
from typing import Any

from fastapi import APIRouter, Depends, Request

from src.monkey_brain.api.dependencies import require_permission
from src.monkey_brain.api.idempotency import idempotent
from src.monkey_brain.api.helpers.healing_helpers import (
    reset_cooldown,
    run_post_workload,
)
from src.monkey_brain.api.helpers.prompt_helpers import (
    resolve_run_type,
    validate_propagation_scope,
)
from src.monkey_brain.api.helpers.stability_helpers import check_stability
from src.monkey_brain.kernel.plan.intents.predicates.self_healing_workload import (
    is_self_healing_question,
)
from src.monkey_brain.kernel.plan.intents.predicates.sittingface_workload import (
    is_sittingface_workload_question,
)
from src.monkey_brain.runtime.routers import get_mongo_client
from src.monkey_brain.kernel.models import (
    PromptRequest,
    PromptResponse,
    _RequestErrorCapture,
)

logger = logging.getLogger("agentos.prompt")
router = APIRouter()
_background_tasks: set[asyncio.Task] = set()

# How long the internal forward (below) waits for a dedicated actor Pod's
# own /prompt to finish a real planning+execution cycle — matches
# model_backend.py's own Ollama httpx timeout (120s) with margin, not the
# short per-call timeouts used for plain health/liveness checks elsewhere
# in this file.
_ACTOR_POD_FORWARD_TIMEOUT_SEC = 150.0


def _actor_pod_node_id(planetary_runtime: Any, actor_id: str) -> str | None:
    """Does the Actor Registry (kernel/society/integration.py's
    PlanetaryRuntime.locate_actor(), the real placement record a
    SchedulingDecision.node_id/ActorScheduler.schedule() call wrote)
    record `actor_id` as placed on its own dedicated
    actor-deployment.yaml/drone-actor-deployment.yaml Pod? Returns the
    recorded node_id (a raw Pod name, ACTOR_NODE_ID = metadata.name,
    which changes every restart) if so, else None — the caller only ever
    uses this as a yes/no signal to recognize dedicated-Pod placement,
    never to address the Pod directly (see _try_forward_to_actor_pod's
    own docstring for why the Service name is dialed instead). Shared by
    both the proactive check in unified_prompt (forward BEFORE ever
    attempting local execution for a dedicated-Pod actor) and
    _try_forward_to_actor_pod's own reactive fallback, so the two can
    never disagree on what "dedicated-Pod-placed" means."""
    locate_actor = getattr(planetary_runtime, "locate_actor", None) if planetary_runtime is not None else None
    entry = locate_actor(actor_id) if callable(locate_actor) else None
    node_id = getattr(entry, "node_id", "") or ""
    if node_id.startswith(f"cognitiveos-actor-{actor_id}"):
        return node_id
    return None


async def _try_forward_to_actor_pod(planetary_runtime: Any, actor_id: str, question: str) -> dict[str, Any] | None:
    """Forward one /prompt request to `actor_id`'s own dedicated Pod
    instead of this shared central control-plane process executing it.

    Called two ways from unified_prompt: PROACTIVELY, before local
    execution is ever attempted, for any actor _actor_pod_node_id()
    recognizes as dedicated-Pod-placed — confirmed live this matters for
    more than just an outright failure: this process CAN often execute a
    dedicated-Pod actor's cognition locally without error (most action
    types don't need any node-specific binding), which silently
    "succeeded" for a human/grocery actor but, for a robot actor, quietly
    failed its actual flight actions ("no ROS adapter bound to this
    actor") while still reporting a normal-looking outcome — only that
    Pod's own drone-actor-deployment.yaml env vars (ROS_ADAPTER_KIND,
    ROS_BRIDGE_URL) wire it to the real PX4 simulator. And REACTIVELY, as
    a fallback when local execution raises kernel/society/integration.py's
    "was not reached by its effective societies" (an actor the registry
    knows about but that isn't resident in any society this process
    manages) — kept for the inverse edge case, a registry lookup that
    said "not dedicated" (or wasn't consulted) at request time but local
    execution still couldn't find the actor.

    Same idea as `kubectl logs <pod>` transparently proxying through the
    API server to whichever kubelet actually holds the Pod, rather than
    requiring the caller to know or address that kubelet directly: the
    client here always calls the SAME central /prompt (via Kong); this
    function is what makes "the actor happens to live on its own
    dedicated Pod" invisible to that caller. actor-deployment.yaml's
    Service name (cognitiveos-actor-{actor_id}) is what's actually
    dialed, since it deterministically follows whichever Pod currently
    backs that Deployment regardless of the specific node_id recorded.

    Never raises: a registry miss, a stale node_id, or the forward itself
    failing all degrade to None, so the caller's own existing fallback
    (local execution, or the original error) still applies when there's
    truly nowhere else to try.
    """
    import os

    node_id = _actor_pod_node_id(planetary_runtime, actor_id)
    if node_id is None:
        logger.debug(
            "[prompt] locate_actor(%r) recorded no dedicated-Pod placement — not forwarding",
            actor_id,
        )
        return None

    token = os.environ.get("INTERNAL_SERVICE_TOKEN", "")
    if not token:
        return None
    import httpx

    url = f"http://cognitiveos-actor-{actor_id}:8051/prompt"
    try:
        async with httpx.AsyncClient(timeout=_ACTOR_POD_FORWARD_TIMEOUT_SEC) as client:
            resp = await client.post(
                url,
                headers={
                    "X-Internal-Service-Token": token,
                    "Content-Type": "application/json",
                },
                json={"question": question},
            )
        resp.raise_for_status()
        logger.info(
            "[prompt] forwarded to dedicated actor Pod for %r (registry node_id=%r, %s)",
            actor_id,
            node_id,
            url,
        )
        return resp.json()
    except Exception as exc:
        logger.debug(
            "[prompt] dedicated actor Pod unreachable for %r (%s): %s",
            actor_id,
            url,
            exc,
        )
        return None


def _response_from_forwarded(
    question: str, actor_id: str, forwarded: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Adapt a dedicated actor Pod's own /prompt response into the same
    query_result/business_flow shape _actor_query_result() builds for a
    locally-executed request — shared by unified_prompt's proactive
    (before local execution) and reactive (after a "not reached" error)
    forward call sites, so the two never render this differently."""
    actions = forwarded.get("actions") or []
    goal_achieved = forwarded.get("goal_achieved")
    query_result = {
        "question": question,
        "answer": (
            f"{question} executed through the planetary cycle successfully"
            if goal_achieved
            else f"{question} executed (goal not fully achieved)"
        ),
        "semantic_hits": [],
        "graph_paths": [],
        "citations": [],
        "llm_answered": True,
        "actor_id": actor_id,
        "actor_execution": {
            "actions": actions,
            "actual_outcome": {"goal_achieved": goal_achieved},
            "plan": forwarded.get("plan"),
        },
    }
    business_flow = {
        "question": question,
        "actor": actor_id,
        "flow": [
            {"index": i, "action_id": a.get("action_id"), "success": a.get("success")} for i, a in enumerate(actions)
        ],
        "result": {"actions_taken": len(actions), "goal_achieved": goal_achieved},
    }
    return query_result, business_flow


def _on_task_done(task: asyncio.Task) -> None:
    _background_tasks.discard(task)
    if not task.cancelled() and task.exception() is not None:
        logger.error("[prompt] background workload failed: %s", task.exception())


def _result_value(result: Any) -> Any:
    if is_dataclass(result):
        return asdict(result)
    if isinstance(result, dict):
        return result
    return getattr(result, "__dict__", result)


def _actor_query_result(question: str, actor_id: str, result: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Adapt the scheduler's actor result to the existing prompt response shape."""
    value = _result_value(result)
    actions = value.get("actions", []) if isinstance(value, dict) else []
    outcome = value.get("actual_outcome", {}) if isinstance(value, dict) else {}
    achieved = bool(outcome.get("goal_achieved", False)) if isinstance(outcome, dict) else False
    answer = f"{question} executed through the planetary cycle"
    if achieved:
        answer += " successfully"

    # Real gap this closes: RespondToInquiryCapability (kernel/domains/
    # grocery.py) is Autonomous Dialogue's termination signal — its own
    # docstring says "An orchestrator watches for this action name to know
    # the conversation is over" and parameters["answer"] is the real,
    # planner-written natural-language answer to whoever asked the original
    # question. But this — the actual HTTP-facing orchestrator a caller's
    # answer comes back through — never watched for it at all: `answer`
    # above was always this generic "executed through the planetary cycle"
    # string, silently discarding the real answer every time (confirmed
    # live, repeatedly, across this session's own /prompt calls). plan.steps
    # and actions are parallel, same-order lists (one action per step) —
    # cross-reference by index to find which action, if any, was a
    # successful RespondToInquiry step, and surface ITS real answer instead.
    plan_steps = value.get("plan", {}).get("steps", []) if isinstance(value, dict) else []
    for index, action in enumerate(actions):
        if not isinstance(action, dict) or index >= len(plan_steps):
            continue
        step = plan_steps[index]
        step_action = step.get("action") if isinstance(step, dict) else None
        if step_action == "RespondToInquiry" and action.get("success"):
            respond_answer = (action.get("result") or {}).get("answer")
            if respond_answer:
                answer = respond_answer

    query_result = {
        "question": question,
        "answer": answer,
        "semantic_hits": [],
        "graph_paths": [],
        "citations": [],
        "llm_answered": True,
        "actor_id": actor_id,
        "actor_execution": value,
    }
    business_flow = {
        "question": question,
        "actor": actor_id,
        "flow": [
            {
                "step": index + 1,
                "action": (action.get("action_id", f"action-{index + 1}") if isinstance(action, dict) else str(action)),
                "result": action.get("result") if isinstance(action, dict) else None,
                "success": action.get("success") if isinstance(action, dict) else None,
            }
            for index, action in enumerate(actions)
        ],
        "result": {
            "actions_taken": len(actions),
            "goal_achieved": achieved,
        },
    }
    return query_result, business_flow


@router.post("/prompt")
@idempotent("prompt.execute")
async def unified_prompt(
    request: Request,
    payload: PromptRequest,
    mongo_client: Any = Depends(get_mongo_client),
    user_id: str = Depends(require_permission("perm-execute-prompt")),
) -> PromptResponse:
    """Execute one prompt as the requesting actor's next planetary tick."""

    started = time.monotonic()

    # Determine the run type and max healing level for this prompt execution.
    run_type, max_healing = resolve_run_type(payload)

    # get the propagation scope
    validate_propagation_scope(payload)

    # Set up a logging capture to collect any errors that occur during execution.
    capture = _RequestErrorCapture()
    root_logger = logging.getLogger()
    root_logger.addHandler(capture)
    query_result: dict[str, Any] | None = None
    business_flow: dict[str, Any] | None = None

    try:
        # get the planetary runtime to run the cycle this is inti on app boot and is used to run the planetary cycle
        # for the actor. this is the world level runtime
        # the planetary runtime takes care of
        # 1. actor/society/geography resolution,
        # 2. recursive traversal,
        # 3. context/world updates,
        # 4. and actor coordination
        # the planetary runtime acts as a controller for actor scheduling and execution
        planetary_runtime = getattr(request.app.state, "planetary_runtime", None)
        if planetary_runtime is None:
            raise RuntimeError("PlanetaryRuntime is not booted")

        # validate the world state before executing the promptok fix the
        import os

        if os.getenv("WORLD_VALIDATION_GATE_EXECUTE", "true").strip().lower() != "false":
            from src.monkey_brain.kernel.validation.world_validator import (
                validate_world,
            )

            # validate the world before execution
            _report = validate_world(planetary_runtime, actor_id=user_id)
            if not _report["ok"]:
                raise RuntimeError(
                    f"world validation failed ({_report['violation_count']} violations across "
                    f"categories {_report['categories']}) — refusing to execute"
                )

        # Dedicated-Pod-placed actors (edge/device/robot — actor-
        # deployment.yaml/drone-actor-deployment.yaml) carry node-specific
        # bindings only that Pod has: a robot's real ROS_ADAPTER_KIND/
        # ROS_BRIDGE_URL to its PX4 simulator, an edge node's offline-
        # safety gate. This shared central process can often execute
        # such an actor's cognition anyway (belief/plan/most action types
        # need none of that) — confirmed live that's exactly the trap:
        # it silently "succeeded" for a human/grocery actor, but for a
        # robot actor it executed a normal-looking plan whose actual
        # flight actions all failed with "no ROS adapter bound to this
        # actor," no exception raised at all. Checking placement FIRST
        # and forwarding before local execution is ever attempted (not
        # only reactively, after a "was not reached" error below) closes
        # that gap for every actor type. Skipped when THIS process IS the
        # actor's own dedicated Pod (ACTOR_ID == user_id) — actor_runtime.py
        # always sets ACTOR_ID to itself, the shared control-plane process
        # never sets it at all, so this never forwards a Pod to itself.
        forwarded_early = None
        if os.environ.get("ACTOR_ID", "") != user_id and _actor_pod_node_id(planetary_runtime, user_id):
            forwarded_early = await _try_forward_to_actor_pod(planetary_runtime, user_id, payload.question)

        if forwarded_early is not None:
            query_result, business_flow = _response_from_forwarded(payload.question, user_id, forwarded_early)
        else:
            # on getting the  world validation result we have to create a local copy of the worlds state as the belief state of the actor and then we have to run the
            # prompt on that local copy of the world state and then we have to update the world state with the result of the prompt execution.
            # This is to ensure that the world state is not modified by the prompt execution and that the world state is consistent across all actors.
            planetary_runtime.restore_actor_belief(user_id)

            # execute the actor requests recieved from the planetary runtime
            actor_result = await planetary_runtime.execute_actor_request(user_id, payload)

            # checkpont the local belief for the actor after the execution of the actor request
            planetary_runtime.checkpoint_actor_belief(user_id)

            # Adapt the scheduler's actor result to the existing prompt response shape.
            query_result, business_flow = _actor_query_result(payload.question, user_id, actor_result)

    except Exception as exc:
        logger.error("[prompt] planetary execution failed: %s", exc)
        # This specific failure means the actor exists in the registry
        # but isn't resident in any society THIS process manages — the
        # exact shape of an actor exclusively scheduled to its own
        # dedicated actor-deployment.yaml/drone-actor-deployment.yaml Pod
        # (see kernel/society/integration.py's own raise site). Kept as a
        # fallback for the INVERSE edge case from the proactive check
        # above: a registry lookup that said "not dedicated-Pod-placed"
        # (or a stale/unavailable one) at request time, where local
        # execution then couldn't find the actor at all. See
        # _try_forward_to_actor_pod's own docstring for why this is safe
        # to attempt unconditionally (degrades to None, never raises, for
        # every other failure reason too).
        forwarded = None
        if "was not reached by its effective societies" in str(exc):
            forwarded = await _try_forward_to_actor_pod(planetary_runtime, user_id, payload.question)
        if forwarded is not None:
            query_result, business_flow = _response_from_forwarded(payload.question, user_id, forwarded)
        else:
            query_result = {
                "question": payload.question,
                "answer": f"Error: {exc}",
                "semantic_hits": [],
                "graph_paths": [],
                "citations": [],
                "llm_answered": False,
            }
    finally:
        root_logger.removeHandler(capture)

    elapsed_ms = (time.monotonic() - started) * 1000

    # SittingFace workload questions get their post-execution healing/answer pass
    # kicked off in the background, after the actor request has actually run.
    if (
        is_sittingface_workload_question(payload.question)
        and not is_self_healing_question(payload.question)
        and run_type not in {"healing", "stability"}
    ):
        task = asyncio.create_task(
            run_post_workload(
                question=payload.question,
                error_lines=list(capture.lines),
                mongo_client=mongo_client,
                run_type=run_type,
                max_healing=max_healing,
            )
        )
        _background_tasks.add(task)
        task.add_done_callback(_on_task_done)

    return PromptResponse(
        question=payload.question,
        query_result=query_result,
        business_flow=business_flow,
        execution_summary={
            "total_execution_time_ms": f"{elapsed_ms:.2f}",
            "endpoints_called": ["planetary_runtime.execute_actor_request"],
            "error_count": len(capture.lines),
            "run_type": run_type,
        },
        error_lines=capture.lines,
    )


@router.get("/prompt/health")
async def prompt_health() -> dict[str, Any]:
    return {"status": "healthy", "service": "unified-prompt"}


@router.get("/prompt/stability")
async def get_stability_status(
    user_id: str = Depends(require_permission("perm-view-stability")),
) -> dict[str, Any]:
    return check_stability()


@router.post("/prompt/reset")
@idempotent("prompt.reset_workload")
async def reset_workload(
    user_id: str = Depends(require_permission("perm-reset-workload")),
) -> dict[str, Any]:
    reset_cooldown()
    return {"status": "reset"}


@router.post("/prompt/cicd")
@idempotent("prompt.cicd_prompt")
async def cicd_prompt(
    request: Request,
    payload: PromptRequest,
    mongo_client: Any = Depends(get_mongo_client),
    user_id: str = Depends(require_permission("perm-execute-prompt")),
) -> dict[str, Any]:
    response = await unified_prompt(request, payload, mongo_client, user_id)
    return {
        **response.model_dump(),
        "cicd_metadata": {
            "workload_stage": "prompt_execution",
            "run_type": resolve_run_type(payload)[0],
            "orchestration": {"planetary_runtime": True},
        },
    }
