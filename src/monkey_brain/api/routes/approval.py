"""Human approval / pause / resume — the generic execution state machine's
real API surface (Qualification Gap Closure, Phase 3).

GET  /executions/{execution_id}/pending-approval — inspect
POST /executions/{execution_id}/approve          — approve/reject and
                                                    resume the SAME
                                                    execution

RUNTIME APPROVAL GATE (NEW):
GET  /runtime-approvals/{approval_id}            — inspect approval artifact
POST /runtime-approvals/{approval_id}/approve    — human approval decision
POST /runtime-approvals/{approval_id}/reject     — human rejection decision
GET  /runtime-approvals/operation/{operation_id} — list approvals for operation
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from src.monkey_brain.api.dependencies import require_permission
from src.monkey_brain.api.idempotency import idempotent
from src.monkey_brain.kernel.models.prompt import PromptRequest
from src.monkey_brain.kernel.pipeline.approval_store import (
    load_pending_approval,
    resolve_pending_approval,
)
from src.monkey_brain.kernel.approval import (
    ApprovalMode,
    ApprovalStatus,
    get_approval_store,
)
from src.monkey_brain.kernel.trusted_auth import get_trusted_auth

logger = logging.getLogger("agentos.gateway.approval")
router = APIRouter()


def _get_planetary_runtime(request: Request) -> Any:
    selector = getattr(getattr(request.app.state, "kernel", None), "runtime_selector", None)
    if selector is not None:
        try:
            return selector.select("planetary")
        except LookupError:
            logger.debug("_get_planetary_runtime: suppressed exception", exc_info=True)
    return getattr(request.app.state, "planetary_runtime", None)


class ApprovalDecisionRequest(BaseModel):
    approved: bool


# ============================================================================
# RUNTIME APPROVAL GATE ENDPOINTS (NEW)
# ============================================================================


class HumanApprovalRequest(BaseModel):
    """Request to approve a pending approval artifact."""

    reason: str = Field(default="", description="Human's reason for approval")
    scope: dict[str, Any] = Field(default_factory=dict, description="Optional scope refinements")


class HumanRejectionRequest(BaseModel):
    """Request to reject a pending approval artifact."""

    reason: str = Field(..., description="Human's reason for rejection")


class ApprovalArtifactResponse(BaseModel):
    """Response containing an approval artifact."""

    approval_id: str
    operation_id: str
    approval_mode: str
    approval_source: str
    approval_status: str
    requesting_principal: str
    approving_principal: str
    target_operation: str
    target_resource: str
    operation_class: str
    risk_level: str
    policy_rule: str
    approved_at: float
    expires_at: float
    revoked_at: float | None = None
    revocation_reason: str = ""


@router.get("/runtime-approvals", tags=["Runtime Approval Gate"])
async def list_pending_approvals(
    user_id: str = Depends(require_permission("perm-view-learn")),
) -> dict[str, Any]:
    """List every approval artifact currently awaiting a human decision,
    across all operations — the discovery endpoint a UI needs before it can
    show anything, since every other endpoint here requires already knowing
    an approval_id or operation_id."""
    store = get_approval_store()
    artifacts = store.list_pending()

    return {
        "approvals": [
            {
                "approval_id": a.approval_id,
                "operation_id": a.operation_id,
                "approval_status": a.approval_status.value,
                "risk_level": a.risk_level,
                "requesting_principal": a.requesting_principal,
                "target_operation": a.target_operation,
                "target_resource": a.target_resource,
                "policy_rule": a.policy_rule,
                "created_at": a.created_at,
                "expires_at": a.expires_at,
            }
            for a in artifacts
        ],
        "total": len(artifacts),
    }


@router.get("/runtime-approvals/{approval_id}", tags=["Runtime Approval Gate"])
async def inspect_approval(
    approval_id: str,
    user_id: str = Depends(require_permission("perm-view-learn")),
) -> ApprovalArtifactResponse:
    """Inspect a pending approval artifact.

    Returns full provenance: who requested, what operation, why (policy rule),
    risk level, and time bounds.
    """
    store = get_approval_store()
    artifact = store.get(approval_id)

    if artifact is None:
        raise HTTPException(
            status_code=404,
            detail=f"approval {approval_id} not found",
        )

    if artifact.approval_mode != ApprovalMode.HUMAN_APPROVAL_REQUIRED:
        raise HTTPException(
            status_code=400,
            detail=f"approval {approval_id} does not require human approval (mode={artifact.approval_mode.value})",
        )

    return ApprovalArtifactResponse(
        approval_id=artifact.approval_id,
        operation_id=artifact.operation_id,
        approval_mode=artifact.approval_mode.value,
        approval_source=artifact.approval_source.value,
        approval_status=artifact.approval_status.value,
        requesting_principal=artifact.requesting_principal,
        approving_principal=artifact.approving_principal,
        target_operation=artifact.target_operation,
        target_resource=artifact.target_resource,
        operation_class=artifact.operation_class,
        risk_level=artifact.risk_level,
        policy_rule=artifact.policy_rule,
        approved_at=artifact.approved_at,
        expires_at=artifact.expires_at,
        revoked_at=artifact.revoked_at,
        revocation_reason=artifact.revocation_reason,
    )


@router.post(
    "/runtime-approvals/{approval_id}/approve",
    tags=["Runtime Approval Gate"],
    status_code=status.HTTP_200_OK,
)
@idempotent("approval.grant_runtime_approval")
async def grant_approval(
    approval_id: str,
    body: HumanApprovalRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-action")),
) -> dict[str, Any]:
    """Grant human approval for a HUMAN_APPROVAL_REQUIRED operation.

    This converts the approval artifact to AUTO_APPROVE status, allowing
    the previously-blocked operation to proceed.

    Invariants:
    - Only HUMAN_APPROVAL_REQUIRED approvals can be approved
    - Approving principal must be authenticated and authorized
    - Self-approval is prevented (requesting_principal != approving_principal)
    - Approval records the human's identity and timestamp
    """
    store = get_approval_store()
    artifact = store.get(approval_id)

    if artifact is None:
        raise HTTPException(
            status_code=404,
            detail=f"approval {approval_id} not found",
        )

    if artifact.approval_mode != ApprovalMode.HUMAN_APPROVAL_REQUIRED:
        raise HTTPException(
            status_code=400,
            detail=f"approval {approval_id} is not HUMAN_APPROVAL_REQUIRED (mode={artifact.approval_mode.value})",
        )

    if artifact.approval_status != ApprovalStatus.ACTIVE:
        raise HTTPException(
            status_code=400,
            detail=f"approval {approval_id} is not ACTIVE (status={artifact.approval_status.value})",
        )

    trusted_auth = get_trusted_auth()
    if trusted_auth.principal_id == artifact.requesting_principal:
        logger.warning(
            "Self-approval prevented: approval=%s, principal=%s",
            approval_id,
            trusted_auth.principal_id,
        )
        raise HTTPException(
            status_code=403,
            detail="Cannot approve your own request (self-approval prevention)",
        )

    # Create approved artifact (with human approver recorded)
    # Note: ApprovalArtifact is frozen, so we create a new one with updated approving_principal
    from src.monkey_brain.kernel.approval import ApprovalArtifact, ApprovalSource
    import time

    approved_artifact = ApprovalArtifact(
        approval_id=artifact.approval_id,
        operation_id=artifact.operation_id,
        approval_mode=ApprovalMode.AUTO_APPROVE,  # Upgrade to AUTO_APPROVE
        approval_source=ApprovalSource.HUMAN,  # Mark as human-approved
        approval_status=ApprovalStatus.ACTIVE,
        requesting_principal=artifact.requesting_principal,
        approving_principal=trusted_auth.principal_id,  # Record who approved
        target_operation=artifact.target_operation,
        target_resource=artifact.target_resource,
        operation_class=artifact.operation_class,
        scope=artifact.scope,
        constraints=artifact.constraints,
        policy_rule=artifact.policy_rule,
        policy_decision=artifact.policy_decision,
        policy_revision=artifact.policy_revision,
        risk_level=artifact.risk_level,
        approved_at=time.time(),  # Update approval timestamp
        expires_at=artifact.expires_at,
        correlation_id=artifact.correlation_id,
        audit_entry_id=artifact.audit_entry_id,
        signature=artifact.signature,
        created_at=artifact.created_at,
    )

    # Replace in store (this is safe because we're replacing the same approval_id)
    store._artifacts[approval_id] = approved_artifact

    logger.info(
        "Granted approval %s for operation %s by %s",
        approval_id,
        artifact.operation_id,
        trusted_auth.principal_id,
    )

    return {
        "approval_id": approval_id,
        "status": "approved",
        "approving_principal": trusted_auth.principal_id,
        "operation_id": artifact.operation_id,
    }


@router.post(
    "/runtime-approvals/{approval_id}/reject",
    tags=["Runtime Approval Gate"],
    status_code=status.HTTP_200_OK,
)
@idempotent("approval.reject_runtime_approval")
async def reject_approval(
    approval_id: str,
    body: HumanRejectionRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-action")),
) -> dict[str, Any]:
    """Reject a HUMAN_APPROVAL_REQUIRED operation.

    This revokes the approval artifact, preventing the operation from
    ever proceeding. If the operation was queued (in AWAITING_APPROVAL state),
    it is transitioned to FAILED with rejection reason.
    """
    from src.monkey_brain.kernel.security_operation import (
        get_operation_ledger,
        SecurityOperationState,
    )

    store = get_approval_store()
    artifact = store.get(approval_id)

    if artifact is None:
        raise HTTPException(
            status_code=404,
            detail=f"approval {approval_id} not found",
        )

    if artifact.approval_mode != ApprovalMode.HUMAN_APPROVAL_REQUIRED:
        raise HTTPException(
            status_code=400,
            detail=f"approval {approval_id} is not HUMAN_APPROVAL_REQUIRED (mode={artifact.approval_mode.value})",
        )

    trusted_auth = get_trusted_auth()
    revocation_reason = f"Rejected by {trusted_auth.principal_id}: {body.reason}"

    store.revoke(approval_id, revocation_reason)

    logger.info(
        "Rejected approval %s for operation %s by %s: %s",
        approval_id,
        artifact.operation_id,
        trusted_auth.principal_id,
        body.reason,
    )

    # Transition operation to FAILED in the security ledger
    try:
        ledger = get_operation_ledger()
        op = ledger.get(artifact.operation_id)
        if op and op.state == SecurityOperationState.AWAITING_APPROVAL:
            ledger.transition(
                artifact.operation_id,
                SecurityOperationState.FAILED,
                rejection_reason=body.reason,
                rejected_by=trusted_auth.principal_id,
                approval_id=approval_id,
            )
            logger.info(
                "Operation %s transitioned to FAILED due to approval rejection",
                artifact.operation_id,
            )
    except Exception as exc:
        logger.error(
            "Failed to transition operation %s to FAILED: %s",
            artifact.operation_id,
            exc,
        )
        # Don't fail the rejection endpoint — approval is already revoked

    return {
        "approval_id": approval_id,
        "status": "rejected",
        "rejecting_principal": trusted_auth.principal_id,
        "operation_id": artifact.operation_id,
    }


@router.get("/runtime-approvals/operation/{operation_id}", tags=["Runtime Approval Gate"])
async def list_approvals_for_operation(
    operation_id: str,
    user_id: str = Depends(require_permission("perm-view-learn")),
) -> dict[str, Any]:
    """List all approvals for an operation.

    Useful for auditing: what approvals were created for a given operation,
    their status, and who made decisions.
    """
    store = get_approval_store()
    artifacts = store.get_for_operation(operation_id)

    return {
        "operation_id": operation_id,
        "approvals": [
            {
                "approval_id": a.approval_id,
                "approval_mode": a.approval_mode.value,
                "approval_status": a.approval_status.value,
                "risk_level": a.risk_level,
                "requesting_principal": a.requesting_principal,
                "approving_principal": a.approving_principal,
            }
            for a in artifacts
        ],
        "total": len(artifacts),
    }


# ============================================================================
# EXECUTION APPROVAL ENDPOINTS (EXISTING)
# ============================================================================


@router.get("/executions/{execution_id}/pending-approval", tags=["Approval"])
async def get_pending_approval(
    execution_id: str,
    user_id: str = Depends(require_permission("perm-view-learn")),
) -> dict[str, Any]:
    """Real, read-only inspection of a paused execution — what capability
    proposed what, and why, so a real approval decision can be made with
    full context rather than a bare yes/no."""
    pending = load_pending_approval(execution_id)
    if pending is None:
        raise HTTPException(
            status_code=404,
            detail=f"no pending approval for execution {execution_id!r}",
        )
    return pending.to_dict()


@router.post("/executions/{execution_id}/approve", tags=["Approval"])
@idempotent("approval.approve_pending_execution")
async def approve_pending_execution(
    execution_id: str,
    body: ApprovalDecisionRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-prompt")),
) -> dict[str, Any]:
    """Records the real human decision, then resumes the exact same
    execution via the real, already-proven meta.resume_execution_id
    mechanism (kernel/compile/cognitive_actor.py, Phase 3's checkpoint/
    restart infrastructure) — never a new, unrelated task. The resumed
    tick's own real result (completed, with the approved substitution or
    an honest rejection failure) is returned directly, not a synthetic
    "ok" acknowledgement."""
    pending = load_pending_approval(execution_id)
    if pending is None:
        raise HTTPException(
            status_code=404,
            detail=f"no pending approval for execution {execution_id!r}",
        )
    if not pending.actor_id:
        raise HTTPException(
            status_code=500,
            detail=f"pending approval for {execution_id!r} has no actor_id on record",
        )

    resolve_pending_approval(execution_id, body.approved)

    pr = _get_planetary_runtime(request)
    if pr is None:
        raise HTTPException(status_code=503, detail="PlanetaryRuntime not available")

    # Qualification Gap Closure, Phase 9 fix: reuse the REAL original
    # prompt text, not a generic placeholder -- belief_runtime.py::
    # _generate_plan only reuses the checkpointed plan when the request's
    # own question parses to a real goal (has_goal); a placeholder like
    # "resume this" doesn't, which silently produced an empty, goal-less
    # plan that PlanValidator then rejected outright
    # ("plan_has_no_goal") before the checkpoint was ever reached — found
    # by live-testing this exact resume path end-to-end, not by any of
    # this session's own executor-level unit tests (which bypass
    # belief_runtime.py's planning stage entirely).
    prompt_request = PromptRequest(
        question=pending.original_question or "Resume after a real human approval decision.",
        meta={"resume_execution_id": execution_id},
    )
    pr.restore_actor_belief(pending.actor_id)
    result = await pr.execute_actor_request(pending.actor_id, prompt_request)
    pr.checkpoint_actor_belief(pending.actor_id)

    # Reuse api/routes/prompt.py's own real result-adaptation (the exact
    # same shape a normal /prompt response uses) rather than returning
    # PlanetaryRuntime's internal result object directly.
    from src.monkey_brain.api.routes.prompt import _actor_query_result

    query_result, business_flow = _actor_query_result(prompt_request.question, pending.actor_id, result)

    return {
        "execution_id": execution_id,
        "approved": body.approved,
        "actor_id": pending.actor_id,
        "query_result": query_result,
        "business_flow": business_flow,
    }
