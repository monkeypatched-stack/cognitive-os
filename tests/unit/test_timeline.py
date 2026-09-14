"""Tests for the Temporal Presence & Actor Timeline Model refactor:
append-only TimelineStore, PresenceTimeline's write-path invariants
(no overlap, exactly one open interval, valid Space required), Goal/
Membership timelines never losing history on update, and cross-kind
replay() ordering.
"""

from __future__ import annotations

from src.monkey_brain.kernel.timeline.store import TimelineStore
from src.monkey_brain.kernel.timeline.entry import (
    Presence,
    GoalRecord,
    MembershipRecord,
    ExecutionRecord,
    TimelineKind,
)
from src.monkey_brain.kernel.timeline.presence import PresenceTimeline
from src.monkey_brain.kernel.timeline.query import TimelineQueryEngine
from src.monkey_brain.kernel.society.membership import SocietyMembershipRegistry
from src.monkey_brain.kernel.geography.registry import GeographicRegistry
from src.monkey_brain.kernel.geography.entity import GeographicEntityType


def _build_space_chain(registry: GeographicRegistry):
    planet = registry.create(GeographicEntityType.PLANET, "Earth")
    country = registry.create(GeographicEntityType.COUNTRY, "C", parent_id=planet.entity_id)
    state = registry.create(GeographicEntityType.STATE, "S", parent_id=country.entity_id)
    county = registry.create(GeographicEntityType.COUNTY, "Co", parent_id=state.entity_id)
    city = registry.create(GeographicEntityType.CITY, "City", parent_id=county.entity_id)
    street = registry.create(GeographicEntityType.STREET, "St", parent_id=city.entity_id)
    building = registry.create(GeographicEntityType.BUILDING, "B", parent_id=street.entity_id)
    space_a = registry.create(GeographicEntityType.SPACE, "SpaceA", parent_id=building.entity_id)
    space_b = registry.create(GeographicEntityType.SPACE, "SpaceB", parent_id=building.entity_id)
    return building, space_a, space_b


# ── TimelineStore: append/query/current/close ────────────────────────────


def test_append_and_query_ordering():
    store = TimelineStore()
    store.record(TimelineKind.GOAL, actor_id="a1", name="g1", start_time=10.0)
    store.record(TimelineKind.GOAL, actor_id="a1", name="g2", start_time=5.0)
    records = store.query("a1", TimelineKind.GOAL)
    assert [r.name for r in records] == ["g2", "g1"]  # sorted by start_time


def test_current_returns_open_entry_over_closed():
    store = TimelineStore()
    e1 = store.record(TimelineKind.PRESENCE, actor_id="a1", space_id="s1", start_time=1.0)
    store.close(e1, TimelineKind.PRESENCE, end_time=2.0)
    e2 = store.record(TimelineKind.PRESENCE, actor_id="a1", space_id="s2", start_time=2.0)
    current = store.current("a1", TimelineKind.PRESENCE)
    assert current.space_id == "s2"
    assert current.is_open()


def test_close_does_not_duplicate_or_lose_history():
    store = TimelineStore()
    e1 = store.record(TimelineKind.PRESENCE, actor_id="a1", space_id="s1", start_time=1.0)
    store.close(e1, TimelineKind.PRESENCE, end_time=2.0)
    all_entries = store.query("a1", TimelineKind.PRESENCE)
    assert len(all_entries) == 1
    assert all_entries[0].end_time == 2.0


def test_at_point_in_time_lookup():
    store = TimelineStore()
    e1 = store.record(TimelineKind.PRESENCE, actor_id="a1", space_id="s1", start_time=100.0)
    store.close(e1, TimelineKind.PRESENCE, end_time=200.0)
    store.record(TimelineKind.PRESENCE, actor_id="a1", space_id="s2", start_time=200.0)
    assert store.at("a1", TimelineKind.PRESENCE, 150.0).space_id == "s1"
    assert store.at("a1", TimelineKind.PRESENCE, 250.0).space_id == "s2"
    assert store.at("a1", TimelineKind.PRESENCE, 50.0) is None


# ── PresenceTimeline invariants ───────────────────────────────────────────


def test_presence_no_overlap_exactly_one_open():
    registry = GeographicRegistry()
    _building, space_a, space_b = _build_space_chain(registry)
    pt = PresenceTimeline(registry, TimelineStore())

    pt.move_actor("alice", space_a.entity_id, activity="working")
    pt.move_actor("alice", space_b.entity_id, activity="meeting")

    history = pt.history("alice")
    assert len(history) == 2
    open_entries = [e for e in history if e.is_open()]
    assert len(open_entries) == 1
    assert open_entries[0].space_id == space_b.entity_id
    closed_entries = [e for e in history if not e.is_open()]
    assert len(closed_entries) == 1
    assert closed_entries[0].end_time is not None


