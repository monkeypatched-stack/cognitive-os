"""Aggregation Layer — cross-node analytics and knowledge.

Persists everything in Elasticsearch.
Provides full-text search, semantic search, analytics, and dashboards.

Aggregation never performs cognition.
Aggregation never controls execution.
Aggregation never executes pipelines.
"""

from deepdive.aggregation import Aggregation
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
