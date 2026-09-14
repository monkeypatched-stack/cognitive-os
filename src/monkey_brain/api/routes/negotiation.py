"""Pre-commit negotiation gate — real API surface (mirrors approval.py's
own pause/resume shape exactly, for the counterpart state machine).

GET  /executions/{execution_id}/pending-negotiation — inspect
POST /executions/{execution_id}/negotiate           — record agree/reject
                                                        and resume the SAME
                                                        execution
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from src.monkey_brain.api.dependencies import require_permission, _audit_auth_failure
from src.monkey_brain.api.idempotency import idempotent
from src.monkey_brain.kernel.models.prompt import PromptRequest
from src.monkey_brain.kernel.pipeline.negotiation_store import (
    load_pending_negotiation,
    resolve_pending_negotiation,
)

logger = logging.getLogger("agentos.gateway.negotiation")
router = APIRouter()


def _get_planetary_runtime(request: Request) -> Any:
    selector = getattr(getattr(request.app.state, "kernel", None), "runtime_selector", None)
    if selector is not None:
        try:
            return selector.select("planetary")
        except LookupError:
            logger.debug("_get_planetary_runtime: suppressed exception", exc_info=True)
    return getattr(request.app.state, "planetary_runtime", None)


class NegotiationDecisionRequest(BaseModel):
    accepted: bool


@router.get("/executions/{execution_id}/pending-negotiation", tags=["Negotiation"])
async def get_pending_negotiation(
    execution_id: str,
    user_id: str = Depends(require_permission("perm-view-learn")),
) -> dict[str, Any]:
    """Real, read-only inspection of a paused execution — the exact
    ProposedTransition the gate evaluated, who must agree, and why."""
    pending = load_pending_negotiation(execution_id)
    if pending is None:
        raise HTTPException(
            status_code=404,
            detail=f"no pending negotiation for execution {execution_id!r}",
        )
    return pending.to_dict()


@router.post("/executions/{execution_id}/negotiate", tags=["Negotiation"])
@idempotent("negotiation.negotiate_pending_execution")
async def negotiate_pending_execution(
    execution_id: str,
    body: NegotiationDecisionRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-prompt")),
) -> dict[str, Any]:
    """Records the real negotiation outcome, then resumes the exact same
    execution via meta.resume_execution_id (same mechanism approval.py's
    /approve already uses). An accepted decision lets the gated capability
    run for the first time — it was never invoked while negotiation was
    pending; a rejected decision resumes only to observe the gate's own
    honest abort, never mutating shared state."""
    pending = load_pending_negotiation(execution_id)
    if pending is None:
        raise HTTPException(
            status_code=404,
            detail=f"no pending negotiation for execution {execution_id!r}",
        )
    if not pending.actor_id:
        raise HTTPException(
            status_code=500,
            detail=f"pending negotiation for {execution_id!r} has no actor_id on record",
        )

    # SECURITY (Doot audit, BYPASS-01's necessary complement): the gate
    # already computed exactly who must agree (pending.counterparties) —
    # without this check ANY caller holding perm-execute-prompt (every
    # actor's own default token, per the identity/auth audit) could
    # accept or reject a negotiation on a counterparty's behalf,
    # including the proposing actor accepting their own gate. Real
    # consent means the counterparty decides, not whoever calls first.
    if pending.counterparties and user_id not in pending.counterparties:
        await _audit_auth_failure(
            "perm-execute-prompt",
            "deny",
            "not_a_negotiation_counterparty",
            subject=user_id,
        )
        raise HTTPException(
            status_code=403,
            detail=f"only a required counterparty {list(pending.counterparties)!r} may resolve this negotiation",
        )

    resolve_pending_negotiation(execution_id, body.accepted)

    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")

    # Surface the counterparty's decision as a reply in the same
    # conversation thread the proposal was published to (see
    # action_executor.py's negotiation-gate publish) — same context_stream,
    # same thread_id/interaction_id (execution_id), so the Conversations
    # panel groups proposal + decision as one exchange instead of the
    # decision being invisible.
    try:
        from src.monkey_brain.kernel.society.context_stream import (
            ContextEvent,
            ContextEventType,
        )

        pr.context_stream.publish(
            ContextEvent(
                event_type=ContextEventType.INTERACTION,
                actor_id=user_id,
                description=f"{user_id} {'accepted' if body.accepted else 'rejected'} the proposal from {pending.actor_id}",
                payload={
                    "from_actor_id": user_id,
                    "to_actor_id": pending.actor_id,
                    "participants": [
                        user_id,
                        pending.actor_id,
                        *pending.counterparties,
                    ],
                    "thread_id": execution_id,
                    "interaction_id": execution_id,
                    "message": (
                        f"{'Accepted' if body.accepted else 'Rejected'}: {pending.reason}"
                        if pending.reason
                        else ("Accepted" if body.accepted else "Rejected")
                    ),
                },
                provenance="negotiation:decision",
                correlation_id=execution_id,
            )
        )
    except Exception:
        logger.warning(
            "[negotiation] failed to publish decision event for execution %s",
            execution_id,
        )

    prompt_request = PromptRequest(
        question=pending.original_question or "Resume after a real negotiation decision.",
        meta={"resume_execution_id": execution_id},
    )
    pr.restore_actor_belief(pending.actor_id)
    result = await pr.execute_actor_request(pending.actor_id, prompt_request)
    pr.checkpoint_actor_belief(pending.actor_id)

    from src.monkey_brain.api.routes.prompt import _actor_query_result

    query_result, business_flow = _actor_query_result(prompt_request.question, pending.actor_id, result)

    return {
        "execution_id": execution_id,
        "accepted": body.accepted,
        "actor_id": pending.actor_id,
        "counterparties": pending.counterparties,
        "query_result": query_result,
        "business_flow": business_flow,
    }
