"""ETASSPipelineOrchestrator — connects the full ETASS pipeline.

Architecture:
    ETASSSpec
        │
        ▼
    Prompt Compiler  (PromptCompilerAgent → StructuredPromptIR)
        │
        ▼
    Planner          (StructuredPromptIR → ExecutionDAG)
        │
        ▼
    Execution DAG
        │
  ┌─────┬─────┬──────┐
  ▼     ▼     ▼      ▼
  MB   n8n  OpenClaw Mermaid
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("etass.pipeline.orchestrator")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class AdapterResult:
    adapter: str
    status: str  # ok | error | unavailable | skipped
    output: Any = None
    error: str = ""
    elapsed_ms: float = 0.0


@dataclass
class PipelineResult:
    spec_workload: str
    spec_goal: str
    dag_id: str
    node_count: int
    elapsed_ms: float
    compiled_prompt: str
    mermaid: str
    adapters: list[AdapterResult] = field(default_factory=list)

    def ok(self) -> bool:
        return any(a.status == "ok" for a in self.adapters)

    def to_dict(self) -> dict:
        return {
            "spec_workload": self.spec_workload,
            "spec_goal": self.spec_goal,
            "dag_id": self.dag_id,
            "node_count": self.node_count,
            "elapsed_ms": self.elapsed_ms,
            "compiled_prompt_length": len(self.compiled_prompt),
            "mermaid": self.mermaid,
            "adapters": [
                {
                    "adapter": a.adapter,
                    "status": a.status,
                    "output": a.output,
                    "error": a.error,
                    "elapsed_ms": a.elapsed_ms,
                }
                for a in self.adapters
            ],
        }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class ETASSPipelineOrchestrator:
    """Runs the full ETASS pipeline and fans out to all four outputs.

    All four adapters run in parallel via asyncio.gather.
    A failure in one adapter does not stop the others.
    """

    def __init__(
        self,
        monkeybrain_url: str = "",
        n8n_webhook_url: str = "",
        openclaw_api_url: str = "",
    ):
        self._mb_url = monkeybrain_url or os.environ.get("MONKEYBRAIN_URL", "http://localhost:8031")
        self._n8n_url = n8n_webhook_url or os.environ.get("N8N_WEBHOOK_URL", "")
        self._openclaw_api_url = openclaw_api_url or os.environ.get("OPENCLAW_API_URL", "")

    async def run(self, spec) -> PipelineResult:
        """Execute the full pipeline for one ETASSSpec."""
        t0 = time.monotonic()

        # Phase 1 — Prompt Compiler
        ir = await self._compile(spec)
        logger.info(
            "[orchestrator] compiled prompt for workload=%s domain=%s",
            spec.workload,
            spec.domain,
        )

        # Phase 2 — Build Execution DAG
        dag = self._build_dag(spec, ir)
        logger.info("[orchestrator] dag=%s nodes=%d", dag.dag_id, len(dag.nodes))

        # Phase 3 — Fan out to 4 adapters in parallel
        results = await asyncio.gather(
            self._dispatch_monkeybrain(spec, ir, dag),
            self._dispatch_n8n(spec, ir, dag),
            self._dispatch_openclaw(spec, ir, dag),
            self._generate_mermaid(spec, dag),
            return_exceptions=True,
        )

        adapter_results = []
        adapter_names = ["monkeybrain", "n8n", "openclaw", "mermaid"]
        for name, r in zip(adapter_names, results):
            if isinstance(r, Exception):
                adapter_results.append(AdapterResult(adapter=name, status="error", error=str(r)))
            else:
                adapter_results.append(r)

        mermaid_output = ""
        for r in adapter_results:
            if r.adapter == "mermaid" and r.status == "ok":
                mermaid_output = r.output or ""
                break

        return PipelineResult(
            spec_workload=spec.workload,
            spec_goal=spec.goal,
            dag_id=dag.dag_id,
            node_count=len(dag.nodes),
            elapsed_ms=(time.monotonic() - t0) * 1000,
            compiled_prompt=ir.compiled_prompt,
            mermaid=mermaid_output,
            adapters=adapter_results,
        )

    # ------------------------------------------------------------------
    # Phase 1 — Prompt Compiler
    # ------------------------------------------------------------------

    async def _compile(self, spec) -> "StructuredPromptIR":
        from broca.agents.prompt_compiler import PromptCompilerAgent

        agent = PromptCompilerAgent()
        result = await agent.handle({"spec": spec})
        payload = result.get("payload", {}) if isinstance(result, dict) else {}
        ir = payload.get("prompt_ir")
        if ir is None:
            # Fallback: build a minimal IR inline without the full agent chain
            return self._minimal_ir(spec)
        return ir

    def _minimal_ir(self, spec) -> "StructuredPromptIR":
        from src.monkey_brain.kernel.execute.provider.prompt_ir import (
            StructuredPromptIR,
            REASONING_PREAMBLES,
        )

        preamble = REASONING_PREAMBLES.get(spec.reasoning, REASONING_PREAMBLES["chain_of_thought"])
        compiled = (
            f"# ETASS Workload: {spec.workload}\n\n"
            f"## Goal\n{spec.goal}\n\n"
            f"## Domain\n{spec.domain} / {spec.bounded_context}\n\n"
            f"## Reasoning ({spec.reasoning})\n{preamble}"
        )
        return StructuredPromptIR(
            workload=spec.workload,
            goal=spec.goal,
            domain=spec.domain,
            reasoning=spec.reasoning,
            reasoning_preamble=preamble,
            compiled_prompt=compiled,
        )

    # ------------------------------------------------------------------
    # Phase 2 — Build Execution DAG
    # ------------------------------------------------------------------

    def _build_dag(self, spec, ir) -> "ExecutionDAG":
        # NOT src.monkey_brain.kernel.dag — that module doesn't exist. The
        # real ExecutionDAG/DAGNode/DAGEdge live here; this was a
        # ModuleNotFoundError on every single call, meaning run() crashed at
        # Phase 2 before any adapter ever dispatched.
        from src.monkey_brain.kernel.execute.runtime.dag import (
            ExecutionDAG,
            DAGNode,
            DAGEdge,
        )

        dag = ExecutionDAG(metadata={"workload": spec.workload, "domain": spec.domain})

        # Linear spine: Spec → Compiler → Planner → Dispatcher
        spec_node = DAGNode(
            operator_type="etass_spec",
            metadata={"workload": spec.workload, "goal": spec.goal[:120]},
        )
        compiler_node = DAGNode(operator_type="prompt_compiler", metadata={"reasoning": spec.reasoning})
        planner_node = DAGNode(operator_type="planner", metadata={"domain": spec.domain})
        dispatcher_node = DAGNode(operator_type="dispatcher", metadata={"fan_out": 4})

        dag.nodes.extend([spec_node, compiler_node, planner_node, dispatcher_node])
        dag.edges.extend(
            [
                DAGEdge(
                    source_node_id=spec_node.node_id,
                    target_node_id=compiler_node.node_id,
                ),
                DAGEdge(
                    source_node_id=compiler_node.node_id,
                    target_node_id=planner_node.node_id,
                ),
                DAGEdge(
                    source_node_id=planner_node.node_id,
                    target_node_id=dispatcher_node.node_id,
                ),
            ]
        )

        # Fan-out leaves
        for adapter in (
            "monkeybrain_runtime",
            "n8n_workflow",
            "openclaw_a2a",
            "mermaid_viz",
        ):
            leaf = DAGNode(operator_type=adapter, metadata={"from": dispatcher_node.node_id})
            dag.nodes.append(leaf)
            dag.edges.append(DAGEdge(source_node_id=dispatcher_node.node_id, target_node_id=leaf.node_id))

        return dag

    # ------------------------------------------------------------------
    # Adapter 1 — MonkeyBrain Runtime
    # ------------------------------------------------------------------

    async def _dispatch_monkeybrain(self, spec, ir, dag) -> AdapterResult:
        t0 = time.monotonic()
        try:
            import httpx

            url = f"{self._mb_url}/api/v1/agentos/prompt"
            payload = {
                "question": ir.compiled_prompt,
                "run_query": True,
                "run_simulate": False,
                "etass_metadata": {
                    "workload": spec.workload,
                    "domain": spec.domain,
                    "dag_id": dag.dag_id,
                },
            }
            # /prompt only reaches CodeGenRuntime when run_type == "codegen"
            # (resolve_run_type, prompt_helpers.py) — omitting this made every
            # ETASS run, regardless of workload, silently degrade to an
            # ordinary "full" cognitive query, despite this adapter's own
            # docstring/diagram showing MonkeyBrain as a real execution leaf.
            # Only "code_generation" workloads should set it: the other
            # workloads (self_healing, chaos, sre, architecture_review, ...)
            # genuinely are ordinary queries, not codegen requests.
            if spec.workload == "code_generation":
                payload["run_type"] = "codegen"
                # CodeGenRuntime derives the service_slug from this "question"
                # via _slugify_question (first 4 words) when none is passed
                # explicitly — ir.compiled_prompt's own text always starts
                # with "# ETASS Workload: code_generation\n\n## Goal\n...", so
                # slugifying THAT (rather than the goal) produced the same
                # generic "etass_workload_code_generation" slug for every
                # single codegen run, regardless of spec.goal — a guaranteed
                # collision. spec.goal is the part that's actually unique.
                payload["question"] = spec.goal
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, json=payload)
            elapsed = (time.monotonic() - t0) * 1000
            if resp.status_code >= 400:
                return AdapterResult(
                    adapter="monkeybrain",
                    status="error",
                    error=f"HTTP {resp.status_code}: {resp.text[:200]}",
                    elapsed_ms=elapsed,
                )
            return AdapterResult(
                adapter="monkeybrain",
                status="ok",
                output=resp.json(),
                elapsed_ms=elapsed,
            )
        except Exception as exc:
            return AdapterResult(
                adapter="monkeybrain",
                status="error",
                error=str(exc),
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

    # ------------------------------------------------------------------
    # Adapter 2 — n8n JSON Workflow
    # ------------------------------------------------------------------

    async def _dispatch_n8n(self, spec, ir, dag) -> AdapterResult:
        t0 = time.monotonic()
        if not self._n8n_url:
            return AdapterResult(adapter="n8n", status="unavailable", error="N8N_WEBHOOK_URL not set")
        try:
            import httpx

            payload = {
                "workload": spec.workload,
                "goal": spec.goal,
                "domain": spec.domain,
                "bounded_context": spec.bounded_context,
                "dag_id": dag.dag_id,
                "node_count": len(dag.nodes),
                "reasoning": spec.reasoning,
                "compiled_prompt": ir.compiled_prompt[:500],
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(self._n8n_url, json=payload)
            elapsed = (time.monotonic() - t0) * 1000
            if resp.status_code >= 400:
                return AdapterResult(
                    adapter="n8n",
                    status="error",
                    error=f"HTTP {resp.status_code}: {resp.text[:200]}",
                    elapsed_ms=elapsed,
                )
            try:
                body = resp.json()
            except Exception:
                body = {"raw": resp.text}
            return AdapterResult(adapter="n8n", status="ok", output=body, elapsed_ms=elapsed)
        except Exception as exc:
            return AdapterResult(
                adapter="n8n",
                status="error",
                error=str(exc),
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

    # ------------------------------------------------------------------
    # Adapter 3 — OpenClaw A2A Execution Plan
    # ------------------------------------------------------------------

    async def _dispatch_openclaw(self, spec, ir, dag) -> AdapterResult:
        t0 = time.monotonic()
        try:
            from cerebellum.capabilities.agent.agents import OpenClawCapability

            cap = OpenClawCapability(api_url=self._openclaw_api_url)
            result = await cap.execute(
                {
                    "message": (
                        f"ETASS {spec.workload} task for domain {spec.domain}.\n"
                        f"Goal: {spec.goal}\n"
                        f"Reasoning: {spec.reasoning}\n"
                        f"DAG: {dag.dag_id} ({len(dag.nodes)} nodes)"
                    ),
                    "agent": os.environ.get("OPENCLAW_AGENT", "main"),
                    "model": os.environ.get("OPENCLAW_MODEL", "claude-cli/claude-sonnet-4-6"),
                }
            )
            elapsed = (time.monotonic() - t0) * 1000
            status = result.get("status", "unknown")
            return AdapterResult(
                adapter="openclaw",
                status="ok" if status == "ok" else status,
                output=result,
                error=result.get("error", ""),
                elapsed_ms=elapsed,
            )
        except Exception as exc:
            return AdapterResult(
                adapter="openclaw",
                status="error",
                error=str(exc),
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

    # ------------------------------------------------------------------
    # Adapter 4 — Mermaid Visualization
    # ------------------------------------------------------------------

    async def _generate_mermaid(self, spec, dag) -> AdapterResult:
        t0 = time.monotonic()
        try:
            lines = [
                "```mermaid",
                "flowchart TD",
                f"    classDef fanout fill:#4a90d9,color:#fff,stroke:#2c6fad",
                f"    classDef spine fill:#2ecc71,color:#fff,stroke:#27ae60",
            ]

            node_labels: dict[str, str] = {}
            for node in dag.nodes:
                nid = node.node_id.replace("-", "_")
                op = node.operator_type
                label_map = {
                    "etass_spec": f"ETASS Spec\\n[{spec.workload}]",
                    "prompt_compiler": "Prompt Compiler\\nto StructuredPromptIR",
                    "planner": "Planner\\nto ExecutionDAG",
                    "dispatcher": "Dispatcher\\n[fan-out x 4]",
                    "monkeybrain_runtime": "MonkeyBrain Runtime\\n:8031",
                    "n8n_workflow": "n8n JSON Workflow\\n:5678",
                    "openclaw_a2a": "OpenClaw A2A\\nExecution Plan",
                    "mermaid_viz": "Visualization\\n[Mermaid]",
                }
                label = label_map.get(op, op)
                node_labels[node.node_id] = nid
                shape = f'["{label}"]'
                style_class = (
                    "fanout"
                    if op
                    in (
                        "monkeybrain_runtime",
                        "n8n_workflow",
                        "openclaw_a2a",
                        "mermaid_viz",
                    )
                    else "spine"
                )
                lines.append(f"    {nid}{shape}:::{style_class}")

            for edge in dag.edges:
                src = node_labels.get(edge.source_node_id, edge.source_node_id.replace("-", "_"))
                tgt = node_labels.get(edge.target_node_id, edge.target_node_id.replace("-", "_"))
                lines.append(f"    {src} --> {tgt}")

            lines.append("```")
            diagram = "\n".join(lines)
            elapsed = (time.monotonic() - t0) * 1000
            return AdapterResult(adapter="mermaid", status="ok", output=diagram, elapsed_ms=elapsed)
        except Exception as exc:
            return AdapterResult(
                adapter="mermaid",
                status="error",
                error=str(exc),
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
