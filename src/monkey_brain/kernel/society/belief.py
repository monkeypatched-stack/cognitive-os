"""Distributed Belief Formation (Step 12.4) — each actor maintains its
own beliefs derived from their observations.

Each actor performs:
    Observation → BeliefFusion → BeliefState

Support:
    disagreement, uncertainty, competing hypotheses,
    confidence tracking.

No actor should directly modify another actor's beliefs.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from src.monkey_brain.kernel.society.observation import ActorObservation


@dataclass(frozen=True)
class BeliefHypothesis:
    """One hypothesis about the world state, with confidence."""

    hypothesis_id: str = field(default_factory=lambda: uuid4().hex)
    subject: str = ""
    predicate: str = ""
    object_value: Any = None
    confidence: float = 0.5
    evidence_count: int = 0
    sources: tuple[str, ...] = ()
    last_updated: float = field(default_factory=time.time)
    correlation_id: str = ""
    """Id of the logical operation (e.g. the delivering message's
    correlation_id) that produced this belief update, when known."""
    causation_id: str = ""
    """Id of the immediate cause (e.g. the CommunicationDecision.decision_id
    that authorized the delivering message), when known. Left empty rather
    than fabricated for beliefs formed outside the communication path."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "subject": self.subject,
            "predicate": self.predicate,
            "object_value": self.object_value,
            "confidence": self.confidence,
            "evidence_count": self.evidence_count,
            "sources": list(self.sources),
            "last_updated": self.last_updated,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "BeliefHypothesis":
        return cls(
            hypothesis_id=d.get("hypothesis_id", uuid4().hex),
            subject=d.get("subject", ""),
            predicate=d.get("predicate", ""),
            object_value=d.get("object_value"),
            confidence=d.get("confidence", 0.5),
            evidence_count=d.get("evidence_count", 0),
            sources=tuple(d.get("sources", [])),
            last_updated=d.get("last_updated", 0.0),
            correlation_id=d.get("correlation_id", ""),
            causation_id=d.get("causation_id", ""),
        )


@dataclass(frozen=True)
class BeliefEntry:
    """A single belief: subject → multiple competing hypotheses."""

    subject: str = ""
    hypotheses: tuple[BeliefHypothesis, ...] = ()
    last_observation_time: float = 0.0
    staleness: float = 0.0
    """0.0 = fresh, 1.0 = very stale."""

    @property
    def best_hypothesis(self) -> BeliefHypothesis | None:
        if not self.hypotheses:
            return None
        return max(self.hypotheses, key=lambda h: h.confidence)

    @property
    def confidence(self) -> float:
        best = self.best_hypothesis
        return best.confidence if best else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "hypotheses": [h.to_dict() for h in self.hypotheses],
            "last_observation_time": self.last_observation_time,
            "staleness": self.staleness,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "BeliefEntry":
        return cls(
            subject=d.get("subject", ""),
            hypotheses=tuple(BeliefHypothesis.from_dict(h) for h in d.get("hypotheses", [])),
            last_observation_time=d.get("last_observation_time", 0.0),
            staleness=d.get("staleness", 0.0),
        )


