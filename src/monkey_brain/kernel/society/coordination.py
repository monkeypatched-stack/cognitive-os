"""Coordination & Negotiation (Step 12.6) — supports cooperative planning.

Actors negotiate:
    resource allocation, task ownership, schedules,
    priorities, conflicts.

Support:
    bilateral negotiation, auctions, consensus, delegation.

Planning remains local. Coordination becomes social.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


class NegotiationType(Enum):
    BILATERAL = "bilateral"
    AUCTION = "auction"
    CONSENSUS = "consensus"
    DELEGATION = "delegation"
    VOTE = "vote"


class NegotiationStatus(Enum):
    PROPOSED = "proposed"
    COUNTER_PROPOSED = "counter_proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SETTLED = "settled"
    FAILED = "failed"


class ResourceAllocationType(Enum):
    EQUAL = "equal"
    PRIORITY = "priority"
    AUCTION = "auction"
    NEED_BASED = "need_based"


@dataclass(frozen=True)
class NegotiationProposal:
    """One proposal in a negotiation."""

    proposal_id: str = field(default_factory=lambda: uuid4().hex)
    proposer_id: str = ""
    content: Any = None
    rationale: str = ""
    confidence: float = 1.0
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class ResourceAllocation:
    """A proposed resource allocation."""

    resource_id: str = ""
    allocations: dict[str, float] = field(default_factory=dict)
    """actor_id -> quantity allocated."""
    allocation_type: ResourceAllocationType = ResourceAllocationType.EQUAL
    total_quantity: float = 0.0
    rationale: str = ""


@dataclass(frozen=True)
class TaskAssignment:
    """A proposed task assignment."""

    task_id: str = ""
    task_description: str = ""
    assigned_to: str = ""
    assigned_by: str = ""
    priority: int = 0
    deadline: float = 0.0
    rationale: str = ""


@dataclass(frozen=True)
class ConflictResolution:
    """A resolution to a conflict between actors."""

    conflict_id: str = ""
    parties: tuple[str, ...] = ()
    resolution: str = ""
    strategy: str = ""
    """e.g. 'compromise', 'authority', 'majority_vote', 'random'."""
    outcome: Any = None


@dataclass(frozen=True)
class Negotiation:
    """A complete negotiation session between actors."""

    negotiation_id: str = field(default_factory=lambda: uuid4().hex)
    negotiation_type: NegotiationType = NegotiationType.BILATERAL
    participants: tuple[str, ...] = ()
    topic: str = ""
    status: NegotiationStatus = NegotiationStatus.PROPOSED
    proposals: tuple[NegotiationProposal, ...] = ()
    current_proposal: NegotiationProposal | None = None
    resource_allocation: ResourceAllocation | None = None
    task_assignment: TaskAssignment | None = None
    conflict_resolution: ConflictResolution | None = None
    round_count: int = 0
    max_rounds: int = 10
    deadline: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class CoordinationEngine:
    """Manages coordination and negotiation between actors."""

    def __init__(self, strategic_runtime: Any = None) -> None:
        self._negotiations: dict[str, Negotiation] = {}
        self._actor_negotiations: dict[str, list[str]] = {}
        # Optional by design: ordinary coordination remains allocation-free.
        self.strategic_runtime = strategic_runtime

    def evaluate_strategies(self, profile: Any, context: dict[str, Any] | None = None):
        """Evaluate an actor's strategies through the optional game runtime."""
        if self.strategic_runtime is None:
            from src.monkey_brain.kernel.society.game_theory import GameTheoryRuntime

            self.strategic_runtime = GameTheoryRuntime()
        return self.strategic_runtime.evaluate(profile, context)

    def negotiate_strategically(
        self,
        topic: str,
        profiles: tuple[Any, ...],
        context: dict[str, Any] | None = None,
        messages: tuple[Any, ...] = (),
    ):
        """Settle a utility-ranked agreement while retaining this boundary."""
        if self.strategic_runtime is None:
            from src.monkey_brain.kernel.society.game_theory import GameTheoryRuntime

            self.strategic_runtime = GameTheoryRuntime()
        return self.strategic_runtime.negotiate(
            topic,
            profiles,
            context=context,
            messages=messages,
        )

    def propose(
        self,
        negotiation_type: NegotiationType,
        proposer_id: str,
        participants: tuple[str, ...],
        topic: str,
        proposal_content: Any,
        rationale: str = "",
        max_rounds: int = 10,
    ) -> Negotiation:
        proposal = NegotiationProposal(
            proposer_id=proposer_id,
            content=proposal_content,
            rationale=rationale,
        )
        negotiation = Negotiation(
            negotiation_type=negotiation_type,
            participants=participants,
            topic=topic,
            proposals=(proposal,),
            current_proposal=proposal,
            max_rounds=max_rounds,
        )
        self._negotiations[negotiation.negotiation_id] = negotiation
        all_actors = {proposer_id} | set(participants)
        for actor_id in all_actors:
            self._actor_negotiations.setdefault(actor_id, []).append(negotiation.negotiation_id)
        return negotiation

    def counter_propose(
        self, negotiation_id: str, actor_id: str, content: Any, rationale: str = ""
    ) -> Negotiation | None:
        negotiation = self._negotiations.get(negotiation_id)
        if negotiation is None or negotiation.status == NegotiationStatus.SETTLED:
            return None
        proposal = NegotiationProposal(
            proposer_id=actor_id,
            content=content,
            rationale=rationale,
        )
        updated = Negotiation(
            negotiation_id=negotiation.negotiation_id,
            negotiation_type=negotiation.negotiation_type,
            participants=negotiation.participants,
            topic=negotiation.topic,
            status=NegotiationStatus.COUNTER_PROPOSED,
            proposals=negotiation.proposals + (proposal,),
            current_proposal=proposal,
            resource_allocation=negotiation.resource_allocation,
            task_assignment=negotiation.task_assignment,
            conflict_resolution=negotiation.conflict_resolution,
            round_count=negotiation.round_count + 1,
            max_rounds=negotiation.max_rounds,
            deadline=negotiation.deadline,
            metadata=negotiation.metadata,
            created_at=negotiation.created_at,
            updated_at=time.time(),
        )
        self._negotiations[negotiation_id] = updated
        return updated

    def accept(self, negotiation_id: str) -> Negotiation | None:
        negotiation = self._negotiations.get(negotiation_id)
        if negotiation is None:
            return None
        updated = Negotiation(
            negotiation_id=negotiation.negotiation_id,
            negotiation_type=negotiation.negotiation_type,
            participants=negotiation.participants,
            topic=negotiation.topic,
            status=NegotiationStatus.ACCEPTED,
            proposals=negotiation.proposals,
            current_proposal=negotiation.current_proposal,
            resource_allocation=negotiation.resource_allocation,
            task_assignment=negotiation.task_assignment,
            conflict_resolution=negotiation.conflict_resolution,
            round_count=negotiation.round_count,
            max_rounds=negotiation.max_rounds,
            deadline=negotiation.deadline,
            metadata=negotiation.metadata,
            created_at=negotiation.created_at,
            updated_at=time.time(),
        )
        self._negotiations[negotiation_id] = updated
        return updated

    def settle(self, negotiation_id: str) -> Negotiation | None:
        negotiation = self._negotiations.get(negotiation_id)
        if negotiation is None:
            return None
        updated = Negotiation(
            negotiation_id=negotiation.negotiation_id,
            negotiation_type=negotiation.negotiation_type,
            participants=negotiation.participants,
            topic=negotiation.topic,
            status=NegotiationStatus.SETTLED,
            proposals=negotiation.proposals,
            current_proposal=negotiation.current_proposal,
            resource_allocation=negotiation.resource_allocation,
            task_assignment=negotiation.task_assignment,
            conflict_resolution=negotiation.conflict_resolution,
            round_count=negotiation.round_count,
            max_rounds=negotiation.max_rounds,
            deadline=negotiation.deadline,
            metadata=negotiation.metadata,
            created_at=negotiation.created_at,
            updated_at=time.time(),
        )
        self._negotiations[negotiation_id] = updated
        return updated

    def resolve_conflict(
        self, parties: tuple[str, ...], resolution: str, strategy: str = "compromise"
    ) -> ConflictResolution:
        conflict_id = uuid4().hex
        return ConflictResolution(
            conflict_id=conflict_id,
            parties=parties,
            resolution=resolution,
            strategy=strategy,
        )

    def allocate_resources(
        self,
        resource_id: str,
        allocations: dict[str, float],
        allocation_type: ResourceAllocationType = ResourceAllocationType.EQUAL,
        total_quantity: float = 0.0,
    ) -> ResourceAllocation:
        return ResourceAllocation(
            resource_id=resource_id,
            allocations=allocations,
            allocation_type=allocation_type,
            total_quantity=total_quantity,
        )

    def assign_task(
        self,
        task_id: str,
        task_description: str,
        assigned_to: str,
        assigned_by: str,
        priority: int = 0,
    ) -> TaskAssignment:
        return TaskAssignment(
            task_id=task_id,
            task_description=task_description,
            assigned_to=assigned_to,
            assigned_by=assigned_by,
            priority=priority,
        )

    def get_negotiation(self, negotiation_id: str) -> Negotiation | None:
        return self._negotiations.get(negotiation_id)

    def negotiations_for(self, actor_id: str) -> tuple[Negotiation, ...]:
        ids = self._actor_negotiations.get(actor_id, [])
        return tuple(self._negotiations[nid] for nid in ids if nid in self._negotiations)

    def active_negotiations(self) -> tuple[Negotiation, ...]:
        return tuple(
            n
            for n in self._negotiations.values()
            if n.status in (NegotiationStatus.PROPOSED, NegotiationStatus.COUNTER_PROPOSED)
        )
