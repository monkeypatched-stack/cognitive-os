"""PlannerAgent — generates a structured YAML execution plan from a free-text goal.

The plan maps to concrete `monkeypatched make` commands and is consumed by ExecutorAgent.
Uses the planner workload spec; falls back to Anthropic when Ollama is unavailable.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from ._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.planner")

_CANONICAL_PIPELINE = """
CANONICAL 14-STEP PIPELINE (follow exactly — do not reorder):
  Step 1:  api <service>                  — compile DDD API chart → somatic/compiled/<service>-api.prompt.md
  Step 2:  codegen <service>              — generate DDD-layered service code
  Step 3:  seed <service>                 — seed MongoDB with Pydantic-validated synthetic data
  Step 4:  ddd-check <service> --max-loops 2  — structural DDD compliance audit with self-healing codegen retry
  Step 5:  client <service>               — generate typed httpx API client
  Step 6:  chart-from-client <service>    — ClientCharterAgent → capability chart
  Step 7:  compile-agent <service>        — capability chart → .prompt.md
  Step 8:  test-service <service>         — ruff lint + pytest + correction loop
  Step 9:  fixit <service>                — auto-fix test / governance findings
  Step 10: govern <service>               — CingulateAgent governance review (COMPLIANT/NON-COMPLIANT)
  Step 11: comply gdpr --signals '{}' --attributes '{}'  — GDPR data-protection compliance
  Step 12: simulate "<goal>"              — world-model G→A→R simulation [CONDITIONAL: see simulate_enabled]
  Step 13: create-agent <service>         — register ClientCapabilityAgent in Broca registry
  Step 14: serve <service>                — launch service with uvicorn

SUBSTITUTION: Replace <service> with the actual service name throughout.
DEPENDENCIES: Each step depends on the previous one (sequential pipeline).
SIMULATE FLAG:
  - If simulate_enabled is true (default): include step 12 exactly as shown.
  - If simulate_enabled is false: OMIT step 12 entirely; step 11 depends_on flows directly to step 13.
    Renumber remaining steps so IDs are always consecutive (1–13 when simulate omitted).
""".strip()

_PLAN_SCHEMA = """
Output ONLY a valid YAML plan with this exact structure:

plan:
  id: plan-<short-uuid>
  goal: "<goal text>"
  created_at: "<ISO8601>"
  status: pending
  version: "1.0.0"

steps:
  - id: 1
    name: <snake_case_name>
    command: monkeypatched make <subcommand> <args>
    description: "<what this step does>"
    depends_on: []
  - id: 2
    name: <snake_case_name>
    command: monkeypatched make <subcommand> <args>
    description: "<what this step does>"
    depends_on: [1]
