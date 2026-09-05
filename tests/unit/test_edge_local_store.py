"""EdgeLocalStore + freshness classification (kernel/edge/local_store.py,
kernel/edge/freshness.py)."""
from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from src.monkey_brain.kernel.edge.freshness import (
    CacheProvenance,
    Freshness,
    classify_freshness,
    is_usable,
)
from src.monkey_brain.kernel.edge.local_store import EdgeLocalStore, EdgeLocalStoreError


@pytest.fixture()
def store(tmp_path):
    s = EdgeLocalStore(str(tmp_path / "edge.db"))
    yield s
    s.close()


class TestReadWrite:
    def test_put_then_get_round_trips(self, store):
        prov = CacheProvenance(source="neo4j:kg", expires_at=time.time() + 60)
        store.put("world_projection", "product:milk", {"price": 3.99}, prov)
        entry = store.get("world_projection", "product:milk")
        assert entry.value == {"price": 3.99}
        assert entry.provenance.source == "neo4j:kg"

    def test_get_missing_key_returns_none(self, store):
        assert store.get("world_projection", "nope") is None

    def test_list_returns_all_entries_in_namespace(self, store):
        prov = CacheProvenance(source="x")
        store.put("belief", "a", {"v": 1}, prov)
        store.put("belief", "b", {"v": 2}, prov)
        store.put("negotiation", "c", {"v": 3}, prov)
        assert {e.key for e in store.list("belief")} == {"a", "b"}

    def test_delete_removes_entry(self, store):
        prov = CacheProvenance(source="x")
        store.put("belief", "a", {"v": 1}, prov)
        store.delete("belief", "a")
        assert store.get("belief", "a") is None

    def test_clear_namespace_removes_only_that_namespace(self, store):
        prov = CacheProvenance(source="x")
        store.put("belief", "a", {"v": 1}, prov)
        store.put("negotiation", "b", {"v": 2}, prov)
        removed = store.clear_namespace("belief")
        assert removed == 1
        assert store.get("belief", "a") is None
        assert store.get("negotiation", "b") is not None


class TestBatchedReadWrite:
    def test_put_many_then_get_many_round_trips(self, store):
        prov = CacheProvenance(source="neo4j:kg", expires_at=time.time() + 60)
        store.put_many("world_projection", {
            "product:milk": ({"price": 3.99}, prov),
            "product:bread": ({"price": 2.50}, prov),
        })
        result = store.get_many("world_projection", ["product:milk", "product:bread", "product:missing"])
        assert set(result.keys()) == {"product:milk", "product:bread"}
        assert result["product:milk"].value == {"price": 3.99}

    def test_put_many_upserts_existing_keys(self, store):
        prov = CacheProvenance(source="neo4j:kg", expires_at=time.time() + 60)
        store.put("world_projection", "product:milk", {"price": 3.99}, prov)
        store.put_many("world_projection", {"product:milk": ({"price": 4.50}, prov)})
        assert store.get("world_projection", "product:milk").value == {"price": 4.50}

    def test_get_many_with_empty_keys_returns_empty(self, store):
        assert store.get_many("world_projection", []) == {}

    def test_put_many_with_empty_entries_is_a_noop(self, store):
        store.put_many("world_projection", {})
        assert store.list("world_projection") == []


class TestVersioningAndUpsert:
    def test_put_same_key_overwrites_not_appends(self, store):
        prov1 = CacheProvenance(source="x", version="v1")
        prov2 = CacheProvenance(source="x", version="v2")
        store.put("belief", "a", {"v": 1}, prov1)
        store.put("belief", "a", {"v": 2}, prov2)
        entry = store.get("belief", "a")
        assert entry.value == {"v": 2}
        assert entry.provenance.version == "v2"
        assert len(store.list("belief")) == 1


