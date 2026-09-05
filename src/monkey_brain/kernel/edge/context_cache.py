"""ContextConstructionEngine memoization for the edge hot path.

A wrapper, not a modification of ContextConstructionEngine itself
(kernel/pipeline/planning/context_engine.py is untouched) -- this caches
its OUTPUT (a PlanningContext) keyed by exactly the composite version
this task specifies, so a repeated tick with nothing materially different
skips retrieval/timeline/organizational/world-state assembly entirely.

    context_key = actor_id + goal_hash + world_state_version
                  + policy_version + knowledge_version

Never caches across a materially different world state: any one of
those four version inputs changing is a cache miss, full stop -- there
is no partial reuse or heuristic staleness tolerance here (contrast with
kernel/edge/freshness.py's STALE_BUT_USABLE, which is for individual
world-state PROJECTIONS, not a whole assembled PlanningContext).
"""
from __future__ import annotations

import hashlib
from typing import Any

from src.monkey_brain.kernel.edge.local_cache import BoundedTTLCache

DEFAULT_CONTEXT_CACHE_TTL_SECONDS = 15.0
"""Short by design: even with all four version inputs unchanged, a
PlanningContext embeds retrieved facts (timeline, organizational,
semantic hits) that legitimately drift over tens of seconds in a live
system; this bounds how long a "nothing changed" judgment is trusted
before a fresh build is forced regardless."""


def _goal_hash(goal: Any) -> str:
    text = f"{getattr(goal, 'name', '')} {getattr(goal, 'description', '')}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def build_context_key(
    *, actor_id: str, goal: Any, world_state_version: str, policy_version: str, knowledge_version: str,
) -> str:
    return "|".join([
        actor_id, _goal_hash(goal), str(world_state_version), str(policy_version), str(knowledge_version),
    ])


class CachedContextConstructionEngine:
    """Drop-in in front of a real ContextConstructionEngine -- delegates
    every actual build to it on a miss, never reimplements retrieval."""

    def __init__(self, engine: Any, *, max_size: int = 256, ttl_seconds: float = DEFAULT_CONTEXT_CACHE_TTL_SECONDS) -> None:
        self._engine = engine
        self._cache: BoundedTTLCache = BoundedTTLCache(max_size=max_size, default_ttl_seconds=ttl_seconds)
        self._ttl = ttl_seconds

    def build(
        self, actor_id: str, goal: Any, execution_id: str = "", *,
        world_state_version: str = "", policy_version: str = "", knowledge_version: str = "",
    ) -> tuple[Any, bool]:
        """Returns (PlanningContext, cache_hit). cache_hit is surfaced
        explicitly (Section 19's observability requirement) rather than
        hidden inside the context object itself."""
        key = build_context_key(
            actor_id=actor_id, goal=goal, world_state_version=world_state_version,
            policy_version=policy_version, knowledge_version=knowledge_version,
        )
        version_key = f"{world_state_version}|{policy_version}|{knowledge_version}"
        cached = self._cache.get(key, version_key=version_key)
        if cached is not None:
            return cached, True

        context = self._engine.build(actor_id, goal, execution_id=execution_id)
        self._cache.put(key, context, version_key=version_key, ttl_seconds=self._ttl)
        return context, False

    async def build_async(
        self, actor_id: str, goal: Any, execution_id: str = "", *,
        world_state_version: str = "", policy_version: str = "", knowledge_version: str = "",
    ) -> tuple[Any, bool]:
        key = build_context_key(
            actor_id=actor_id, goal=goal, world_state_version=world_state_version,
            policy_version=policy_version, knowledge_version=knowledge_version,
        )
        version_key = f"{world_state_version}|{policy_version}|{knowledge_version}"
        cached = self._cache.get(key, version_key=version_key)
        if cached is not None:
            return cached, True

        context = await self._engine.build_async(actor_id, goal, execution_id=execution_id)
        self._cache.put(key, context, version_key=version_key, ttl_seconds=self._ttl)
        return context, False

    def invalidate_actor(self, actor_id: str) -> None:
        """Coarse invalidation hook -- BoundedTTLCache has no per-actor
        index, so this is a full clear; acceptable because actor-level
        invalidation (e.g. a manual belief reset) is not itself a
        hot-path operation."""
        self._cache.clear()

    def stats(self) -> dict[str, Any]:
        return self._cache.stats()