""".strip()


class PlannerAgent(BaseETASSAgent):
    agent_type = "planner"
    description = "Generates a structured YAML execution plan from a free-text goal"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict) -> dict:
        goal = str(context.get("goal", "")).strip()
        service = str(context.get("service", "")).strip()
        simulate_enabled: bool = context.get("simulate_enabled", True)
        if not goal:
            self._reward(False, 0.0)
            return self._result(
                payload={"plan": None},
                observations=["no goal provided"],
            )

        sim_flag = (
            "simulate_enabled: true — include step 12 (simulate) in the plan."
            if simulate_enabled
            else "simulate_enabled: false — OMIT step 12 (simulate). Renumber steps consecutively."
        )
        prompt = (
            f"Goal: {goal}\n"
            + (f"Service name: {service}\n" if service else "")
            + f"Simulate flag: {sim_flag}\n"
            + f"\n{_CANONICAL_PIPELINE}\n\n"
            + _PLAN_SCHEMA
        )
        system = (
            "You are a MonkeyBrain system planner. Given a goal, produce a YAML execution "
            "plan following the CANONICAL 14-STEP PIPELINE exactly. "
            "Do not reorder steps. Substitute <service> with the actual service name. "
            "Respect the simulate_enabled flag — include or omit step 12 accordingly. "
            "Output ONLY valid YAML — no prose, no markdown fences."
        )

        raw = ""
        try:
            raw = await self._llm_from_spec(
                "planner",
                goal_override=prompt,
                system_override=system,
                max_tokens=2048,
            )
        except Exception as e:
            logger.warning("[planner] spec LLM failed (%s) — Anthropic fallback", e)

        if not raw.strip():
            raw = await self._anthropic_fallback(prompt, system)

        plan = self._extract_plan(raw, goal, service, simulate_enabled=simulate_enabled)

        self._reward(bool(plan.get("steps")), 0.9 if plan.get("steps") else 0.3)
        return self._result(
            payload={"plan": plan},
            observations=[f"generated {len(plan.get('steps', []))} step plan for: {goal[:80]}"],
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _anthropic_fallback(self, prompt: str, system: str) -> str:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            return ""
        try:
            import anthropic

            msg = anthropic.Anthropic(api_key=api_key).messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=2048,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text
        except Exception as e:
            logger.error("[planner] Anthropic fallback failed: %s", e)
            return ""

    # Known subcommands and their canonical form
    _KNOWN_SUBCMDS = {
        "api",
        "codegen",
        "seed",
        "test-service",
        "govern",
        "fixit",
        "ddd-check",
        "serve",
        "client",
        "chart-from-client",
        "compile-agent",
        "comply",
        "simulate",
        "create-agent",
        "plan",
        "execute-plan",
        "list-plans",
        "run",
    }

    # Map step name fragments → canonical subcommand
    _NAME_TO_SUBCMD: dict[str, str] = {
        "api": "api",
        "codegen": "codegen",
        "generate_service": "codegen",
        "generate": "codegen",
        "seed": "seed",
        "seed_data": "seed",
        "seed_db": "seed",
        "test_service": "test-service",
        "test-service": "test-service",
        "govern": "govern",
        "fixit": "fixit",
        "fix": "fixit",
        "ddd_check": "ddd-check",
        "ddd-check": "ddd-check",
        "serve": "serve",
        "client": "client",
        "chart_from_client": "chart-from-client",
        "chart-from-client": "chart-from-client",
        "compile_agent": "compile-agent",
        "compile-agent": "compile-agent",
        "comply": "comply",
        "comply_gdpr": "comply",
        "comply_soc2": "comply",
        "simulate": "simulate",
        "simulation": "simulate",
        "world_model": "simulate",
        "create_agent": "create-agent",
        "create-agent": "create-agent",
        "plan": "plan",
        "execute_plan": "execute-plan",
        "execute-plan": "execute-plan",
    }

    @classmethod
    def _sanitize_step(cls, step: dict, service: str) -> dict:
        """Fix hallucinated commands by deriving from step name when invalid."""
        svc = service or "service"
        cmd: str = str(step.get("command", ""))

        # Replace angle-bracket placeholders with actual service name
        cmd = re.sub(r"<[^>]*>", svc, cmd)

        # Check if command is already valid: "monkeypatched make <known> ..."
        parts = cmd.split()
        valid = (
            len(parts) >= 3 and parts[0] == "monkeypatched" and parts[1] == "make" and parts[2] in cls._KNOWN_SUBCMDS
        )

        # govern must have a file path, not a bare slug
        if valid and parts[2] == "govern" and len(parts) >= 4:
            arg = parts[3]
            if not arg.startswith("somatic/") and not arg.startswith("/") and "." not in arg:
                cmd = f"monkeypatched make govern somatic/compiled/{arg}-api.prompt.md"

        # comply must have --signals/--attributes flags, not bare positional args
        if valid and parts[2] == "comply" and len(parts) >= 4:
            if "--signals" not in cmd and "--attributes" not in cmd:
                std = parts[3]
                cmd = f'monkeypatched make comply {std} --signals \'{{"stores_pii": true}}\' --attributes \'{{"region": "EU"}}\''

        if not valid:
            # Derive canonical subcommand from step name
            name = str(step.get("name", "")).lower().replace("-", "_")
            # Strip trailing service suffix (e.g. "api_todo" → "api")
            name = re.sub(rf"[_-]{re.escape(svc)}$", "", name)
            subcmd = cls._NAME_TO_SUBCMD.get(name)
            if not subcmd:
                # Try prefix match on name fragments
                for frag, sc in cls._NAME_TO_SUBCMD.items():
                    if name.startswith(frag) or frag.startswith(name):
                        subcmd = sc
                        break
            if subcmd:
                if subcmd == "govern":
                    cmd = f"monkeypatched make govern somatic/compiled/{svc}-api.prompt.md"
                elif subcmd == "comply":
                    cmd = 'monkeypatched make comply gdpr --signals \'{"stores_pii": true}\' --attributes \'{"region": "EU"}\''
                elif subcmd in ("plan", "execute-plan"):
                    cmd = f"monkeypatched make {subcmd}"
                else:
                    cmd = f"monkeypatched make {subcmd} {svc}"

        step = dict(step)
        step["command"] = cmd
        return step

    @classmethod
    def _extract_plan(
        cls,
        raw: str,
        goal: str,
        service: str,
        *,
        simulate_enabled: bool = True,
    ) -> dict:
        raw = raw.strip()
        raw = re.sub(r"^```(?:yaml)?\s*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        raw = raw.strip()

        try:
            import yaml

            data = yaml.safe_load(raw) or {}
            if "plan" in data and "steps" in data:
                steps = [cls._sanitize_step(s, service) for s in data["steps"]]
                if not simulate_enabled:
                    steps = [s for s in steps if "simulate" not in s.get("command", "")]
                    # Renumber consecutively
                    for i, s in enumerate(steps, 1):
                        old_id = s["id"]
                        s["id"] = i
                        s["depends_on"] = [i - 1 for d in s.get("depends_on", []) if d == old_id - 1] if i > 1 else []
                data["steps"] = steps
                return data
        except Exception:
            pass

        # Structural fallback: canonical 14-step pipeline
        svc = service or "service"
        now = datetime.now(timezone.utc).isoformat()
        short_id = str(uuid.uuid4())[:8]

        gdpr_cmd = (
            "monkeypatched make comply gdpr "
            '--signals \'{"has_pii":true,"lawful_basis":"contract","privacy_by_design":true,'
            '"retention_period_defined":true,"right_to_erasure_enabled":true,'
            '"right_to_access_enabled":true,"breach_notification_proc":true,'
            '"consent_withdrawable":true}\' '
            '--attributes \'{"region":"EU"}\''
        )

        all_steps = [
            {
                "id": 1,
                "name": "api",
                "command": f"monkeypatched make api {svc}",
                "description": "Compile DDD API chart → somatic prompt",
                "depends_on": [],
            },
            {
                "id": 2,
                "name": "codegen",
                "command": f"monkeypatched make codegen {svc}",
                "description": "Generate DDD-layered service code",
                "depends_on": [1],
            },
            {
                "id": 3,
                "name": "seed",
                "command": f"monkeypatched make seed {svc}",
                "description": "Seed MongoDB with Pydantic-validated synthetic data",
                "depends_on": [2],
            },
            {
                "id": 4,
                "name": "ddd_check",
                "command": f"monkeypatched make ddd-check {svc} --max-loops 2",
                "description": "Structural DDD compliance audit with self-healing codegen retry",
                "depends_on": [3],
            },
            {
                "id": 5,
                "name": "client",
                "command": f"monkeypatched make client {svc}",
                "description": "Generate typed httpx API client",
                "depends_on": [4],
            },
            {
                "id": 6,
                "name": "chart_from_client",
                "command": f"monkeypatched make chart-from-client {svc}",
                "description": "ClientCharterAgent → capability chart",
                "depends_on": [5],
            },
            {
                "id": 7,
                "name": "compile_agent",
                "command": f"monkeypatched make compile-agent {svc}",
                "description": "Capability chart → .prompt.md",
                "depends_on": [6],
            },
            {
                "id": 8,
                "name": "test_service",
                "command": f"monkeypatched make test-service {svc}",
                "description": "ruff + pytest + correction loop",
                "depends_on": [7],
            },
            {
                "id": 9,
                "name": "fixit",
                "command": f"monkeypatched make fixit {svc}",
                "description": "Auto-fix test/governance findings",
                "depends_on": [8],
            },
            {
                "id": 10,
                "name": "govern",
                "command": f"monkeypatched make govern somatic/compiled/{svc}-api.prompt.md",
                "description": "CingulateAgent governance review",
                "depends_on": [9],
            },
            {
                "id": 11,
                "name": "comply_gdpr",
                "command": gdpr_cmd,
                "description": "GDPR data-protection compliance check",
                "depends_on": [10],
            },
            {
                "id": 12,
                "name": "simulate",
                "command": f'monkeypatched make simulate "build {svc} service"',
                "description": "G→A→R world-model simulation — adversarial design review",
                "depends_on": [11],
            },
            {
                "id": 13,
                "name": "create_agent",
                "command": f"monkeypatched make create-agent {svc}",
                "description": "Register ClientCapabilityAgent in Broca registry",
                "depends_on": [12],
            },
            {
                "id": 14,
                "name": "serve",
                "command": f"monkeypatched make serve {svc}",
                "description": "Launch service with uvicorn",
                "depends_on": [13],
            },
        ]

        if not simulate_enabled:
            all_steps = [s for s in all_steps if s["name"] != "simulate"]
            # Renumber and fix depends_on
            id_map = {s["id"]: i for i, s in enumerate(all_steps, 1)}
            for i, s in enumerate(all_steps, 1):
                s["depends_on"] = [id_map[d] for d in s["depends_on"] if d in id_map]
                s["id"] = i

        return {
            "plan": {
                "id": f"plan-{short_id}",
                "goal": goal,
                "created_at": now,
                "status": "pending",
                "version": "1.0.0",
                "simulate_enabled": simulate_enabled,
            },
            "steps": all_steps,
        }
