"""Operational Evidence v1 — Deterministic evidence collection and aggregation.

Instead of logs, produce structured evidence packages that enable:
- Pattern detection across executions
- Architecture evolution based on evidence
- Engineering KPIs and cognitive monitoring
"""

from .evidence_collector import EvidenceCollector
from .evidence_aggregator import EvidenceAggregator
from .evidence_package import EvidencePackage
from .cingulate_integration import CingulateEvidenceReview

__all__ = [
    "EvidenceCollector",
    "EvidenceAggregator",
    "EvidencePackage",
    "CingulateEvidenceReview",
]
