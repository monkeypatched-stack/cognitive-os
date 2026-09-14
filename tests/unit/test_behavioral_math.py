"""Behavioral-math validation harness.

These are NOT mechanics tests ("does frequency increment", "does it avoid touching the
tensor"). They assert that the math produces the RIGHT VALUES for known inputs — the
behavioral invariants that unit-mechanics tests miss.

This file exists because a comparator bug (a perfect prediction scored epistemic_loss 0.2
instead of 0.0, silently distorting every learning reward) passed all 1000+ mechanics tests
and only surfaced when the behavior was exercised directly. Each test here encodes an
invariant such a bug would violate. If one fails, a learning signal is wrong at the source.
"""

from __future__ import annotations

import asyncio

import pytest

from src.monkey_brain.kernel.comparator_runtime import ComparatorRuntime
from src.monkey_brain.kernel.policy.store import PolicyStore
from src.monkey_brain.kernel.compile.tensor import SparseTransitionTensor, Feature
from src.monkey_brain.kernel.compile.action_operator import ActionOperator
from src.monkey_brain.kernel.learn.world_learner import WorldLearner
from src.monkey_brain.kernel.learn.policy_learner import PolicyLearner


# ── helpers: build comparator inputs in the shapes /compare uses ────────────────
def _sim(nodes, ops, pstate, reward, grounding):
    return {
        "graph_id": "g",
        "nodes": [{"id": n} for n in nodes],
        "edges": [],
        "execution_order": [list(nodes)],
        "metadata": {
            "summary": {
                "predicted_state": pstate,
                "operations": ops,
                "events": [],
                "artifacts": [],
                "latency_ms": 100.0,
                "predicted_reward": reward,
                "grounding_score": grounding,
                "answer": "x",
            }
        },
    }


def _exec(nodes, ops, state, reward, confidence):
    return {
        "answer": "x",
        "state": state,
        "confidence": confidence,
        "reward": reward,
        "latency_ms": 100.0,
        "operations": ops,
        "events": [],
        "artifacts": [],
        "nodes": [{"id": n} for n in nodes],
        "edges": [],
        "execution_order": [list(nodes)],
        "graph_id": "g",
    }


def _loss(sim, ex):
    return asyncio.run(ComparatorRuntime().compare(sim, ex))


