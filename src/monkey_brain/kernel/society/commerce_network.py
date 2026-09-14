"""CCB-400 — shared learning across retail-store societies."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class CommerceExperience:
    experience_id: str = field(default_factory=lambda: uuid4().hex)
    source_store_id: str = ""
    subject: str = ""
    lesson: str = ""
    outcome: str = "success"
    confidence: float = 0.5
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class StoreReputation:
    store_id: str
    score: float = 0.5
    evidence_count: int = 0
    late_deliveries: int = 0


@dataclass(frozen=True)
class CapabilityPublication:
    capability_id: str = field(default_factory=lambda: uuid4().hex)
    publisher_store_id: str = ""
    name: str = ""
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class CommerceNetwork:
    """Network knowledge shared by participating store societies."""

    def __init__(self) -> None:
        self._experiences: list[CommerceExperience] = []
        self._reputations: dict[str, StoreReputation] = {}
        self._preferences: dict[tuple[str, str], dict[str, int]] = {}
        self._capabilities: dict[str, CapabilityPublication] = {}
        self._federation_members: dict[str, frozenset[str]] = {}

    def attach_federation(self, federation_id: str, society_ids: tuple[str, ...]) -> None:
        """Register the societies allowed to exchange this network intelligence."""
        self._federation_members[federation_id] = frozenset(society_ids)

    def _visible_store_ids(self, requesting_society_id: str = "", federation_id: str = "") -> frozenset[str] | None:
        if not federation_id:
            return None if not requesting_society_id else frozenset({requesting_society_id})
        members = self._federation_members.get(federation_id, frozenset())
        return members if requesting_society_id in members else frozenset()

    def publish_experience(self, experience: CommerceExperience) -> CommerceExperience:
        self._experiences.append(experience)
        return experience

    def experiences(self, subject: str | None = None) -> tuple[CommerceExperience, ...]:
        if subject is None:
            return tuple(self._experiences)
        needles = {token for token in subject.lower().split() if len(token) > 2}
        return tuple(
            e for e in self._experiences if any(token in f"{e.subject} {e.lesson}".lower() for token in needles)
        )

    def record_delivery(self, store_id: str, *, late: bool) -> StoreReputation:
        current = self._reputations.get(store_id, StoreReputation(store_id=store_id))
        score = current.score - 0.1 if late else current.score + 0.03
        updated = StoreReputation(
            store_id=store_id,
            score=max(0.0, min(1.0, score)),
            evidence_count=current.evidence_count + 1,
            late_deliveries=current.late_deliveries + (1 if late else 0),
        )
        self._reputations[store_id] = updated
        self.publish_experience(
            CommerceExperience(
                source_store_id=store_id,
                subject="delivery reliability",
                lesson="delivery was late" if late else "delivery arrived on time",
                outcome="failure" if late else "success",
                confidence=1.0,
            )
        )
        return updated

    def reputation(self, store_id: str) -> StoreReputation:
        return self._reputations.get(store_id, StoreReputation(store_id=store_id))

    def learn_preference(self, scope_id: str, subject: str, value: str) -> str:
        counts = self._preferences.setdefault((scope_id, subject), {})
        counts[value] = counts.get(value, 0) + 1
        return max(counts, key=counts.get)

    def preference(self, scope_id: str, subject: str) -> str | None:
        counts = self._preferences.get((scope_id, subject), {})
        return max(counts, key=counts.get) if counts else None

    def publish_capability(self, publication: CapabilityPublication) -> CapabilityPublication:
        self._capabilities[publication.name] = publication
        return publication

    def adopt_capability(self, store_id: str, name: str) -> dict[str, Any] | None:
        publication = self._capabilities.get(name)
        if publication is None:
            return None
        return {
            "store_id": store_id,
            "capability": name,
            "publisher_store_id": publication.publisher_store_id,
            "description": publication.description,
            "metadata": dict(publication.metadata),
        }

    def planning_facts(
        self,
        subject: str = "",
        requesting_society_id: str = "",
        federation_id: str = "",
    ) -> tuple[dict[str, Any], ...]:
        visible = self._visible_store_ids(requesting_society_id, federation_id)
        facts = [
            {
                "type": "commerce_experience",
                "store_id": e.source_store_id,
                "subject": e.subject,
                "lesson": e.lesson,
                "confidence": e.confidence,
            }
            for e in self.experiences(subject or None)
            if visible is None or e.source_store_id in visible
        ]
        facts.extend(
            {"type": "store_reputation", **reputation.__dict__}
            for reputation in self._reputations.values()
            if visible is None or reputation.store_id in visible
        )
        facts.extend(
            {
                "type": "shared_capability",
                "name": capability.name,
                "publisher_store_id": capability.publisher_store_id,
                "description": capability.description,
            }
            for capability in self._capabilities.values()
            if visible is None or capability.publisher_store_id in visible
        )
        return tuple(facts)
