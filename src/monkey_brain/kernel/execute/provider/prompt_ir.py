"""StructuredPromptIR — the intermediate representation between ETASSSpec and model backend.

The Prompt Compiler produces this. The Model Backend consumes it.
Nothing else touches it — the IR is the contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

REASONING_PREAMBLES: dict[str, str] = {
    "chain_of_thought": (
        "Think step-by-step. Number each reasoning step explicitly. "
        "Complete all steps before producing your final output."
    ),
    "reflection": (
        "Follow this cognitive loop:\n"
        "  1. OBSERVE — what is the current state?\n"
        "  2. CRITIQUE — what is wrong or suboptimal?\n"
        "  3. REVISE — how should it change?\n"
        "  4. VERIFY — does the revision satisfy the goal and constraints?\n"
        "Repeat until VERIFY passes."
    ),
    "graph_of_thought": (
        "Explore multiple paths before converging. For each decision point:\n"
        "  - Branch: list at least 2 alternative approaches\n"
        "  - Evaluate: score each branch (feasibility, risk, reward)\n"
        "  - Prune: eliminate dominated branches\n"
        "  - Converge: select the highest-scoring surviving branch\n"
        "Show the full exploration graph before your final answer."
    ),
    "debate": (
        "Before reaching a conclusion, run an internal debate:\n"
        "  ADVOCATE — make the strongest case FOR the proposed approach\n"
        "  CRITIC    — make the strongest case AGAINST it\n"
        "  JUDGE     — weigh the arguments, identify key trade-offs, decide\n"
        "State the final decision only after the debate is complete."
    ),
    "tree_of_thought": (
        "Build a tree of possible next steps. At each node:\n"
        "  - Generate 3 candidate continuations\n"
        "  - Score each (1-10) against the goal\n"
        "  - Expand only the top scorer\n"
        "Stop when you reach a complete, valid solution."
    ),
    "step_back": (
        "Before answering, step back and ask the higher-level question: "
        "what general principle, pattern, or law applies here? "
        "Answer that first, then apply it to the specific problem."
    ),
}


@dataclass
class StructuredPromptIR:
    """Compiled prompt ready for the model backend.

    Produced by PromptCompilerAgent. Consumed by ModelBackend.
    The compiled_prompt field is the final string sent to the LLM.
    """

    # Source
    workload: str
    goal: str
    domain: str
    reasoning: str

    # Compiled sections
    role: str = ""  # domain-aware role injection
    domain_ontology: str = ""  # auto-injected from DomainRegistry
    agent_catalog: str = ""  # matching agents from BrocaRegistry
    reasoning_preamble: str = ""  # from REASONING_PREAMBLES
    constraints_block: str = ""  # compiled constraints
    evidence_block: str = ""  # evidence sources
    success_criteria_block: str = ""  # success criteria
    constitutions_block: str = ""  # injected constitutions
    policies_block: str = ""  # injected policies
    outputs_block: str = ""  # expected output format

    # Resolved DDD agents for this spec
    resolved_agents: list[str] = field(default_factory=list)

    # The final assembled prompt string
    compiled_prompt: str = ""

    # Metadata
    metadata: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.compiled_prompt
