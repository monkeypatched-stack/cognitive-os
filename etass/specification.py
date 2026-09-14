"""ETASSSpec — the canonical schema for every engineering workload.

Every workload — self-healing, chaos, SRE, architecture review, code generation —
uses the same interface. The Prompt Compiler generates the actual prompt.

Reasoning strategies by workload type:
  self_healing      → reflection
  chaos             → graph_of_thought
  sre               → chain_of_thought
  architecture_review → debate
  code_generation   → chain_of_thought
  governance_review → debate
  test_generation   → chain_of_thought
  security_scan     → chain_of_thought

DDD fields:
  domain            → "software_engineering" | "manufacturing" | ...
  bounded_context   → "production" | "ci_cd" | "code_review" | ...
  aggregate         → "batch" | "pull_request" | "work_order" | ...
  entity            → "lot" | "commit" | "worker" | ...
  value_object      → "recipe" | "branch_name" | "priority" | ...

The compiler injects the domain ontology, aggregate invariants,
value semantics, available agents, and relevant constitutions automatically.
The prompt author never writes any of that.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

REASONING_STRATEGIES = {
    "chain_of_thought",
    "reflection",
    "graph_of_thought",
    "debate",
    "tree_of_thought",
    "step_back",
}

WORKLOAD_REASONING_DEFAULTS: dict[str, str] = {
    # Engineering
    "self_healing": "reflection",
    "chaos": "graph_of_thought",
    "sre": "chain_of_thought",
    "architecture_review": "debate",
    "code_generation": "chain_of_thought",
    "governance_review": "debate",
    "test_generation": "chain_of_thought",
    "security_scan": "chain_of_thought",
    "performance": "chain_of_thought",
    "etass": "chain_of_thought",
    # Source control / spec lifecycle
    "source_control": "chain_of_thought",
    "spec_lifecycle": "chain_of_thought",
    "spec_sync": "chain_of_thought",
    "branch_strategy": "debate",
    "release": "chain_of_thought",
    # Manufacturing
    "batch_release": "debate",
    "work_order": "chain_of_thought",
    "change_control": "debate",
    # Default
    "default": "chain_of_thought",
}


@dataclass
class ETASSSpec:
    """The canonical ETASS workload specification.

    One schema for every workload. Prompt Compiler compiles this into
    a StructuredPromptIR ready for the model backend.
    """

    workload: str  # e.g. "self_healing", "code_generation"
    goal: str  # the engineering objective
    domain: str = "software_engineering"  # DDD domain
    bounded_context: str = ""  # DDD bounded context
    aggregate: str = ""  # DDD aggregate (optional)
    entity: str = ""  # DDD entity (optional)
    value_object: str = ""  # DDD value object (optional)

    # Reasoning strategy — defaults to workload-specific if not set
    reasoning: str = ""

    # Compiler-injectable blocks
    constraints: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    constitutions: list[str] = field(default_factory=list)
    policies: list[str] = field(default_factory=list)

    # Arbitrary extra context passed through to adapters
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.reasoning:
            self.reasoning = WORKLOAD_REASONING_DEFAULTS.get(self.workload, WORKLOAD_REASONING_DEFAULTS["default"])
        if self.reasoning not in REASONING_STRATEGIES:
            raise ValueError(
                f"Unknown reasoning strategy {self.reasoning!r}. Valid strategies: {sorted(REASONING_STRATEGIES)}"
            )

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_question(cls, question: str, intent: str = "default", **kwargs) -> "ETASSSpec":
        """Build a minimal spec from a free-form question string."""
        return cls(workload=intent, goal=question, **kwargs)

    @classmethod
    def from_dict(cls, data: dict) -> "ETASSSpec":
        """Build a spec from a plain dict (e.g. from JSON/YAML)."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    @classmethod
    def from_yaml(cls, path: str) -> "ETASSSpec":
        with open(path) as f:
            return cls.from_dict(yaml.safe_load(f))

    def to_dict(self) -> dict:
        from dataclasses import asdict

        return asdict(self)