# ────────────────────────────────────────────────────────────────────────────────
# Comparator: loss must be honest and the world/policy split must not leak
# ────────────────────────────────────────────────────────────────────────────────
class TestComparatorLossInvariants:
    PS = {"nodes_complete": 2, "total_nodes": 2, "feasibility": "feasible"}
    OPS = ["a", "b"]

    def test_perfect_prediction_is_zero_loss(self):
        """A flawless prediction must score exactly 0 world/epistemic loss. (This is the
        invariant the confidence-diff sign bug violated — perfect scored 0.2.)"""
        r = _loss(
            _sim(["n1", "n2"], self.OPS, self.PS, 1.0, 0.9),
            _exec(["n1", "n2"], self.OPS, self.PS, 1.0, 0.9),
        )
        assert r.epistemic_loss == 0.0, f"perfect prediction scored epistemic_loss {r.epistemic_loss}"
        assert r.world_loss == 0.0, f"perfect prediction scored world_loss {r.world_loss}"
        assert r.policy_loss == 0.0, f"perfect prediction scored policy_loss {r.policy_loss}"

    def test_loss_is_monotonic(self):
        """Worse agreement must never score LOWER loss than better. The partial case is a
        reward-only miss (structure/state perfect) so it grades cleanly between perfect and a
        total miss rather than saturating world_loss — which a single node mismatch already
        maxes (the loss is deliberately sensitive)."""
        perfect = _loss(
            _sim(["n1", "n2"], self.OPS, self.PS, 1.0, 0.9),
            _exec(["n1", "n2"], self.OPS, self.PS, 1.0, 0.9),
        )
        partial = _loss(
            _sim(["n1", "n2"], self.OPS, self.PS, 1.0, 0.9),
            _exec(["n1", "n2"], self.OPS, self.PS, 0.5, 0.9),
        )  # reward off by 0.5 only
        total = _loss(
            _sim(["n1", "n2"], self.OPS, self.PS, 1.0, 0.9),
            _exec(
                ["xx", "yy"],
                ["other"],
                {"nodes_complete": 0, "total_nodes": 2, "feasibility": "infeasible"},
                0.0,
                0.0,
            ),
        )
        assert perfect.actor_loss < partial.actor_loss < total.actor_loss

    def test_reward_error_moves_policy_loss_only(self):
        """Changing ONLY the reward must move policy_loss and leave world_loss untouched —
        otherwise the world model learns from a policy signal (the split leaks)."""
        base = _loss(
            _sim(["n1", "n2"], self.OPS, self.PS, 1.0, 0.9),
            _exec(["n1", "n2"], self.OPS, self.PS, 1.0, 0.9),
        )
        reward_off = _loss(
            _sim(["n1", "n2"], self.OPS, self.PS, 1.0, 0.9),
            _exec(["n1", "n2"], self.OPS, self.PS, 0.0, 0.9),
        )  # reward mismatch only
        assert reward_off.policy_loss > base.policy_loss, "reward error did not raise policy_loss"
        assert reward_off.world_loss == base.world_loss, "reward error leaked into world_loss"

    def test_structure_error_moves_world_loss_only(self):
        """Changing ONLY the structure must move world_loss and leave policy_loss untouched."""
        base = _loss(
            _sim(["n1", "n2"], self.OPS, self.PS, 1.0, 0.9),
            _exec(["n1", "n2"], self.OPS, self.PS, 1.0, 0.9),
        )
        struct_off = _loss(
            _sim(["n1", "n2"], self.OPS, self.PS, 1.0, 0.9),
            _exec(["n1", "zz"], ["a"], self.PS, 1.0, 0.9),
        )  # node/op mismatch, same reward
        assert struct_off.world_loss > base.world_loss, "structure error did not raise world_loss"
        assert struct_off.policy_loss == base.policy_loss, "structure error leaked into policy_loss"


# ────────────────────────────────────────────────────────────────────────────────
# Q-learning: values must CONVERGE to the right target, not just move once
# ────────────────────────────────────────────────────────────────────────────────
class TestQLearningConvergence:
    def test_terminal_q_converges_to_reward(self):
        """A terminal (state,action) Q must converge to the reward, bounded — not diverge
        toward r/(1-γ)."""
        ps = PolicyStore(lr=0.1, discount=0.95)
        for _ in range(500):
            ps.update("s", "a", 0.8)  # terminal (no next_state)
        assert abs(ps.value("s", "a") - 0.8) < 0.02
        assert 0.0 <= ps.value("s", "a") <= 1.0

    def test_higher_reward_yields_higher_q(self):
        ps = PolicyStore(lr=0.1, discount=0.95)
        for _ in range(500):
            ps.update("s", "good", 0.9)
            ps.update("s", "bad", 0.1)
        assert ps.value("s", "good") > ps.value("s", "bad")

    def test_bootstrap_uses_discounted_future(self):
        """Non-terminal Q must reflect r + γ·max Q(next): a step before a high-value state
        is worth more than its own immediate reward."""
        ps = PolicyStore(lr=0.1, discount=0.95)
        for _ in range(500):
            ps.update("s1", "go", 0.0, next_state="s2")  # bootstraps off s2
            ps.update("s2", "go", 1.0)  # terminal, ~1.0
        # Q(s1,go) ≈ 0 + 0.95·Q(s2,go) ≈ 0.95, strictly above its own reward (0)
        assert ps.value("s1", "go") > 0.5


