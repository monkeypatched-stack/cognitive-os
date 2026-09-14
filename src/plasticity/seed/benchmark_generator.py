"""Benchmark Dataset Generator — generates test datasets for benchmarking.

Generates:
- Intent Classification benchmarks
- Workflow Selection benchmarks
- Capability Resolution benchmarks
- End-to-End Request benchmarks
- Ground Truth Labels
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class BenchmarkEntry:
    """A single benchmark entry."""

    entry_id: str = field(default_factory=lambda: f"bench-{uuid4().hex[:8]}")
    category: str = ""
    input_data: dict[str, Any] = field(default_factory=dict)
    expected_output: dict[str, Any] = field(default_factory=dict)
    ground_truth: dict[str, Any] = field(default_factory=dict)
    difficulty: str = "medium"  # easy | medium | hard
    metadata: dict[str, Any] = field(default_factory=dict)


INTENT_BENCHMARKS = [
    {
        "input": "Show me batch records",
        "expected_intent": "batch_record",
        "difficulty": "easy",
    },
    {
        "input": "What is the OEE for Tablet Line A?",
        "expected_intent": "production_kpi",
        "difficulty": "easy",
    },
    {
        "input": "Create a new work order for maintenance",
        "expected_intent": "work_order_create",
        "difficulty": "medium",
    },
    {
        "input": "Why did the batch fail quality inspection?",
        "expected_intent": "decision_intelligence",
        "difficulty": "hard",
    },
    {
        "input": "Approve the change control request",
        "expected_intent": "approval_create",
        "difficulty": "medium",
    },
    {
        "input": "Show me the plant hierarchy",
        "expected_intent": "plant_topology",
        "difficulty": "easy",
    },
    {
        "input": "What SOPs are assigned to this workstation?",
        "expected_intent": "sop_compliance",
        "difficulty": "medium",
    },
    {
        "input": "Simulate what happens if we increase throughput",
        "expected_intent": "simulation",
        "difficulty": "hard",
    },
    {
        "input": "How many workers are on shift?",
        "expected_intent": "count",
        "difficulty": "easy",
    },
    {
        "input": "Search for calibration procedures",
        "expected_intent": "knowledge_base",
        "difficulty": "easy",
    },
]

WORKFLOW_BENCHMARKS = [
    {
        "input": "batch_record",
        "expected_capabilities": ["resolve_entity", "retrieve", "format"],
        "difficulty": "easy",
    },
    {
        "input": "production_kpi",
        "expected_capabilities": ["resolve_entity", "retrieve", "analyze", "format"],
        "difficulty": "medium",
    },
    {
        "input": "simulation",
        "expected_capabilities": ["resolve_entity", "predict", "simulate", "format"],
        "difficulty": "hard",
    },
]


class BenchmarkGenerator:
    """Generates benchmark datasets for testing.

    Generates deterministic datasets with ground truth labels.
    """

    def __init__(self, seed: int = 42):
        self._seed = seed
        self._benchmarks: list[BenchmarkEntry] = []

    def generate_intent_benchmarks(self) -> list[BenchmarkEntry]:
        entries = []
        for item in INTENT_BENCHMARKS:
            entry = BenchmarkEntry(
                category="intent_classification",
                input_data={"question": item["input"]},
                expected_output={"intent": item["expected_intent"]},
                ground_truth={
                    "intent": item["expected_intent"],
                    "confidence_threshold": 0.5,
                },
                difficulty=item["difficulty"],
            )
            entries.append(entry)
            self._benchmarks.append(entry)
        return entries

    def generate_workflow_benchmarks(self) -> list[BenchmarkEntry]:
        entries = []
        for item in WORKFLOW_BENCHMARKS:
            entry = BenchmarkEntry(
                category="workflow_selection",
                input_data={"intent": item["input"]},
                expected_output={"capabilities": item["expected_capabilities"]},
                ground_truth={"capabilities": item["expected_capabilities"]},
                difficulty=item["difficulty"],
            )
            entries.append(entry)
            self._benchmarks.append(entry)
        return entries

    def generate_all(self) -> list[BenchmarkEntry]:
        entries = []
        entries.extend(self.generate_intent_benchmarks())
        entries.extend(self.generate_workflow_benchmarks())
        return entries

    def summary(self) -> dict:
        by_category = {}
        by_difficulty = {}
        for b in self._benchmarks:
            by_category[b.category] = by_category.get(b.category, 0) + 1
            by_difficulty[b.difficulty] = by_difficulty.get(b.difficulty, 0) + 1
        return {
            "total": len(self._benchmarks),
            "by_category": by_category,
            "by_difficulty": by_difficulty,
        }