def test_presence_rejects_non_space_entity():
    registry = GeographicRegistry()
    building, _space_a, _space_b = _build_space_chain(registry)
    pt = PresenceTimeline(registry, TimelineStore())

    result = pt.move_actor("alice", building.entity_id, activity="working")
    assert result is None
    assert pt.current("alice") is None


def test_presence_rejects_unknown_space_id():
    registry = GeographicRegistry()
    pt = PresenceTimeline(registry, TimelineStore())
    assert pt.move_actor("alice", "does-not-exist") is None


def test_presence_occupants():
    registry = GeographicRegistry()
    _building, space_a, space_b = _build_space_chain(registry)
    store = TimelineStore()
    pt = PresenceTimeline(registry, store)

    pt.move_actor("alice", space_a.entity_id)
    pt.move_actor("bob", space_a.entity_id)
    pt.move_actor("carol", space_b.entity_id)

    assert sorted(pt.occupants(space_a.entity_id)) == ["alice", "bob"]
    assert pt.occupants(space_b.entity_id) == ("carol",)


# ── Goal/Membership timelines never lose history ─────────────────────────


def test_goal_history_preserved_across_updates():
    from src.monkey_brain.kernel.pipeline.belief_state import BeliefState

    bs = BeliefState(actor_id="goal-actor")
    bs.update_goal(name="goal1")
    bs.update_goal(name="goal2")
    bs.update_goal(name="goal3")

    history = TimelineStore().query("goal-actor", TimelineKind.GOAL)
    assert [g.name for g in history] == ["goal1", "goal2", "goal3"]
    assert bs.goal.name == "goal3"


def test_membership_history_preserved_across_leave_and_rejoin():
    registry = SocietyMembershipRegistry()
    registry.add("alice", "society-1", role="engineer")
    registry.remove("alice", "society-1", reason="left")
    registry.add("alice", "society-1", role="consultant")

    # remove() then add() creates a genuinely NEW membership_id (a
    # terminated membership's slot is free for a fresh one) — three rows:
    # the original active "engineer" row, its "terminated" supersession
    # (carrying the reason), and the new "consultant" membership.
    history = registry.history_for_actor("alice")
    assert len(history) == 3
    assert history[0].roles == ("engineer",)
    assert history[0].status == "active"
    assert history[0].end_time is not None
    assert history[1].roles == ("engineer",)
    assert history[1].status == "terminated"
    assert history[1].reason == "left"
    assert history[2].roles == ("consultant",)
    assert history[2].membership_id != history[0].membership_id
    assert history[2].is_open()
    assert registry.is_member("alice", "society-1")


# ── Cross-kind replay() ───────────────────────────────────────────────────


def test_replay_merges_all_kinds_sorted_by_time():
    store = TimelineStore()
    store.record(TimelineKind.GOAL, actor_id="replay-actor", name="g1", start_time=10.0)
    store.record(TimelineKind.PRESENCE, actor_id="replay-actor", space_id="s1", start_time=5.0)
    store.record(TimelineKind.EXECUTION, actor_id="replay-actor", goal="g1", start_time=15.0)

    engine = TimelineQueryEngine(store)
    entries = engine.replay("replay-actor")
    assert [type(e).__name__ for e in entries] == [
        "Presence",
        "GoalRecord",
        "ExecutionRecord",
    ]


def test_replay_respects_time_range():
    store = TimelineStore()
    store.record(TimelineKind.GOAL, actor_id="range-actor", name="early", start_time=1.0)
    store.record(TimelineKind.GOAL, actor_id="range-actor", name="late", start_time=100.0)

    engine = TimelineQueryEngine(store)
    entries = engine.replay("range-actor", since=50.0)
    assert len(entries) == 1
    assert entries[0].name == "late"


def test_current_state_derivation():
    store = TimelineStore()
    store.record(
        TimelineKind.GOAL,
        actor_id="cs-actor",
        name="active-goal",
        status="active",
        start_time=1.0,
    )
    store.record(
        TimelineKind.GOAL,
        actor_id="cs-actor",
        name="done-goal",
        status="completed",
        start_time=2.0,
    )

    engine = TimelineQueryEngine(store)
    state = engine.current_state("cs-actor")
    goal_names = [g["name"] for g in state["goals"]]
    assert "active-goal" in goal_names
    assert "done-goal" not in goal_names


# ── Independence: timeline entries don't interfere across kinds/actors ────


def test_timelines_independent_across_actors():
    store = TimelineStore()
    store.record(TimelineKind.GOAL, actor_id="actor-x", name="x-goal")
    store.record(TimelineKind.GOAL, actor_id="actor-y", name="y-goal")

    assert [g.name for g in store.query("actor-x", TimelineKind.GOAL)] == ["x-goal"]
    assert [g.name for g in store.query("actor-y", TimelineKind.GOAL)] == ["y-goal"]
