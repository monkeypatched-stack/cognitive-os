"""Aggregation Layer — cross-node analytics and knowledge.

Persists everything in Elasticsearch.
Provides full-text search, semantic search, analytics, and dashboards.

Aggregation never performs cognition.
Aggregation never controls execution.
Aggregation never executes pipelines.
"""

from __future__ import annotations

from typing import Any

from deepdive.elasticsearch_adapter import ElasticsearchAdapter
from deepdive.fleet_analytics import FleetAnalytics, NodeMetrics, FleetMetrics
from deepdive.knowledge_aggregator import (
    KnowledgeAggregator,
    KnowledgeEntry,
    AggregatedKnowledge,
)
from deepdive.digital_twin_aggregator import (
    DigitalTwinAggregator,
    TwinSnapshot,
    AggregatedTwin,
)


class Aggregation:
    """Aggregation Layer for AgentOS.

    Responsibilities:
    - Fleet analytics
    - Knowledge aggregation
    - Digital twin aggregation
    - Long-term storage
    - Enterprise dashboards
    - Cross-site learning

    Aggregation never performs cognition.
    Aggregation never controls execution.
    Aggregation never executes pipelines.
    """

    def __init__(self, elasticsearch_url: str = "http://localhost:9200"):
        self.es = ElasticsearchAdapter(url=elasticsearch_url)
        self.fleet = FleetAnalytics(elasticsearch_adapter=self.es)
        self.knowledge = KnowledgeAggregator(elasticsearch_adapter=self.es)
        self.twins = DigitalTwinAggregator(elasticsearch_adapter=self.es)
        self._lemon = None

    def set_lemon(self, lemon) -> None:
        """Set the Lemon observability manager."""
        self._lemon = lemon

    async def connect(self) -> None:
        """Connect to Elasticsearch."""
        await self.es.connect()
        if self._lemon:
            self._lemon.health_check(
                "elasticsearch", "healthy" if self.es.is_connected() else "unhealthy"
            )

    async def disconnect(self) -> None:
        await self.es.disconnect()

    # --- Fleet Analytics ---

    def record_node_metrics(self, metrics: NodeMetrics) -> None:
        self.fleet.record_node_metrics(metrics)
        if self._lemon:
            self._lemon.counter("aggregation.node_metrics", node=metrics.node_id)

    def aggregate_fleet(self, fleet_id: str = "default") -> FleetMetrics:
        return self.fleet.aggregate_fleet(fleet_id)

    # --- Knowledge ---

    def add_knowledge(self, entry: KnowledgeEntry) -> None:
        self.knowledge.add_entry(entry)
        if self._lemon:
            self._lemon.counter(
                "aggregation.knowledge_entries", type=entry.knowledge_type
            )

    def aggregate_knowledge(self, knowledge_type: str) -> AggregatedKnowledge:
        return self.knowledge.aggregate(knowledge_type)

    # --- Digital Twins ---

    def add_twin_snapshot(self, snapshot: TwinSnapshot) -> None:
        self.twins.add_snapshot(snapshot)
        if self._lemon:
            self._lemon.counter(
                "aggregation.twin_snapshots", entity=snapshot.entity_type
            )

    def get_aggregated_twin(
        self, entity_type: str, entity_id: str
    ) -> AggregatedTwin | None:
        return self.twins.get_aggregated(entity_type, entity_id)

    # --- Persistence ---

    async def persist_all(self) -> dict[str, Any]:
        """Persist all aggregated data to Elasticsearch."""
        results = {}
        results["fleet"] = await self.fleet.persist_to_elasticsearch()
        results["knowledge"] = await self.knowledge.persist_to_elasticsearch()
        results["twins"] = await self.twins.persist_to_elasticsearch()
        return results

    # --- Search ---

    async def search(
        self, index: str, query: dict[str, Any], size: int = 100
    ) -> list[dict]:
        return await self.es.search(index, query, size)

    async def aggregate_query(self, index: str, aggs: dict[str, Any]) -> dict[str, Any]:
        return await self.es.aggregate(index, aggs)

    # --- Summary ---

    def summary(self) -> dict:
        return {
            "elasticsearch": {"connected": self.es.is_connected()},
            "fleet": self.fleet.summary(),
            "knowledge": self.knowledge.summary(),
            "twins": self.twins.summary(),
        }
