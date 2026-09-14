"""Hybrid Router query endpoint — end-to-end query processing.

POST /api/v1/agentos/query
  Classify question, route to appropriate handler, execute, and return result.
  Single call end-to-end (unlike /plan + /execute two-step flow).

Flow: Question → Classification → Routing → Execution → Response
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.hybrid import HybridRouter
from src.monkey_brain.api.dependencies import require_permission
from src.monkey_brain.api.idempotency import idempotent
from src.introspection.lemon import get_lemon

logger = logging.getLogger("agentos.query")

router = APIRouter()


class QueryRequest(BaseModel):
    """Query request model"""

    question: str = Field(..., description="User's question or request")
    session_id: Optional[str] = Field(None, description="Optional session ID for multi-turn conversations")


class QueryResponse(BaseModel):
    """Query response model"""

    status: str
    response_text: str
    handler_type: str
    query_type: str
    confidence: float
    run_id: str = ""
    session_id: Optional[str] = None
    total_latency_ms: float
    classification_time_ms: float
    metadata: dict = Field(default_factory=dict)


def _get_hybrid_router(request: Request) -> HybridRouter:
    """Get or create HybridRouter from app state"""
    if not hasattr(request.app.state, "hybrid_router"):
        raise RuntimeError("HybridRouter not initialized in app state")
    return request.app.state.hybrid_router


@router.post("/query", response_model=QueryResponse)
@idempotent("query.process_query")
async def process_query(
    payload: QueryRequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-query")),
) -> Any:
    """POST /query — Process query through hybrid router.

    Single endpoint for end-to-end query handling:
    1. Classify query type (LLM or pattern-based)
    2. Route to appropriate handler
    3. Execute and return response

    Requires perm-query permission.
    """
    run_id = str(uuid4())

    # Input validation
    try:
        from src.monkey_brain.kernel.security import sanitize_input

        question = sanitize_input(payload.question)
    except ValueError as e:
        logger.warning("run=%r [query] input validation failed: %s", run_id, e)
        return JSONResponse(status_code=400, content={"error": "invalid_input", "detail": str(e)})
    except ImportError:
        logger.error("Security sanitizer unavailable — denying query")
        return JSONResponse(
            status_code=500,
            content={
                "error": "security_unavailable",
                "detail": "Input sanitizer unavailable",
            },
        )

    # Governance check
    try:
        from src.monkey_brain.kernel.governance import get_governance_engine

        gov = get_governance_engine()
        gov_result = await gov.evaluate(user_id, "query", {"question": question[:200], "run_id": run_id})
        if not gov_result.get("allowed"):
            logger.warning("run=%r [query] governance denied for user %s", run_id, user_id)
            return JSONResponse(
                status_code=403,
                content={
                    "error": "governance_denied",
                    "detail": gov_result.get("reason"),
                },
            )
    except ImportError:
        logger.error("Governance module not available — denying query")
        return JSONResponse(
            status_code=500,
            content={"error": "governance_error", "detail": "Governance unavailable"},
        )
    except Exception as exc:
        logger.error("run=%r [query] governance check failed: %s", run_id, exc)
        return JSONResponse(
            status_code=500,
            content={"error": "governance_error", "detail": "Governance check failed"},
        )

    # Audit: record query request
    try:
        from src.monkey_brain.kernel.audit import get_audit_log

        get_audit_log().record(
            runtime_id=user_id,
            event_type="query",
            action="query_requested",
            actor=user_id,
            details={"run_id": run_id, "question": question[:200]},
        )
    except Exception as exc:
        logger.warning("run=%r [query] audit record failed: %s", run_id, exc)

    lemon = get_lemon()

    if lemon:
        lemon.start_trace(
            name="api.query",
            trace_id=run_id,
            user=user_id,
            run_id=run_id,
            question_preview=question[:80],
        )
        lemon.counter("api.query.request")

    try:
        # Get or create hybrid router
        try:
            hybrid_router = _get_hybrid_router(request)
        except RuntimeError:
            logger.warning(
                "run=%r [query] HybridRouter not initialized, creating new instance",
                run_id,
            )
            hybrid_router = HybridRouter()

        # Process query through hybrid router
        logger.info("run=%r [query] Processing: %s (user: %s)", run_id, question[:60], user_id)

        response = await hybrid_router.process(question=question, actor_id=user_id, session_id=payload.session_id)

        if lemon:
            lemon.counter(f"api.query.{response.status}")
            lemon.counter(f"api.query.type.{response.routing_decision.query_type.value}")
            lemon.histogram("api.query.latency_ms", response.total_latency_ms)
            lemon.histogram(
                "api.query.classification_time_ms",
                response.routing_decision.classification_time_ms,
            )

        logger.info(
            "run=%r [query] Completed with %s "
            f"(type: {response.routing_decision.query_type.value}, "
            f"handler: {response.handler_type}, latency: {response.total_latency_ms:.0f}ms)",
            run_id,
            response.status,
        )

        # Audit: record execution
        try:
            from src.monkey_brain.kernel.audit import get_audit_log

            get_audit_log().record(
                runtime_id=user_id,
                event_type="query",
                action="query_completed",
                actor=user_id,
                details={
                    "run_id": run_id,
                    "status": response.status,
                    "query_type": response.routing_decision.query_type.value,
                    "handler": response.handler_type,
                    "latency_ms": response.total_latency_ms,
                },
            )
        except Exception as exc:
            logger.warning("run=%r [query] audit record (query_completed) failed: %s", run_id, exc)

        # Convert response to QueryResponse model
        return QueryResponse(
            status=response.status,
            response_text=response.response_text,
            handler_type=response.handler_type,
            query_type=response.routing_decision.query_type.value,
            confidence=response.routing_decision.confidence,
            run_id=response.run_id,
            session_id=response.session_id,
            total_latency_ms=response.total_latency_ms,
            classification_time_ms=response.routing_decision.classification_time_ms,
            metadata={
                "matched_patterns": response.routing_decision.matched_patterns,
                "run_id": response.run_id,
            },
        )

    except Exception as exc:
        logger.error("run=%r [query] Failed: %s", run_id, exc, exc_info=True)

        if lemon:
            lemon.counter("api.query.error")
            lemon.exception("api.query", exc)

        # Audit: record error
        try:
            from src.monkey_brain.kernel.audit import get_audit_log

            get_audit_log().record(
                runtime_id=user_id,
                event_type="query",
                action="query_failed",
                actor=user_id,
                details={"run_id": run_id, "error": str(exc)[:200]},
            )
        except Exception as audit_exc:
            logger.warning(
                "run=%r [query] audit record (query_failed) failed: %s",
                run_id,
                audit_exc,
            )

        return JSONResponse(
            status_code=500,
            content={
                "error": "query_processing_failed",
                "detail": str(exc)[:200],
                "run_id": run_id,
            },
        )


@router.get("/query/health")
async def query_health(request: Request) -> dict:
    """GET /query/health — Check hybrid router health status"""
    try:
        hybrid_router = _get_hybrid_router(request)
        stats = hybrid_router.get_router_stats()

        return {
            "status": "healthy",
            "total_queries": stats.get("total_queries", 0),
            "by_type": stats.get("by_type", {}),
            "by_status": stats.get("by_status", {}),
        }
    except Exception as exc:
        logger.error("[query/health] Failed: %s", exc)
        return JSONResponse(status_code=503, content={"status": "unhealthy", "error": str(exc)})
