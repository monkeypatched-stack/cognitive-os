"""STABILITY-001..003 — presence/membership lifecycle qualification tests.

kernel/society/membership.py::SocietyMembershipRegistry.add() was a real
read-then-write with no compare-and-swap (existing = self._open_record_for
(...); if existing is not None: return existing; ... self._store.record
(...)) -- two concurrent callers could both observe "no existing record"
before either writes, producing a genuine duplicate_active_membership
violation. kernel/domains/grocery.py::try_reserve is this codebase's own
established pattern for exactly this shape of problem; TimelineStore
doesn't expose a KnowledgeGraph-style version/CAS primitive, so the real
fix is a real lock (kernel/society/membership.py::
SocietyMembershipRegistry._add_lock), mirroring PlanetaryRuntime.
_tick_lock's own precedent -- serializing add()/remove()'s check-then-write
against real concurrent callers (confirmed live this session: the
background _auto_tick_loop interleaves with real /prompt requests on the
same event loop).

A second, distinct real cause was also found and fixed: an actor
registered by one PlanetaryRuntime process/instance sharing the same Redis
as another (this session's own in-process pytest tests are one real,
confirmed example) never got reconciled into the OTHER process's in-memory
actor registry, since kernel/society/integration.py::PlanetaryRuntime.
_load_actors() was only ever called once, at __init__. world_validator.py
now calls the new, real PlanetaryRuntime.reconcile_actors_from_redis()
(a thin, idempotent wrapper around the existing _load_actors()) before
flagging anything -- real reconciliation, not a raised threshold and not
a periodic destructive reset.

Real Redis-backed tests (this session's own established convention,
confirmed via test_mb3015_inventory_reservation.py's own real-threads
precedent for testing a CAS/lock fix under genuine concurrency, not just
asyncio's cooperative single-thread model).
"""

from __future__ import annotations

import threading
import time
import uuid

import pytest

from src.monkey_brain.kernel.society.domain import (
    ActorIdentity,
    ActorProfile,
    ActorType,
)
from src.monkey_brain.kernel.society.integration import PlanetaryRuntime
from src.monkey_brain.kernel.society.membership import SocietyMembershipRegistry
from src.monkey_brain.kernel.validation.world_validator import validate_world


def _run_concurrent(fn, count: int) -> list:
    """Same real-OS-thread harness test_mb3015_inventory_reservation.py
    already established for genuinely stressing a CAS/lock fix -- real
    threads race, not asyncio's cooperative scheduling."""
    results = [None] * count

    def worker(i):
        results[i] = fn(i)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_stability001_concurrent_membership_add_never_duplicates():
    """300 real OS threads all call add() for the SAME (actor_id,
    society_id) pair concurrently -- exactly one real, active membership
    must result, never a duplicate_active_membership violation."""
    registry = SocietyMembershipRegistry()
    actor_id = f"stability_actor_{uuid.uuid4().hex[:8]}"
    society_id = f"stability_society_{uuid.uuid4().hex[:8]}"

    _run_concurrent(lambda i: registry.add(actor_id, society_id, role="member"), 300)

    active = [m for m in registry.active_memberships() if m.actor_id == actor_id and m.society_id == society_id]
    assert len(active) == 1
    memberships_for_pair = [
        r for r in registry.history_for_actor(actor_id) if r.society_id == society_id and r.is_open()
    ]
    assert len(memberships_for_pair) == 1


def test_stability002_concurrent_membership_add_no_cross_actor_contamination():
    """20 distinct actors, each with 15 concurrent add() calls for its OWN
    (actor_id, society_id) pair, all racing together in one real thread
    pool -- every actor ends with exactly its own one real membership, no
    duplicate, no actor receiving another actor's membership."""
    registry = SocietyMembershipRegistry()
    actor_count = 20
    per_actor_attempts = 15
    actor_ids = [f"stability_actor_{i}_{uuid.uuid4().hex[:6]}" for i in range(actor_count)]
    society_id = f"stability_shared_society_{uuid.uuid4().hex[:8]}"

    calls = [(actor_ids[i // per_actor_attempts]) for i in range(actor_count * per_actor_attempts)]

    def _add(i):
        return registry.add(calls[i], society_id, role="member")

    _run_concurrent(_add, len(calls))

    for actor_id in actor_ids:
        active = [m for m in registry.active_memberships() if m.actor_id == actor_id and m.society_id == society_id]
        assert len(active) == 1, f"actor {actor_id} has {len(active)} active memberships, expected exactly 1"


def test_stability003_actor_registered_by_another_process_is_reconciled_not_flagged():
    """Real cross-process/cross-instance scenario: pr_b boots (and loads
    whatever exists in the shared Redis at that moment) BEFORE pr_a
    registers a new actor into the SAME shared Redis -- pr_b's own
    _load_actors() already ran once, before the new actor existed, so
    without reconciliation pr_b's world-validation would see a real
    Presence/Membership record for an actor_id absent from its own
    sr.all_actors() and (incorrectly) flag it as corruption. Real fix:
    validate_world() now reconciles first."""
    pr_b = PlanetaryRuntime()  # boots first — its own _load_actors() sees nothing new yet

    pr_a = PlanetaryRuntime()  # shares the same real Redis
    club = pr_a.create_society("Stability Test Society", society_type="community")
    registered = pr_a.register_actor(
        ActorProfile(
            identity=ActorIdentity(
                name=f"Stability Actor {uuid.uuid4().hex[:8]}",
                actor_type=ActorType.HUMAN,
            )
        ),
        society_id=club.society.society_id,
    )

    # Give pr_a's own Redis writes a moment to land (real network I/O).
    time.sleep(0.2)

    report = validate_world(pr_b)
    presence_violations = [
        v
        for v in report.get("violations", [])
        if v.get("category") == "presence_consistency" and v.get("actor_id") == registered.actor_id
    ]
    membership_violations = [
        v
        for v in report.get("violations", [])
        if v.get("category") == "membership_consistency" and v.get("actor_id") == registered.actor_id
    ]
    assert presence_violations == [], f"actor should have been reconciled, not flagged: {presence_violations}"
    assert membership_violations == [], f"actor should have been reconciled, not flagged: {membership_violations}"

    # A genuinely orphaned actor_id (a real membership record with NO
    # corresponding real actor entity anywhere for reconciliation to find)
    # must still be correctly flagged -- reconciliation doesn't mean
    # validation lost its ability to detect real corruption.
    orphan_actor_id = f"orphan_actor_{uuid.uuid4().hex[:8]}"
    pr_b.membership_registry.add(orphan_actor_id, club.society.society_id, role="member")
    report_after_orphan = validate_world(pr_b)
    orphan_violations = [
        v
        for v in report_after_orphan.get("violations", [])
        if v.get("category") == "membership_consistency" and v.get("actor_id") == orphan_actor_id
    ]
    assert orphan_violations, "a genuinely orphaned membership record must still be flagged"