@dataclass(frozen=True)
class BeliefState:
    """An actor's complete belief state about the world."""

    actor_id: str = ""
    beliefs: tuple[BeliefEntry, ...] = ()
    uncertainty_level: float = 0.0
    """0.0 = completely certain, 1.0 = completely uncertain."""
    disagreement_count: int = 0
    """Number of beliefs with competing hypotheses."""
    version: int = 0
    updated_at: float = field(default_factory=time.time)

    def belief_for(self, subject: str) -> BeliefEntry | None:
        for b in self.beliefs:
            if b.subject == subject:
                return b
        return None

    def confident_beliefs(self, threshold: float = 0.7) -> tuple[BeliefEntry, ...]:
        return tuple(b for b in self.beliefs if b.confidence >= threshold)

    def uncertain_beliefs(self, threshold: float = 0.5) -> tuple[BeliefEntry, ...]:
        return tuple(b for b in self.beliefs if b.confidence < threshold)

    def to_dict(self) -> dict[str, Any]:
        return {
            "actor_id": self.actor_id,
            "beliefs": [b.to_dict() for b in self.beliefs],
            "uncertainty_level": self.uncertainty_level,
            "disagreement_count": self.disagreement_count,
            "version": self.version,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "BeliefState":
        return cls(
            actor_id=d.get("actor_id", ""),
            beliefs=tuple(BeliefEntry.from_dict(b) for b in d.get("beliefs", [])),
            uncertainty_level=d.get("uncertainty_level", 0.0),
            disagreement_count=d.get("disagreement_count", 0),
            version=d.get("version", 0),
            updated_at=d.get("updated_at", 0.0),
        )


class BeliefFusion:
    """Fuses observations into beliefs for a single actor.

    Handles:
    - New observations create new beliefs
    - Conflicting observations create competing hypotheses
    - Confidence decreases with uncertainty
    - Staleness tracking for old beliefs
    """

    def __init__(self, confidence_decay: float = 0.01, stale_threshold: float = 300.0) -> None:
        self._confidence_decay = confidence_decay
        self._stale_threshold = stale_threshold

    def fuse(
        self,
        actor_id: str,
        observation: ActorObservation,
        existing: BeliefState | None = None,
    ) -> BeliefState:
        existing_beliefs = {b.subject: b for b in (existing.beliefs if existing else ())}
        new_beliefs: dict[str, BeliefEntry] = {}

        for obs_entity in observation.entities:
            subject = obs_entity.entity.name or obs_entity.entity.entity_id
            hypothesis = BeliefHypothesis(
                subject=subject,
                predicate="exists",
                object_value=obs_entity.entity.attributes,
                confidence=obs_entity.entity.confidence,
                evidence_count=1,
                sources=(actor_id,),
            )
            if subject in existing_beliefs:
                merged = self._merge_hypothesis(existing_beliefs[subject], hypothesis)
                new_beliefs[subject] = merged
            else:
                new_beliefs[subject] = BeliefEntry(
                    subject=subject,
                    hypotheses=(hypothesis,),
                    last_observation_time=time.time(),
                )
            # Temporal Presence & Actor Timeline Model refactor: additive
            # BeliefTimeline layer alongside the existing per-subject
            # "current competing hypotheses" view above (unchanged) — every
            # observation this fuse() call folds in becomes a permanently
            # queryable BeliefRecord, even once a later fuse() decays or
            # supersedes this subject's current BeliefEntry.
            self._record_belief_history(actor_id, subject, hypothesis, existing_beliefs.get(subject))

        for existing_subject, existing_entry in existing_beliefs.items():
            if existing_subject not in new_beliefs:
                decayed = self._decay_belief(existing_entry)
                new_beliefs[existing_subject] = decayed

        beliefs = tuple(new_beliefs.values())
        disagreement = sum(1 for b in beliefs if len(b.hypotheses) > 1)
        uncertainty = self._compute_uncertainty(beliefs)

        return BeliefState(
            actor_id=actor_id,
            beliefs=beliefs,
            uncertainty_level=uncertainty,
            disagreement_count=disagreement,
            version=(existing.version if existing else 0) + 1,
        )

    def merge_observations(
        self,
        actor_id: str,
        observations: tuple[ActorObservation, ...],
        existing: BeliefState | None = None,
    ) -> BeliefState:
        current = existing
        for obs in observations:
            current = self.fuse(actor_id, obs, current)
        return current

    # ── Internals ────────────────────────────────────────────────────────

    def _record_belief_history(
        self,
        actor_id: str,
        subject: str,
        hypothesis: BeliefHypothesis,
        previous: BeliefEntry | None = None,
    ) -> None:
        """Cognitive State refactor: metadata now carries evidence/
        previous_value/reason too, not just subject/predicate/value/
        confidence — same BeliefRecord shape (metadata already existed on
        the base TimelineEntry), just no longer left empty. `previous` is
        this subject's BeliefEntry from BEFORE this fuse() call folded the
        new observation in (fuse() already has it in existing_beliefs;
        this just threads it through instead of discarding it)."""
        from src.monkey_brain.kernel.timeline.entry import TimelineKind
        from src.monkey_brain.kernel.timeline.store import TimelineStore
        from src.monkey_brain.kernel.compile import _obs

        previous_best = previous.best_hypothesis if previous else None
        TimelineStore().record(
            TimelineKind.BELIEF,
            actor_id=actor_id,
            subject=subject,
            predicate=hypothesis.predicate,
            value=hypothesis.object_value,
            confidence=hypothesis.confidence,
            source=",".join(hypothesis.sources),
            metadata={
                "evidence": list(hypothesis.sources),
                "evidence_count": hypothesis.evidence_count,
                "previous_value": previous_best.object_value if previous_best else None,
                "reason": ("merged with existing hypothesis" if previous else "new observation"),
            },
        )
        _obs.counter("cognitive.beliefs_updated" if previous else "cognitive.beliefs_created")

    def _merge_hypothesis(self, existing: BeliefEntry, new: BeliefHypothesis) -> BeliefEntry:
        existing_hyps = list(existing.hypotheses)
        for i, hyp in enumerate(existing_hyps):
            if hyp.predicate == new.predicate and hyp.object_value == new.object_value:
                merged_conf = min(1.0, hyp.confidence + new.confidence * 0.3)
                existing_hyps[i] = BeliefHypothesis(
                    subject=hyp.subject,
                    predicate=hyp.predicate,
                    object_value=hyp.object_value,
                    confidence=merged_conf,
                    evidence_count=hyp.evidence_count + 1,
                    sources=tuple(dict.fromkeys(hyp.sources + new.sources)),
                )
                return BeliefEntry(
                    subject=existing.subject,
                    hypotheses=tuple(existing_hyps),
                    last_observation_time=time.time(),
                )
        existing_hyps.append(new)
        return BeliefEntry(
            subject=existing.subject,
            hypotheses=tuple(existing_hyps),
            last_observation_time=time.time(),
        )

    def _decay_belief(self, entry: BeliefEntry) -> BeliefEntry:
        age = time.time() - entry.last_observation_time
        if age < self._stale_threshold:
            return entry
        decayed_hyps = tuple(
            BeliefHypothesis(
                subject=h.subject,
                predicate=h.predicate,
                object_value=h.object_value,
                confidence=max(0.0, h.confidence - self._confidence_decay),
                evidence_count=h.evidence_count,
                sources=h.sources,
                last_updated=h.last_updated,
            )
            for h in entry.hypotheses
        )
        return BeliefEntry(
            subject=entry.subject,
            hypotheses=decayed_hyps,
            last_observation_time=entry.last_observation_time,
            staleness=min(1.0, age / (self._stale_threshold * 10)),
        )

    def _compute_uncertainty(self, beliefs: tuple[BeliefEntry, ...]) -> float:
        if not beliefs:
            return 1.0
        total_conf = sum(b.confidence for b in beliefs)
        return 1.0 - (total_conf / len(beliefs))
