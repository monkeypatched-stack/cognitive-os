"""Real web search backend for the actor chat's RAG fallback
(api/routes/actors.py::actor_chat) — reached only when this actor's own
knowledge graph has no facts relevant to a question. Tavily is the one
real provider wired in; honest empty-list return (never fabricated
results) when TAVILY_API_KEY is unset or the call fails, matching the
same "never raise into the caller, degrade to an honest empty" idiom
kernel/timeline/store.py and the other provider clients in this package
already use.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("agentos.execute.provider.web_search")

TAVILY_SEARCH_URL = "https://api.tavily.com/search"


async def tavily_search(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """Real Tavily search — returns [{"title", "url", "content"}], newest
    API contract (POST /search, api_key in the JSON body, "results" list
    of {title, url, content, score}). Returns [] (not an exception) when
    no key is configured or the request fails, so a caller can always
    treat "no results" as "web search found nothing" without a separate
    try/except of its own.
    """
    api_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not api_key or not query.strip():
        return []
    try:
        import httpx

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                TAVILY_SEARCH_URL,
                json={
                    "api_key": api_key,
                    "query": query,
                    "max_results": max_results,
                    "search_depth": "basic",
                },
            )
        resp.raise_for_status()
        data = resp.json()
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content", ""),
            }
            for r in (data.get("results") or [])[:max_results]
        ]
    except Exception as exc:
        logger.warning("tavily_search failed for query=%r: %s", query[:80], exc)
        return []
