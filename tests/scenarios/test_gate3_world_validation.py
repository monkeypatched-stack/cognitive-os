"""Gate 3 — World Validation engine (ADR-010).

Deliberately does NOT boot the full FastAPI app (TestClient(app) requires
real Mongo/Redis/Neo4j to be reachable — confirmed against this session's
own dev environment to inherit real, persisted state across process
restarts, which makes "assert zero violations on a fresh boot" an unsafe
assumption to hard-code, and CI's architecture-conformance workflow has no
such services configured at all). Instead constructs a PlanetaryRuntime
directly, the same lightweight, infra-free pattern
tests/test_wave3_planetary_world_convergence.py already uses successfully
in this exact CI job — validate_world() takes a PlanetaryRuntime, not an
app, so this is a strictly more direct test of the engine itself.

Two things this proves:

  1. A PlanetaryRuntime with one correctly-registered actor (via the
     canonical register_actor() workflow, which guarantees space+presence+
     membership consistency) has zero violations — the ten-category engine
     doesn't false-positive on ordinary, correctly-built state.
  2. A deliberately-corrupted world (a Membership record surviving after
     its actor is removed from the society) IS caught, with the right
     category and the right actor_id.
"""

from __future__ import annotations

from src.monkey_brain.kernel.society.domain import (
    ActorIdentity,
    ActorProfile,
    ActorType,
    Society,
)
from src.monkey_brain.kernel.society.integration import PlanetaryRuntime
from src.monkey_brain.kernel.validation.world_validator import validate_world

ALL_CATEGORIES = {
    "geography_tree",
    "society_hierarchy",
    "presence_consistency",
    "membership_consistency",
    "inventory_consistency",
    "graph_integrity",
    "orphaned_nodes",
    "cycles_forbidden",
    "duplicate_identifiers",
    "referential_integrity",
}


def _make_planetary_runtime() -> PlanetaryRuntime:
    return PlanetaryRuntime(Society(name="gate3-test-society"))


def test_correctly_registered_actor_has_no_violations():
    """Registering one actor through the canonical register_actor() workflow
    must not itself introduce any violation naming that actor — checked
    relative to this specific actor_id, not "the whole world is clean",
    because kernel/timeline's stores are real, shared, cross-process
    singletons in this environment (confirmed: even a bare
    PlanetaryRuntime() inherits this session's own accumulated dev-testing
    state) and CI's isolation level is not something this test can assume
    either way."""
    pr = _make_planetary_runtime()
    profile = ActorProfile(identity=ActorIdentity(name="Gate3FreshActor", actor_type=ActorType.HUMAN))

    state = pr.register_actor(profile)
    actor_id = state.actor_id

    report = validate_world(pr)
    assert set(report["categories"]) == ALL_CATEGORIES
    assert not any(v.get("actor_id") == actor_id for v in report["violations"]), report["violations"]


def test_dangling_membership_referencing_unknown_actor_is_caught():
    """A Membership record pointing at an actor_id that was never
    registered — the referential-integrity failure this session's own
    DELETE /actors REST sweeps against the live server actually produced
    (a Membership surviving an actor's removal), reproduced directly at
    the registry level rather than via actor removal: this environment's
    kernel/timeline stores turned out to be real, shared, cross-process
    persistent state (confirmed while writing this test — even a bare
    PlanetaryRuntime() picks up actors from unrelated earlier test runs),
    which makes "pop this actor from _actors and expect it gone" an
    unreliable way to construct the scenario. Writing the Membership
    record directly is both simpler and a more precise reproduction of
    "the reference is wrong," independent of that environment quirk."""
    import uuid

    pr = _make_planetary_runtime()
    society_id = pr._society_runtime.society.society_id
    fake_actor_id = f"gate3-nonexistent-{uuid.uuid4().hex}"

    record = pr.membership_registry.add(fake_actor_id, society_id, role="member")

    report = validate_world(pr)
    assert report["ok"] is False
    assert report["categories"]["membership_consistency"] >= 1
    assert any(
        v["type"] == "membership_invalid_actor"
        and v["actor_id"] == fake_actor_id
        and v["membership_id"] == record.membership_id
        for v in report["violations"]
    )


def test_unrelated_actors_own_dangling_membership_does_not_block_a_different_real_actor():
    """Real, live-discovered bug: Gate 3 used to block EVERY actor's
    request on ANY violation anywhere in the whole planet, including one
    completely unrelated actor's own permanently-orphaned membership
    record (found live: a throwaway pytest actor sharing the same dev
    Redis whose referenced space could never be reconciled). A real,
    correctly-registered actor's own request must not be blocked by
    someone else's stale data -- the violation itself must still be
    fully reported, just not treated as blocking for an unrelated actor.
    The unscoped, default call (actor_id=None, every other existing
    caller) keeps blocking on it, unchanged."""
    import uuid

    pr = _make_planetary_runtime()
    society_id = pr._society_runtime.society.society_id
    fake_actor_id = f"gate3-nonexistent-{uuid.uuid4().hex}"
    pr.membership_registry.add(fake_actor_id, society_id, role="member")

    real_actor_id = pr.register_actor(
        ActorProfile(identity=ActorIdentity(name="Gate3RealRequester", actor_type=ActorType.HUMAN)),
    ).actor_id

    # The violation is real and still fully reported...
    unscoped = validate_world(pr)
    assert unscoped["ok"] is False
    assert any(v.get("actor_id") == fake_actor_id for v in unscoped["violations"])

    # ...but a DIFFERENT, real actor's own request must not be blocked by it.
    scoped_to_real_actor = validate_world(pr, actor_id=real_actor_id)
    assert scoped_to_real_actor["ok"] is True
    assert scoped_to_real_actor["violation_count"] == unscoped["violation_count"]
    assert any(v.get("actor_id") == fake_actor_id for v in scoped_to_real_actor["violations"])

    # The affected actor's OWN request is still honestly blocked.
    scoped_to_fake_actor = validate_world(pr, actor_id=fake_actor_id)
    assert scoped_to_fake_actor["ok"] is False


def test_validate_world_report_shape_is_stable():
    """The report shape itself is a contract other tools (the /verify/world
    REST route, CI, future benchmark scenarios) all depend on — a category
    silently renamed or a key silently dropped is a real regression."""
    pr = _make_planetary_runtime()
    report = validate_world(pr)

    assert set(report.keys()) == {
        "ok",
        "violation_count",
        "violations",
        "categories",
        "checked_at",
    }
    assert report["violation_count"] == len(report["violations"])
    assert report["ok"] == (report["violation_count"] == 0)
    assert set(report["categories"]) == ALL_CATEGORIES
    assert sum(report["categories"].values()) == report["violation_count"]
