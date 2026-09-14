"""TimelineQueryEngine — cross-timeline queries and replay (Temporal
Presence & Actor Timeline Model refactor).

Backs api/routes/actors.py's `GET /actors/{id}/timeline` and friends.
Doesn't own any data itself — every method here is a thin composition over
kernel.timeline.store.TimelineStore, kernel.timeline.presence.PresenceTimeline,
and kernel.society.membership.SocietyMembershipRegistry.
"""

from __future__ import annotations

from typing import Any

from src.monkey_brain.kernel.timeline.entry import TimelineEntry, TimelineKind
from src.monkey_brain.kernel.timeline.store import TimelineStore


class TimelineQueryEngine:
    def __init__(self, store: TimelineStore | None = None) -> None:
        self._store = store or TimelineStore()

    def query(
        self,
        actor_id: str,
        kind: TimelineKind,
        since: float | None = None,
        until: float | None = None,
    ) -> tuple[TimelineEntry, ...]:
        return self._store.query(actor_id, kind, since, until)

    def replay(
        self, actor_id: str, since: float | None = None, until: float | None = None
    ) -> tuple[TimelineEntry, ...]:
        """Every timeline entry for actor_id across all 7 kinds, merged and
        sorted by start_time — "replay Alice's day." Deterministic: the
        same (actor_id, since, until) always returns the same ordered
        sequence, since entries are never mutated (see TimelineEntry's
        module docstring) other than the one well-defined "close" transition."""
        entries: list[TimelineEntry] = []
        for kind in TimelineKind:
            entries.extend(self._store.query(actor_id, kind, since, until))
        return tuple(sorted(entries, key=lambda e: e.start_time))

    def current_state(self, actor_id: str) -> dict[str, Any]:
        """Current state, entirely derived — never a stored field. Mirrors
        the spec's own "Current Location = Latest Presence where end_time
        is null" / "Current Memberships = Memberships where end_time is
        null" / "Current Goals = Goals where status != completed" examples."""
        presence = self._store.current(actor_id, TimelineKind.PRESENCE)
        memberships = [r for r in self._store.query(actor_id, TimelineKind.MEMBERSHIP) if r.is_open()]
        goals = [
            r for r in self._store.query(actor_id, TimelineKind.GOAL) if r.status not in ("completed", "cancelled")
        ]
        return {
            "actor_id": actor_id,
            "presence": presence.to_dict() if presence else None,
            "memberships": [m.to_dict() for m in memberships],
            "goals": [g.to_dict() for g in goals],
        }