class TestExpirationAndFreshness:
    def test_missing_provenance_is_unknown(self):
        assert classify_freshness(None) == Freshness.UNKNOWN

    def test_fresh_within_expiry(self):
        prov = CacheProvenance(source="x", expires_at=time.time() + 100)
        assert classify_freshness(prov) == Freshness.FRESH

    def test_requires_authority_has_no_grace_window(self):
        prov = CacheProvenance(source="x", expires_at=time.time() - 1, freshness_requirement="requires_authority")
        assert classify_freshness(prov) == Freshness.STALE_MUST_REFRESH

    def test_requires_world_state_has_a_grace_window(self):
        prov = CacheProvenance(
            source="x", expires_at=time.time() - 10, freshness_requirement="requires_world_state",
        )
        assert classify_freshness(prov) == Freshness.STALE_BUT_USABLE

    def test_requires_world_state_past_grace_window_must_refresh(self):
        prov = CacheProvenance(
            source="x", expires_at=time.time() - 10_000, freshness_requirement="requires_world_state",
        )
        assert classify_freshness(prov) == Freshness.STALE_MUST_REFRESH

    def test_safe_offline_never_expires(self):
        prov = CacheProvenance(source="x", expires_at=time.time() - 999999, freshness_requirement="safe_offline")
        assert classify_freshness(prov) == Freshness.FRESH

    def test_revoked_epoch_overrides_unexpired_timestamp(self):
        prov = CacheProvenance(source="x", expires_at=time.time() + 1000, authority_epoch=1)
        assert classify_freshness(prov, current_authority_epoch=2) == Freshness.STALE_MUST_REFRESH

    def test_is_usable_matrix(self):
        assert is_usable(Freshness.FRESH) is True
        assert is_usable(Freshness.STALE_BUT_USABLE) is True
        assert is_usable(Freshness.STALE_MUST_REFRESH) is False
        assert is_usable(Freshness.UNKNOWN) is False


class TestCorruptionHandling:
    def test_corrupt_row_raises_rather_than_returning_bad_data(self, store, tmp_path):
        # Write a syntactically-corrupt JSON value directly, bypassing put().
        raw_conn = sqlite3.connect(str(tmp_path / "edge.db"))
        raw_conn.execute(
            "INSERT INTO cache_entries (namespace, key, value, provenance, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("belief", "corrupt", "{not valid json", "{}", time.time()),
        )
        raw_conn.commit()
        raw_conn.close()

        with pytest.raises(EdgeLocalStoreError):
            store.get("belief", "corrupt")

    def test_corrupt_entry_does_not_break_other_reads(self, store, tmp_path):
        prov = CacheProvenance(source="x")
        store.put("belief", "good", {"v": 1}, prov)
        raw_conn = sqlite3.connect(str(tmp_path / "edge.db"))
        raw_conn.execute(
            "INSERT INTO cache_entries (namespace, key, value, provenance, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("belief", "corrupt", "{not valid json", "{}", time.time()),
        )
        raw_conn.commit()
        raw_conn.close()

        assert store.get("belief", "good").value == {"v": 1}
        with pytest.raises(EdgeLocalStoreError):
            store.get("belief", "corrupt")


class TestRestartPersistence:
    def test_data_survives_reopening_the_same_db_file(self, tmp_path):
        db_path = str(tmp_path / "edge.db")
        prov = CacheProvenance(source="x", version="v1")

        store1 = EdgeLocalStore(db_path)
        store1.put("belief", "a", {"v": 1}, prov)
        store1.close()

        store2 = EdgeLocalStore(db_path)
        entry = store2.get("belief", "a")
        assert entry is not None
        assert entry.value == {"v": 1}
        store2.close()

    def test_sync_state_survives_restart(self, tmp_path):
        db_path = str(tmp_path / "edge.db")
        store1 = EdgeLocalStore(db_path)
        store1.set_sync_state("policy", last_epoch=7, cursor="c1")
        store1.close()

        store2 = EdgeLocalStore(db_path)
        epoch, _, cursor = store2.get_sync_state("policy")
        assert epoch == 7
        assert cursor == "c1"
        store2.close()


class TestConcurrentAccess:
    def test_concurrent_writes_from_multiple_threads_do_not_corrupt_the_store(self, store):
        errors = []

        def writer(n: int) -> None:
            try:
                prov = CacheProvenance(source="x", version=str(n))
                for i in range(20):
                    store.put("belief", f"key-{n}-{i}", {"v": i}, prov)
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(store.list("belief")) == 8 * 20

    def test_concurrent_read_and_write_do_not_raise(self, store):
        prov = CacheProvenance(source="x")
        store.put("belief", "shared", {"v": 0}, prov)
        errors = []
        stop = threading.Event()

        def reader() -> None:
            while not stop.is_set():
                try:
                    store.get("belief", "shared")
                except Exception as exc:  # pragma: no cover
                    errors.append(exc)

        def writer() -> None:
            for i in range(50):
                store.put("belief", "shared", {"v": i}, prov)

        t_reader = threading.Thread(target=reader)
        t_reader.start()
        writer()
        stop.set()
        t_reader.join()

        assert not errors
