"""RepositoryAgent — abstracts persistence; decides cache vs. store query."""

from __future__ import annotations
import logging
from typing import Any
from broca.agents.ddd._base_ddd import BaseDDDAgent

logger = logging.getLogger("broca.agents.ddd.repository")


class RepositoryAgent(BaseDDDAgent):
    """Repository agent — owns persistence decisions.

    Perceive: reads entity type, query parameters, cache state.
    Reason:   decides cache hit, db query, or write path.
    Act:      executes the persistence operation via a Cerebellum capability.
    Learn:    tracks cache hit rate to inform eviction strategy.
    """

    agent_type = "repository"
    description = "Repository agent — manages persistence, cache, and data access for domain entities"
    ddd_layer = "repository"
    workload_spec = "source_control"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._cache: dict[str, Any] = {}
        self._hits = 0
        self._misses = 0

    def perceive(self, context: dict[str, Any]) -> dict[str, Any]:
        query_key = context.get("query_key") or str(context.get("query", ""))
        return {
            "layer": self.ddd_layer,
            "entity_type": context.get("entity_type", "generic"),
            "operation": context.get("operation", "find"),  # find | save | delete
            "query_key": query_key,
            "cache_hit": query_key in self._cache,
            "payload": context.get("payload"),
        }

    def reason(self, perception: dict[str, Any]) -> dict[str, Any]:
        op = perception.get("operation", "find")
        if op == "find" and perception.get("cache_hit"):
            return {
                "action": "cache_hit",
                "query_key": perception["query_key"],
                "entity_type": perception["entity_type"],
            }
        if op == "save":
            return {
                "action": "write",
                "query_key": perception["query_key"],
                "payload": perception.get("payload"),
                "entity_type": perception["entity_type"],
            }
        return {
            "action": "db_query",
            "query_key": perception["query_key"],
            "entity_type": perception["entity_type"],
        }

    def act(self, decision: dict[str, Any]) -> dict[str, Any]:
        action = decision.get("action")
        key = decision.get("query_key", "")

        if action == "cache_hit":
            self._hits += 1
            return {
                "action": "cache_hit",
                "entity": self._cache.get(key),
                "hit_rate": self._hits / max(1, self._hits + self._misses),
            }

        if action == "write":
            self._cache[key] = decision.get("payload")
            return {
                "action": "written",
                "query_key": key,
                "entity_type": decision.get("entity_type"),
            }

        self._misses += 1
        return {
            "action": "db_queried",
            "query_key": key,
            "entity_type": decision.get("entity_type"),
            "hit_rate": self._hits / max(1, self._hits + self._misses),
        }

    def learn(self, outcome: dict[str, Any]) -> None:
        hit_rate = outcome.get("hit_rate", 0)
        if hit_rate < 0.3:
            logger.debug(
                "[repository] low cache hit rate: %.0f%% — consider cache warming",
                hit_rate * 100,
            )
