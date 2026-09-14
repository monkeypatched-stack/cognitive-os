"""Data Routing Policy — Bellman-learned routing for enterprise data sources.

The policy learns which database is authoritative for each business entity
through experience. No global master database. No enterprise-wide joins.
Every table, collection, graph label, or measurement is an independent
source of truth.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("agentos.data_routing")

ROUTING_STATE_DIR = Path.home() / ".monkeybrain" / "data_routing"


@dataclass
class RoutingEntry:
    """A single routing decision for an entity capability."""

    capability: str
    entity: str
    database: str
    repository: str
    confidence: float = 0.0
    reward: float = 0.0
    latency_ms: float = 0.0
    completeness: float = 0.0
    freshness: float = 0.0
    availability: float = 1.0
    success_count: int = 0
    failure_count: int = 0
    last_success: float = 0.0
    last_failure: float = 0.0
    last_validated: float = 0.0
    exploration_count: int = 0

    @property
    def q_value(self) -> float:
        """Composite Q-value for this routing decision."""
        success_rate = self.success_count / max(1, self.success_count + self.failure_count)
        return (
            0.35 * self.confidence
            + 0.25 * self.reward
            + 0.15 * success_rate
            + 0.10 * self.completeness
            + 0.10 * self.freshness
            + 0.05 * self.availability
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "entity": self.entity,
            "database": self.database,
            "repository": self.repository,
            "confidence": round(self.confidence, 4),
            "reward": round(self.reward, 4),
            "q_value": round(self.q_value, 4),
            "latency_ms": round(self.latency_ms, 2),
            "completeness": round(self.completeness, 4),
            "freshness": round(self.freshness, 4),
            "availability": round(self.availability, 4),
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "last_success": self.last_success,
            "last_failure": self.last_failure,
            "last_validated": self.last_validated,
            "exploration_count": self.exploration_count,
        }


@dataclass
class RoutingCandidate:
    """A candidate data source for a routing decision."""

    database: str
    repository: str
    capability: str
    advertised: bool = True
    latency_ms: float = 0.0
    available: bool = True


@dataclass
class RoutingObservation:
    """An observation from a routing decision for learning."""

    capability: str
    entity: str
    database: str
    repository: str
    latency_ms: float
    confidence: float
    completeness: float
    freshness: float
    success: bool
    timestamp: float = field(default_factory=time.time)


class DataRoutingPolicy:
    """Bellman-learned routing policy for enterprise data sources.

    Learns the optimal source of truth for every business entity
    through experience. Supports exploration vs exploitation.
    """

    def __init__(
        self,
        state_dir: Path | None = None,
        exploration_rate: float = 0.1,
        learning_rate: float = 0.1,
    ):
        self._state_dir = state_dir or ROUTING_STATE_DIR
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._exploration_rate = exploration_rate
        self._learning_rate = learning_rate

        self._routing_table: dict[str, RoutingEntry] = {}
        self._candidates: dict[str, list[RoutingCandidate]] = {}
        self._observations: list[RoutingObservation] = []
        self._max_observations = 1000

        self._load_state()

    def _routing_key(self, capability: str, entity: str) -> str:
        return f"{capability}:{entity}"

    def select_source(
        self,
        capability: str,
        entity: str,
        candidates: list[RoutingCandidate] | None = None,
    ) -> RoutingCandidate | None:
        """Select the best data source for a capability+entity query.

        Uses epsilon-greedy: explore randomly sometimes, exploit best otherwise.
        """
        key = self._routing_key(capability, entity)

        if candidates is None:
            candidates = self._candidates.get(key, [])

        if not candidates:
            return None

        import random

        if random.random() < self._exploration_rate:
            candidate = random.choice(candidates)
            entry = self._routing_table.get(key)
            if entry:
                entry.exploration_count += 1
            return candidate

        best_entry = None
        best_candidate = None
        for c in candidates:
            entry_key = self._routing_key(capability, entity)
            existing = self._routing_table.get(entry_key)
            if existing and existing.database == c.database:
                if best_entry is None or existing.q_value > best_entry.q_value:
                    best_entry = existing
                    best_candidate = c

        if best_candidate:
            return best_candidate

        return max(candidates, key=lambda c: c.latency_ms == 0)

    def observe(self, observation: RoutingObservation) -> None:
        """Record a routing observation and update the policy."""
        self._observations.append(observation)
        if len(self._observations) > self._max_observations:
            self._observations = self._observations[-self._max_observations :]

        key = self._routing_key(observation.capability, observation.entity)

        if key not in self._routing_table:
            self._routing_table[key] = RoutingEntry(
                capability=observation.capability,
                entity=observation.entity,
                database=observation.database,
                repository=observation.repository,
            )

        entry = self._routing_table[key]
        entry.latency_ms = observation.latency_ms
        entry.freshness = observation.freshness
        entry.completeness = observation.completeness

        if observation.success:
            entry.success_count += 1
            entry.last_success = observation.timestamp
            reward = self._compute_reward(observation)
            entry.reward = entry.reward + self._learning_rate * (reward - entry.reward)
            entry.confidence = min(1.0, entry.confidence + self._learning_rate * 0.1)
        else:
            entry.failure_count += 1
            entry.last_failure = observation.timestamp
            entry.confidence = max(0.0, entry.confidence - self._learning_rate * 0.2)

        entry.last_validated = observation.timestamp

        self._save_state()

    def _compute_reward(self, obs: RoutingObservation) -> float:
        """Compute reward from observation."""
        latency_reward = max(0.0, 1.0 - obs.latency_ms / 1000.0)
        success_reward = 1.0 if obs.success else 0.0
        return 0.4 * success_reward + 0.3 * latency_reward + 0.2 * obs.completeness + 0.1 * obs.freshness

    def register_candidates(self, capability: str, entity: str, candidates: list[RoutingCandidate]) -> None:
        """Register candidate data sources for a capability+entity."""
        key = self._routing_key(capability, entity)
        self._candidates[key] = candidates

    def get_routing_table(self) -> list[RoutingEntry]:
        """Return the full routing table."""
        return sorted(self._routing_table.values(), key=lambda e: -e.q_value)

    def get_entry(self, capability: str, entity: str) -> RoutingEntry | None:
        """Get a specific routing entry."""
        return self._routing_table.get(self._routing_key(capability, entity))

    def invalidate(self, capability: str, entity: str, database: str) -> None:
        """Invalidate a routing entry when a source fails."""
        key = self._routing_key(capability, entity)
        entry = self._routing_table.get(key)
        if entry and entry.database == database:
            entry.confidence = max(0.0, entry.confidence - 0.3)
            entry.availability = 0.0
            entry.failure_count += 1
            self._save_state()

    def revalidate(self, capability: str, entity: str) -> list[RoutingCandidate]:
        """Return candidates for revalidation when primary source fails."""
        key = self._routing_key(capability, entity)
        return self._candidates.get(key, [])

    def _save_state(self) -> None:
        """Persist routing table to disk."""
        state = {
            "routing_table": {k: e.to_dict() for k, e in self._routing_table.items()},
            "exploration_rate": self._exploration_rate,
            "learning_rate": self._learning_rate,
        }
        path = self._state_dir / "routing_policy.json"
        try:
            path.write_text(json.dumps(state, indent=2, default=str))
        except Exception as e:
            logger.warning("[data_routing] Failed to save state: %s", e)

    def _load_state(self) -> None:
        """Load routing table from disk."""
        path = self._state_dir / "routing_policy.json"
        if not path.exists():
            return
        try:
            state = json.loads(path.read_text())
            self._exploration_rate = state.get("exploration_rate", self._exploration_rate)
            self._learning_rate = state.get("learning_rate", self._learning_rate)
            for k, d in state.get("routing_table", {}).items():
                self._routing_table[k] = RoutingEntry(**d)
        except Exception as e:
            logger.warning("[data_routing] Failed to load state: %s", e)

    def summary(self) -> dict[str, Any]:
        """Return routing policy summary."""
        entries = list(self._routing_table.values())
        databases = {}
        for e in entries:
            databases.setdefault(e.database, 0)
            databases[e.database] += 1
        return {
            "total_routes": len(entries),
            "databases": databases,
            "avg_confidence": sum(e.confidence for e in entries) / max(1, len(entries)),
            "avg_reward": sum(e.reward for e in entries) / max(1, len(entries)),
            "exploration_rate": self._exploration_rate,
            "learning_rate": self._learning_rate,
        }
