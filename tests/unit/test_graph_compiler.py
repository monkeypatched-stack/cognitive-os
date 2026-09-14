"""Unit tests for the execution-graph compiler (ADR 0021).

Pure module — no infra needed. Covers: row-stochastic compile, DAG semantics
preservation (path-sum + policy agreement), determinism, dangling policies,
coarsening, and the cyclic (fixed-point) verify regime.
"""

from __future__ import annotations

from src.monkey_brain.kernel.compile import (
    CompiledOperator,
    DanglingPolicy,
    GraphCompiler,
    SemanticGraphSnapshot,
)
from src.monkey_brain.kernel.compile.registry import (
    DomainOperatorRegistry,
)  # deprecated, imported directly


def _two_domain_graph() -> SemanticGraphSnapshot:
    # apiary: A→B ;  weather: W→R ;  cross-domain edge W→B
    nodes = [
        {"id": "A", "agent": "ApiaryRegistryAgent", "domain": "apiary"},
        {"id": "B", "agent": "HiveInspectionAgent", "domain": "apiary"},
        {"id": "W", "agent": "WeatherAgent", "domain": "weather"},
        {"id": "R", "agent": "RadarAgent", "domain": "weather"},
    ]
    edges = [
        {"from": "A", "to": "B"},  # intra apiary
        {"from": "W", "to": "R"},  # intra weather
        {"from": "W", "to": "B"},  # cross-domain
    ]
    strengths = {
        ("ApiaryRegistryAgent", "HiveInspectionAgent"): 0.8,
        ("WeatherAgent", "RadarAgent"): 0.5,
        ("WeatherAgent", "HiveInspectionAgent"): 0.3,
    }
    return SemanticGraphSnapshot(nodes=nodes, edges=edges, strengths=strengths, graph_id="multi", revision=3)


def _diamond() -> SemanticGraphSnapshot:
    # A → B, A → C, B → D, C → D   (a DAG diamond); D is a sink.
    nodes = [{"id": n, "agent": n} for n in ("A", "B", "C", "D")]
    edges = [
        {"from": "A", "to": "B"},
        {"from": "A", "to": "C"},
        {"from": "B", "to": "D"},
        {"from": "C", "to": "D"},
    ]
    strengths = {
        ("A", "B"): 0.9,
        ("A", "C"): 0.1,  # A strongly prefers B
        ("B", "D"): 0.7,
        ("C", "D"): 0.4,
    }
    return SemanticGraphSnapshot(nodes=nodes, edges=edges, strengths=strengths, graph_id="diamond", revision=1)


def test_compile_shapes_and_row_stochastic():
    op = GraphCompiler().compile(_diamond())
    assert op.n == 4
    # A has two out-edges, normalized to sum 1
    ai = op.index_of["A"]
    row = [op.data[p] for p in range(op.indptr[ai], op.indptr[ai + 1])]
    assert abs(sum(row) - 1.0) < 1e-12
    # A→B should carry 0.9/(0.9+0.1) = 0.9
    cols = {op.node_of[op.indices[p]]: op.data[p] for p in range(op.indptr[ai], op.indptr[ai + 1])}
    assert abs(cols["B"] - 0.9) < 1e-9 and abs(cols["C"] - 0.1) < 1e-9
    # D is a sink → dangling
    assert op.index_of["D"] in op.dangling


def test_verify_dag_semantics_preserved():
    g = _diamond()
    op = GraphCompiler().compile(g)
    report = GraphCompiler().verify(g, op)
    assert report.regime == "dag"
    assert report.row_stochastic
    assert report.max_path_sum_error < 1e-9
    assert report.policy_agreement == 1.0
    assert report.ok, report.failures


def test_determinism_hash():
    g = _diamond()
    a = GraphCompiler().compile(g)
    b = GraphCompiler().compile(g)
    assert a.compile_hash == b.compile_hash
    # A changed strength → different hash
    g2 = SemanticGraphSnapshot(
        nodes=g.nodes,
        edges=g.edges,
        strengths={**dict(g.strengths), ("A", "B"): 0.5},
        graph_id=g.graph_id,
        revision=2,
    )
    assert GraphCompiler().compile(g2).compile_hash != a.compile_hash


def test_recompile_equivalent_to_compile():
    g = _diamond()
    prev = GraphCompiler().compile(g)
    re = GraphCompiler().recompile(prev, g, dirty=[("A", "B")])
    assert re.compile_hash == prev.compile_hash
    assert list(re.data) == list(prev.data)


