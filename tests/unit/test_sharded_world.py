"""ShardedWorldStore — out-of-core tenant worlds with bounded residency."""

from __future__ import annotations

from src.monkey_brain.kernel.compile.sharded_world import ShardedWorldStore


def test_each_tenant_is_a_separate_shard(tmp_path):
    store = ShardedWorldStore(tmp_path)
    store.get("acme").observe("A", "B", domain="d")
    store.get("globex").observe("X", "Y", domain="d")
    store.flush()
    shards = list(tmp_path.glob("*.json"))
    assert len(shards) == 2
    assert set(store.tenants()) == {"acme", "globex"}


def test_shards_reload_across_restart(tmp_path):
    s1 = ShardedWorldStore(tmp_path)
    s1.get("acme").observe("A", "B", domain="d")
    s1.flush()
    s2 = ShardedWorldStore(tmp_path)  # fresh store, cold memory
    assert s2.resident_count() == 0
    t = s2.get("acme")  # lazy-loaded from shard
    assert ("A", "B") in set(t)


def test_residency_is_bounded_and_evicted_tenants_persist(tmp_path):
    store = ShardedWorldStore(tmp_path, max_resident=2)
    for name in ("a", "b", "c", "d"):
        store.get(name).observe("S", "T", domain="d")
    assert store.resident_count() == 2  # memory bounded regardless of tenant count
    assert store.evictions() >= 2
    # an evicted tenant's data is durable and reloads intact
    reload = store.get("a")
    assert ("S", "T") in set(reload)


def test_cross_tenant_isolation(tmp_path):
    store = ShardedWorldStore(tmp_path, max_resident=8)
    store.get("t1").observe("P", "Q", domain="d")
    assert ("P", "Q") not in set(store.get("t2"))  # t2 never sees t1's edges


def test_hostile_tenant_id_cannot_escape_dir(tmp_path):
    store = ShardedWorldStore(tmp_path)
    store.get("../../etc/passwd").observe("A", "B", domain="d")
    store.flush()
    # every shard file stays inside the shard dir
    for p in tmp_path.rglob("*"):
        assert tmp_path in p.parents or p == tmp_path
    assert list(tmp_path.glob("*.json"))  # written safely, encoded name
