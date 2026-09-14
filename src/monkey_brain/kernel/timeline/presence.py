"""PresenceTimeline — manages physical presence only: which Space an actor
is currently, or was previously, located in. Its invariants (no overlapping
intervals, exactly one open interval per actor, always a valid Space) are
enforced at the write path itself, not left as a convention tests merely
assert on. Mirrors kernel/geography/registry.py::GeographicRegistry.
add_child's own strict-enforcement-at-the-write-path precedent.

PresenceTimeline must never manage society membership. Its sole
responsibility is tracking physical occupancy and movement between Spaces.
Society membership changes resulting from movement are handled by the
membership system described in Prompt 3.

When an actor enters or leaves a space, PresenceTimeline should publish
movement events that other systems (such as the membership manager) can
consume.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from src.monkey_brain.kernel.timeline.entry import Presence, TimelineKind
from src.monkey_brain.kernel.timeline.store import TimelineStore
from src.monkey_brain.kernel.geography.entity import GeographicEntityType
from src.monkey_brain.kernel.geography.registry import GeographicRegistry

logger = logging.getLogger("agentos.presence_timeline")


class MovementEventType(Enum):
    ENTER = "enter"
    LEAVE = "leave"
    MOVE = "move"
    """A lateral move: the new Space shares the same immediate parent as
    the Space just left (see move_actor()) -- published INSTEAD OF a
    LEAVE+ENTER pair, not in addition to one."""
    BEGIN_ACTIVITY = "begin_activity"
    END_ACTIVITY = "end_activity"


@dataclass(frozen=True)
class MovementEvent:
    """A single enter/leave notification — the unit PresenceTimeline
    publishes to subscribers (e.g. the Prompt 3 membership manager) so they
    can react to physical movement without PresenceTimeline knowing
    anything about what a "reaction" means (no society/membership import
    here — see module docstring)."""

    event_type: MovementEventType
    actor_id: str
    space_id: str
    timestamp: float
    activity: str = ""
    prior_space_id: str = ""
    """Only set for MOVE events: the Space just left. A MOVE event carries
    both sides of the transition in one object (unlike a LEAVE+ENTER pair,
    which is two), so subscribers that need to know what was left as well
    as what was entered -- e.g. MembershipGovernor revoking the prior
    Space's temporary Society membership before granting the new Space's
    -- need this field."""


class PresenceTimeline:
    """actor LOCATED_IN Space, valid_time=[start_time, end_time or now)."""

    def __init__(self, geo_registry: GeographicRegistry, store: TimelineStore | None = None) -> None:
        self._geo_registry = geo_registry
        self._store = store or TimelineStore()
        self._subscribers: list[Callable[[MovementEvent], None]] = []
        # Prompt 8 — Lemon Observability counters.
        self._movement_count = 0
        """Successful move_actor() calls — Lemon's actor_movements metric."""
        self._presence_update_count = 0
        """PresenceTimeline writes (closes + new records) — Lemon's
        presence_updates metric. >= movement_count, since a move that
        closes a prior open Presence is 2 writes (close + new record) but
        only 1 movement."""

    def subscribe(self, callback: Callable[[MovementEvent], None]) -> None:
        """Register to receive every future MovementEvent (ENTER/LEAVE)."""
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[MovementEvent], None]) -> None:
        self._subscribers = [s for s in self._subscribers if s is not callback]

    def _publish(self, event: MovementEvent) -> None:
        for subscriber in self._subscribers:
            try:
                subscriber(event)
            except Exception:
                logger.exception("presence movement subscriber raised for %s", event)

    def move_actor(
        self,
        actor_id: str,
        space_id: str,
        activity: str = "",
        confidence: float = 1.0,
        source: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Presence | None:
        """Close the actor's current open Presence (if any) and open a new
        one at space_id. Returns None (no write performed) if space_id
        doesn't resolve to an actual Space — this is the sole gate that
        makes "Presence always references a valid Space" true by
        construction. Closing-then-opening atomically (from the caller's
        perspective — no code path exists that returns before the close AND
        after the open) is what makes "no overlap" and "exactly one active"
        true by construction rather than a checked convention.

        On success, publishes a LEAVE MovementEvent for the prior space (if
        any was open) followed by an ENTER MovementEvent for space_id, to
        every subscriber registered via subscribe() -- UNLESS the prior
        Space and the new Space share the same immediate parent (a lateral
        move within the same containing entity, e.g. between two Spaces on
        the same Floor), in which case a single MOVE event is published
        instead of the LEAVE+ENTER pair."""
        space = self._geo_registry.get(space_id)
        if space is None or space.entity_type != GeographicEntityType.SPACE:
            return None

        now = time.time()
        current = self._store.current(actor_id, TimelineKind.PRESENCE)
        is_lateral_move = False
        prior_space_id = ""
        if current is not None and current.is_open():
            if current.space_id != space_id:
                prior_parent = self._geo_registry.parent_of(current.space_id)
                new_parent = self._geo_registry.parent_of(space_id)
                is_lateral_move = (
                    prior_parent is not None
                    and new_parent is not None
                    and prior_parent.entity_id == new_parent.entity_id
                )
            if is_lateral_move:
                prior_space_id = current.space_id
            self._store.close(current, TimelineKind.PRESENCE, end_time=now)
            self._presence_update_count += 1
            if not is_lateral_move:
                self._publish(
                    MovementEvent(
                        event_type=MovementEventType.LEAVE,
                        actor_id=actor_id,
                        space_id=current.space_id,
                        timestamp=now,
                        activity=current.activity,
                    )
                )

        recorded = self._store.record(
            TimelineKind.PRESENCE,
            actor_id=actor_id,
            space_id=space_id,
            activity=activity,
            confidence=confidence,
            source=source,
            metadata=metadata or {},
            start_time=now,
        )
        self._presence_update_count += 1
        self._movement_count += 1
        self._publish(
            MovementEvent(
                event_type=(MovementEventType.MOVE if is_lateral_move else MovementEventType.ENTER),
                actor_id=actor_id,
                space_id=space_id,
                timestamp=now,
                activity=activity,
                prior_space_id=prior_space_id,
            )
        )
        return recorded

    def begin_activity(
        self,
        actor_id: str,
        activity: str,
        confidence: float = 1.0,
        source: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Presence | None:
        """Change the actor's activity without moving -- closes the
        current open Presence and reopens one at the SAME space_id with
        the new activity, publishing BEGIN_ACTIVITY (not ENTER) so
        subscribers can tell an activity change from a real move. Returns
        None if the actor has no open Presence (there's no Space to stay
        in) -- same "no write without a valid Space" guarantee move_actor()
        enforces via the geo_registry lookup, here enforced by requiring
        an already-open Presence instead."""
        current = self._store.current(actor_id, TimelineKind.PRESENCE)
        if current is None or not current.is_open():
            return None
        now = time.time()
        self._store.close(current, TimelineKind.PRESENCE, end_time=now)
        self._presence_update_count += 1
        recorded = self._store.record(
            TimelineKind.PRESENCE,
            actor_id=actor_id,
            space_id=current.space_id,
            activity=activity,
            confidence=confidence,
            source=source,
            metadata=metadata or {},
            start_time=now,
        )
        self._presence_update_count += 1
        self._publish(
            MovementEvent(
                event_type=MovementEventType.BEGIN_ACTIVITY,
                actor_id=actor_id,
                space_id=current.space_id,
                timestamp=now,
                activity=activity,
            )
        )
        return recorded

    def end_activity(
        self,
        actor_id: str,
        confidence: float = 1.0,
        source: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Presence | None:
        """Clears the actor's current activity (back to "") without
        moving -- same close-then-reopen-at-the-same-Space pattern as
        begin_activity(), published as END_ACTIVITY."""
        current = self._store.current(actor_id, TimelineKind.PRESENCE)
        if current is None or not current.is_open():
            return None
        now = time.time()
        ended_activity = current.activity
        self._store.close(current, TimelineKind.PRESENCE, end_time=now)
        self._presence_update_count += 1
        recorded = self._store.record(
            TimelineKind.PRESENCE,
            actor_id=actor_id,
            space_id=current.space_id,
            activity="",
            confidence=confidence,
            source=source,
            metadata=metadata or {},
            start_time=now,
        )
        self._presence_update_count += 1
        self._publish(
            MovementEvent(
                event_type=MovementEventType.END_ACTIVITY,
                actor_id=actor_id,
                space_id=current.space_id,
                timestamp=now,
                activity=ended_activity,
            )
        )
        return recorded

    @property
    def movement_count(self) -> int:
        return self._movement_count

    @property
    def presence_update_count(self) -> int:
        return self._presence_update_count

    def current(self, actor_id: str) -> Presence | None:
        return self._store.current(actor_id, TimelineKind.PRESENCE)

    def at(self, actor_id: str, timestamp: float) -> Presence | None:
        return self._store.at(actor_id, TimelineKind.PRESENCE, timestamp)

    def history(self, actor_id: str, since: float | None = None, until: float | None = None) -> tuple[Presence, ...]:
        return self._store.query(actor_id, TimelineKind.PRESENCE, since, until)

    def enriched_history(
        self,
        actor_id: str,
        since: float | None = None,
        until: float | None = None,
    ) -> list[dict[str, Any]]:
        """Presence History enrichment: for each visit from history(),
        joins in associated_societies (GeographicRegistry.
        societies_at_or_above), nearby_actors (occupants() at the visit's
        start), and goals active during the visit (TimelineStore.query
        against GOAL, scoped to the visit's time window) -- reusing each
        of these exactly as they already exist elsewhere rather than
        storing this as redundant, potentially-stale denormalized fields
        on Presence itself. Returns dicts (Presence.to_dict() plus the
        joined fields), not Presence instances, since the enrichment
        isn't part of the append-only Presence record."""
        enriched: list[dict[str, Any]] = []
        for record in self.history(actor_id, since, until):
            visit_end = record.end_time if record.end_time is not None else time.time()
            goals = self._store.query(actor_id, TimelineKind.GOAL, record.start_time, visit_end)
            d = record.to_dict()
            d["associated_societies"] = sorted(self._geo_registry.societies_at_or_above(record.space_id))
            d["nearby_actors"] = tuple(a for a in self.occupants(record.space_id, record.start_time) if a != actor_id)
            d["goals_active_during"] = tuple(g.to_dict() for g in goals)
            enriched.append(d)
        return enriched

    def current_occupancy(self) -> dict[str, str]:
        """actor_id -> its current space_id, for every actor with an open
        Presence right now — the inverse of occupants() (which needs a
        space_id up front): callers that need to enumerate "who is
        currently where" without already knowing which Spaces to ask about
        (e.g. kernel/society/movement_perturbation.py::MovementPerturbationEngine,
        choosing an occupied Space at random) use this instead."""
        occupancy: dict[str, str] = {}
        for actor_id in self._store.known_actors(TimelineKind.PRESENCE):
            presence = self.current(actor_id)
            if presence is not None and presence.is_open():
                occupancy[actor_id] = presence.space_id
        return occupancy

    def occupants(self, space_id: str, timestamp: float | None = None) -> tuple[str, ...]:
        """Which actors were present in space_id at `timestamp` (default:
        now) — "who occupied Building A at 10:15." Scans every actor known
        to the backing store; fine for the in-memory/small-actor-count
        case this codebase runs at today (same scale assumption as
        PlanetaryRuntime._societies)."""
        ts = timestamp if timestamp is not None else time.time()
        occupants: list[str] = []
        for actor_id in self._store.known_actors(TimelineKind.PRESENCE):
            presence = self.at(actor_id, ts)
            if presence is not None and presence.space_id == space_id:
                occupants.append(actor_id)
        return tuple(occupants)
