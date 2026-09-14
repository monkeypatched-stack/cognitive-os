"""Tests for TrustKVStore — JSON file-backed trust storage."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.monkey_brain.persistence.trust_kv_store import TrustKVStore


class TestTrustKVStore:
    """TrustKVStore operations."""

    def test_save_and_load_edge(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = TrustKVStore(Path(tmpdir) / "trust.json")
            store.save_edge("alice", "bob", relationship="colleague", trust_score=0.8)

            edges = store.load_edges()
            assert len(edges) == 1
            assert edges[0]["src"] == "alice"
            assert edges[0]["dst"] == "bob"
            assert edges[0]["trust_score"] == 0.8

    def test_get_edge(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = TrustKVStore(Path(tmpdir) / "trust.json")
            store.save_edge("alice", "bob", trust_score=0.9)

            edge = store.get_edge("alice", "bob")
            assert edge is not None
            assert edge["trust_score"] == 0.9

    def test_get_nonexistent_edge(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = TrustKVStore(Path(tmpdir) / "trust.json")
            assert store.get_edge("alice", "bob") is None

    def test_revoke_edge(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = TrustKVStore(Path(tmpdir) / "trust.json")
            store.save_edge("alice", "bob")
            store.revoke_edge("alice", "bob", revoked_by="admin")

            # Revoked edge not returned by load_edges
            edges = store.load_edges()
            assert len(edges) == 0

            # But can still get it directly
            edge = store.get_edge("alice", "bob")
            assert edge is None  # get_edge filters revoked

    def test_update_reputation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = TrustKVStore(Path(tmpdir) / "trust.json")
            store.save_edge("alice", "bob", reputation=0.5)

            store.update_reputation("alice", "bob", 0.2)
            edge = store.get_edge("alice", "bob")
            assert edge["reputation"] == 0.7

    def test_reputation_bounds(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = TrustKVStore(Path(tmpdir) / "trust.json")
            store.save_edge("alice", "bob", reputation=0.1)

            store.update_reputation("alice", "bob", -0.5)
            edge = store.get_edge("alice", "bob")
            assert edge["reputation"] == 0.0  # Clamped to 0

    def test_get_edges_from(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = TrustKVStore(Path(tmpdir) / "trust.json")
            store.save_edge("alice", "bob")
            store.save_edge("alice", "charlie")

            edges = store.get_edges_from("alice")
            assert len(edges) == 2

    def test_get_edges_to(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = TrustKVStore(Path(tmpdir) / "trust.json")
            store.save_edge("alice", "bob")
            store.save_edge("charlie", "bob")

            edges = store.get_edges_to("bob")
            assert len(edges) == 2

    def test_persistence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "trust.json"

            # Save
            store1 = TrustKVStore(path)
            store1.save_edge("alice", "bob", trust_score=0.8)

            # Load in new instance
            store2 = TrustKVStore(path)
            edges = store2.load_edges()
            assert len(edges) == 1
            assert edges[0]["trust_score"] == 0.8

    def test_permission_scopes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = TrustKVStore(Path(tmpdir) / "trust.json")
            store.save_edge(
                "alice",
                "bob",
                permissions=["read", "write"],
                knowledge_scope=["manufacturing"],
                policy_scope=["internal"],
            )

            edge = store.get_edge("alice", "bob")
            assert edge["permissions"] == ["read", "write"]
            assert edge["knowledge_scope"] == ["manufacturing"]
            assert edge["policy_scope"] == ["internal"]

    def test_delegation_chain(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = TrustKVStore(Path(tmpdir) / "trust.json")
            store.save_edge(
                "alice",
                "bob",
                delegation_chain=["charlie", "diana"],
            )

            edge = store.get_edge("alice", "bob")
            assert edge["delegation_chain"] == ["charlie", "diana"]


class TestTrustKVStoreAsync:
    """Async compatibility with GraphStore interface."""

    def test_async_save_and_load(self):
        import asyncio

        with tempfile.TemporaryDirectory() as tmpdir:
            store = TrustKVStore(Path(tmpdir) / "trust.json")
            asyncio.run(store.save_trust_edge(src="alice", dst="bob", trust_score=0.8))

            edges = asyncio.run(store.load_trust_edges())
            assert len(edges) == 1
            assert edges[0]["trust_score"] == 0.8

    def test_async_revoke(self):
        import asyncio

        with tempfile.TemporaryDirectory() as tmpdir:
            store = TrustKVStore(Path(tmpdir) / "trust.json")
            asyncio.run(store.save_trust_edge(src="alice", dst="bob"))
            asyncio.run(store.revoke_trust_edge("alice", "bob", revoked_by="admin"))

            edges = asyncio.run(store.load_trust_edges())
            assert len(edges) == 0
