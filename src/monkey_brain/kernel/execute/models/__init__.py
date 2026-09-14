"""Executor domain types — shared across executor, routing, and learning modules."""

from __future__ import annotations

from enum import Enum
from typing import NamedTuple

SELF_HEALING_INTENT = "self_healing_workload"


class ExecutionMode(str, Enum):
    QUERY = "query"  # read-only — mutating goals (CREATE/UPDATE/DELETE) are refused
    EXECUTE = "execute"  # /execute — commits work, mutating goals are allowed
    SIMULATION = "simulation"
    PLANNING = "planning"
    QA_BACKGROUND = "qa_background"


class ExecutionResult(NamedTuple):
    """Typed, unpackable result — backward compatible with 4-tuple callers."""

    answer: str
    semantic_hits: list
    graph_paths: list
    llm_answered: bool
