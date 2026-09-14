"""Q&A router — immediate knowledge-pack answers with background planning."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from src.monkey_brain.api.dependencies import require_permission
from src.monkey_brain.api.idempotency import idempotent

logger = logging.getLogger("agentos.qa")

router = APIRouter()


class QARequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=10_000)


@router.post("/qa", tags=["Q&A"])
@idempotent("qa.quick_answer")
async def quick_answer(
    payload: QARequest,
    request: Request,
    user_id: str = Depends(require_permission("perm-execute-query")),
):
    """Immediate answer from KnowledgePack; fires background capability planning.

    Returns the best available knowledge-pack answer without waiting for full
    executor workload. A background task runs the executor and updates epistemic
    state asynchronously.
    """
    logger.info("[qa] user=%r question=%r", user_id, payload.question[:100])

    runtime = getattr(request.app.state, "runtime", None)

    knowledge_answer: str = ""
    knowledge_items: list = []
    try:
        from src.knowledge.pack import KnowledgePack

        kp: KnowledgePack | None = getattr(runtime, "_knowledge_pack", None)
        if kp is None:
            kp = KnowledgePack()
        items = kp.search(payload.question) if hasattr(kp, "search") else list(kp._items.values())[:5]
        knowledge_items = [getattr(i, "content", str(i)) for i in items[:5]]
        knowledge_answer = knowledge_items[0] if knowledge_items else ""
    except Exception as e:
        logger.debug("Knowledge pack query failed: %s", e)

    async def _background_plan():
        try:
            from src.monkey_brain.kernel.execute.runtime.executor import UnifiedExecutor

            executor = UnifiedExecutor()
            mongo_client = None
            try:
                pm = getattr(request.app.state, "persistence_manager", None)
                if pm:
                    mongo_client = await pm.get_client("mongodb")
            except Exception as e:
                logger.debug("MongoDB client acquisition failed: %s", e)
            await executor.execute(payload.question, mongo_client, question_source="qa_background")
        except Exception as _e:
            logger.debug("[qa] background planning failed: %s", _e)

    asyncio.create_task(_background_plan())

    return {
        "question": payload.question,
        "answer": knowledge_answer or "No immediate answer found — background planning triggered.",
        "knowledge_items": knowledge_items,
        "background_planning": True,
        "user_id": user_id,
    }
