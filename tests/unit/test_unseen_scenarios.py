"""Generalization tests — UNSEEN actions by actors across many domains (ADR 0021).

An actor starts with an empty local graph and faces goals in domains it has never seen.
The framework must generalize: discover the path by exploring the world, fold it into its
own graph, and predict it next time — for ANY world, without hardcoding. Covered by ~20
concrete diverse-domain scenarios plus property-based random worlds.

Invariants that must hold for every unseen scenario:
  1. terminates and returns a result (never hangs / crashes)
  2. the global world is never mutated by an actor's actions
  3. every transition an actor learns actually exists in the world (local ⊆ global)
  4. executing ≥1 transition grows the actor's own graph (adaptation)
  5. repeating a deterministic task does not increase epistemic loss (convergence)
"""

from __future__ import annotations

import random

import pytest

from src.monkey_brain.kernel.compile import ActorNetwork, SparseTransitionTensor


def _world(transitions, domain="d"):
    W = SparseTransitionTensor()
    W.batch_update([{"src": a, "dst": b, "domain": domain, "reward": 1.0} for a, b in transitions])
    return W


def _chain(*states):
    return list(zip(states, states[1:]))


# ── 20 diverse, structurally-real domains (unseen to every fresh actor) ──────────

SCENARIOS = {
    "travel": (
        _chain("Search", "Book", "Hotel", "Pay", "Confirmed"),
        "Search",
        "Confirmed",
    ),
    "finance": (
        _chain("Open", "Deposit", "Invest", "Withdraw", "Closed"),
        "Open",
        "Closed",
    ),
    "healthcare": (
        _chain("Symptom", "Diagnose", "Prescribe", "Fill", "Recover"),
        "Symptom",
        "Recover",
    ),
    "cooking": (
        _chain("Ingredients", "Prep", "Cook", "Plate", "Serve"),
        "Ingredients",
        "Serve",
    ),
    "software": (
        _chain("Requirement", "Design", "Code", "Test", "Deploy"),
        "Requirement",
        "Deploy",
    ),
    "robotics": (_chain("Sense", "Plan", "Move", "Grasp", "Place"), "Sense", "Place"),
    "logistics": (
        _chain("Order", "Pick", "Pack", "Ship", "Deliver"),
        "Order",
        "Deliver",
    ),
    "hiring": (
        _chain("Apply", "Screen", "Interview", "Offer", "Hire"),
        "Apply",
        "Hire",
    ),
    "legal": (
        _chain("Complaint", "File", "Discovery", "Trial", "Judgment"),
        "Complaint",
        "Judgment",
    ),
    "manufacturing": (
        _chain("Order", "Produce", "QC", "Ship", "Invoice"),
        "Order",
        "Invoice",
    ),
    "support": (
        _chain("Ticket", "Triage", "Assign", "Resolve", "Close"),
        "Ticket",
        "Close",
    ),
    "education": (
        _chain("Enroll", "Study", "Exam", "Grade", "Graduate"),
        "Enroll",
        "Graduate",
    ),
    "incident": (
        _chain("Alert", "Ack", "Investigate", "Mitigate", "Postmortem"),
        "Alert",
        "Postmortem",
    ),
    "onboarding": (
        _chain("Signup", "Verify", "Configure", "Activate", "Live"),
        "Signup",
        "Live",
    ),
    "checkout": (
        _chain("Cart", "Address", "Payment", "Confirm", "Receipt"),
        "Cart",
        "Receipt",
    ),
    "research": (
        _chain("Question", "Hypothesis", "Experiment", "Analyze", "Publish"),
        "Question",
        "Publish",
    ),
    "farming": (_chain("Sow", "Water", "Grow", "Harvest", "Sell"), "Sow", "Sell"),
    "devops": (
        _chain("Commit", "Build", "Test", "Stage", "Release"),
        "Commit",
        "Release",
    ),
    "banking_loan": (
        _chain("Request", "Assess", "Approve", "Disburse", "Repay"),
        "Request",
        "Repay",
    ),
    "beekeeping": (
        _chain("Hive", "Inspect", "Detect", "Treat", "Healthy"),
        "Hive",
        "Healthy",
    ),
}


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_fresh_actor_generalizes_to_unseen_domain(name):
    transitions, start, goal = SCENARIOS[name]
    world = _world(transitions, domain=name)
    g_nnz, g_rev = world.nnz(), world.revision
    net = ActorNetwork(world)
    actor = net.add("agent")
    assert actor.world.nnz() == 0  # never seen this domain

    r1 = actor.cognitive_cycle(start, goal, world)  # discovers by exploring
    assert r1.reached_goal, f"{name}: did not reach goal via exploration"
    assert actor.world.nnz() > 0  # (4) adapted its own graph
    assert set(actor.world) <= set(world)  # (3) only learned real transitions
    assert (world.nnz(), world.revision) == (g_nnz, g_rev)  # (2) global unmutated

    r2 = actor.cognitive_cycle(start, goal, world)  # now knows the path
    assert r2.reached_goal
    assert r2.plan[-1] == goal  # plans the full learned route
    assert r2.epistemic_loss <= r1.epistemic_loss  # (5) convergence


