"""Per-actor cognitive cycle + persistence + batch world updates (ADR 0021).

Each actor plans / simulates / executes / learns over its OWN local graph, which adapts
to it. Two actors against the same world diverge because each learns from its own runs.
"""

from __future__ import annotations

from src.monkey_brain.kernel.compile import (
    ActorNetwork,
    Context,
    ContextChange,
    Feature,
    SparseTransitionTensor,
)


def _world() -> SparseTransitionTensor:
    W = SparseTransitionTensor()
    W.batch_update(
        [
            {"src": "Hive", "dst": "Inspect", "domain": "beekeeping", "reward": 1.0},
            {"src": "Inspect", "dst": "Detect", "domain": "beekeeping", "reward": 1.0},
            {"src": "Detect", "dst": "Treat", "domain": "beekeeping", "reward": 1.0},
        ],
        source="seed",
    )
    return W


def test_actor_cycle_reaches_goal_and_adapts_local_graph():
    world = _world()
    net = ActorNetwork(world)
    alice = net.add("alice")
    assert alice.world.nnz() == 0  # starts knowing nothing

    result = alice.cognitive_cycle("Hive", "Treat", world)
    # execution discovered the true path and folded it into alice's OWN graph
    assert alice.world.nnz() > 0
    assert result.observed[0] == ("Hive", "Inspect")
    assert result.reached_goal is True


def test_two_actors_diverge():
    world = _world()
    net = ActorNetwork(world)
    a, b = net.add("a"), net.add("b")
    # a works the beekeeping task; b never does
    a.cognitive_cycle("Hive", "Treat", world)
    assert a.world.nnz() > 0
    assert b.world.nnz() == 0  # b's local graph is different (empty)


def test_learning_lowers_epistemic_loss_over_repeats():
    world = _world()
    net = ActorNetwork(world)
    a = net.add("a")
    first = a.cognitive_cycle("Hive", "Treat", world)
    second = a.cognitive_cycle("Hive", "Treat", world)
    # after learning the path once, the second cycle predicts it correctly → loss drops
    assert second.epistemic_loss <= first.epistemic_loss


def test_world_persistence_roundtrip(tmp_path):
    world = _world()
    world.add_store("newstore", "grocery", [{"src": "Milk", "dst": "Cart"}])
    p = tmp_path / "world.json"
    world.save(p)
    restored = SparseTransitionTensor()
    restored.load(p)
    assert restored.revision == world.revision
    assert restored.nnz() == world.nnz()
    assert restored.feature("Hive", "Inspect", Feature.REWARD) == 1.0
    assert "grocery" in restored.domains()


def test_context_persistence_roundtrip(tmp_path):
    ctx = Context()
    ctx.submit(ContextChange("allow_domains", ["beekeeping"]))
    ctx.submit(ContextChange("max_cost", 3.0))
    ctx.apply_pending()
    p = tmp_path / "ctx.json"
    ctx.save(p)
    ctx2 = Context()
    ctx2.load(p)
    assert ctx2.constraints.max_cost == 3.0
    assert ctx2.constraints.allowed_domains == frozenset({"beekeeping"})


def test_batch_update_bumps_revision():
    W = SparseTransitionTensor()
    assert W.revision == 0
    W.batch_update([{"src": "A", "dst": "B"}])
    W.add_store("s", "d", [{"src": "C", "dst": "D"}])
    assert W.revision == 2
