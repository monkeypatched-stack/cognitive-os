"""Prompt Compiler — bridges structured specifications and raw prompts.

Two input modes:
1. Structured Specification (YAML) → Compiler generates optimal prompt
2. Raw Prompt → MonkeyBrain executes directly (like raw SQL vs ORM)

The Prompt Compiler is a cognitive agent that:
- Parses input (structured YAML or raw text)
- Determines compilation strategy
- Generates optimal prompt using domain knowledge
- Caches successful prompt patterns

Usage:
    from domains.compiler import PromptCompiler

    compiler = PromptCompiler()

    # Mode 1: Structured specification
    spec = '''
    goal: self_healing
    domain: software_engineering
    bounded_context: architecture
    reasoning: graph_of_thought
    inputs:
      repository: .
    constraints:
      - preserve_public_api
      - fix_runtime_errors
      - no_manual_changes
    outputs:
      - evidence
      - diff_report
      - recommendations
    meta:
      runtime: stabilize
      max_iterations: 10
    '''
    prompt = compiler.compile(spec)

    # Mode 2: Raw prompt
    prompt = compiler.compile("Fix my runtime.")
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

logger = logging.getLogger("domains.compiler")


# ---------------------------------------------------------------------------
# Compiled Prompt
# ---------------------------------------------------------------------------


@dataclass
class CompiledPrompt:
    """A compiled prompt ready for MonkeyBrain execution.

    Includes all reasoning decisions made during compilation.
    """

    raw_input: str
    is_structured: bool
    goal: str = ""
    domain: str = ""
    bounded_context: str = ""
    reasoning: str = "chain_of_thought"
    inputs: dict[str, Any] = field(default_factory=dict)
    constraints: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    prompt_text: str = ""
    agent_level: str = "domain"

    # Reasoning decisions
    reasoning_strategy: str = "chain_of_thought"
    model: str = "gemma3:latest"
    context_scope: str = "full_repository"
    evidence_schema: str = "generic"
    constitution: str = "default_constitution"
    token_budget: int = 4096
    output_format: str = "yaml"
    use_reflection: bool = False
    cost_latency: str = "balanced"
    retry_strategy: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Prompt Templates
# ---------------------------------------------------------------------------

DOMAIN_AGENT_PROMPT = """You are the {domain} Domain Agent.

GOAL: {goal}

DOMAIN: {domain}
REASONING: {reasoning}

CONSTRAINTS:
{constraints_text}

INPUTS:
{inputs_text}

You are reasoning at the DOMAIN level — the highest abstraction.
Your job is to determine which BOUNDED CONTEXT should handle this goal.

Available bounded contexts:
{available_contexts}

Respond with:
1. Which bounded context should handle this
2. Why this context is appropriate
3. Any domain-level concerns or constraints

OUTPUT FORMAT:
```yaml
context: <selected_context>
reasoning: <why_this_context>
concerns: [<list_of_concerns>]
```"""


CONTEXT_AGENT_PROMPT = """You are the {context} Bounded Context Agent within the {domain} domain.

GOAL: {goal}
DOMAIN: {domain}
CONTEXT: {context}
REASONING: {reasoning}

CONSTRAINTS:
{constraints_text}

INPUTS:
{inputs_text}

You are reasoning at the CONTEXT level — a bounded business capability.
Your job is to determine which AGGREGATE should handle this goal.

Available aggregates in this context:
{available_aggregates}

Respond with:
1. Which aggregate should handle this
2. Why this aggregate is appropriate
3. Any context-level concerns

OUTPUT FORMAT:
```yaml
aggregate: <selected_aggregate>
reasoning: <why_this_aggregate>
concerns: [<list_of_concerns>]
```"""


AGGREGATE_AGENT_PROMPT = """You are the {aggregate} Aggregate Agent within the {context} context.

GOAL: {goal}
DOMAIN: {domain}
CONTEXT: {context}
AGGREGATE: {aggregate}
REASONING: {reasoning}

CONSTRAINTS:
{constraints_text}

INVARIANTS:
{invariants_text}

INPUTS:
{inputs_text}

You are reasoning at the AGGREGATE level — consistency and invariants.
Your job is to determine which ENTITY should handle this goal,
and verify that all invariants are satisfied.

Available entities in this aggregate:
{available_entities}

Respond with:
1. Which entity should handle this
2. Whether all invariants are satisfied
3. Any consistency concerns

OUTPUT FORMAT:
```yaml
entity: <selected_entity>
invariants_satisfied: <true|false>
concerns: [<list_of_concerns>]
```"""


ENTITY_AGENT_PROMPT = """You are the {entity} Entity Agent within the {aggregate} aggregate.