def test_default_prior_for_missing_strength():
    # Edge with no learned strength falls back to the neutral prior.
    nodes = [{"id": "X", "agent": "X"}, {"id": "Y", "agent": "Y"}]
    edges = [{"from": "X", "to": "Y"}]
    op = GraphCompiler().compile(SemanticGraphSnapshot(nodes=nodes, edges=edges, strengths={}))
    xi = op.index_of["X"]
    # single out-edge normalizes to 1.0 regardless of the prior value
    assert abs(op.data[op.indptr[xi]] - 1.0) < 1e-12


def test_dangling_uniform_policy():
    nodes = [{"id": "A", "agent": "A"}, {"id": "B", "agent": "B"}]
    edges = [{"from": "A", "to": "B"}]  # B is a sink
    op = GraphCompiler(dangling=DanglingPolicy.UNIFORM).compile(
        SemanticGraphSnapshot(nodes=nodes, edges=edges, strengths={("A", "B"): 1.0})
    )
    bi = op.index_of["B"]
    row = [op.data[p] for p in range(op.indptr[bi], op.indptr[bi + 1])]
    assert abs(sum(row) - 1.0) < 1e-12  # redistributed, stays stochastic


def test_coarsen_preserves_operator():
    g = _diamond()
    op = GraphCompiler().compile(g)
    # cluster {A,B} → 0, {C,D} → 1
    assignment = {"A": 0, "B": 0, "C": 1, "D": 1}
    m2 = GraphCompiler().coarsen(op, assignment)
    assert m2.n == 2
    # every non-dangling coarse row is row-stochastic
    for i in range(m2.n):
        if i in m2.dangling:
            continue
        total = sum(m2.data[p] for p in range(m2.indptr[i], m2.indptr[i + 1]))
        assert abs(total - 1.0) < 1e-9


def test_compile_by_domain_block_diagonal():
    g = _two_domain_graph()
    c = GraphCompiler()
    op_set = c.compile_by_domain(g)
    # one operator per domain, each independently verifiable
    assert set(op_set.domains) == {"apiary", "weather"}
    assert op_set.domains["apiary"].n == 2 and op_set.domains["weather"].n == 2
    for d, op in op_set.domains.items():
        assert c.verify(SemanticGraphSnapshot(nodes=[], edges=[]), op).regime in (
            "dag",
            "empty",
        )
    # the cross-domain edge W→B is not inside either block; it lives in the mesh
    assert op_set.cross_edges == 1
    assert op_set.mesh is not None and op_set.mesh.n == 2  # apiary, weather as domain-nodes
    assert op_set.node_domain["B"] == "apiary"


def test_domain_registry_collection():
    g = _two_domain_graph()
    c = GraphCompiler()
    reg = DomainOperatorRegistry()

    updated = reg.register_set(c.compile_by_domain(g), revision=g.revision)
    assert set(updated) == {"apiary", "weather"}
    assert set(reg.domains()) == {"apiary", "weather"}
    assert "apiary" in reg and len(reg) == 2
    assert reg.get("weather").n == 2
    assert reg.mesh() is not None

    # re-registering a domain bumps its version (latest-wins)
    reg.register_set(c.compile_by_domain(g))
    assert reg.version("apiary") == 2

    summary = reg.summary()
    assert summary["domains"] == 2 and summary["has_mesh"]


def test_domain_registry_persistence(tmp_path):
    g = _two_domain_graph()
    c = GraphCompiler()
    reg = DomainOperatorRegistry()
    reg.register_set(c.compile_by_domain(g), revision=3)

    p = tmp_path / "domains.json"
    reg.save(p)
    reg2 = DomainOperatorRegistry()
    reg2.load(p)
    assert set(reg2.domains()) == {"apiary", "weather"}
    # operator survives the round-trip byte-for-byte
    assert reg2.get("apiary").compile_hash == reg.get("apiary").compile_hash
    assert list(reg2.get("weather").data) == list(reg.get("weather").data)


def test_cyclic_verify_converges():
    # A ⇄ B cycle → cyclic regime, power iteration converges.
    nodes = [{"id": "A", "agent": "A"}, {"id": "B", "agent": "B"}]
    edges = [{"from": "A", "to": "B"}, {"from": "B", "to": "A"}]
    g = SemanticGraphSnapshot(nodes=nodes, edges=edges, strengths={("A", "B"): 1.0, ("B", "A"): 1.0})
    op = GraphCompiler().compile(g)
    report = GraphCompiler().verify(g, op)
    assert report.regime == "cyclic"
    assert report.converged
    assert report.ok
