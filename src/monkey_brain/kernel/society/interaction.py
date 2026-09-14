"""Social Interaction Framework (Step 12.5) — actors communicate intentionally.

Support interactions:
    Request, Inform, Negotiate, Delegate, Coordinate, Vote,
    Approve, Reject, Broadcast.

Every interaction becomes part of the shared context stream.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


class InteractionType(Enum):
    REQUEST = "request"
    INFORM = "inform"
    NEGOTIATE = "negotiate"
    DELEGATE = "delegate"
    COORDINATE = "coordinate"
    VOTE = "vote"
    APPROVE = "approve"
    REJECT = "reject"
    BROADCAST = "broadcast"


class InteractionStatus(Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


@dataclass(frozen=True)
class InteractionMessage:
    """One message in a social interaction."""

    message_id: str = field(default_factory=lambda: uuid4().hex)
    interaction_id: str = ""
    sender_id: str = ""
    receiver_id: str = ""
    interaction_type: InteractionType = InteractionType.INFORM
    content: Any = None
    """The payload — can be any type."""
    in_reply_to: str = ""
    """message_id this is a reply to."""
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class Interaction:
    """A complete social interaction between actors."""

    interaction_id: str = field(default_factory=lambda: uuid4().hex)
    interaction_type: InteractionType = InteractionType.INFORM
    initiator_id: str = ""
    participant_ids: tuple[str, ...] = ()
    status: InteractionStatus = InteractionStatus.PENDING
    messages: tuple[InteractionMessage, ...] = ()
    topic: str = ""
    """What this interaction is about."""
    proposal: Any = None
    """The current proposal under discussion (for Negotiate/Coordinate)."""
    votes: dict[str, bool] = field(default_factory=dict)
    """actor_id -> vote (for Vote interactions)."""
    deadline: float = 0.0
    """0.0 means no deadline."""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class InteractionManager:
    """Manages social interactions between actors. Every interaction
    becomes part of the shared context stream."""

    def __init__(self) -> None:
        self._interactions: dict[str, Interaction] = {}
        self._actor_interactions: dict[str, list[str]] = {}

    def create_interaction(
        self,
        interaction_type: InteractionType,
        initiator_id: str,
        participant_ids: tuple[str, ...],
        topic: str = "",
        proposal: Any = None,
        deadline: float = 0.0,
    ) -> Interaction:
        interaction = Interaction(
            interaction_type=interaction_type,
            initiator_id=initiator_id,
            participant_ids=participant_ids,
            topic=topic,
            proposal=proposal,
            deadline=deadline,
        )
        self._interactions[interaction.interaction_id] = interaction
        all_actors = {initiator_id} | set(participant_ids)
        for actor_id in all_actors:
            self._actor_interactions.setdefault(actor_id, []).append(interaction.interaction_id)
        return interaction

    def send_message(self, interaction_id: str, message: InteractionMessage) -> Interaction | None:
        interaction = self._interactions.get(interaction_id)
        if interaction is None:
            return None
        updated = Interaction(
            interaction_id=interaction.interaction_id,
            interaction_type=interaction.interaction_type,
            initiator_id=interaction.initiator_id,
            participant_ids=interaction.participant_ids,
            status=interaction.status,
            messages=interaction.messages + (message,),
            topic=interaction.topic,
            proposal=interaction.proposal,
            votes=dict(interaction.votes),
            deadline=interaction.deadline,
            metadata=dict(interaction.metadata),
            created_at=interaction.created_at,
            updated_at=time.time(),
        )
        self._interactions[interaction_id] = updated
        return updated

    def respond(self, interaction_id: str, actor_id: str, accept: bool, message: str = "") -> Interaction | None:
        interaction = self._interactions.get(interaction_id)
        if interaction is None:
            return None
        status = InteractionStatus.ACCEPTED if accept else InteractionStatus.REJECTED
        updated = Interaction(
            interaction_id=interaction.interaction_id,
            interaction_type=interaction.interaction_type,
            initiator_id=interaction.initiator_id,
            participant_ids=interaction.participant_ids,
            status=status,
            messages=interaction.messages,
            topic=interaction.topic,
            proposal=interaction.proposal,
            votes={**interaction.votes, actor_id: accept},
            deadline=interaction.deadline,
            metadata=interaction.metadata,
            created_at=interaction.created_at,
            updated_at=time.time(),
        )
        self._interactions[interaction_id] = updated
        return updated

    def cast_vote(self, interaction_id: str, actor_id: str, vote: bool) -> Interaction | None:
        return self.respond(interaction_id, actor_id, vote)

    def complete(self, interaction_id: str) -> Interaction | None:
        interaction = self._interactions.get(interaction_id)
        if interaction is None:
            return None
        updated = Interaction(
            interaction_id=interaction.interaction_id,
            interaction_type=interaction.interaction_type,
            initiator_id=interaction.initiator_id,
            participant_ids=interaction.participant_ids,
            status=InteractionStatus.COMPLETED,
            messages=interaction.messages,
            topic=interaction.topic,
            proposal=interaction.proposal,
            votes=dict(interaction.votes),
            deadline=interaction.deadline,
            metadata=interaction.metadata,
            created_at=interaction.created_at,
            updated_at=time.time(),
        )
        self._interactions[interaction_id] = updated
        return updated

    def get_interaction(self, interaction_id: str) -> Interaction | None:
        return self._interactions.get(interaction_id)

    def interactions_for(self, actor_id: str) -> tuple[Interaction, ...]:
        ids = self._actor_interactions.get(actor_id, [])
        return tuple(self._interactions[iid] for iid in ids if iid in self._interactions)

    def active_interactions(self) -> tuple[Interaction, ...]:
        return tuple(
            i
            for i in self._interactions.values()
            if i.status in (InteractionStatus.PENDING, InteractionStatus.ACCEPTED)
        )

    def all_interactions(self) -> tuple[Interaction, ...]:
        return tuple(self._interactions.values())
