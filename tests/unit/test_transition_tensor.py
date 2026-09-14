"""Tests for the SparseTransitionTensor world model W[d,i,j,f] (ADR 0021).

The tensor is populated dynamically from observed transitions — nothing about the
graph structure is hardcoded; domains and states are interned as executions are seen.
"""

from __future__ import annotations

from src.monkey_brain.kernel.compile import (
    Feature,
    GraphCompiler,
    SemanticGraphSnapshot,
    SparseTransitionTensor,
)

_EMPTY = SemanticGraphSnapshot(nodes=[], edges=[])  # verify() only needs strengths for policy-agreement (DAG)


def test_states_and_domains_interned_dynamically():
    W = SparseTransitionTensor()
    # feed transitions as an execution trace would — no predefined node list
    W.observe("Hive", "Inspect", domain="beekeeping", reward=1.0)
    W.observe("Inspect", "DetectDisease", domain="beekeeping", reward=0.8)
    W.observe("SearchFlights", "BookFlight", domain="travel", reward=0.9)
    assert set(W.domains()) == {"beekeeping", "travel"}
    assert "Hive" in W.states("beekeeping")
    assert W.nnz() == 3


def test_feature_vector_per_edge():
    W = SparseTransitionTensor()
    W.observe("A", "B", domain="d", reward=1.0, latency_ms=50, cost=2.0, confidence=0.9)
    W.observe("A", "B", domain="d", reward=0.0, latency_ms=150, cost=4.0, confidence=0.7)
    W.observe("A", "C", domain="d", reward=0.5)
    # edges carry a feature vector, not a scalar
    fv = W.feature_vector("A", "B")
    assert len(fv) == 9
    assert abs(W.feature("A", "B", Feature.REWARD) - 0.5) < 1e-9  # mean of 1.0, 0.0
    assert abs(W.feature("A", "B", Feature.LATENCY) - 100.0) < 1e-9  # mean of 50, 150
    assert abs(W.feature("A", "B", Feature.PROBABILITY) - 2 / 3) < 1e-9  # 2 of 3 A-transitions
    assert W.feature("A", "B", Feature.FREQUENCY) == 2.0
    assert W.feature("A", "B", Feature.UNCERTAINTY) < W.feature("A", "C", Feature.UNCERTAINTY)  # more evidence


def test_bellman_updates_sparse_subset():
    W = SparseTransitionTensor(learning_rate=0.5, discount=0.9)
    W.observe("S1", "S2", domain="d")
    before = W.feature("S1", "S2", Feature.Q_VALUE)
    W.bellman_update("S1", "S2", reward=1.0, next_best_q=0.0, domain="d")
    after = W.feature("S1", "S2", Feature.Q_VALUE)
    assert after > before  # Q moved toward reward
    assert W.feature("S1", "S2", Feature.FREQUENCY) == 1.0  # a different feature untouched


def test_slice_is_a_domain_transition_matrix():
    W = SparseTransitionTensor()
    W.observe("Lex", "Parse", domain="compiler")
    W.observe("Parse", "AST", domain="compiler")
    W.observe("Sun", "Rain", domain="weather")
    sl = W.slice("compiler", f=Feature.PROBABILITY)
    assert ("Lex", "Parse") in sl and ("Sun", "Rain") not in sl  # W[compiler,:,:]


def test_cross_domain_off_diagonal_and_world_operator():
    W = SparseTransitionTensor()
    # the weather → agriculture → supply → finance chain — crossing domains
    W.observe(
        "WeatherState",
        "CropYield",
        domain="weather",
        dst_domain="agriculture",
        reward=0.7,
    )
    W.observe("CropYield", "Supply", domain="agriculture", dst_domain="supply_chain")
    W.observe("Supply", "Price", domain="supply_chain", dst_domain="finance")
    W.observe("Price", "Revenue", domain="finance")
    cross = W.cross_domain_edges()
    assert len(cross) == 3  # three domain boundaries crossed
    # the whole world compiles into ONE giant sparse operator (block-diagonal + off-diagonal)
    world = W.to_operator(domain=None)
    rep = GraphCompiler().verify(_EMPTY, world)
    assert world.n == 5  # 5 states across 4 domains
    assert rep.regime in ("dag", "cyclic")


def test_slice_projects_to_operator():
    W = SparseTransitionTensor()
    W.observe("Hive", "Inspect", domain="beekeeping", reward=1.0)
    W.observe("Inspect", "Treat", domain="beekeeping", reward=0.8)
    op = W.to_operator(domain="beekeeping", f=Feature.PROBABILITY)
    # the beekeeping slice is now a CompiledOperator (CSR) ready for propagation
    assert op.n == 3
    assert GraphCompiler().verify(_EMPTY, op).regime == "dag"
