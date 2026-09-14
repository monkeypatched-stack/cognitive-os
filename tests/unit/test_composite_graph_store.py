"""Tests for CompositeGraphStore — tensor + KV replacement for Neo4j."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from src.monkey_brain.kernel.compile.tensor import SparseTransitionTensor
from src.monkey_brain.persistence.composite_graph_store import CompositeGraphStore


class TestCompositeGraphStore:
    """CompositeGraphStore combines TensorGraphStore + TrustKVStore."""

    def test_is_connected(self):
        store = CompositeGraphStore()
        assert store.is_connected() is True

    def test_capability_composition(self):
        store = CompositeGraphStore()
        asyncio.run(store.save_capability_composition("pipeline", ["a", "b", "c"]))

        composition = asyncio.run(store.get_capability_composition("pipeline"))
        assert composition == ["a", "b", "c"]

    def test_provider_resolution(self):
        store = CompositeGraphStore()
        asyncio.run(store.register_capability_provider("n8n:Agent", "task", "n8n"))

        providers = asyncio.run(store.find_capability_providers("task"))
        assert len(providers) == 1

    def test_ontology(self):
        store = CompositeGraphStore()
        asyncio.run(store.link_capability_generalization("specific", "general"))

        general = asyncio.run(store.find_generalization("specific"))
        assert general == "general"

    def test_trust_operations(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = CompositeGraphStore(trust_path=Path(tmpdir) / "trust.json")
            asyncio.run(store.save_trust_edge(src="alice", dst="bob", trust_score=0.8))

            edges = asyncio.run(store.load_trust_edges())
            assert len(edges) == 1
            assert edges[0]["trust_score"] == 0.8

    def test_trust_revoke(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = CompositeGraphStore(trust_path=Path(tmpdir) / "trust.json")
            asyncio.run(store.save_trust_edge(src="alice", dst="bob"))
            asyncio.run(store.revoke_trust_edge("alice", "bob", revoked_by="admin"))

            edges = asyncio.run(store.load_trust_edges())
            assert len(edges) == 0

    def test_sync_graph(self):
        store = CompositeGraphStore()
        graph = {
            "nodes": [{"id": "n1", "name": "agent1"}],
            "edges": [{"from": "agent1", "to": "agent2"}],
        }
        asyncio.run(store.sync_graph(graph))
        assert store.tensor.nnz() >= 1

    def test_tensor_access(self):
        store = CompositeGraphStore()
        assert isinstance(store.tensor, SparseTransitionTensor)

    def test_close(self):
        store = CompositeGraphStore()
        asyncio.run(store.close())  # Should not raise


class TestCompositeGraphStoreIntegration:
    """Integration tests with real tensor."""

    def test_full_workflow(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = CompositeGraphStore(trust_path=Path(tmpdir) / "trust.json")

            # Capability composition
            asyncio.run(store.save_capability_composition("data_pipeline", ["extract", "transform"]))

            # Provider resolution
            asyncio.run(store.register_capability_provider("n8n:Agent", "invoicing", "n8n"))

            # Ontology
            asyncio.run(store.link_capability_generalization("SpecificAgent", "appointment"))

            # Trust
            asyncio.run(store.save_trust_edge(src="runtime_a", dst="runtime_b", trust_score=0.9))

            # Verify all operations worked
            composition = asyncio.run(store.get_capability_composition("data_pipeline"))
            assert composition == ["extract", "transform"]

            providers = asyncio.run(store.find_capability_providers("invoicing"))
            assert len(providers) == 1

            general = asyncio.run(store.find_generalization("SpecificAgent"))
            assert general == "appointment"

            edges = asyncio.run(store.load_trust_edges())
            assert len(edges) == 1
            assert edges[0]["trust_score"] == 0.9
