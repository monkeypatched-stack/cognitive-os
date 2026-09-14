"""API utils — response builders that depend on API models.

This module lives in api/ (not kernel/) because it imports API-layer types.
Kernel code re-exports from here for backward compatibility.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def graph_query_response(
    payload: Any,
    answer: str,
    semantic_hits: list | None = None,
    graph_paths: list | None = None,
    llm_answered: bool = False,
    policy_decision: dict | None = None,
    data_source: str | None = None,
) -> Any:
    from src.monkey_brain.kernel.models.graph_query import GraphRagQueryResponse
    from src.monkey_brain.kernel.plan.intents.helpers import (
        clean_user_answer_for_question,
        semantic_hit_citation,
    )

    semantic_hits = semantic_hits or []
    graph_paths = graph_paths or []
    citations = [semantic_hit_citation(hit) for hit in semantic_hits] + [path.citation for path in graph_paths]
    return GraphRagQueryResponse(
        question=payload.question,
        answer=clean_user_answer_for_question(answer, payload.question),
        semantic_hits=semantic_hits,
        graph_paths=graph_paths,
        citations=list(dict.fromkeys(citations)),
        llm_answered=llm_answered,
        query_id=(policy_decision or {}).get("decision_id"),
        policy_key=(policy_decision or {}).get("policy_key"),
        strategy=(policy_decision or {}).get("selected_strategy"),
        user_id=payload.user_id,
        role=payload.role,
        session_id=payload.session_id,
        data_source=data_source,
    )


async def log_unmatched_graph_question(
    mongo_client: Any,
    payload: Any,
    visible_question: str,
    classified_intent: dict,
    reason: str,
) -> None:
    from services.common.reasoning_traces import log_reasoning_trace
    from services.common.config import settings

    record = {
        "question": payload.question,
        "visible_question": visible_question,
        "reason": reason,
        "classified_intent": classified_intent,
        "collections": payload.collections or [],
        "top_k": payload.top_k,
        "created_at": datetime.now(timezone.utc),
    }
    try:
        await mongo_client[settings.DB_NAME][settings.GRAPH_RAG_UNMATCHED_QUESTIONS_COLLECTION].insert_one(record)
    except Exception as e:
        logger.debug("Exception caught: %s", e)
    log_reasoning_trace(
        "agentos",
        stage="intent_classification_unmatched",
        reasoning_summary="Question did not meet the GraphRAG intent confidence threshold and no deterministic route matched it.",
        input_summary={"question": payload.question},
        output_summary={"answer": "unsupported_intent"},
        metadata=record,
    )
