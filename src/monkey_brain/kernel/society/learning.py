"""Collective Learning (Step 12.9) — allows learning across actors without
sharing beliefs directly.

Support:
    shared experiences, capability improvements,
    world refinement, policy evolution, reputation.

Actors keep independent beliefs.
Learning may influence the shared world and future decisions.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

logger = logging.getLogger("agentos.society.learning")


class LearningType(Enum):
    SHARED_EXPERIENCE = "shared_experience"
    CAPABILITY_IMPROVEMENT = "capability_improvement"
    WORLD_REFINEMENT = "world_refinement"
    POLICY_EVOLUTION = "policy_evolution"
    REPUTATION_UPDATE = "reputation_update"


@dataclass(frozen=True)
class SharedExperience:
    """A learning experience shared by one actor that others can benefit from."""

    experience_id: str = field(default_factory=lambda: uuid4().hex)
    actor_id: str = ""
    learning_type: LearningType = LearningType.SHARED_EXPERIENCE
    description: str = ""
    outcome: str = ""
    """success, failure, partial."""
    confidence: float = 0.5
    lessons: tuple[str, ...] = ()
    world_impact: dict[str, Any] = field(default_factory=dict)
    """What this experience suggests about the shared world."""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "experience_id": self.experience_id,
            "actor_id": self.actor_id,
            "learning_type": self.learning_type.value,
            "description": self.description,
            "outcome": self.outcome,
            "confidence": self.confidence,
            "lessons": list(self.lessons),
            "world_impact": self.world_impact,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SharedExperience":
        return cls(
            experience_id=d.get("experience_id", uuid4().hex),
            actor_id=d.get("actor_id", ""),
            learning_type=LearningType(d.get("learning_type", LearningType.SHARED_EXPERIENCE.value)),
            description=d.get("description", ""),
            outcome=d.get("outcome", ""),
            confidence=d.get("confidence", 0.5),
            lessons=tuple(d.get("lessons") or ()),
            world_impact=d.get("world_impact") or {},
            timestamp=d.get("timestamp", 0.0),
        )


@dataclass(frozen=True)
class CapabilityImprovement:
    """An improvement in an actor's capability derived from experience."""

    improvement_id: str = field(default_factory=lambda: uuid4().hex)
    actor_id: str = ""
    capability_name: str = ""
    previous_level: str = ""
    new_level: str = ""
    evidence_count: int = 0
    confidence: float = 0.5


@dataclass(frozen=True)
class ReputationEntry:
    """A reputation record for an actor."""

    actor_id: str = ""
    score: float = 0.5
    """0.0 = worst reputation, 1.0 = best."""
    evidence_count: int = 0
    sources: tuple[str, ...] = ()
    last_updated: float = field(default_factory=time.time)


@dataclass(frozen=True)
class CollectiveLearningResult:
    """Result of processing a shared experience across the society."""

    result_id: str = field(default_factory=lambda: uuid4().hex)
    experience_id: str = ""
    actors_influenced: tuple[str, ...] = ()
    world_refinements: dict[str, Any] = field(default_factory=dict)
    reputation_updates: tuple[ReputationEntry, ...] = ()
    capability_improvements: tuple[CapabilityImprovement, ...] = ()
    lessons_learned: tuple[str, ...] = ()


