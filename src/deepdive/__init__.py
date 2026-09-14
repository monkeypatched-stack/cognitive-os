"""Aggregation Layer — cross-node analytics and knowledge.

Persists everything in Elasticsearch.
Provides full-text search, semantic search, analytics, and dashboards.

Aggregation never performs cognition.
Aggregation never controls execution.
Aggregation never executes pipelines.
"""

from __future__ import annotations

from src.deepdive.aggregation import Aggregation
from src.deepdive.elasticsearch_adapter import ElasticsearchAdapter
from src.deepdive.fleet_analytics import FleetAnalytics, NodeMetrics, FleetMetrics
from src.deepdive.knowledge_aggregator import (
    KnowledgeAggregator,
    KnowledgeEntry,
    AggregatedKnowledge,
)
from src.deepdive.digital_twin_aggregator import (
    DigitalTwinAggregator,
    TwinSnapshot,
    AggregatedTwin,
)

__all__ = [
    "Aggregation",
    "ElasticsearchAdapter",
    "FleetAnalytics",
    "NodeMetrics",
    "FleetMetrics",
    "KnowledgeAggregator",
    "KnowledgeEntry",
    "AggregatedKnowledge",
    "DigitalTwinAggregator",
    "TwinSnapshot",
    "AggregatedTwin",
]
