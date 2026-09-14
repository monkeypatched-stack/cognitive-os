"""ActorRuntime + WorldModelRuntime — the cognitive-OS runtime architecture (ADR 0021).

Asserts the dependency inversion (cognitive runtime injected INTO each actor), local
belief isolation (actor actions modify only the injected belief), and batch-only global
mutation (actor actions never change the global world).
"""

from __future__ import annotations

from src.monkey_brain.kernel.compile import (
    ActorRuntime,
    Context,
    WorldModelRuntime,
)


class _StubCognitive:
    """A stand-in reasoning engine injected into an actor runtime."""

    name = "stub"


def _world() -> WorldModelRuntime:
    wm = WorldModelRuntime()
    # global world changes ONLY via batch
    wm.batch_update(
        "acme",
        [
            {"src": "Hive", "dst": "Inspect", "domain": "beekeeping", "reward": 1.0},
            {"src": "Inspect", "dst": "Treat", "domain": "beekeeping", "reward": 1.0},
        ],
        source="seed",
    )
    return wm


def _actor(wm: WorldModelRuntime, actor_id="alice", tenant="acme") -> ActorRuntime:
    ctx = Context(tenant_id=tenant)
    return ActorRuntime(
        actor_id,
        cognitive_runtime=_StubCognitive(),  # dependency INVERTED: injected into the actor
        context=ctx,
        local_belief=wm.new_local_belief(),  # fresh local belief injected
        world_view=wm.view(ctx),  # read-only view of the global world
    )


def test_dependency_is_inverted():
    wm = _world()
    a = _actor(wm)
    # the cognitive runtime lives INSIDE the actor runtime, not the reverse
    assert isinstance(a.cognitive, _StubCognitive)
    assert a.summary()["cognitive"] == "_StubCognitive"


def test_actor_action_modifies_only_local_belief():
    wm = _world()
    a = _actor(wm)
    assert a.belief_size() == 0  # fresh belief
    before = wm.summary()["private_transitions"].get("acme", 0)

    a.act("Hive")  # take an action
    assert a.belief_size() > 0  # learned into LOCAL belief
    after = wm.summary()["private_transitions"].get("acme", 0)
    assert after == before  # GLOBAL world unchanged by the action


def test_global_changes_only_via_batch():
    wm = _world()
    rev0 = wm.summary()["private_transitions"]["acme"]
    a = _actor(wm)
    a.cognitive_cycle("Hive", "Treat")  # actor runs a full cycle
    # the actor discovered and learned — but only in its belief
    assert a.belief_size() > 0
    assert wm.summary()["private_transitions"]["acme"] == rev0  # global untouched
    # the ONLY way global grows is a batch
    wm.batch_update("acme", [{"src": "Treat", "dst": "Healthy", "domain": "beekeeping"}])
    assert wm.summary()["private_transitions"]["acme"] == rev0 + 1


def test_two_actors_diverge_own_beliefs():
    wm = _world()
    alice, bob = _actor(wm, "alice"), _actor(wm, "bob")
    alice.cognitive_cycle("Hive", "Treat")
    assert alice.belief_size() > 0 and bob.belief_size() == 0  # independent lifelong runtimes


def test_actor_only_sees_own_tenant_world():
    wm = _world()
    wm.batch_update("globex", [{"src": "Ticket", "dst": "Resolve", "domain": "support"}])
    alice = _actor(wm, "alice", tenant="acme")
    # alice's world view is acme-scoped — she cannot discover globex's transitions
    r = alice.act("Ticket")
    assert r["learned"] == []  # cross-tenant isolation holds


def test_observe_updates_runtime_state():
    wm = _world()
    a = _actor(wm)
    new_ctx = Context(tenant_id="acme")
    a.observe(new_ctx)
    assert a.context is new_ctx
