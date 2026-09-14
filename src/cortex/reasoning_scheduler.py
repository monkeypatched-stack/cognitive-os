"""reasoning_scheduler.py — Reasoning topology selection before execution backend.

A prompt is no longer text. It is a reasoning program:

    reasoning_program:
      strategy: graph_of_thought
      parallelism: parallel
      merge: constitution_vote
      max_branches: 8

The Reasoning Scheduler's job is not to pick a model first.
It is to pick a reasoning topology.

Only after selecting the topology does it choose the execution backend
(local Ollama, Claude, etc.).  This is consistent with the rest of the
Cognitive OS: classify the problem first, then choose the solver.

Reasoning topologies:

    CoT (Chain of Thought)
        Linear dependency chain — each step builds on the previous.
        Parallelism: sequential.  Inconsistency risk is high if parallelised.
        Examples: entity → value object → aggregate → repository → command → handler

    GoT (Graph of Thought)
        Independent subproblems that can be explored in parallel, then merged.
        Parallelism: parallel.  Merge step resolves conflicts.
        Examples: architecture synthesis (domain/app/infra/api in parallel),
                  adversarial agents, compliance constitutions, governance review.

    ToT (Tree of Thought)
        Candidate generation + pruning.  Branching factor K, depth D.
        Good for design-space exploration where you want diverse options.
        Examples: alternative service designs, refactoring strategies.

    Debate
        Two or more branches argue opposing positions.  A judge merges.
        Examples: trade-off analysis, security review vs. usability.

    Repair Loop
        Proposal → critique → repair → repeat until convergence.
        Examples: code generation with DDD check, simulation with adversarial pass.

Problem Structure → Topology dispatch table:

    Reasoning Task          Topology    Why
    ─────────────────────────────────────────────────────────────────────────
    Requirement extraction  CoT         Sequential semantic decomposition
    Ontology mapping        CoT         Deterministic classification
    DDD modeling            CoT         Build entities → aggregates sequentially
    Architecture synthesis  GoT         Bounded contexts are independent
    Code generation         CoT         Layer-by-layer deterministic
    Adversarial review      GoT         Parallel attack strategies
    Governance              GoT         Constitutions review independently
    Compliance              GoT         GDPR, SOC2, FDA, ISO are independent
    Simulation              GoT         Parallel solver execution
    Repair                  Repair Loop One coherent patch at a time
    Trade-off analysis      Debate      Competing views need reconciliation
    Design exploration      ToT         Branch, explore, prune
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Reasoning Topologies
# ---------------------------------------------------------------------------


class ReasoningTopology:
    CHAIN_OF_THOUGHT = "chain_of_thought"  # CoT — sequential, deterministic
    GRAPH_OF_THOUGHT = "graph_of_thought"  # GoT — parallel branches + merge
    TREE_OF_THOUGHT = "tree_of_thought"  # ToT — branching + pruning
    DEBATE = "debate"  # competing branches + judge
    REPAIR_LOOP = "repair_loop"  # propose → critique → repair


class MergeStrategy:
    CONSTITUTION_VOTE = "constitution_vote"  # each branch is a constitution; majority wins
    CONFIDENCE_WEIGHT = "confidence_weight"  # merge proportional to branch confidence
    FIRST_VALID = "first_valid"  # take the first branch that passes validation
    UNION = "union"  # collect all findings (adversarial style)
    JUDGE = "judge"  # LLM judge resolves between branches


class ExecutionBackend:
    LOCAL_OLLAMA = "ollama"
    CLAUDE_SONNET = "claude-sonnet-4-6"
    CLAUDE_OPUS = "claude-opus-4-8"
    CLAUDE_HAIKU = "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# ReasoningProgram — a prompt is computation, not text
# ---------------------------------------------------------------------------


@dataclass
class ReasoningProgram:
    """A structured reasoning program — replaces the flat prompt string.

    Instead of:
        prompt: "Generate the domain model for a work-order service"

    You have:
        reasoning_program:
          task: "Generate the domain model for a work-order service"
          strategy: chain_of_thought
          parallelism: sequential
          deterministic: true
          max_branches: 1
          solver: llm
          verifier: ddd
          repair: enabled

    The topology is selected before the backend.
    """

    task: str
    strategy: str = ReasoningTopology.CHAIN_OF_THOUGHT
    parallelism: str = "sequential"  # sequential | parallel
    deterministic: bool = True
    max_branches: int = 1
    merge: str = MergeStrategy.FIRST_VALID
    solver: str = "llm"
    verifier: str = ""
    repair: bool = False
    backend: str = ""  # set by scheduler; empty = auto-select
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_parallel(self) -> bool:
        return self.parallelism == "parallel" and self.max_branches > 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "strategy": self.strategy,
            "parallelism": self.parallelism,
            "deterministic": self.deterministic,
            "max_branches": self.max_branches,
            "merge": self.merge,
            "solver": self.solver,
            "verifier": self.verifier,
            "repair": self.repair,
            "backend": self.backend,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ReasoningProgram":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Problem → Topology dispatch table
# ---------------------------------------------------------------------------

# Each problem type maps to (topology, parallelism, merge, max_branches, repair)
_TOPOLOGY_DISPATCH: dict[str, dict[str, Any]] = {
    "requirement_extraction": {
        "strategy": ReasoningTopology.CHAIN_OF_THOUGHT,
        "parallelism": "sequential",
        "max_branches": 1,
        "merge": MergeStrategy.FIRST_VALID,
        "repair": False,
    },
    "ontology_mapping": {
        "strategy": ReasoningTopology.CHAIN_OF_THOUGHT,
        "parallelism": "sequential",
        "max_branches": 1,
        "merge": MergeStrategy.FIRST_VALID,
        "repair": False,
    },
    "ddd_modeling": {
        "strategy": ReasoningTopology.CHAIN_OF_THOUGHT,
        "parallelism": "sequential",
        "max_branches": 1,
        "merge": MergeStrategy.FIRST_VALID,
        "repair": True,
    },
    "architecture_synthesis": {
        "strategy": ReasoningTopology.GRAPH_OF_THOUGHT,
        "parallelism": "parallel",
        "max_branches": 5,
        "merge": MergeStrategy.CONSTITUTION_VOTE,
        "repair": True,
    },
    "code_generation": {
        "strategy": ReasoningTopology.CHAIN_OF_THOUGHT,
        "parallelism": "sequential",
        "max_branches": 1,
        "merge": MergeStrategy.FIRST_VALID,
        "repair": True,
    },
    "adversarial_review": {
        "strategy": ReasoningTopology.GRAPH_OF_THOUGHT,
        "parallelism": "parallel",
        "max_branches": 8,
        "merge": MergeStrategy.UNION,
        "repair": True,
    },
    "governance": {
        "strategy": ReasoningTopology.GRAPH_OF_THOUGHT,
        "parallelism": "parallel",
        "max_branches": 5,
        "merge": MergeStrategy.CONSTITUTION_VOTE,
        "repair": False,
    },
    "compliance": {
        "strategy": ReasoningTopology.GRAPH_OF_THOUGHT,
        "parallelism": "parallel",
        "max_branches": 4,
        "merge": MergeStrategy.CONSTITUTION_VOTE,
        "repair": False,
    },
    "simulation": {
        "strategy": ReasoningTopology.GRAPH_OF_THOUGHT,
        "parallelism": "parallel",
        "max_branches": 4,
        "merge": MergeStrategy.CONFIDENCE_WEIGHT,
        "repair": True,
    },
    "repair": {
        "strategy": ReasoningTopology.REPAIR_LOOP,
        "parallelism": "sequential",
        "max_branches": 1,
        "merge": MergeStrategy.FIRST_VALID,
        "repair": True,
    },
    "trade_off_analysis": {
        "strategy": ReasoningTopology.DEBATE,
        "parallelism": "parallel",
        "max_branches": 2,
        "merge": MergeStrategy.JUDGE,
        "repair": False,
    },
    "design_exploration": {
        "strategy": ReasoningTopology.TREE_OF_THOUGHT,
        "parallelism": "parallel",
        "max_branches": 6,
        "merge": MergeStrategy.CONFIDENCE_WEIGHT,
        "repair": True,
    },
}

# Backend selection based on topology + context
# GoT parallel branches benefit most from large-context models (Claude)
# CoT sequential tasks can run locally on Ollama
_BACKEND_DISPATCH: dict[str, str] = {
    ReasoningTopology.CHAIN_OF_THOUGHT: ExecutionBackend.LOCAL_OLLAMA,
    ReasoningTopology.TREE_OF_THOUGHT: ExecutionBackend.CLAUDE_SONNET,
    ReasoningTopology.GRAPH_OF_THOUGHT: ExecutionBackend.CLAUDE_SONNET,
    ReasoningTopology.DEBATE: ExecutionBackend.CLAUDE_SONNET,
    ReasoningTopology.REPAIR_LOOP: ExecutionBackend.CLAUDE_SONNET,
}


# ---------------------------------------------------------------------------
# ReasoningScheduler
# ---------------------------------------------------------------------------


class ReasoningScheduler:
    """Selects reasoning topology before execution backend.

    Usage:
        scheduler = ReasoningScheduler()
        program = scheduler.schedule("architecture_synthesis", task="Design order service")
        # program.strategy == "graph_of_thought"
        # program.backend  == "claude-sonnet-4-6"
        # program.max_branches == 5
        # program.merge == "constitution_vote"
    """

    def schedule(
        self,
        problem_type: str,
        *,
        task: str = "",
        force_backend: str = "",
        local_only: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> ReasoningProgram:
        """Classify problem → select topology → select backend.

        problem_type: key from the dispatch table (e.g. "adversarial_review")
        task:         the actual prompt/instruction to execute
        force_backend: override auto-selection (e.g. "ollama" for local testing)
        local_only:   if True, force all branches to LOCAL_OLLAMA
        """
        dispatch = _TOPOLOGY_DISPATCH.get(problem_type, _TOPOLOGY_DISPATCH["code_generation"])
        strategy = dispatch["strategy"]

        backend = force_backend
        if not backend:
            backend = (
                ExecutionBackend.LOCAL_OLLAMA
                if local_only
                else _BACKEND_DISPATCH.get(strategy, ExecutionBackend.CLAUDE_SONNET)
            )

        return ReasoningProgram(
            task=task,
            strategy=strategy,
            parallelism=dispatch["parallelism"],
            deterministic=(strategy == ReasoningTopology.CHAIN_OF_THOUGHT),
            max_branches=dispatch["max_branches"],
            merge=dispatch["merge"],
            repair=dispatch["repair"],
            backend=backend,
            metadata=metadata or {},
        )

    def classify(self, prompt: str) -> str:
        """Heuristic: classify a natural-language prompt into a problem type.

        Used when the caller doesn't explicitly provide a problem_type.
        In production this should be replaced by a fast LLM classifier
        or a regex + keyword dispatch.
        """
        p = prompt.lower()
        if any(k in p for k in ("generate", "create", "implement", "build", "write code")):
            return "code_generation"
        if any(k in p for k in ("architecture", "design", "bounded context", "microservice")):
            return "architecture_synthesis"
        if any(k in p for k in ("ddd", "domain model", "aggregate", "value object", "entity")):
            return "ddd_modeling"
        if any(k in p for k in ("adversarial", "attack", "flaw", "violation", "breaking")):
            return "adversarial_review"
        if any(k in p for k in ("comply", "compliance", "gdpr", "hipaa", "iso", "sox")):
            return "compliance"
        if any(k in p for k in ("govern", "policy", "constitution", "review")):
            return "governance"
        if any(k in p for k in ("simulate", "simulation", "what if", "scenario")):
            return "simulation"
        if any(k in p for k in ("trade-off", "tradeoff", "vs.", "versus", "compare")):
            return "trade_off_analysis"
        if any(k in p for k in ("fix", "repair", "patch", "correct", "resolve")):
            return "repair"
        if any(k in p for k in ("explore", "alternative", "option", "candidate")):
            return "design_exploration"
        if any(k in p for k in ("extract", "requirement", "need", "must", "shall")):
            return "requirement_extraction"
        return "code_generation"  # safe default

    def schedule_from_prompt(
        self,
        prompt: str,
        *,
        force_backend: str = "",
        local_only: bool = False,
    ) -> ReasoningProgram:
        """Classify + schedule in one step."""
        problem_type = self.classify(prompt)
        return self.schedule(
            problem_type,
            task=prompt,
            force_backend=force_backend,
            local_only=local_only,
        )
