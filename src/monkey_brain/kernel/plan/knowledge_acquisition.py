"""Knowledge acquisition — the remediation path for a low-grounding plan.

When /plan reports low_grounding + remediation="acquire_knowledge_and_replan", a caller
(or an orchestration loop) runs this OUT OF BAND, then re-plans. It is deliberately NOT
called from /plan itself: /plan is select-only and must not incur an unbounded web-search
latency inside a planning call.

What it actually does — end to end, no faking:
  1. Read grounding BEFORE (the cognitive kernel's knowledge pack, fused).
  2. Gather evidence from every AVAILABLE source: existing semantic memory + an injected
     web-search backend (if one is configured).
  3. Materialize each retrieved snippet as a KnowledgeItem in the SAME knowledge pack that
     _get_grounding_confidence fuses — so a subsequent /plan measures higher grounding.
  4. Read grounding AFTER and report the delta.

Honesty over theatre: if no source returns anything (e.g. no web-search backend is wired,
which is the current default — see world_model_simulation._web_search), it acquires 0 and
says so, with the list of sources it actually queried. It never fabricates knowledge to
make grounding look better.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger("agentos.knowledge_acquisition")

# A web-search backend: async (question, limit) -> list of {title, text/snippet, url}.
WebSearchFn = Callable[[str, int], Awaitable[list[dict[str, Any]]]]


def _grounding(kernel: Any) -> float:
    """Current fused grounding from the kernel's knowledge pack (0.0 if unavailable)."""
    try:
        kp = getattr(kernel, "knowledge_pack", None)
        if kp is None:
            return 0.0
        return float(kp.fuse().effective_knowledge)
    except Exception:
        return 0.0


async def _default_web_search(question: str, limit: int) -> list[dict[str, Any]]:
    """The runtime's own web search (ollama → tavily → fallback chain).

    Returns [] when no backend is configured — which is the CURRENT state: the chain
    imports helpers that don't exist yet, so _web_search returns results_found=0. This is
    the honest default; inject a real WebSearchFn to actually reach the web.
    """
    try:
        from src.cortex.world_model_simulation import _web_search

        result = await _web_search(question)
        return [
            {
                "title": d.get("title", ""),
                "text": d.get("text", ""),
                "url": d.get("url", ""),
            }
            for d in (result.get("details") or [])
        ]
    except Exception as exc:
        logger.debug("[acquire] default web search unavailable: %s", exc)
        return []


async def acquire_knowledge(
    question: str,
    *,
    kernel: Any = None,
    semantic_memory: Any = None,
    web_search: WebSearchFn | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """Acquire knowledge for `question` and fold it into the grounding knowledge pack.

    Returns a structured, honest report:
        {
          "question", "acquired", "raised" (bool),
          "grounding_before", "grounding_after",
          "sources_queried": {source: count}, "sources_unavailable": [...],
          "items": [{"id", "source", "modality"}],
        }
    """
    from src.knowledge.item import KnowledgeItem, Modality

    if kernel is None:
        from src.monkey_brain.kernel.cognitive_kernel import get_cognitive_kernel

        kernel = get_cognitive_kernel()
    if semantic_memory is None:
        semantic_memory = getattr(kernel, "semantic_memory", None)
    web_search = web_search or _default_web_search

    grounding_before = _grounding(kernel)
    kp = getattr(kernel, "knowledge_pack", None)
    if kp is None:
        return {
            "question": question,
            "acquired": 0,
            "raised": False,
            "grounding_before": grounding_before,
            "grounding_after": grounding_before,
            "sources_queried": {},
            "sources_unavailable": ["knowledge_pack"],
            "items": [],
        }

    sources_queried: dict[str, int] = {}
    sources_unavailable: list[str] = []
    items: list[dict[str, str]] = []
    seq = 0

    def _add(content: str, source: str, modality: "Modality", *, provenance: float) -> None:
        nonlocal seq
        content = (content or "").strip()
        if not content:
            return
        seq += 1
        item_id = f"acq:{abs(hash((question, source, content))) & 0xFFFFFFFF:x}:{seq}"
        # KnowledgeItem carries `tags`, not `metadata` — record the provenance question there.
        kp.add(
            KnowledgeItem(
                id=item_id,
                content=content,
                modality=modality,
                source=source,
                provenance=provenance,
                freshness=1.0,
                completeness=0.6,
                uncertainty=0.4,
                tags=["acquired", f"for:{question[:60]}"],
            )
        )
        items.append({"id": item_id, "source": source, "modality": modality.value})

    # ── Source 1: existing semantic memory (already-indexed knowledge) ──────────
    if semantic_memory is not None and getattr(semantic_memory, "available", False):
        try:
            hits = (await semantic_memory.query(question)) or {}
            results = hits.get("results", []) if isinstance(hits, dict) else []
            n = 0
            for hit in results[:limit]:
                text = hit.get("text") or hit.get("content") or "" if isinstance(hit, dict) else str(hit)
                _add(text, "semantic_memory", Modality.DATABASE, provenance=0.8)
                n += 1
            sources_queried["semantic_memory"] = n
        except Exception as exc:
            logger.debug("[acquire] semantic_memory query failed: %s", exc)
            sources_unavailable.append("semantic_memory")
    else:
        sources_unavailable.append("semantic_memory")

    # ── Source 2: web search (the injected/default backend) ─────────────────────
    try:
        web_results = await web_search(question, limit)
        n = 0
        for r in (web_results or [])[:limit]:
            text = r.get("text") or r.get("snippet") or "" if isinstance(r, dict) else str(r)
            _add(
                text,
                r.get("url", "web") if isinstance(r, dict) else "web",
                Modality.DOCUMENT,
                provenance=0.6,
            )
            n += 1
        sources_queried["web_search"] = n
        if n == 0:
            # backend present but empty, OR (the default) no backend wired at all.
            sources_unavailable.append("web_search")
    except Exception as exc:
        logger.debug("[acquire] web search failed: %s", exc)
        sources_unavailable.append("web_search")

    # ── Mirror into semantic memory so future /plan queries retrieve it too ─────
    if semantic_memory is not None and getattr(semantic_memory, "available", False) and items:
        for it in items:
            node = kp.get(it["id"]) if hasattr(kp, "get") else None
            content = getattr(node, "content", "") if node else ""
            if content:
                try:
                    await semantic_memory.store(it["id"], content, {"type": "acquired", "question": question})
                except Exception as exc:
                    logger.debug("[acquire] semantic_memory.store failed: %s", exc)

    grounding_after = _grounding(kernel)
    acquired = len(items)
    raised = grounding_after > grounding_before + 1e-9

    (logger.info if acquired else logger.warning)(
        "[acquire] question=%r acquired=%d grounding %.3f -> %.3f (sources=%s, unavailable=%s)",
        question[:80],
        acquired,
        grounding_before,
        grounding_after,
        sources_queried,
        sources_unavailable,
    )

    return {
        "question": question,
        "acquired": acquired,
        "raised": raised,
        "grounding_before": round(grounding_before, 4),
        "grounding_after": round(grounding_after, 4),
        "sources_queried": sources_queried,
        "sources_unavailable": sorted(set(sources_unavailable)),
        "items": items,
    }