GOAL: {goal}
DOMAIN: {domain}
CONTEXT: {context}
AGGREGATE: {aggregate}
ENTITY: {entity}
REASONING: {reasoning}

CONSTRAINTS:
{constraints_text}

INPUTS:
{inputs_text}

You are reasoning at the ENTITY level — lifecycle and identity.
Your job is to:
1. Determine the current state of the entity
2. Determine what state transition is needed
3. Identify what value objects are required

Current entity state:
{entity_state}

Available value objects:
{available_value_objects}

Respond with:
1. Current state
2. Required state transition
3. Required value objects

OUTPUT FORMAT:
```yaml
current_state: <state>
target_state: <state>
value_objects: [<list_of_value_objects>]
```"""


VALUE_OBJECT_AGENT_PROMPT = """You are the {value_object} Value Object Agent.

GOAL: {goal}
DOMAIN: {domain}
CONTEXT: {context}
AGGREGATE: {aggregate}
ENTITY: {entity}
VALUE_OBJECT: {value_object}
REASONING: {reasoning}

CONSTRAINTS:
{constraints_text}

INPUTS:
{inputs_text}

You are reasoning at the VALUE OBJECT level — transformation and validation.
Your job is to:
1. Validate the input value
2. Apply any transformations
3. Return the validated/transformed value

Validation rules:
{validation_rules}

Respond with:
```yaml
valid: <true|false>
transformed_value: <value>
errors: [<list_of_errors>]
```"""


DOMAIN_EVIDENCE_PROMPT = """You are the {domain} Domain Evidence Adapter.

Given this ExecutionOutcome:
{outcome_json}

Interpret it as {domain}-specific evidence.