# ── structural edge cases ────────────────────────────────────────────────────────


def test_branching_diamond_reaches_goal():
    world = _world([("A", "B"), ("A", "C"), ("B", "D"), ("C", "D")])
    actor = ActorNetwork(world).add("x")
    assert actor.cognitive_cycle("A", "D", world).reached_goal  # either branch reaches D


def test_cross_domain_chain():
    W = SparseTransitionTensor()
    W.observe("Weather", "CropYield", domain="weather", dst_domain="agri")
    W.observe("CropYield", "Supply", domain="agri", dst_domain="chain")
    W.observe("Supply", "Price", domain="chain", dst_domain="finance")
    W.observe("Price", "Revenue", domain="finance")
    actor = ActorNetwork(W).add("x")
    r = actor.cognitive_cycle("Weather", "Revenue", W)
    assert r.reached_goal  # crosses four domains
    assert set(actor.world) <= set(W)


def test_cycle_with_exit_terminates_and_reaches():
    # A→B→C, C loops back to A (0.3) or exits to Goal (0.7) → greedy exits
    W = SparseTransitionTensor()
    W.observe("A", "B")
    W.observe("B", "C")
    for _ in range(7):
        W.observe("C", "Goal")  # weight up the exit
    W.observe("C", "A")
    actor = ActorNetwork(W).add("x")
    r = actor.cognitive_cycle("A", "Goal", W)
    assert r.reached_goal  # terminates, exits the cycle


def test_unreachable_goal_does_not_crash():
    world = _world([("A", "B"), ("B", "C")])  # no path to Z
    actor = ActorNetwork(world).add("x")
    r = actor.cognitive_cycle("A", "Z", world)
    assert r.reached_goal is False  # honest failure, no crash
    assert set(actor.world) <= set(world)


def test_single_step_action():
    world = _world([("Start", "Done")])
    r = ActorNetwork(world).add("x").cognitive_cycle("Start", "Done", world)
    assert r.reached_goal and r.observed == [("Start", "Done")]


def test_long_chain_20_steps():
    states = [f"s{i}" for i in range(21)]
    world = _world(_chain(*states))
    r = ActorNetwork(world).add("x").cognitive_cycle("s0", "s20", world)
    assert r.reached_goal


def test_two_actors_same_unseen_task_diverge_then_both_learn():
    world = _world(SCENARIOS["logistics"][0], domain="logistics")
    net = ActorNetwork(world)
    a, b = net.add("a"), net.add("b")
    a.cognitive_cycle("Order", "Deliver", world)
    assert a.world.nnz() > 0 and b.world.nnz() == 0  # diverged
    b.cognitive_cycle("Order", "Deliver", world)
    assert set(a.world) == set(b.world)  # both converge to the true path


# ── property-based: many random unseen worlds ───────────────────────────────────


@pytest.mark.parametrize("seed", range(40))
def test_random_unseen_world_invariants(seed):
    rng = random.Random(seed)
    n = rng.randint(3, 9)
    states = [f"n{seed}_{i}" for i in range(n)]
    # a weighted spine guarantees a greedy route start→goal, plus random extra DAG edges
    trans = _chain(*states)
    W = SparseTransitionTensor()
    W.batch_update([{"src": a, "dst": b, "domain": f"dom{seed}", "reward": 1.0} for a, b in trans])
    for a, b in trans:  # weight the spine so greedy follows it
        for _ in range(3):
            W.observe(a, b, domain=f"dom{seed}")
    for _ in range(rng.randint(0, n)):  # random extra forward edges (still a DAG)
        i = rng.randint(0, n - 2)
        j = rng.randint(i + 1, n - 1)
        W.observe(states[i], states[j], domain=f"dom{seed}")

    g_nnz, g_rev = W.nnz(), W.revision
    actor = ActorNetwork(W).add("a")
    r1 = actor.cognitive_cycle(states[0], states[-1], W)

    assert r1.reached_goal  # spine dominates → reaches goal
    assert actor.world.nnz() > 0  # adapted
    assert set(actor.world) <= set(W)  # only real transitions
    assert (W.nnz(), W.revision) == (g_nnz, g_rev)  # global unmutated
    r2 = actor.cognitive_cycle(states[0], states[-1], W)
    assert r2.epistemic_loss <= r1.epistemic_loss  # convergence
