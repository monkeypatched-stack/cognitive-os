"""CodeGen Agent — generates Python code from somatic prompts.

Provider priority: Ollama local (qwen2.5-coder:7b) primary, Claude
(claude-sonnet-4-6) and OpenRouter (openai/gpt-4o-mini) as fallbacks.
Set CODEGEN_PRIMARY=ollama|claude|openrouter to change which is tried
first; the other two remain fallbacks regardless.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

GENERATED_DIR = Path(os.environ.get("SITTINGFACE_GENERATED_DIR", "/Users/prashunjaveri/Code/generated/monkeypatched"))
SRC_ROOT = Path(os.environ.get("SITTINGFACE_SRC_ROOT", str(Path(__file__).parents[2] / "src")))


class ClaudeClient:
    """Primary client — Anthropic Claude via official SDK."""

    def __init__(self, model: str = "claude-sonnet-4-6"):
        self.model = model

    async def generate(self, prompt: str, system: str = "") -> str:
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system or "You are a Python code generator.",
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text

    def close(self):
        pass


class MiMoClient:
    """Client for MiMo Code Agent API."""

    def __init__(self, api_key: str = "", model: str = "mimo-auto"):
        self.api_key = api_key or os.environ.get("MIMO_API_KEY", "")
        self.model = model
        self.base_url = "https://mi.com"

    async def generate(self, prompt: str, system: str = "") -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self.base_url}",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                json={"model": self.model, "messages": messages},
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]

    def close(self):
        pass


class OpenRouterClient:
    """Client for OpenRouter API (cloud LLM fallback)."""

    def __init__(self, api_key: str = "", model: str = "openai/gpt-4o-mini"):
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        self.model = model
        self.base_url = "https://openrouter.ai/api/v1"

    async def generate(self, prompt: str, system: str = "") -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                json={"model": self.model, "messages": messages},
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]

    def close(self):
        pass


class OllamaClient:
    """Fallback client for Ollama local LLM."""

    def __init__(self, model: str = "gemma3:latest", base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = base_url

    async def generate(self, prompt: str, system: str = "") -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self.base_url}/api/chat",
                json={"model": self.model, "messages": messages, "stream": False},
            )
            response.raise_for_status()
            return response.json()["message"]["content"]

    def close(self):
        pass


@dataclass
class StepResult:
    step: int
    instruction: str
    constraint: str | None = None
    audit_gate: bool = False
    generated_code: str = ""
    target_file: str = ""
    success: bool = True
    error: str = ""


@dataclass
class CodeGenReport:
    chart_name: str
    steps: list[StepResult] = field(default_factory=list)
    files_written: list[str] = field(default_factory=list)

    @property
    def success_count(self) -> int:
        return len([s for s in self.steps if s.success])


# Fixed DDD layout for generate_ddd_service()'s from-scratch scaffold — one
# LLM call per file, generated in dependency order (domain has no deps,
# application depends on domain, infrastructure/api depend on both).
_DDD_SERVICE_FILES: list[tuple[str, str]] = [
    ("domain/entities.py", "domain entity classes with identity and business invariants"),
    ("domain/value_objects.py", "immutable value objects with validation"),
    ("domain/aggregate.py", "the aggregate root enforcing consistency boundaries"),
    ("domain/repository.py", "an abstract repository interface (ABC) for the aggregate"),
    ("domain/events.py", "domain events raised by the aggregate"),
    ("application/dto.py", "request/response DTOs (Pydantic models) for the API layer"),
    ("application/commands.py", "command handlers: create/update/delete use cases"),
    ("application/queries.py", "query handlers: get/list use cases"),
    ("infrastructure/repository_impl.py", "a MongoDB-backed implementation of the repository interface"),
    ("infrastructure/persistence.py", "MongoDB client/database connection setup"),
    ("api/routes.py", "a FastAPI APIRouter exposing CRUD endpoints via the command/query handlers"),
    ("main.py", "the FastAPI app entrypoint that mounts the router and configures the DB connection"),
]


class CodeGenAgent:
    """Generates Python code from compiled somatic prompts using LLM API.

    Provider priority:
    1. Ollama local (qwen2.5-coder:7b) — free, fast, no API key needed
    2. OpenRouter (openai/gpt-4o-mini) — cheap, good for code generation
    """

    def __init__(self, output_dir: Path | None = None, model: str = ""):
        self.output_dir = output_dir or GENERATED_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.reports: list[CodeGenReport] = []
        self._system_prompt = (
            "You are a Python code generator for MonkeyBrain, a Cognitive Operating System. "
            "Output ONLY valid Python code, no markdown fences. "
            "Preserve existing API, imports, and structure."
        )

        # Provider order: whichever CODEGEN_PRIMARY names goes first, the
        # other two remain fallbacks in a fixed order behind it.
        primary = os.environ.get("CODEGEN_PRIMARY", "ollama").lower()
        try_fns = {
            "openrouter": self._try_openrouter,
            "claude": self._try_claude,
            "ollama": self._try_ollama,
        }
        order = [primary] + [p for p in ("openrouter", "claude", "ollama") if p != primary]
        providers = [try_fns[p] for p in order]
        for try_fn in providers:
            client = try_fn(model)
            if client:
                self.llm = client
                return

        logger.warning("No LLM available — codegen will mirror src/ files")
        self.llm = None

    def _try_openrouter(self, model: str) -> "OpenRouterClient | None":
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            logger.warning("OpenRouter unavailable (OPENROUTER_API_KEY not set) — trying next provider")
            return None
        client = OpenRouterClient(api_key=api_key, model=model or os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini"))
        logger.info("Using OpenRouter (%s)", client.model)
        return client

    def _try_claude(self, model: str) -> "ClaudeClient | None":
        try:
            import anthropic
            anthropic.Anthropic()  # raises if ANTHROPIC_API_KEY missing
            client = ClaudeClient(model=model or "claude-sonnet-4-6")
            logger.info("Using Claude (claude-sonnet-4-6)")
            return client
        except Exception as e:
            logger.warning("Claude unavailable (falling back to Ollama): %s", e)
            return None

    def _try_ollama(self, model: str) -> "OllamaClient | None":
        try:
            import socket
            s = socket.socket()
            s.settimeout(1)
            reachable = s.connect_ex(("localhost", 11434)) == 0
            s.close()
            if reachable:
                client = OllamaClient(model=model or "qwen2.5-coder:7b")
                logger.info("Using Ollama local (qwen2.5-coder:7b)")
                return client
        except Exception as e:
            logger.debug("Ollama socket check failed: %s", e)
        return None

    def run_all(self, prompts: list[dict]) -> list[CodeGenReport]:
        self.reports = []
        for prompt in prompts:
            report = self.run_prompt(prompt)
            self.reports.append(report)
        return self.reports

    def run_prompt(self, prompt: dict) -> CodeGenReport:
        chart_name = prompt.get("chart", "unknown")
        preamble = prompt.get("preamble", "")
        steps = prompt.get("steps", [])
        constraints = prompt.get("constraints", [])

        report = CodeGenReport(chart_name=chart_name)

        src_dir = SRC_ROOT / chart_name
        if not src_dir.exists():
            return report

        src_files = [
            f for f in sorted(src_dir.rglob("*.py"))
            if f.name != "__init__.py"
        ]
        if not src_files:
            return report

        # Build all (rel, existing_code, gen_prompt) tuples up front
        tasks = []
        for src_file in src_files:
            rel = str(src_file.relative_to(SRC_ROOT))
            existing_code = src_file.read_text()
            gen_prompt = self._build_gen_prompt(
                chart_name, rel, preamble, steps, constraints, existing_code
            )
            tasks.append((rel, existing_code, gen_prompt))

        # Run all LLM calls in parallel via asyncio.gather
        results = asyncio.run(self._generate_all(tasks))

        for (rel, existing_code, _), generated in zip(tasks, results):
            self._write_file(rel, generated)
            report.steps.append(StepResult(
                step=0, instruction=f"generate {rel}",
                generated_code=generated, target_file=rel, success=True,
            ))
            report.files_written.append(rel)

        return report

    async def _generate_all(self, tasks: list[tuple]) -> list[str]:
        """Generate code for all files in parallel."""
        async def _one(rel: str, existing_code: str, gen_prompt: str) -> str:
            if not self.llm:
                return existing_code
            try:
                generated = await self.llm.generate(gen_prompt, system=self._system_prompt)
                return self._clean_code(generated)
            except Exception as e:
                logger.warning("LLM generation failed for %s: %s", rel, e)
                return existing_code

        return list(await asyncio.gather(*[_one(*t) for t in tasks]))

    async def generate_ddd_service(self, prompt_content: str, service_slug: str, output_dir: Path) -> dict[str, str]:
        """Generate a brand-new DDD-layered service from scratch.

        Unlike run_prompt()/run_all() — which require an existing
        SRC_ROOT/<chart_name>/ directory to mirror and regenerate — this has
        nothing to mirror: the compiled prompt (preamble/CoT steps/
        constraints from the .prompt.md file) is the only specification.
        One focused LLM call per file, run serially (a local Ollama backend
        chokes on concurrent generation requests), each given every
        already-generated file as context so later layers (application,
        infrastructure, api) reference the right class/function names from
        earlier ones (domain).

        Async (unlike run_prompt()/run_all(), which are sync and use
        asyncio.run() internally) because its only caller, ServiceGenAgent,
        already runs inside an active event loop (the SDLC pipeline's own
        async execution) — asyncio.run() would raise "cannot be called from
        a running event loop" there.
        """
        if not self.llm:
            logger.warning("[codegen] no LLM available — cannot generate a new service from scratch")
            return {}

        files: dict[str, str] = {}
        for rel_path, description in _DDD_SERVICE_FILES:
            prompt = self._build_scaffold_prompt(service_slug, rel_path, description, prompt_content, files)
            try:
                generated = await self.llm.generate(prompt, system=self._scaffold_system_prompt)
                files[rel_path] = self._clean_code(generated)
            except Exception as e:
                logger.warning("[codegen] scaffold generation failed for %s: %s", rel_path, e)
        return files

    @property
    def _scaffold_system_prompt(self) -> str:
        return (
            "You are a Python code generator for MonkeyBrain, a Cognitive Operating System. "
            "Generate a single Domain-Driven Design layer file for a brand-new FastAPI "
            "microservice — there is no existing code to preserve, you are writing it from "
            "scratch. Output ONLY valid Python code, no markdown fences, no prose."
        )

    def _build_scaffold_prompt(
        self, service_slug: str, rel_path: str, description: str,
        prompt_content: str, already_generated: dict[str, str],
    ) -> str:
        parts = [
            f"Service: {service_slug}",
            f"File to generate: {rel_path}",
            f"Purpose: {description}",
            "",
            "## Specification (compiled prompt for this service):",
            prompt_content[:4000],
        ]
        if already_generated:
            parts.append("\n## Already-generated files in this service (reference their exact class/function names, don't redefine them):")
            for path, content in already_generated.items():
                parts.append(f"\n### {path}\n```python\n{content[:1500]}\n```")
        parts.append(
            f"\nWrite the complete contents of {rel_path}. Use standard DDD conventions: "
            "domain/ has no dependencies on application/infrastructure/api; application/ "
            "depends only on domain/; infrastructure/ implements domain/ interfaces; api/ "
            "wires a FastAPI APIRouter to application/ handlers. Output ONLY the Python code:"
        )
        return "\n".join(parts)

    def _build_gen_prompt(self, chart_name: str, file_path: str, preamble: str, steps: list, constraints: list, existing_code: str) -> str:
        parts = [
            f"You are generating Python code for the MonkeyBrain Cognitive Operating System.",
            f"Module: {chart_name}",
            f"File: {file_path}",
            "",
            "## Existing code (reference):",
            "```python",
            existing_code[:2000],
            "```",
            "",
            "## Requirements:",
        ]
        for s in steps[:5]:
            parts.append(f"- {s.get('instruction', '')}")
        if constraints:
            parts.append("\n## Constraints (MUST NOT violate):")
            for c in constraints:
                parts.append(f"- {c}")
        parts.append("\nRegenerate the code preserving the exact same API, imports, and structure. Output ONLY the Python code:")
        return "\n".join(parts)

    def _clean_code(self, code: str) -> str:
        code = code.strip()
        if code.startswith("```python"):
            code = code[len("```python"):]
        elif code.startswith("```"):
            code = code[3:]
        if code.endswith("```"):
            code = code[:-3]
        return code.strip()

    async def _call_llm(self, user: str, system: str = "") -> str:
        """Call the LLM directly — for callers already running inside an
        async context (fixit_agent.py, test_service_agent.py's correction
        loop). Returns raw text; "" if no LLM provider is available."""
        if not self.llm:
            return ""
        return await self.llm.generate(user, system=system or self._system_prompt)

    def _run_llm_sync(self, user: str, system: str = "") -> str:
        """Call the LLM and block for the result — for callers that cannot
        await (client_gen_agent.py's async _impl calls this without await;
        executor.py's healing chain is plain sync all the way up).

        Runs the async call on a dedicated thread with its own event loop
        rather than asyncio.run() on the current thread: run_prompt() above
        does exactly that (`asyncio.run(self._generate_all(tasks))`), but
        SittingFaceCodegenCapability.execute() already has to wrap run_all()
        in run_in_executor() to call it safely from a running loop — proof
        that a bare asyncio.run() here would raise "cannot be called from a
        running event loop" the moment this is reached from client_gen_
        agent.py's own async _impl, which runs on the broca dispatch loop.
        """
        if not self.llm:
            return ""
        import concurrent.futures

        def _runner() -> str:
            return asyncio.run(self.llm.generate(user, system=system or self._system_prompt))

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_runner).result()

    def _parse_file_blocks(self, raw: str) -> dict[str, str]:
        """Parse the '=== FILE: <path> ===\\n<content>\\n=== END FILE ===' format
        every caller's system prompt asks the LLM to use (client_gen_agent.py,
        executor.py's _llm_fix_from_errors, fixit_agent.py, test_service_
        agent.py, and soma.py's correction pass all share this exact
        convention) into {relative_path: content}. Missing this parser is why
        all five of those correction/generation paths raised AttributeError
        instead of ever writing a file."""
        import re

        files: dict[str, str] = {}
        pattern = re.compile(
            r"===\s*FILE:\s*(?P<path>[^\n=]+?)\s*===\r?\n"
            r"(?P<content>.*?)"
            r"(?:\r?\n===\s*END FILE\s*===|\Z)",
            re.DOTALL,
        )
        for m in pattern.finditer(raw or ""):
            path = m.group("path").strip()
            if not path:
                continue
            files[path] = self._clean_code(m.group("content"))
        return files

    def _load_owns(self, chart_name: str) -> list[str]:
        import yaml
        somatic = Path("/Users/prashunjaveri/Code/monkeypatched/somatic/charts")
        values_file = somatic / chart_name / "values.yaml"
        if not values_file.exists():
            return []
        values = yaml.safe_load(values_file.read_text()) or {}
        module = values.get("module", {})
        return module.get("owns", [])

    def _determine_targets(self, chart_name: str, instruction: str, owns: list[str]) -> list[str]:
        """Map instruction to owned files."""
        instr_lower = instruction.lower()
        targets = []

        # Map instruction keywords to files
        keyword_map = {
            "world_model": ["world_model.py"],
            "simulator": ["simulator.py"],
            "prediction": ["prediction.py"],
            "replay": ["replay.py"],
            "counterfactual": ["counterfactual.py"],
            "digital_twin": ["digital_twin.py"],
            "experience": ["experience.py"],
            "feedback": ["feedback.py"],
            "reward": ["reward.py"],
            "loss": ["loss.py"],
            "cost": ["cost.py"],
            "xavier": ["xavier.py"],
            "world_state": ["world_state.py"],
            # world_model_simulation is a hand-written critical file — excluded from codegen
            "control_plane": ["control_plane.py"],
            "fleet": ["fleet_analytics.py"],
            "aggregation": ["aggregation.py"],
            "knowledge": ["knowledge_aggregator.py"],
            "digital_twin_aggregator": ["digital_twin_aggregator.py"],
            "elasticsearch_adapter": ["elasticsearch_adapter.py"],
            "lemon": ["lemon.py"],
            "tracing": ["tracing.py"],
            "logging": ["logging.py"],
            "metrics": ["metrics.py"],
            "health": ["health.py"],
            "alerting": ["alerting.py"],
            "governance": ["governance/governance.py"],
            "architecture_validator": ["governance/architecture_validator.py"],
            "compliance": ["governance/compliance.py"],
            "policy_registry": ["governance/policy_registry.py"],
            "benchmark": ["benchmark/runner.py", "benchmark/validator.py", "benchmark/reporter.py", "benchmark/scenario_runner.py"],
            "seeder": ["seed/seeder.py"],
            "seed_data": ["seed/seed_data.py"],
            "scenario_builder": ["seed/scenario_builder.py"],
            "event_generator": ["seed/event_generator.py"],
            "benchmark_generator": ["seed/benchmark_generator.py"],
            "runner": ["testing/runner.py"],
            "performance": ["testing/performance.py"],
            "profiler": ["testing/profiler.py"],
            "reporter": ["testing/reporter.py"],
            "sync_manager": ["sync_manager.py"],
            "edge_node": ["edge_node.py"],
            "cloud_aggregator": ["cloud_aggregator.py"],
            "agent": ["agent.py"],
        }

        for keyword, files in keyword_map.items():
            if keyword in instr_lower:
                for f in files:
                    full = f"{chart_name}/{f}"
                    if full not in targets:
                        targets.append(full)

        # Fallback: use owns list
        if not targets and owns:
            for own in owns:
                if own.endswith(".py"):
                    targets.append(f"{chart_name}/{own}")
                else:
                    targets.append(f"{chart_name}/{own}.py")

        return targets

    def _generate_code_for_file(self, chart_name: str, target_file: str, instruction: str, constraint: str | None) -> str:
        filename = Path(target_file).name

        src_path = SRC_ROOT / target_file
        if src_path.exists():
            existing = src_path.read_text()
            return existing

        return f'''\
"""{filename} — {instruction}

Module: {chart_name}
Constraint: {constraint or 'none'}
"""

from __future__ import annotations

# Generated from somatic chart: {chart_name}
# Instruction: {instruction}
'''

    def _write_file(self, target: str, code: str) -> None:
        path = self.output_dir / target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code + "\n")
        logger.info("Written: %s", target)

    def summary(self) -> dict:
        total_steps = sum(len(r.steps) for r in self.reports)
        total_files = len(set(f for r in self.reports for f in r.files_written))
        return {
            "prompts_run": len(self.reports),
            "total_steps": total_steps,
            "files_written": total_files,
            "output_dir": str(self.output_dir),
        }