Respond with:
```yaml
domain: {domain}
evidence_type: <type>
entities: {<extracted_entities>}
compliance_flags: [<flags>]
```"""


# ---------------------------------------------------------------------------
# Prompt Compiler
# ---------------------------------------------------------------------------


class PromptCompiler:
    """A cognitive agent that reasons about compilation strategy.

    The Prompt Compiler is NOT a template engine.
    It makes reasoning decisions:
    - Which reasoning strategy? (GoT, CoT, ReAct, Tree-of-Thought)
    - Which model? (gemma3, qwen2.5-coder, claude-sonnet)
    - How much context? (full repository, diff only, last N changes)
    - Which evidence? (which domain evidence schema to use)
    - Which constitution? (which governance policies to enforce)
    - How much token budget? (cost vs quality tradeoff)
    - What output format? (YAML, JSON, markdown, structured report)
    - Reflection? (should the model review its own output)
    - GoT or CoT? (graph-of-thought vs chain-of-thought)
    - Cost vs latency? (cheap+slow vs expensive+fast)
    - Retry strategy? (how many retries, backoff, fallback model)

    These are reasoning decisions, not string formatting.
    """

    def __init__(self, domain_packages: dict | None = None):
        self._domain_packages = domain_packages or {}
        self._cache: dict[str, str] = {}
        self._templates = {
            "domain": DOMAIN_AGENT_PROMPT,
            "context": CONTEXT_AGENT_PROMPT,
            "aggregate": AGGREGATE_AGENT_PROMPT,
            "entity": ENTITY_AGENT_PROMPT,
            "value_object": VALUE_OBJECT_AGENT_PROMPT,
            "evidence": DOMAIN_EVIDENCE_PROMPT,
        }

    def compile(self, input_text: str) -> CompiledPrompt:
        """Compile input into a prompt.

        If structured YAML → parse and generate optimal prompt.
        If raw text → pass through as-is.

        The compiler makes reasoning decisions about:
        - Which reasoning strategy to use
        - Which model to target
        - How much context to include
        - Which evidence schema to apply
        - Token budget allocation
        - Output format
        """
        # Try to parse as YAML
        try:
            spec = yaml.safe_load(input_text)
            if isinstance(spec, dict) and "goal" in spec:
                return self._compile_structured(spec, input_text)
        except yaml.YAMLError:
            pass

        # Raw prompt — pass through
        return CompiledPrompt(
            raw_input=input_text,
            is_structured=False,
            prompt_text=input_text,
        )

    def _compile_structured(self, spec: dict, raw_input: str) -> CompiledPrompt:
        """Compile a structured specification into a prompt.

        Makes reasoning decisions about compilation strategy.
        """
        goal = spec.get("goal", "")
        domain = spec.get("domain", "")
        bounded_context = spec.get("bounded_context", "")
        reasoning = spec.get("reasoning", "chain_of_thought")
        inputs = spec.get("inputs", {})
        constraints = spec.get("constraints", [])
        outputs = spec.get("outputs", [])
        meta = spec.get("meta", {})

        # Determine agent level from spec
        agent_level = self._determine_agent_level(spec)

        # Get domain package
        pkg = self._domain_packages.get(domain)

        # ── Reasoning Decisions ──

        # 1. Which reasoning strategy?
        reasoning_strategy = self._select_reasoning_strategy(spec, pkg)

        # 2. Which model?
        model = self._select_model(spec, pkg)

        # 3. How much context?
        context_scope = self._select_context_scope(spec, pkg)

        # 4. Which evidence schema?
        evidence_schema = self._select_evidence_schema(pkg)

        # 5. Which constitution/policies?
        constitution = self._select_constitution(pkg)

        # 6. Token budget
        token_budget = self._select_token_budget(spec, pkg)

        # 7. Output format
        output_format = self._select_output_format(spec)

        # 8. Reflection?
        use_reflection = self._should_use_reflection(spec, pkg)

        # 9. Cost vs latency tradeoff
        cost_latency = self._select_cost_latency(spec, pkg)

        # 10. Retry strategy
        retry_strategy = self._select_retry_strategy(spec, pkg)

        # Build constraints text
        constraints_text = (
            "\n".join(f"- {c}" for c in constraints)
            if constraints
            else "- None specified"
        )

        # Build inputs text
        inputs_text = (
            "\n".join(f"- {k}: {v}" for k, v in inputs.items())
            if inputs
            else "- None specified"
        )

        # Get available options from domain package
        available_contexts = self._get_available_contexts(pkg)
        available_aggregates = self._get_available_aggregates(pkg, bounded_context)
        available_entities = self._get_available_entities(pkg, bounded_context)
        available_value_objects = self._get_available_value_objects(pkg)
        invariants_text = self._get_invariants(pkg, bounded_context)
        validation_rules = self._get_validation_rules(pkg)
        entity_state = "unknown (to be determined at runtime)"

        # Select template based on agent level
        template = self._templates.get(agent_level, DOMAIN_AGENT_PROMPT)

        # Generate prompt
        prompt_text = template.format(
            domain=domain or "unknown",
            context=bounded_context or "unknown",
            aggregate="unknown",  # determined at runtime
            entity="unknown",  # determined at runtime
            value_object="unknown",  # determined at runtime
            goal=goal,
            reasoning=reasoning_strategy,
            constraints_text=constraints_text,
            inputs_text=inputs_text,
            available_contexts=available_contexts,
            available_aggregates=available_aggregates,
            available_entities=available_entities,
            available_value_objects=available_value_objects,
            invariants_text=invariants_text,
            validation_rules=validation_rules,
            entity_state=entity_state,
            outcome_json="{}",  # determined at runtime
        )

        # Cache the compiled prompt
        cache_key = f"{domain}:{bounded_context}:{goal}"
        self._cache[cache_key] = prompt_text

        return CompiledPrompt(
            raw_input=raw_input,
            is_structured=True,
            goal=goal,
            domain=domain,
            bounded_context=bounded_context,
            reasoning=reasoning_strategy,
            inputs=inputs,
            constraints=constraints,
            outputs=outputs,
            meta=meta,
            prompt_text=prompt_text,
            agent_level=agent_level,
            reasoning_strategy=reasoning_strategy,
            model=model,
            context_scope=context_scope,
            evidence_schema=evidence_schema,
            constitution=constitution,
            token_budget=token_budget,
            output_format=output_format,
            use_reflection=use_reflection,
            cost_latency=cost_latency,
            retry_strategy=retry_strategy,
        )

    def _determine_agent_level(self, spec: dict) -> str:
        """Determine the agent level from the spec."""
        if spec.get("bounded_context"):
            return "context"
        if spec.get("aggregate"):
            return "aggregate"
        if spec.get("entity"):
            return "entity"
        if spec.get("value_object"):
            return "value_object"
        return "domain"

    # ── Reasoning Decisions ──

    def _select_reasoning_strategy(self, spec: dict, pkg) -> str:
        """Select reasoning strategy based on goal complexity."""
        goal = spec.get("goal", "")
        reasoning = spec.get("reasoning", "")

        if reasoning:
            return reasoning

        # Auto-select based on goal
        if "investigate" in goal or "analyze" in goal:
            return "graph_of_thought"
        if "fix" in goal or "heal" in goal or "stabilize" in goal:
            return "chain_of_thought"
        if "generate" in goal or "create" in goal:
            return "chain_of_thought"
        if "compare" in goal or "evaluate" in goal:
            return "tree_of_thought"

        return "chain_of_thought"

    def _select_model(self, spec: dict, pkg) -> str:
        """Select model based on task requirements."""
        meta = spec.get("meta", {})
        runtime = meta.get("runtime", "")

        # Map runtime to model
        model_map = {
            "stabilize": "gemma3:latest",  # fast, local
            "generate": "qwen2.5-coder:7b",  # code generation
            "analyze": "gemma3:latest",  # analysis
            "investigate": "gemma3:latest",  # investigation
        }

        return model_map.get(runtime, "gemma3:latest")

    def _select_context_scope(self, spec: dict, pkg) -> str:
        """Select how much context to include."""
        inputs = spec.get("inputs", {})

        if inputs.get("repository") == ".":
            return "full_repository"
        if inputs.get("diff"):
            return "diff_only"
        if inputs.get("last_n"):
            return f"last_{inputs['last_n']}_changes"

        return "full_repository"

    def _select_evidence_schema(self, pkg) -> str:
        """Select evidence schema from domain package."""
        if pkg and pkg.evidence_adapter:
            return pkg.name
        return "generic"

    def _select_constitution(self, pkg) -> str:
        """Select governance policies from domain package."""
        if pkg and pkg.policies:
            return f"{pkg.name}_constitution"
        return "default_constitution"

    def _select_token_budget(self, spec: dict, pkg) -> int:
        """Select token budget based on task complexity."""
        meta = spec.get("meta", {})
        max_iterations = meta.get("max_iterations", 1)

        # Scale budget with iterations
        base_budget = 4096
        return base_budget * max_iterations

    def _select_output_format(self, spec: dict) -> str:
        """Select output format."""
        outputs = spec.get("outputs", [])

        if "diff_report" in outputs:
            return "structured_report"
        if "evidence" in outputs:
            return "yaml"
        if "recommendations" in outputs:
            return "markdown"

        return "yaml"

    def _should_use_reflection(self, spec: dict, pkg) -> bool:
        """Determine if reflection should be used."""
        meta = spec.get("meta", {})
        max_iterations = meta.get("max_iterations", 1)

        # Use reflection for multi-iteration tasks
        return max_iterations > 1

    def _select_cost_latency(self, spec: dict, pkg) -> str:
        """Select cost vs latency tradeoff."""
        meta = spec.get("meta", {})
        runtime = meta.get("runtime", "")

        if runtime == "stabilize":
            return "low_cost"  # prioritize cost
        if runtime == "generate":
            return "balanced"
        if runtime == "analyze":
            return "low_latency"  # prioritize speed

        return "balanced"

    def _select_retry_strategy(self, spec: dict, pkg) -> dict:
        """Select retry strategy."""
        meta = spec.get("meta", {})
        max_iterations = meta.get("max_iterations", 1)

        return {
            "max_retries": min(max_iterations, 3),
            "backoff": "exponential",
            "fallback_model": "gemma3:latest",
        }

    def _get_available_contexts(self, pkg) -> str:
        """Get available bounded contexts from domain package."""
        if pkg and hasattr(pkg, "bounded_context") and pkg.bounded_context:
            return (
                "\n".join(f"- {c}" for c in pkg.bounded_context.aggregates)
                or "- Default context"
            )
        return "- Default context"

    def _get_available_aggregates(self, pkg, context: str) -> str:
        """Get available aggregates from domain package."""
        if pkg and pkg.aggregates:
            return "\n".join(f"- {a['name']}" for a in pkg.aggregates)
        return "- Default aggregate"

    def _get_available_entities(self, pkg, context: str) -> str:
        """Get available entities from domain package."""
        if pkg and pkg.entities:
            return "\n".join(f"- {e['name']}" for e in pkg.entities)
        return "- Default entity"

    def _get_available_value_objects(self, pkg) -> str:
        """Get available value objects from domain package."""
        if pkg and pkg.value_objects:
            return "\n".join(f"- {v['name']}" for v in pkg.value_objects)
        return "- Default value object"

    def _get_invariants(self, pkg, context: str) -> str:
        """Get invariants from domain package policies."""
        if pkg and pkg.policies:
            invariants = []
            for policy in pkg.policies:
                if "invariants" in policy:
                    invariants.extend(policy["invariants"])
            return (
                "\n".join(f"- {i}" for i in invariants)
                if invariants
                else "- No invariants specified"
            )
        return "- No invariants specified"

    def _get_validation_rules(self, pkg) -> str:
        """Get validation rules from domain package."""
        if pkg and pkg.policies:
            rules = []
            for policy in pkg.policies:
                if "validation_rules" in policy:
                    rules.extend(policy["validation_rules"])
            return (
                "\n".join(f"- {r}" for r in rules)
                if rules
                else "- No validation rules specified"
            )
        return "- No validation rules specified"

    def get_cached_prompt(self, domain: str, context: str, goal: str) -> str | None:
        """Get a cached compiled prompt."""
        cache_key = f"{domain}:{context}:{goal}"
        return self._cache.get(cache_key)
