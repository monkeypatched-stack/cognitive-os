"""PromptCompilerAgent — the canonical implementation of ETASS.

Every engineering workload goes through this agent:
  ETASSSpec → PromptCompilerAgent → StructuredPromptIR → ModelBackend

The compiler:
  1. Reads the ETASSSpec (workload, goal, domain, bounded_context, reasoning, ...)
  2. Resolves DDD agents from BrocaRegistry matching the spec's domain/context/aggregate
  3. Injects domain ontology from DomainRegistry (manufacturing ontology, production context,
     batch invariants, recipe semantics, relevant policies, relevant constitutions, ...)
  4. Applies the reasoning strategy (chain_of_thought, reflection, graph_of_thought, debate)
  5. Assembles StructuredPromptIR — the compiled prompt the model backend receives

The LLM never has to "guess" the architecture. The architecture is compiled into the prompt.
The prompt author writes only: workload, goal, domain, bounded_context, reasoning.
Everything else is injected by the compiler.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from broca.agents._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.prompt_compiler")

if TYPE_CHECKING:
    from src.monkey_brain.kernel.execute.provider.prompt_ir import StructuredPromptIR

# Domain role templates — injected based on spec.domain
DOMAIN_ROLES: dict[str, str] = {
    "software_engineering": (
        "You are a senior software engineering agent operating within the software engineering domain. "
        "You understand CI/CD pipelines, pull request lifecycle, test coverage invariants, "
        "deployment safety gates, and code review governance."
    ),
    "manufacturing": (
        "You are a manufacturing systems engineering agent operating within the pharmaceutical "
        "manufacturing domain. You understand work order lifecycle, batch release invariants, "
        "SOP compliance, change control governance, calibration records, and GMP regulations."
    ),
    "default": ("You are a domain expert agent. Apply your expertise to the stated goal."),
}

# Aggregate semantics injected when aggregate is specified
AGGREGATE_SEMANTICS: dict[str, str] = {
    # software engineering aggregates
    "pull_request": "PRs require at least 1 approval and passing CI before merge. You cannot approve your own PR.",
    "test_suite": "Coverage must not drop below threshold. No new failing tests without explicit waiver.",
    "deployment": "Cannot deploy to production without staging passing. Failing builds cannot be deployed.",
    "pipeline": "Pipeline stages are ordered. A failing stage blocks all downstream stages.",
    # manufacturing aggregates
    "batch": "Batches require QA sign-off before release. Yield must meet minimum threshold. Deviations require CAPA.",
    "work_order": "Work orders require assigned worker and equipment availability confirmation before starting.",
    "change_control": "Production changes require risk assessment and validation protocol. Emergency changes require retrospective docs.",
    "calibration_record": "Calibration must be performed within schedule. Out-of-tolerance results require immediate investigation.",
}


class PromptCompilerAgent(BaseETASSAgent):
    """Compiles ETASSSpec into StructuredPromptIR.

    This is the canonical implementation of ETASS.
    Called by LLMExplorer whenever a model call is needed.
    Output (StructuredPromptIR) is sent to ModelBackend.
    """

    agent_type = "prompt_compiler"
    description = "Prompt Compiler — compiles ETASSSpec into StructuredPromptIR, injecting domain ontology, DDD agents, reasoning strategy"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict[str, Any]):

        spec_data = context.get("spec")
        if spec_data is None:
            # Build a minimal spec from raw context
            from etass.specification import ETASSSpec

            spec = ETASSSpec.from_question(
                question=context.get("goal", context.get("question", "")),
                intent=context.get("workload", context.get("intent", "default")),
            )
        elif isinstance(spec_data, dict):
            from etass.specification import ETASSSpec

            spec = ETASSSpec.from_dict(spec_data)
        else:
            spec = spec_data  # already an ETASSSpec

        ir = await self._compile_async(spec)
        self._reward(True)
        return self._result(
            payload={"prompt_ir": ir, "compiled_prompt": ir.compiled_prompt},
            observations=[f"compiled {spec.workload} prompt for {spec.domain}/{spec.bounded_context}"],
        )

    async def _compile_async(self, spec) -> "StructuredPromptIR":
        external_block = ""
        retrieval_meta: dict = {}
        try:
            from src.monkey_brain.kernel.knowledge.sittingface_retrieval import (
                get_external_knowledge_retriever,
            )

            report = await get_external_knowledge_retriever().retrieve(
                spec.goal,
                cycle_id=f"etass:{spec.workload}",
                meta=getattr(spec, "metadata", None) or {},
            )
            external_block = report.format_for_prompt()
            retrieval_meta = report.to_metadata()
        except Exception as exc:
            logger.debug("[prompt_compiler] external knowledge retrieval skipped: %s", exc)
        return self._compile(spec, external_knowledge_block=external_block, retrieval_meta=retrieval_meta)

    def _compile(
        self,
        spec,
        *,
        external_knowledge_block: str = "",
        retrieval_meta: dict | None = None,
    ) -> "StructuredPromptIR":
        from src.monkey_brain.kernel.execute.provider.prompt_ir import (
            StructuredPromptIR,
            REASONING_PREAMBLES,
        )

        role = self._resolve_role(spec)
        domain_ontology = self._resolve_domain_ontology(spec)
        agent_catalog = self._resolve_agent_catalog(spec)
        resolved_agents = self._resolve_ddd_agents(spec)
        reasoning_preamble = REASONING_PREAMBLES.get(spec.reasoning, REASONING_PREAMBLES["chain_of_thought"])
        constraints_block = self._format_list("CONSTRAINTS", spec.constraints)
        evidence_block = self._format_list("EVIDENCE SOURCES", spec.evidence)
        success_criteria_block = self._format_list("SUCCESS CRITERIA", spec.success_criteria)
        outputs_block = self._format_list("EXPECTED OUTPUTS", spec.outputs)
        constitutions_block = self._format_list("CONSTITUTIONS", spec.constitutions)
        policies_block = self._format_list("POLICIES", spec.policies)

        compiled = self._assemble(
            spec=spec,
            role=role,
            domain_ontology=domain_ontology,
            agent_catalog=agent_catalog,
            resolved_agents=resolved_agents,
            reasoning_preamble=reasoning_preamble,
            constraints_block=constraints_block,
            evidence_block=evidence_block,
            success_criteria_block=success_criteria_block,
            outputs_block=outputs_block,
            constitutions_block=constitutions_block,
            policies_block=policies_block,
            external_knowledge_block=external_knowledge_block,
        )

        meta = dict(retrieval_meta or {})
        if external_knowledge_block:
            meta["external_knowledge_injected"] = True

        return StructuredPromptIR(
            workload=spec.workload,
            goal=spec.goal,
            domain=spec.domain,
            reasoning=spec.reasoning,
            role=role,
            domain_ontology=domain_ontology,
            agent_catalog=agent_catalog,
            reasoning_preamble=reasoning_preamble,
            constraints_block=constraints_block,
            evidence_block=evidence_block,
            success_criteria_block=success_criteria_block,
            constitutions_block=constitutions_block,
            policies_block=policies_block,
            outputs_block=outputs_block,
            resolved_agents=resolved_agents,
            compiled_prompt=compiled,
            metadata=meta,
        )

    # ------------------------------------------------------------------
    # Compilation steps
    # ------------------------------------------------------------------

    def _resolve_role(self, spec) -> str:
        return DOMAIN_ROLES.get(spec.domain, DOMAIN_ROLES["default"])

    def _resolve_domain_ontology(self, spec) -> str:
        lines = [f"Domain: {spec.domain}"]
        if spec.bounded_context:
            lines.append(f"Bounded Context: {spec.bounded_context}")
        if spec.aggregate:
            lines.append(f"Aggregate: {spec.aggregate}")
            semantics = AGGREGATE_SEMANTICS.get(spec.aggregate)
            if semantics:
                lines.append(f"  Invariants: {semantics}")
        if spec.entity:
            lines.append(f"Entity: {spec.entity}")
        if spec.value_object:
            lines.append(f"Value Object: {spec.value_object}")

        # Pull constitutions from the domain package if registered
        try:
            from domains.package import DomainRegistry

            pkg = DomainRegistry.instance().get(spec.domain)
            if pkg and pkg.policies:
                lines.append("Domain Constitutions (from package):")
                for p in pkg.policies[:3]:
                    name = p.get("name", "unnamed")
                    lines.append(f"  - {name}")
        except Exception:
            pass

        return "\n".join(lines)

    def _resolve_agent_catalog(self, spec) -> str:
        """List available agents from Broca registry, filtered by relevance."""
        try:
            from broca.registry import get_registry

            all_agents = get_registry().list_agents()
            # Show all — the model decides which to use
            lines = ["Available Agents (Broca Registry):"]
            for agent_type, description in all_agents.items():
                lines.append(f"  {agent_type}: {description}")
            return "\n".join(lines)
        except Exception:
            return ""

    def _resolve_ddd_agents(self, spec) -> list[str]:
        """Return the list of DDD agent types that match this spec's domain/context/aggregate."""
        resolved = []
        try:
            from broca.registry import get_registry

            registry = get_registry()

            # Map spec fields to DDD agent types
            if spec.domain:
                if registry.discover("domain"):
                    resolved.append("domain")
            if spec.bounded_context:
                if registry.discover("bounded_context"):
                    resolved.append("bounded_context")
            if spec.aggregate:
                if registry.discover("aggregate"):
                    resolved.append("aggregate")
            if spec.entity:
                if registry.discover("entity"):
                    resolved.append("entity")
            if spec.value_object:
                if registry.discover("value_object"):
                    resolved.append("value_object")
        except Exception as exc:
            logger.debug("[prompt_compiler] agent resolution skipped: %s", exc)
        return resolved

    @staticmethod
    def _format_list(header: str, items: list[str]) -> str:
        if not items:
            return ""
        return f"{header}:\n" + "\n".join(f"  - {item}" for item in items)

    def _assemble(
        self,
        spec,
        role: str,
        domain_ontology: str,
        agent_catalog: str,
        resolved_agents: list[str],
        reasoning_preamble: str,
        constraints_block: str,
        evidence_block: str,
        success_criteria_block: str,
        outputs_block: str,
        constitutions_block: str,
        policies_block: str,
        external_knowledge_block: str = "",
    ) -> str:
        sections = [
            f"# ETASS Workload: {spec.workload}",
            "",
            f"## Role\n{role}",
            "",
            f"## Goal\n{spec.goal}",
        ]

        if domain_ontology:
            sections += ["", f"## Domain Context\n{domain_ontology}"]

        if resolved_agents:
            agent_note = (
                "## Resolved DDD Agents\n"
                "The following cognitive agents are available for this domain/context:\n"
                + "\n".join(f"  - {a}" for a in resolved_agents)
            )
            sections += ["", agent_note]

        if agent_catalog:
            sections += ["", f"## {agent_catalog}"]

        sections += [
            "",
            f"## Reasoning Strategy ({spec.reasoning})\n{reasoning_preamble}",
        ]

        for block in (
            constraints_block,
            constitutions_block,
            policies_block,
            evidence_block,
            success_criteria_block,
            outputs_block,
        ):
            if block:
                sections += ["", block]

        if external_knowledge_block:
            sections += ["", external_knowledge_block]

        return "\n".join(sections)
