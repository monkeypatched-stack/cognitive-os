"""Membership — a first-class runtime resource (Membership as a First-Class
Runtime Resource refactor), replacing the implicit `Actor MEMBER_OF Society`
relationship this session originally modeled as a bare timeline record.

A Membership mediates governance: roles, permissions, policies, trust, and
delegation all flow through it rather than being attached directly to an
Actor or Society. Its CURRENT state is a derived, read-only view computed
from the latest kernel/timeline MembershipRecord sharing a membership_id —
every lifecycle event (role assigned/removed, trust updated, delegation
granted/revoked, suspended/resumed/terminated) closes the previous record
and appends a new one, so the full history stays queryable forever (same
"current = derived from timeline" pattern as Presence/GoalRecord).

kernel/society/integration.py imports SocietyMembershipRegistry by name —
kept as this module's class name (extended in place, not replaced) so
PlanetaryRuntime.join_society()/leave_society() (built earlier this
session) need no changes.

Prompt 3 — Governance and Membership Model — splits membership into two
kinds, composed by MembershipGovernor below:

  * PERMANENT — enduring affiliations (employment, citizenship, clubs,
    organizations, families). Explicitly stored: this is exactly what
    SocietyMembershipRegistry (unchanged by this refactor) already is —
    a caller calls add()/remove() deliberately, nothing implicit ever
    touches it.
  * TEMPORARY — granted the instant an Actor is physically present in a
    Space associated with a Society the Actor doesn't already hold a
    permanent membership in, and revoked the instant the Actor leaves
    that Space. Never stored on the timeline (it is fully derived, live,
    from PresenceTimeline movement) and never modifies permanent state.

effective_societies (MembershipGovernor.effective_societies) is the union
of both, for callers that just need "every Society this Actor is currently
affiliated with, permanently or by presence" — the distinction itself must
stay visible to callers that care which kind a given membership is.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from src.monkey_brain.kernel.timeline.entry import MembershipRecord, TimelineKind
from src.monkey_brain.kernel.timeline.store import TimelineStore
from src.monkey_brain.kernel.geography.registry import GeographicRegistry
from src.monkey_brain.kernel.timeline.presence import (
    MovementEvent,
    MovementEventType,
    PresenceTimeline,
)
from src.monkey_brain.kernel.society.context_stream import (
    ContextEvent,
    ContextEventType,
    SocietyContextStream,
)

logger = logging.getLogger("agentos.membership")

__all__ = [
    "MembershipRecord",
    "Membership",
    "SocietyMembershipRegistry",
    "MembershipGovernor",
]


@dataclass(frozen=True)
class Membership:
    """Derived, read-only current-state view of one membership_id's latest
    MembershipRecord — the object every governance lookup (roles,
    permissions, policies, trust, capabilities, constraints) is keyed by."""

    membership_id: str
    actor_id: str
    society_id: str
    team_id: str = ""
    roles: tuple[str, ...] = ()
    status: str = "active"
    start_time: float = 0.0
    end_time: float | None = None
    permissions: tuple[str, ...] = ()
    trust_score: float = 0.5
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_record(cls, record: MembershipRecord) -> "Membership":
        return cls(
            membership_id=record.membership_id,
            actor_id=record.actor_id,
            society_id=record.society_id,
            team_id=record.team_id,
            roles=record.roles,
            status=record.status,
            start_time=record.start_time,
            end_time=record.end_time,
            permissions=record.permissions,
            trust_score=record.trust_score,
            metadata=dict(record.metadata),
        )

    def is_active(self) -> bool:
        """Whether this membership currently grants its governance surface.

        ``end_time`` is the temporal validity boundary of the current view.
        ``expires_at`` is also accepted in metadata for persisted/API records
        that need a scheduled expiry without closing the append-only timeline
        row ahead of time.  All policy, permission, and planner lookups flow
        through this predicate so expiry cannot leave stale benefits behind.
        """
        if self.status != "active":
            return False
        now = time.time()
        if self.end_time is not None and self.end_time <= now:
            return False
        expires_at = self.metadata.get("expires_at")
        if expires_at is not None:
            try:
                if float(expires_at) <= now:
                    return False
            except (TypeError, ValueError):
                logger.debug("is_active: suppressed exception", exc_info=True)
        return True


class SocietyMembershipRegistry:
    """Many-to-many actor_id <-> society_id registry, backed by the
    append-only MembershipTimeline (kernel/timeline). "Leaving" a society
    closes its MembershipRecord (end_time set) rather than deleting it, so
    historical memberships stay queryable via history_for_actor()."""

    def __init__(self, store: TimelineStore | None = None, memory_manager: Any = None) -> None:
        self._store = store or TimelineStore()
        self._memory_manager = memory_manager
        self._add_lock = threading.Lock()
        """Qualification Gap Closure (BUG-002): add() below is a real
        read-then-write (check _open_record_for, then write if nothing was
        found) with no compare-and-swap -- kernel/domains/grocery.py::
        try_reserve is this codebase's own established pattern for exactly
        this shape of problem (two concurrent callers both observing "no
        existing record" before either writes), but TimelineStore doesn't
        expose a version/CAS primitive the way KnowledgeGraph does. A
        single real lock, mirroring PlanetaryRuntime._tick_lock's own
        precedent (a real, already-used concurrency primitive in this
        codebase, not an invented one) serializes the check-then-write
        instead. add() is not a per-tick hot path (called once per real
        membership grant, not once per tick), so one registry-wide lock is
        the correct, minimal fix -- not a per-(actor_id, society_id) lock
        registry that would need its own cleanup lifecycle for no real
        throughput benefit here. Confirmed live this session: the
        background _auto_tick_loop interleaves with real /prompt requests
        on the same event loop, so this race is real, not hypothetical."""
        """Optional kernel/learn/memory/manager.py::MemoryManager (Context-
        Aware Personalized Planning refactor) — when set, every membership
        event also becomes a searchable CognitiveMemory experience (spec's
        "embed membership changes... become searchable experiences"),
        reusing record_experience() rather than a new memory mechanism."""

    # ── Internals ─────────────────────────────────────────────────────────

    def _open_record_for(self, actor_id: str, society_id: str) -> MembershipRecord | None:
        """The current row for (actor_id, society_id) — "open" here means
        both is_open() (latest row) AND not terminated: a terminated
        membership's slot is free for a genuinely NEW membership (add()
        after remove() must create a fresh membership_id, not silently
        return the terminated one) — active/suspended still occupy the
        slot (suspending doesn't free it up for a duplicate add())."""
        for record in self._store.query(actor_id, TimelineKind.MEMBERSHIP):
            if record.society_id == society_id and record.is_open() and record.status != "terminated":
                return record
        return None

    def _find_open_record_by_id(self, membership_id: str) -> MembershipRecord | None:
        for actor_id in self._store.known_actors(TimelineKind.MEMBERSHIP):
            for record in self._store.query(actor_id, TimelineKind.MEMBERSHIP):
                if record.membership_id == membership_id and record.is_open():
                    return record
        return None

    def _supersede(
        self,
        current: MembershipRecord,
        event: str,
        extra_metadata: dict[str, Any] | None = None,
        **field_overrides: Any,
    ) -> Membership:
        """Close `current` and append a new record for the same
        membership_id with the given field overrides applied — the one
        mechanism every mutator below (role/status/trust/delegation
        changes) shares, so every membership event is uniformly a new,
        immutable timeline row."""
        now = time.time()
        self._store.close(current, TimelineKind.MEMBERSHIP, end_time=now)
        merged_metadata = {**current.metadata, **(extra_metadata or {}), "event": event}
        new_record = dataclasses.replace(
            current,
            entry_id=_new_entry_id(),
            start_time=now,
            end_time=None,
            metadata=merged_metadata,
            **field_overrides,
        )
        appended = self._store.append(new_record, TimelineKind.MEMBERSHIP)
        if self._memory_manager is not None:
            try:
                self._memory_manager.record_experience(
                    appended.actor_id,
                    "membership_event",
                    f"Actor {appended.actor_id} membership event '{event}' in society "
                    f"{appended.society_id} (roles={list(appended.roles)}, status={appended.status})",
                    metadata={"membership_id": appended.membership_id, "event": event},
                )
            except Exception:
                logger.debug("_supersede: suppressed exception", exc_info=True)
        return Membership.from_record(appended)

    def record_event(self, membership_id: str, event: str, **metadata_overrides: Any) -> Membership | None:
        """Public entry point for an external collaborator (e.g.
        kernel/society/delegation.py::DelegationRegistry) to append a pure
        event marker for this membership — same MembershipRecord row
        fields carried forward unchanged, only metadata (event name +
        any extra key/value pairs) is new. Returns None if membership_id
        doesn't resolve to a currently-open record."""
        current = self._find_open_record_by_id(membership_id)
        if current is None:
            return None
        return self._supersede(current, event, extra_metadata=metadata_overrides)

    # ── Backward-compatible core API (unchanged signatures) ──────────────

    def add(
        self,
        actor_id: str,
        society_id: str,
        role: str = "member",
        metadata: dict[str, Any] | None = None,
    ) -> MembershipRecord:
        with self._add_lock:
            existing = self._open_record_for(actor_id, society_id)
            if existing is not None:
                return existing
            record = self._store.record(
                TimelineKind.MEMBERSHIP,
                actor_id=actor_id,
                society_id=society_id,
                membership_id=_new_entry_id(),
                roles=(role,),
                status="active",
                metadata={**(metadata or {}), "event": "created"},
            )
        if self._memory_manager is not None:
            try:
                self._memory_manager.record_experience(
                    actor_id,
                    "membership_event",
                    f"Actor {actor_id} joined society {society_id} with role '{role}'",
                    metadata={
                        "membership_id": record.membership_id,
                        "event": "created",
                    },
                )
            except Exception:
                logger.debug("add: suppressed exception", exc_info=True)
        return record

    def remove(self, actor_id: str, society_id: str, reason: str = "") -> None:
        with self._add_lock:
            existing = self._open_record_for(actor_id, society_id)
            if existing is None:
                return
            self._supersede(existing, "terminated", status="terminated", reason=reason)

    def is_member(self, actor_id: str, society_id: str) -> bool:
        record = self._open_record_for(actor_id, society_id)
        return record is not None and Membership.from_record(record).is_active()

    def societies_for_actor(self, actor_id: str) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                r.society_id
                for r in self._store.query(actor_id, TimelineKind.MEMBERSHIP)
                if r.is_open() and Membership.from_record(r).is_active()
            )
        )

    def actors_for_society(self, society_id: str) -> tuple[str, ...]:
        actors = []
        for actor_id in self._store.known_actors(TimelineKind.MEMBERSHIP):
            if self.is_member(actor_id, society_id):
                actors.append(actor_id)
        return tuple(actors)

    def history_for_actor(
        self, actor_id: str, since: float | None = None, until: float | None = None
    ) -> tuple[MembershipRecord, ...]:
        return self._store.query(actor_id, TimelineKind.MEMBERSHIP, since, until)

    # ── Discovery ─────────────────────────────────────────────────────────

    def get_membership(self, membership_id: str) -> Membership | None:
        record = self._find_open_record_by_id(membership_id)
        return Membership.from_record(record) if record is not None else None

    def memberships_for_actor(self, actor_id: str) -> tuple[Membership, ...]:
        return tuple(
            Membership.from_record(r) for r in self._store.query(actor_id, TimelineKind.MEMBERSHIP) if r.is_open()
        )

    def memberships_for_society(self, society_id: str) -> tuple[Membership, ...]:
        result = []
        for actor_id in self._store.known_actors(TimelineKind.MEMBERSHIP):
            for r in self._store.query(actor_id, TimelineKind.MEMBERSHIP):
                if r.is_open() and r.society_id == society_id:
                    result.append(Membership.from_record(r))
        return tuple(result)

    def memberships_for_team(self, team_id: str) -> tuple[Membership, ...]:
        result = []
        for actor_id in self._store.known_actors(TimelineKind.MEMBERSHIP):
            for r in self._store.query(actor_id, TimelineKind.MEMBERSHIP):
                if r.is_open() and r.team_id == team_id:
                    result.append(Membership.from_record(r))
        return tuple(result)

    def memberships_by_role(self, role: str) -> tuple[Membership, ...]:
        result = []
        for actor_id in self._store.known_actors(TimelineKind.MEMBERSHIP):
            for r in self._store.query(actor_id, TimelineKind.MEMBERSHIP):
                if r.is_open() and role in r.roles:
                    result.append(Membership.from_record(r))
        return tuple(result)

    def active_memberships(self) -> tuple[Membership, ...]:
        result = []
        for actor_id in self._store.known_actors(TimelineKind.MEMBERSHIP):
            for r in self._store.query(actor_id, TimelineKind.MEMBERSHIP):
                if r.is_open():
                    membership = Membership.from_record(r)
                    if membership.is_active():
                        result.append(membership)
        return tuple(result)

    # ── Role management ───────────────────────────────────────────────────

    def assign_role(self, membership_id: str, role: str) -> Membership | None:
        current = self._find_open_record_by_id(membership_id)
        if current is None or role in current.roles:
            return Membership.from_record(current) if current is not None else None
        return self._supersede(current, "role_assigned", roles=current.roles + (role,))

    def remove_role(self, membership_id: str, role: str) -> Membership | None:
        current = self._find_open_record_by_id(membership_id)
        if current is None or role not in current.roles:
            return Membership.from_record(current) if current is not None else None
        return self._supersede(current, "role_removed", roles=tuple(r for r in current.roles if r != role))

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def set_status(self, membership_id: str, status: str, reason: str = "") -> Membership | None:
        current = self._find_open_record_by_id(membership_id)
        if current is None:
            return None
        if current.status == status:
            return Membership.from_record(current)
        event = {
            "active": "activated",
            "suspended": "suspended",
            "terminated": "terminated",
        }.get(status, "status_changed")
        return self._supersede(current, event, status=status, reason=reason)

    # ── Trust (canonical store: SocietyGovernanceEngine.TrustRecord) ─────

    def update_trust(
        self,
        membership_id: str,
        trust_score: float,
        governance: Any = None,
        factors: dict[str, float] | None = None,
    ) -> Membership | None:
        """Writes through to the owning society's SocietyGovernanceEngine
        (the canonical trust store — see governance.py::TrustRecord) and
        appends a MembershipRecord snapshot of the new score, so the
        timeline captures "trust updated" without becoming a second trust
        source of truth."""
        current = self._find_open_record_by_id(membership_id)
        if current is None:
            return None
        if governance is not None:
            governance.evaluate_trust(current.actor_id, trust_score, factors)
        return self._supersede(current, "trust_updated", trust_score=trust_score)

    # ── Policy/permission/capability/constraint resolution ───────────────

    def resolve_policies(self, membership_id: str, governance: Any = None) -> tuple[Any, ...]:
        current = self._find_open_record_by_id(membership_id)
        if current is None or not Membership.from_record(current).is_active() or governance is None:
            return ()
        membership = Membership.from_record(current)
        policies = []
        for policy in governance.policies():
            required_role = policy.metadata.get("required_role")
            if required_role and required_role not in membership.roles:
                continue
            scope = (policy.scope or "").strip()
            if scope and scope not in {membership.actor_id, *membership.roles}:
                continue
            policies.append(policy)
        return tuple(policies)

    def resolve_permissions(self, membership_id: str, governance: Any = None) -> tuple[str, ...]:
        current = self._find_open_record_by_id(membership_id)
        if current is None or not Membership.from_record(current).is_active():
            return ()
        granted = tuple(current.permissions)
        if governance is not None:
            granted += tuple(f"{p.resource}:{p.action}" for p in governance.permissions_for(current.actor_id))
        return tuple(dict.fromkeys(granted))

    def resolve_capabilities(self, membership_id: str, actor_profile: Any = None) -> tuple[str, ...]:
        current = self._find_open_record_by_id(membership_id)
        if current is None or not Membership.from_record(current).is_active() or actor_profile is None:
            return ()
        return tuple(c.name for c in getattr(actor_profile, "capabilities", ()))

    def resolve_constraints(self, membership_id: str, governance: Any = None) -> tuple[Any, ...]:
        from src.monkey_brain.kernel.pipeline.planning.domain import PlanningConstraint

        if governance is None:
            return ()
        return tuple(
            PlanningConstraint(
                kind=p.policy_type.value,
                description=p.description,
                parameters=dict(p.metadata),
                hard=(p.priority > 0),
            )
            for p in self.resolve_policies(membership_id, governance=governance)
        )


