"""Planetary-scale test — the whole society on one network.

Builds the 7-tier runtime hierarchy (Personal → Community → Enterprise → Regional →
National → International → Global) and stresses the paths that must survive planetary
scale: a large multi-domain world tensor, Bellman throughput, neighbour queries, durable
persistence, delegated/transitive trust across tiers, and the belief-proposal pipeline
with backpressure. Each test asserts a PRODUCTION property, not just a behaviour — these
are the bars the review flagged.

Run:  python3 -m pytest tests/planetary/test_planetary_scale.py -q -s
"""

from __future__ import annotations

import time

import pytest

from src.monkey_brain.kernel.compile import (
    KnowledgeExchange,
    Relationship,
    SparseTransitionTensor,
    TrustNetwork,
    WorldModelRuntime,
)

# Scale knobs — kept CI-affordable but large enough to expose O(n²)/O(n·nnz) paths.
N_STATES = 4000
N_EDGES = 20000
N_BELLMAN = 20000


# ── the 7-tier hierarchy ─────────────────────────────────────────────────────────


def build_hierarchy(net: TrustNetwork) -> dict[str, list[str]]:
    """Create runtimes at every tier and connect them with delegating relationships,
    each tier reporting up to the one above (National delegates to Regional, …)."""
    tiers = {
        "global": ["earth"],
        "international": ["un", "iso", "w3c"],
        "national": ["gov-us", "gov-eu", "gov-jp"],
        "regional": ["ca", "ny", "bavaria"],
        "enterprise": ["acme", "university", "hospital"],
        "community": ["oss-proj", "family-smith", "school-12"],
        "personal": [f"person-{i}" for i in range(50)],
    }
    order = [
        "global",
        "international",
        "national",
        "regional",
        "enterprise",
        "community",
        "personal",
    ]
    # connect each tier down to a parent in the tier above (delegation edges)
    for upper, lower in zip(order, order[1:]):
        parents = tiers[upper]
        for k, child in enumerate(tiers[lower]):
            parent = parents[k % len(parents)]
            net.connect(parent, child, Relationship.ORGANIZATION, mutual=True)
    return tiers


# ── scale: world tensor ──────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def big_world() -> SparseTransitionTensor:
    w = SparseTransitionTensor()
    rng = _lcg(12345)
    trans = []
    for _ in range(N_EDGES):
        i = next(rng) % N_STATES
        j = next(rng) % N_STATES
        d = f"domain-{i % 40}"
        trans.append(
            {
                "src": f"s{i}",
                "dst": f"s{j}",
                "domain": d,
                "reward": (next(rng) % 100) / 100.0,
            }
        )
    t0 = time.perf_counter()
    w.batch_update(trans, source="planetary-seed")
    dt = time.perf_counter() - t0
    print(f"\n[world] built {w.nnz()} transitions over {len(w.states())} states in {dt * 1000:.0f} ms")
    return w


def test_world_build_scale(big_world):
    s = big_world.summary()
    print(f"[world] summary: {s}")
    assert s["transitions"] > 0 and s["states"] > 0


def test_bellman_throughput(big_world):
    """Bellman must be ~O(out-degree), not O(nnz). At scale a per-update full-cell scan
    (`_max_out_q`) collapses to a crawl — this asserts a real throughput floor."""
    edges = list(big_world)[:N_BELLMAN]
    t0 = time.perf_counter()
    for s, d in edges:
        big_world.bellman_update(s, d, reward=0.5)
    dt = time.perf_counter() - t0
    ups = len(edges) / dt
    print(f"[bellman] {len(edges)} updates in {dt * 1000:.0f} ms → {ups:,.0f} updates/s")
    assert ups > 20000, f"Bellman too slow ({ups:,.0f}/s) — O(nnz) scan in the hot path"


def test_successor_query_latency(big_world):
    """Neighbour lookup is the planning primitive; it must not scan the whole tensor."""
    states = big_world.states()[:200]
    t0 = time.perf_counter()
    for s in states:
        big_world.successors(s)
    dt = time.perf_counter() - t0
    per = dt / len(states) * 1e6
    print(f"[successors] {len(states)} queries, {per:.0f} µs/query")
    assert per < 500, f"successor query too slow ({per:.0f} µs) — full-tensor scan"


def test_persistence_is_atomic_and_roundtrips(big_world, tmp_path):
    p = tmp_path / "world.json"
    big_world.save(p)
    w2 = SparseTransitionTensor()
    w2.load(p)
    assert w2.nnz() == big_world.nnz()
    assert w2.summary()["states"] == big_world.summary()["states"]
    # a crash mid-write must never leave a half-written file readable as truth:
    # no sibling temp file should linger after a successful save
    leftovers = [q.name for q in tmp_path.iterdir() if q.name != "world.json"]
    assert leftovers == [], f"non-atomic save left temp artifacts: {leftovers}"


def test_remove_does_not_leak_state_index():
    """Revocation/withdrawal over a lifetime must not grow the state table unbounded."""
    w = SparseTransitionTensor()
    for i in range(500):
        w.observe(f"a{i}", f"b{i}", domain="d")
    before = w.summary()["states"]
    for i in range(500):
        w.remove(f"a{i}", f"b{i}")
    after = w.summary()["states"]
    print(f"[remove] states before={before} after removing all edges={after}")
    assert after == 0, f"state index leaked {after} orphaned states after full removal"


# ── planetary trust: delegation across tiers ─────────────────────────────────────


def test_hierarchical_delegated_trust():
    """A National runtime trusts a Regional one, which trusts an Enterprise. Authority
    must be delegable transitively along the reporting chain (the pyramid), with a bounded
    depth — not require an explicit edge between every pair on the planet."""
    net = TrustNetwork()
    build_hierarchy(net)
    # earth →(int'l)→ national →(regional)→ enterprise: no direct earth→enterprise edge
    assert net.edge("earth", "acme") is None
    # but authority should be reachable along the delegation chain
    assert net.permits_via("earth", "acme", Relationship.ORGANIZATION), (
        "delegated authority must flow down the hierarchy"
    )


def test_merge_queue_backpressure():
    """A hostile runtime must not be able to flood a recipient's merge queue into OOM."""
    net = TrustNetwork()
    net.connect("spammer", "victim", Relationship.MENTOR)
    ex = KnowledgeExchange(net, max_queue=100)
    wm = WorldModelRuntime()
    belief = wm.new_local_belief()
    accepted = 0
    for k in range(1000):
        prop = ex.publish("spammer", "belief", "api", [(f"A{k}", f"B{k}", 1.0)])
        res = ex.deliver(prop, "victim", belief)
        if res.status == "accepted":
            accepted += 1
    print(f"[backpressure] accepted {accepted} of 1000 before shed")
    assert ex.queue.pending() <= 100, "merge queue is unbounded — DoS risk"


# ── tiny deterministic PRNG (no numpy dependency in the hot loop) ────────────────


def _lcg(seed: int):
    x = seed
    while True:
        x = (1103515245 * x + 12345) & 0x7FFFFFFF
        yield x
