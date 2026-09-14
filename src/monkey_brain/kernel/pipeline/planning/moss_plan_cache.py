"""MossPlanCache — semantic (goal-similarity) plan cache, backed by Moss
(docs.usemoss.dev), for llm_planner.py.

Sibling to kernel/edge/moss_retrieval.py::MossSemanticMemory (same
MOSS_PROJECT_ID/MOSS_PROJECT_KEY gating, same lazy-session-per-index
pattern) but a genuinely different contract: MossSemanticMemory answers
"find knowledge documents relevant to this query" (text in, ranked text
out); this answers "has a goal this similar to THIS one already produced
a real, successful plan?" (goal+facts in, a real belief_state.Plan out).
Two separate indexes, two separate concerns — this is not a drop-in
replacement for that module, and does not touch it.

Why this exists (see docs/pipeline llm_planner.py's own call site): unlike
llm_plan_cache.py's exact sha256(model+system+prompt) match — which only
ever helps the SAME prompt text asked again — a goal re-asked with
slightly different phrasing, or the same goal re-planned against a
slightly different but functionally-equivalent set of facts, produces a
DIFFERENT prompt string and always misses that cache, hitting the full,
slow LLM planning path every time. Moss's embedding-based similarity
search catches that case: "buy 1L milk" and "purchase one litre of milk"
embed close together even though their prompt text differs byte-for-byte.

Trade-off, stated plainly: this is deliberately approximate. A
similarity-matched plan is a real plan that worked for a REAL PAST
situation judged close enough to this one — not a guarantee it is
correct for this exact one. score_threshold exists specifically to keep
that approximation conservative (default 0.85, tunable); a caller that
cannot tolerate ever reusing a near-match instead of asking the LLM fresh
should not enable this (MOSS_PROJECT_ID/KEY unset already disables it
entirely, matching moss_retrieval.py's own opt-in convention).

Never raises: exactly like MossSemanticMemory.query(), any Moss failure
(auth, network, no index yet) degrades to "no cached plan available" /
"store skipped" rather than propagating — a cache is allowed to just not
have an answer, never allowed to break planning.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any

from src.monkey_brain.kernel.pipeline.belief_state import Plan
from src.monkey_brain.kernel.pipeline.planning.current_plan_store import (
    plan_from_dict,
    plan_to_dict,
)

logger = logging.getLogger("agentos.pipeline.planning.moss_plan_cache")

DEFAULT_INDEX_NAME = "cognitiveos-plan-cache"
DEFAULT_SCORE_THRESHOLD = 0.85


def moss_plan_cache_enabled() -> bool:
    return bool(os.environ.get("MOSS_PROJECT_ID", "").strip() and os.environ.get("MOSS_PROJECT_KEY", "").strip())


class MossPlanCache:
    """Dependency-injected client (a real MossClient in production, a fake
    satisfying the same session()/query()/add_docs() shape in tests) —
    same convention as MossSemanticMemory, for the same reason (never
    needs real credentials to unit-test)."""

    def __init__(
        self,
        client: Any,
        *,
        index_name: str = DEFAULT_INDEX_NAME,
        score_threshold: float = DEFAULT_SCORE_THRESHOLD,
    ) -> None:
        self._client = client
        self._index_name = index_name
        self._score_threshold = score_threshold
        self._session: Any = None

    async def _get_session(self) -> Any:
        if self._session is None:
            self._session = await self._client.session(self._index_name)
        return self._session

    @staticmethod
    def _doc_text(goal_name: str, facts_text: str) -> str:
        return f"{goal_name}\n{facts_text}" if facts_text else goal_name

    @staticmethod
    def _doc_id(goal_name: str, facts_text: str) -> str:
        raw = f"{goal_name}\x00{facts_text}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    async def get_similar_plan(self, goal_name: str, facts_text: str) -> Plan | None:
        """Returns a real, previously-successful Plan judged similar
        enough to (goal_name, facts_text) to reuse, or None on a miss,
        below-threshold match, or any Moss error. Never raises."""
        try:
            from moss import QueryOptions

            session = await self._get_session()
            result = await session.query(self._doc_text(goal_name, facts_text), QueryOptions(top_k=1))
        except Exception:
            logger.warning(
                "MossPlanCache.get_similar_plan: Moss call failed, treating as a miss",
                exc_info=True,
            )
            return None

        docs = getattr(result, "docs", None) or []
        if not docs:
            return None
        top = docs[0]
        score = float(getattr(top, "score", 0.0) or 0.0)
        if score < self._score_threshold:
            logger.debug(
                "[moss_plan_cache] best match for goal=%r scored %.3f, below threshold %.3f — miss",
                goal_name,
                score,
                self._score_threshold,
            )
            return None

        metadata = dict(getattr(top, "metadata", None) or {})
        raw_plan = metadata.get("plan")
        if not raw_plan:
            return None
        try:
            plan_dict = json.loads(raw_plan) if isinstance(raw_plan, str) else raw_plan
            plan = plan_from_dict(plan_dict)
        except Exception:
            logger.warning(
                "[moss_plan_cache] cached plan for goal=%r failed to deserialize — treating as a miss",
                goal_name,
                exc_info=True,
            )
            return None

        logger.info(
            "[moss_plan_cache] reusing plan for goal=%r (matched score=%.3f, cached goal=%r)",
            goal_name,
            score,
            plan.goal,
        )
        return plan

    async def store_plan(self, goal_name: str, facts_text: str, plan: Plan) -> None:
        """Indexes a real, successfully-parsed plan for future similarity
        lookups. Never raises; a failed store just means this plan isn't
        cached, not that planning failed."""
        if not plan.steps:
            return  # nothing worth reusing later — see llm_planner.py's call site
        try:
            from moss import DocumentInfo

            session = await self._get_session()
            doc = DocumentInfo(
                id=self._doc_id(goal_name, facts_text),
                text=self._doc_text(goal_name, facts_text),
                metadata={"plan": json.dumps(plan_to_dict(plan), default=str)},
            )
            await session.add_docs([doc])
        except Exception:
            logger.warning(
                "MossPlanCache.store_plan: Moss call failed, plan not cached",
                exc_info=True,
            )


_default_cache: MossPlanCache | None = None


def get_moss_plan_cache() -> MossPlanCache | None:
    """Lazy singleton, matching model_backend.py::get_backend()'s own
    convention. Returns None (not a cache that always misses) when Moss
    isn't configured, so callers can skip the lookup/store entirely
    rather than pay an always-failing round trip."""
    global _default_cache
    if not moss_plan_cache_enabled():
        return None
    if _default_cache is None:
        from moss import MossClient

        client = MossClient(os.environ["MOSS_PROJECT_ID"], os.environ["MOSS_PROJECT_KEY"])
        _default_cache = MossPlanCache(client)
    return _default_cache