class MembershipEventType(Enum):
    TEMPORARY_MEMBERSHIP_GRANTED = "TemporaryMembershipGranted"
    TEMPORARY_MEMBERSHIP_REVOKED = "TemporaryMembershipRevoked"


class MembershipGovernor:
    """Prompt 3 — Governance and Membership Model. The "membership system"
    kernel/timeline/presence.py::PresenceTimeline's docstring defers to:
    subscribes to a PresenceTimeline's MovementEvents and grants/revokes
    TEMPORARY society membership from physical presence, while leaving
    PERMANENT membership (SocietyMembershipRegistry.add()/remove(),
    explicitly called, never touched by movement) alone. Supersedes this
    session's earlier PresenceMembershipBridge, which incorrectly mutated
    PERMANENT membership from movement — forbidden by this prompt's
    "Temporary memberships must never modify permanent memberships" rule.

    A Space is associated with every Society hosted anywhere in its
    geographic lineage (the Space itself plus every ancestor — ``
    GeographicRegistry.societies_at_or_above``, matching host_society()'s
    "no tier restriction" model). Temporary membership is scoped to a
    single Space, literally: ENTER grants every associated Society the
    Actor doesn't already hold permanently; LEAVE immediately revokes
    every temporary membership that Space granted. No buffering across a
    move — moving between two Spaces both associated with the same
    Society revokes and immediately re-grants it, matching the prompt's
    "remove temporary memberships immediately when the Actor leaves the
    Space" wording exactly."""

    def __init__(
        self,
        presence: PresenceTimeline,
        geo_registry: GeographicRegistry,
        permanent: "SocietyMembershipRegistry",
        context_stream: SocietyContextStream | None = None,
        on_temporary_granted: "Callable[[str, str], None] | None" = None,
        on_temporary_revoked: "Callable[[str, str], None] | None" = None,
        memory_manager: Any = None,
    ) -> None:
        self._presence = presence
        self._geo_registry = geo_registry
        self._permanent = permanent
        self._context_stream = context_stream
        self._memory_manager = memory_manager
        """Optional kernel/learn/memory/manager.py::MemoryManager. When set,
        each ENTER/MOVE becomes a searchable CognitiveMemory experience
        (spec's "presence history indexed... for LLM grounding" — commonly
        visited spaces, historical collaborators), reusing
        SocietyMembershipRegistry._supersede's record_experience() call
        shape rather than a new memory mechanism."""
        self._on_temporary_granted = on_temporary_granted
        """(actor_id, society_id) -> None, called whenever a temporary
        Membership is actually granted — Coordination Boundary refactor:
        kernel/society/integration.py wires this to make the Actor a
        coordination participant in that Society too (SocietyRuntime.
        add_temporary_participant), not only a governance-set entry."""
        self._on_temporary_revoked = on_temporary_revoked
        """(actor_id, society_id) -> None, called whenever a temporary
        Membership is actually revoked — wired to SocietyRuntime.
        remove_temporary_participant."""
        self._temporary: dict[str, set[str]] = {}
        # Prompt 8 — Lemon Observability counters.
        self._created_count = 0
        self._revoked_count = 0
        self._events_published_count = 0
        self._effective_calculation_count = 0
        presence.subscribe(self._on_movement)

    def temporary_societies_for_actor(self, actor_id: str) -> frozenset[str]:
        return frozenset(self._temporary.get(actor_id, ()))

    def effective_societies(self, actor_id: str) -> frozenset[str]:
        """permanent_societies UNION temporary_societies — every Society
        this Actor is currently affiliated with, either kind. Callers that
        need to know WHICH kind a given membership is should query
        SocietyMembershipRegistry / temporary_societies_for_actor
        directly instead — this view deliberately erases that distinction
        for convenience, it does not replace it."""
        self._effective_calculation_count += 1
        permanent = frozenset(self._permanent.societies_for_actor(actor_id))
        return permanent | self.temporary_societies_for_actor(actor_id)

    @property
    def created_count(self) -> int:
        """Lemon's temporary_memberships_created."""
        return self._created_count

    @property
    def revoked_count(self) -> int:
        """Lemon's temporary_memberships_revoked."""
        return self._revoked_count

    @property
    def events_published_count(self) -> int:
        """Lemon's membership_events_published — ContextStream publishes
        actually made (0 whenever no context_stream was injected), as
        distinct from created_count/revoked_count (the underlying actions,
        which happen regardless of whether anything observes them)."""
        return self._events_published_count

    @property
    def effective_calculation_count(self) -> int:
        """Lemon's effective_membership_calculations."""
        return self._effective_calculation_count

    def _publish(
        self,
        event_type: MembershipEventType,
        actor_id: str,
        society_id: str,
        space_id: str,
        timestamp: float,
    ) -> None:
        if event_type is MembershipEventType.TEMPORARY_MEMBERSHIP_GRANTED:
            self._created_count += 1
            if self._on_temporary_granted is not None:
                try:
                    self._on_temporary_granted(actor_id, society_id)
                except Exception:
                    logger.exception(
                        "on_temporary_granted callback raised for actor=%s society=%s",
                        actor_id,
                        society_id,
                    )
        else:
            self._revoked_count += 1
            if self._on_temporary_revoked is not None:
                try:
                    self._on_temporary_revoked(actor_id, society_id)
                except Exception:
                    logger.exception(
                        "on_temporary_revoked callback raised for actor=%s society=%s",
                        actor_id,
                        society_id,
                    )
        if self._context_stream is None:
            return
        context_event_type = (
            ContextEventType.TEMPORARY_MEMBERSHIP_GRANTED
            if event_type is MembershipEventType.TEMPORARY_MEMBERSHIP_GRANTED
            else ContextEventType.TEMPORARY_MEMBERSHIP_REVOKED
        )
        self._context_stream.publish(
            ContextEvent(
                event_type=context_event_type,
                actor_id=actor_id,
                description=f"{event_type.value}: actor {actor_id}, society {society_id}, space {space_id}",
                payload={
                    "actor_id": actor_id,
                    "society_id": society_id,
                    "space_id": space_id,
                },
                timestamp=timestamp,
            )
        )
        self._events_published_count += 1

    def _on_movement(self, event: MovementEvent) -> None:
        if event.event_type is MovementEventType.LEAVE:
            self._handle_leave(event)
        elif event.event_type is MovementEventType.ENTER:
            self._handle_enter(event)
            self._record_presence_experience(event)
        elif event.event_type is MovementEventType.MOVE:
            self._handle_move(event)
            self._record_presence_experience(event)
        # BEGIN_ACTIVITY/END_ACTIVITY: same Space, no membership effect.

    def _record_presence_experience(self, event: MovementEvent) -> None:
        """Records an ENTER/MOVE as a searchable CognitiveMemory experience
        (kind="experience", the bucket ContextConstructionEngine._search_memory
        surfaces into PlanningContext.relevant_experiences) so "commonly
        visited spaces / historical collaborators" are retrievable via the
        existing semantic-search path. Non-fatal, and a no-op with no
        memory_manager wired -- presence/membership tracking itself is
        unaffected either way."""
        if self._memory_manager is None:
            return
        others = tuple(a for a in self._presence.occupants(event.space_id, event.timestamp) if a != event.actor_id)
        text = f"Actor {event.actor_id} entered space {event.space_id}"
        if event.activity:
            text += f" for activity {event.activity!r}"
        if others:
            text += f", alongside actors: {', '.join(others)}"
        try:
            self._memory_manager.record_experience(
                event.actor_id,
                "experience",
                text,
                metadata={
                    "presence_event": event.event_type.value,
                    "space_id": event.space_id,
                    "nearby_actors": list(others),
                    "timestamp": event.timestamp,
                },
            )
        except Exception:
            logger.debug("_record_presence_experience: suppressed exception", exc_info=True)

    def _handle_move(self, event: MovementEvent) -> None:
        """A lateral move (PresenceTimeline.move_actor's MOVE event)
        carries both sides of the transition in one event, unlike a
        LEAVE+ENTER pair's two -- revoke the prior Space's temporary
        memberships the new Space doesn't also grant, then grant the new
        Space's, exactly matching what a LEAVE immediately followed by an
        ENTER would have produced. Composes the existing handlers rather
        than duplicating their revoke/grant logic."""
        if event.prior_space_id:
            self._handle_leave(
                MovementEvent(
                    event_type=MovementEventType.LEAVE,
                    actor_id=event.actor_id,
                    space_id=event.prior_space_id,
                    timestamp=event.timestamp,
                    activity=event.activity,
                )
            )
        self._handle_enter(event)

    def _handle_leave(self, event: MovementEvent) -> None:
        held = self._temporary.get(event.actor_id)
        if not held:
            return
        associated = self._geo_registry.societies_at_or_above(event.space_id)
        for society_id in held & associated:
            held.discard(society_id)
            self._publish(
                MembershipEventType.TEMPORARY_MEMBERSHIP_REVOKED,
                event.actor_id,
                society_id,
                event.space_id,
                event.timestamp,
            )
        if not held:
            self._temporary.pop(event.actor_id, None)

    def _handle_enter(self, event: MovementEvent) -> None:
        associated = self._geo_registry.societies_at_or_above(event.space_id)
        permanent = set(self._permanent.societies_for_actor(event.actor_id))
        held = self._temporary.setdefault(event.actor_id, set())
        for society_id in associated:
            if society_id in permanent or society_id in held:
                continue
            held.add(society_id)
            self._publish(
                MembershipEventType.TEMPORARY_MEMBERSHIP_GRANTED,
                event.actor_id,
                society_id,
                event.space_id,
                event.timestamp,
            )
        if not held:
            self._temporary.pop(event.actor_id, None)

    def reconcile(self, actor_id: str) -> int:
        """Prompt 4 — Recursive Geographic Tick's "update Temporary
        Memberships" step: bring actor_id's temporary memberships back in
        sync with its CURRENT physical presence and CURRENT Space-Society
        association, publishing lifecycle events for anything changed.
        Catches drift _on_movement's space-scoped grant/revoke can't see —
        a Society's hosting topology changing (host_society/unhost_society)
        while the Actor stays put, or a new PERMANENT membership superseding
        an already-granted temporary one (permanent always wins: a
        redundant temporary entry for a now-permanent Society is revoked,
        never the other way around — see module docstring). Returns how
        many temporary memberships were granted or revoked."""
        current = self._presence.current(actor_id)
        associated = (
            self._geo_registry.societies_at_or_above(current.space_id)
            if current is not None and current.is_open()
            else set()
        )
        space_id = current.space_id if current is not None else ""
        permanent = set(self._permanent.societies_for_actor(actor_id))
        held = self._temporary.setdefault(actor_id, set())
        changed = 0

        for society_id in list(held):
            if society_id in permanent or society_id not in associated:
                held.discard(society_id)
                changed += 1
                self._publish(
                    MembershipEventType.TEMPORARY_MEMBERSHIP_REVOKED,
                    actor_id,
                    society_id,
                    space_id,
                    time.time(),
                )

        for society_id in associated:
            if society_id in permanent or society_id in held:
                continue
            held.add(society_id)
            changed += 1
            self._publish(
                MembershipEventType.TEMPORARY_MEMBERSHIP_GRANTED,
                actor_id,
                society_id,
                space_id,
                time.time(),
            )

        if not held:
            self._temporary.pop(actor_id, None)
        return changed


def _new_entry_id() -> str:
    from uuid import uuid4

    return uuid4().hex
