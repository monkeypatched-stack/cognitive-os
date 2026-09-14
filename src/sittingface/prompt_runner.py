"""Prompt Runner — executes compiled somatic prompts through MonkeyBrain Runtime."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class StepResult:
    """Result of executing a single CoT step."""

    step: int
    instruction: str
    constraint: str | None = None
    audit_gate: bool = False
    output: str = ""
    passed: bool = True
    violations: list[str] = field(default_factory=list)


@dataclass
class PromptResult:
    """Full result of executing a compiled prompt."""

    chart_name: str
    preamble: str = ""
    steps: list[StepResult] = field(default_factory=list)
    approved: bool = True
    rejection_reasons: list[str] = field(default_factory=list)
    generated_code: str = ""


class PromptRunner:
    """Executes compiled somatic prompts through the MonkeyBrain pipeline."""

    def __init__(self, runtime=None, policy=None, observer=None):
        self._runtime = runtime
        self._policy = policy
        self._observer = observer
        self._llm_client = None
        self._results: list[PromptResult] = []

    def set_llm_client(self, client):
        """Set the LLM client for code generation."""
        self._llm_client = client

    async def run_prompt(self, prompt: dict) -> PromptResult:
        """Execute a single compiled prompt through CoT steps."""
        chart_name = prompt.get("chart", "unknown")
        preamble = prompt.get("preamble", "")
        steps = prompt.get("steps", [])
        review_gate = prompt.get("review_gate", {})
        constraints = prompt.get("constraints", [])

        result = PromptResult(chart_name=chart_name, preamble=preamble)

        logger.info(f"Running prompt for {chart_name}: {len(steps)} steps, {len(constraints)} constraints")

        for step_data in steps:
            step_num = step_data.get("step", 0)
            instruction = step_data.get("instruction", "")
            constraint = step_data.get("constraint")
            audit_gate = step_data.get("auditGate", False)

            logger.info(f"  Step {step_num}: {instruction[:80]}...")

            # Execute the step
            step_result = StepResult(
                step=step_num,
                instruction=instruction,
                constraint=constraint,
                audit_gate=audit_gate,
            )

            if self._llm_client:
                try:
                    output = await self._execute_with_llm(
                        preamble=preamble,
                        instruction=instruction,
                        constraint=constraint,
                        chart_name=chart_name,
                        previous_results=[s.output for s in result.steps],
                    )
                    step_result.output = output
                except Exception as e:
                    step_result.output = f"ERROR: {e}"
                    step_result.passed = False

            # If audit gate, check constraint
            if audit_gate and constraint:
                step_result.passed = await self._check_constraint(constraint, step_result.output)

            result.steps.append(step_result)

            if not step_result.passed and audit_gate:
                result.violations.append(f"Step {step_num}: {constraint} failed")
                logger.warning(f"  Step {step_num} FAILED audit gate: {constraint}")

        # Review gate
        if review_gate:
            if result.violations:
                result.approved = False
                result.rejection_reasons = result.violations
            else:
                result.approved = True

        self._results.append(result)
        return result

    async def run_all(self, prompts: list[dict]) -> list[PromptResult]:
        """Execute all compiled prompts."""
        self._results = []
        for prompt in prompts:
            result = await self.run_prompt(prompt)
            self._results.append(result)
        return self._results

    async def _execute_with_llm(
        self,
        preamble: str,
        instruction: str,
        constraint: str | None,
        chart_name: str,
        previous_results: list[str],
    ) -> str:
        """Send instruction to LLM and get response."""
        context = f"Module: {chart_name}\nPreamble: {preamble}\n\n"
        if previous_results:
            context += "Previous steps:\n"
            for i, prev in enumerate(previous_results[-2:], 1):
                context += f"  {i}. {prev[:200]}\n"
        if constraint:
            context += f"\nConstraint to verify: {constraint}\n"

        prompt_text = f"{context}\nInstruction: {instruction}"

        if self._llm_client:
            return await self._llm_client.complete(prompt_text)

        return f"[generated] {instruction}"

    async def _check_constraint(self, constraint: str, output: str) -> bool:
        """Check if a constraint is satisfied by the output."""
        # For now, all constraints pass — real implementation checks AST/code
        return True

    def summary(self) -> dict:
        return {
            "total_prompts": len(self._results),
            "approved": len([r for r in self._results if r.approved]),
            "rejected": len([r for r in self._results if not r.approved]),
            "total_steps": sum(len(r.steps) for r in self._results),
            "total_violations": sum(len(r.violations) for r in self._results),
        }