# ────────────────────────────────────────────────────────────────────────────────
# Sparse transition compilation: the operator must be a valid stochastic matrix
# ────────────────────────────────────────────────────────────────────────────────
class TestTransitionCompilation:
    def test_operator_rows_are_stochastic(self):
        """Every non-terminal row of M = D⁻¹W must sum to exactly 1.0."""
        t = SparseTransitionTensor()
        for _ in range(3):
            t.observe("a", "b")
        t.observe("a", "c")  # a → b:3, c:1
        for _ in range(2):
            t.observe("b", "c")
        op = t.to_operator(f=Feature.PROBABILITY)
        for i in range(len(op.node_of)):
            row = [op.data[p] for p in range(op.indptr[i], op.indptr[i + 1])]
            if row:  # non-dangling
                assert abs(sum(row) - 1.0) < 1e-9

    def test_probability_matches_frequency(self):
        """P(b|a) must equal the observed frequency ratio."""
        t = SparseTransitionTensor()
        for _ in range(3):
            t.observe("a", "b")
        t.observe("a", "c")
        assert abs(t.feature("a", "b", Feature.PROBABILITY) - 0.75) < 1e-9
        assert abs(t.feature("a", "c", Feature.PROBABILITY) - 0.25) < 1e-9


# ────────────────────────────────────────────────────────────────────────────────
# Action operators: masks over the shared world, no duplicated transitions
# ────────────────────────────────────────────────────────────────────────────────
class TestActionOperatorMask:
    def _world(self):
        w = SparseTransitionTensor()
        for _ in range(4):
            w.observe("s0", "s1")
        w.observe("s0", "s2")  # s0 → s1:4, s2:1
        return w

    def test_apply_is_rownorm_of_mask_times_world(self):
        """P(S'|S,a) = rownorm(mask_a ⊙ W[S]) — and it must sum to 1."""
        w = self._world()
        op = ActionOperator("go", w)
        op.enable("s0", "s1")
        op.enable("s0", "s2")
        dist = op.apply("s0")
        assert abs(sum(dist.values()) - 1.0) < 1e-9
        # weights are mask×world_prob, renormalized → same 4:1 ratio the world holds
        assert abs(dist["s1"] - 0.8) < 1e-9 and abs(dist["s2"] - 0.2) < 1e-9

    def test_different_actions_give_different_distributions(self):
        """The whole point of the action axis: two actions from one state differ."""
        w = self._world()
        a1 = ActionOperator("a1", w)
        a1.enable("s0", "s1")
        a2 = ActionOperator("a2", w)
        a2.enable("s0", "s2")
        assert a1.apply("s0") != a2.apply("s0")

    def test_action_references_only_world_transitions(self):
        """A mask cannot invent a transition the world does not hold."""
        w = self._world()
        op = ActionOperator("bad", w)
        op.enable("s0", "ghost")  # not in the world
        assert "ghost" not in op.apply("s0")


# ────────────────────────────────────────────────────────────────────────────────
# World/policy learning isolation (the architectural invariant)
# ────────────────────────────────────────────────────────────────────────────────
class TestLearningIsolation:
    def test_world_learning_never_writes_q(self):
        """WorldLearner updates frequency only — Q stays at whatever it was."""
        t = SparseTransitionTensor()
        t.bellman_update("s1", "s1", 0.9)  # seed a Q on the self-loop
        q_before = t.feature("s1", "s1", Feature.Q_VALUE)
        WorldLearner(t).observe_transition("s1", "s2")
        assert t.feature("s1", "s1", Feature.Q_VALUE) == q_before
        assert t.feature("s1", "s2", Feature.FREQUENCY) == 1.0

    def test_policy_learning_never_writes_frequency(self):
        """PolicyLearner updates Q only — the world tensor's frequencies are untouched."""
        t = SparseTransitionTensor()
        t.observe("s1", "s2")
        freq_before = t.feature("s1", "s2", Feature.FREQUENCY)
        PolicyLearner(PolicyStore()).update("s1", "act", 1.0, "s2")
        assert t.feature("s1", "s2", Feature.FREQUENCY) == freq_before