class CollectiveLearningEngine:
    """Processes shared experiences to improve the society without
    directly modifying any actor's beliefs.

    Persistence (Learning-hardening follow-up): `api/routes/learning_gateway.py`'s
    own docstring always claimed this route "share[s] and persist[s] an
    experience," but nothing here ever wrote to durable storage -- restart
    silently lost every SharedExperience. Fixed by following the exact
    established pattern already live for society-scoped Redis persistence
    (persistence/context_event_store.py::ContextEventStore, wired into
    PlanetaryRuntime via set_redis()): an injected, optional Redis client
    (None by default -- graceful in-memory-only degradation, same
    convention used throughout this codebase), one append-only per-society
    list (RPUSH+LTRIM, not a new lazy-connect client), and a `load_recent()`
    hydration call for restart recovery. Best-effort: a persistence
    failure never blocks share_experience() itself.
    """

    _KEY_PREFIX = "monkeybrain:collective_learning"

    def __init__(self, max_experiences: int = 10000, society_id: str = "") -> None:
        self._experiences: list[SharedExperience] = []
        self._max_experiences = max_experiences
        self._reputations: dict[str, ReputationEntry] = {}
        self._improvements: list[CapabilityImprovement] = []
        self._society_id = society_id
        self._redis: Any = None

    def set_redis(self, redis_client: Any, society_id: str = "") -> None:
        """Inject a Redis client for persistence (mirrors ContextEventStore.
        set_redis()) -- called by PlanetaryRuntime once a real client is
        available, which may be after this engine was already constructed
        (SocietyRuntime.__init__ runs before PlanetaryRuntime's own
        _init_persistence). Optionally (re)binds the society_id this
        engine persists under, for callers that didn't have it yet at
        construction time."""
        self._redis = redis_client
        if society_id:
            self._society_id = society_id

    def _redis_key(self) -> str:
        return f"{self._KEY_PREFIX}:{self._society_id}"

    def load_recent(self, limit: int = 1000) -> int:
        """Hydrate self._experiences (and reputations derived from them)
        from Redis -- restart recovery. Returns how many were loaded.
        A no-op, not an error, when Redis isn't wired or the key doesn't
        exist yet (nothing persisted for this society before)."""
        if self._redis is None or not self._society_id:
            return 0
        try:
            raw_entries = self._redis.lrange(self._redis_key(), -limit, -1)
        except Exception as exc:
            logger.warning("CollectiveLearningEngine.load_recent failed (non-fatal): %s", exc)
            return 0
        loaded = 0
        for raw in raw_entries:
            try:
                experience = SharedExperience.from_dict(json.loads(raw))
            except Exception:
                continue
            self._experiences.append(experience)
            self._update_reputation(experience)
            loaded += 1
        return loaded

    def share_experience(self, experience: SharedExperience) -> CollectiveLearningResult:
        self._experiences.append(experience)
        if len(self._experiences) > self._max_experiences:
            self._experiences = self._experiences[-self._max_experiences :]
        self._update_reputation(experience)
        self._persist(experience)
        return self._process_experience(experience)

    def _persist(self, experience: SharedExperience) -> None:
        """Best-effort append to Redis -- never raises into share_experience()."""
        if self._redis is None or not self._society_id:
            return
        try:
            key = self._redis_key()
            self._redis.rpush(key, json.dumps(experience.to_dict()))
            self._redis.ltrim(key, -self._max_experiences, -1)
        except Exception as exc:
            logger.warning("CollectiveLearningEngine persist failed (non-fatal): %s", exc)

    def get_reputation(self, actor_id: str) -> ReputationEntry:
        return self._reputations.get(actor_id, ReputationEntry(actor_id=actor_id))

    def all_reputations(self) -> tuple[ReputationEntry, ...]:
        return tuple(self._reputations.values())

    def experiences(
        self, *, actor_id: str | None = None, learning_type: LearningType | None = None
    ) -> tuple[SharedExperience, ...]:
        result = self._experiences
        if actor_id is not None:
            result = [e for e in result if e.actor_id == actor_id]
        if learning_type is not None:
            result = [e for e in result if e.learning_type == learning_type]
        return tuple(result)

    def improvements(self, *, actor_id: str | None = None) -> tuple[CapabilityImprovement, ...]:
        if actor_id is None:
            return tuple(self._improvements)
        return tuple(i for i in self._improvements if i.actor_id == actor_id)

    def _process_experience(self, experience: SharedExperience) -> CollectiveLearningResult:
        """Step 12.8 bugfix: `actors_influenced` and `capability_improvements`
        were declared on CollectiveLearningResult and returned here, but this
        method never populated either — `influenced` stayed an empty list
        forever, and nothing anywhere ever appended to `self._improvements`,
        making the CapabilityImprovement dataclass/accessor entirely dead
        code. Confirmed via a direct script (real experience in, always
        empty tuples out) before fixing."""
        influenced: list[str] = [experience.actor_id]
        world_refinements: dict[str, Any] = {}
        capability_improvements: list[CapabilityImprovement] = []

        if experience.world_impact:
            world_refinements.update(experience.world_impact)

        if experience.outcome == "success" and experience.confidence > 0.7:
            for lesson in experience.lessons:
                world_refinements[f"lesson_{lesson}"] = True

        if experience.learning_type == LearningType.CAPABILITY_IMPROVEMENT:
            capability_name = experience.world_impact.get("capability_name")
            if capability_name:
                improvement = CapabilityImprovement(
                    actor_id=experience.actor_id,
                    capability_name=capability_name,
                    previous_level=experience.world_impact.get("previous_level", ""),
                    new_level=experience.world_impact.get("new_level", ""),
                    evidence_count=1,
                    confidence=experience.confidence,
                )
                self._improvements.append(improvement)
                capability_improvements.append(improvement)

        return CollectiveLearningResult(
            experience_id=experience.experience_id,
            actors_influenced=tuple(dict.fromkeys(influenced)),
            world_refinements=world_refinements,
            reputation_updates=(self.get_reputation(experience.actor_id),),
            capability_improvements=tuple(capability_improvements),
            lessons_learned=experience.lessons,
        )

    def _update_reputation(self, experience: SharedExperience) -> None:
        actor_id = experience.actor_id
        existing = self._reputations.get(actor_id, ReputationEntry(actor_id=actor_id))
        if experience.outcome == "success":
            new_score = min(1.0, existing.score + 0.05 * experience.confidence)
        elif experience.outcome == "failure":
            new_score = max(0.0, existing.score - 0.03 * experience.confidence)
        else:
            new_score = existing.score
        self._reputations[actor_id] = ReputationEntry(
            actor_id=actor_id,
            score=new_score,
            evidence_count=existing.evidence_count + 1,
            sources=tuple(dict.fromkeys(existing.sources + (experience.actor_id,))),
        )
