"""Tests for the ActorModel  A ∈ ℝ^{L×M}  and the composition  A·W ∈ ℝ^{L×N}.

A is built from a user's observed actions; W is a slice of the observed world tensor.
The product is the effect of each action in the world. Nothing hardcoded.
"""

from __future__ import annotations

from src.monkey_brain.kernel.compile import (
    ActorModel,
    Feature,
    SparseTransitionTensor,
)


def _world() -> SparseTransitionTensor:
    # a small beekeeping world: Inspect → Detect → Treat → Healthy
    W = SparseTransitionTensor()
    W.observe("Inspect", "Detect", domain="beekeeping")
    W.observe("Detect", "Treat", domain="beekeeping")
    W.observe("Treat", "Healthy", domain="beekeeping")
    W.observe("Detect", "Isolate", domain="beekeeping")  # Detect can branch
    return W


def test_dimensions_L_M_N():
    world = _world()
    Wop = world.to_operator(domain="beekeeping", f=Feature.PROBABILITY)  # M×N
    actor = ActorModel("alice", world)
    actor.record("morning_check", "Inspect", domain="beekeeping")
    actor.record("respond_alert", "Detect", domain="beekeeping")
    # L actions, M world states
    assert actor.L == 2  # L = 2 actions
    M = len(world.states())  # M world states
    effect = actor.compose(Wop)  # A(L×M) · W(M×N) = L×N
    dense = effect.as_dense(actor.actions(), world.states())
    assert len(dense) == 2  # L rows
    assert all(len(row) == M for row in dense)  # N columns (== M here, square world)


def test_effect_of_action_is_downstream_state():
    world = _world()
    Wop = world.to_operator(domain="beekeeping", f=Feature.PROBABILITY)
    actor = ActorModel("alice", world)
    actor.record("morning_check", "Inspect")  # engages the Inspect state
    actor.record("respond_alert", "Detect")  # engages the Detect state

    effect = actor.compose(Wop, normalize=True)
    # morning_check engages Inspect → its effect is where Inspect leads: Detect
    assert "Detect" in effect.effect_of("morning_check")
    # respond_alert engages Detect → effect over Detect's successors: Treat and Isolate
    outs = effect.effect_of("respond_alert")
    assert "Treat" in outs and "Isolate" in outs
    assert abs(sum(outs.values()) - 1.0) < 1e-9  # normalized distribution over N


def test_action_weight_scales_effect():
    world = _world()
    Wop = world.to_operator(domain="beekeeping", f=Feature.PROBABILITY)
    actor = ActorModel("bob", world)
    # bob takes respond_alert engaging Detect twice — stronger incidence
    actor.record("respond_alert", "Detect", weight=1.0)
    actor.record("respond_alert", "Detect", weight=1.0)
    assert actor.weight("respond_alert", "Detect") == 2.0
    # un-normalized effect carries the weight through the composition
    raw = actor.compose(Wop, normalize=False)
    outs = raw.effect_of("respond_alert")
    # Detect→Treat and Detect→Isolate each had prob 0.5; ×2 weight → 1.0 each
    assert abs(outs["Treat"] - 1.0) < 1e-9 and abs(outs["Isolate"] - 1.0) < 1e-9


def test_multi_state_action_sums_contributions():
    world = _world()
    Wop = world.to_operator(domain="beekeeping", f=Feature.PROBABILITY)
    actor = ActorModel("carol", world)
    # one action that engages two source states → A·W sums both rows of W
    actor.record("full_sweep", "Inspect")
    actor.record("full_sweep", "Treat")
    effect = actor.compose(Wop, normalize=False).effect_of("full_sweep")
    assert "Detect" in effect  # from Inspect
    assert "Healthy" in effect  # from Treat


def test_state_not_in_world_slice_is_skipped():
    world = _world()
    Wop = world.to_operator(domain="beekeeping", f=Feature.PROBABILITY)
    actor = ActorModel("dan", world)
    actor.record("noop", "SomeOtherDomainState")  # not in this world slice
    effect = actor.compose(Wop)
    assert effect.effect_of("noop") == {}  # no effect — cleanly skipped
