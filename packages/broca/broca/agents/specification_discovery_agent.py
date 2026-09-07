"""SpecificationDiscoveryAgent — Autonomous Specification Discovery.

Transforms a natural language intent into a structured, reviewable
specification by inferring: domain, required capabilities, knowledge
sources, providers, agents, security requirements, AND the recommended
execution workflow (DAG).

This is the new first stage of the SDLC pipeline — before any code
is generated, the system becomes a systems analyst and asks for human
approval on the inferred specification and workflow.

Flow:
    User Intent
        → Intent Analysis (extract goal, domain, constraints)
        → Domain Discovery (identify application, platforms, APIs)
        → Capability Discovery (determine required agents, providers)
        → Knowledge Discovery (find knowledge sources, memory, history)
        → Workflow Discovery (build recommended execution DAG)
        → Draft Specification (structured YAML/JSON spec + workflow)
        → Human Approval Gate (approve / reject / edit)
        → ETASS Pipeline (existing SDLC stages)
"""
from __future__ import annotations

import json
import logging
import os
import re
import socket
from typing import Any

from ._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.spec_discovery")


class OllamaClient:
    """Local Ollama LLM client for specification discovery."""

    def __init__(self, model: str = "gemma3:latest", base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = base_url

    async def generate(self, prompt: str, system: str = "") -> str:
        import httpx
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


class ClaudeClient:
    """Anthropic Claude client for specification discovery."""

    def __init__(self, model: str = "claude-sonnet-4-6"):
        self.model = model

    async def generate(self, prompt: str, system: str = "") -> str:
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system or "You are a systems analyst.",
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text


class OpenRouterClient:
    """OpenRouter (multi-model proxy) client — the primary provider (see
    _select_provider below); Claude/Ollama remain fallbacks."""

    def __init__(self, model: str = "", api_key: str = ""):
        self.model = model or os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        self.base_url = os.environ.get("OPENROUTER_API_BASE_URL") or os.environ.get(
            "OPENROUTER_API_URL", "https://openrouter.ai/api/v1",
        )

    async def generate(self, prompt: str, system: str = "") -> str:
        import httpx
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "HTTP-Referer": os.environ.get("APP_URL", "https://github.com/monkeypatched"),
                    "X-Title": os.environ.get("APP_NAME", "MonkeyBrain"),
                },
                json={"model": self.model, "messages": messages, "max_tokens": 4096},
            )
            resp.raise_for_status()
            data = resp.json()
        if "error" in data:
            raise RuntimeError(f"OpenRouter error: {data['error']}")
        return data["choices"][0]["message"]["content"] or ""


def _ollama_available() -> bool:
    try:
        s = socket.socket()
        s.settimeout(1)
        reachable = s.connect_ex(("localhost", 11434)) == 0
        s.close()
        return reachable
    except Exception:
        return False


def _claude_available() -> bool:
    try:
        import anthropic
        anthropic.Anthropic()
        return True
    except Exception:
        return False


class SpecificationDiscoveryAgent(BaseETASSAgent):
    agent_type = "specification_discovery"
    description = (
        "Autonomous Specification Discovery — infers a structured specification "
        "and recommended workflow from natural language intent, producing a "
        "reviewable draft before the SDLC pipeline begins"
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._llm = self._select_provider()

    def _select_provider(self):
        from ._llm_bridge import dev_bridge_client
        from ._llm_cache import maybe_cache
        bridge = dev_bridge_client("spec_discovery")
        if bridge is not None:
            logger.info("[spec_discovery] Using dev LLM bridge")
            return maybe_cache(bridge, "spec_discovery")
        provider = os.environ.get("SPEC_DISCOVERY_PROVIDER", "").lower()
        if provider == "heuristic":
            logger.info("[spec_discovery] Using heuristic mode (no LLM)")
            return None
        if provider == "claude" and _claude_available():
            logger.info("[spec_discovery] Using Claude as LLM provider")
            return maybe_cache(ClaudeClient(), "spec_discovery")
        if _ollama_available():
            model = os.environ.get("OLLAMA_MODEL", "gemma3:latest")
            logger.info("[spec_discovery] Using Ollama (%s) as LLM provider", model)
            return maybe_cache(OllamaClient(model=model), "spec_discovery")
        if _claude_available():
            logger.info("[spec_discovery] Using Claude as LLM provider (Ollama unavailable)")
            return maybe_cache(ClaudeClient(), "spec_discovery")
        logger.info("[spec_discovery] No LLM available — using heuristic discovery")
        return None

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict):
        intent = str(
            context.get("question")
            or context.get("intent")
            or context.get("ask")
            or ""
        ).strip()

        if not intent:
            self._reward(False, 0.0)
            return self._result(
                payload={"discovered": False},
                observations=["no intent/question in context"],
            )

        discovery = await self._discover(intent, context)

        workflow = self._build_workflow(intent, discovery)

        spec = self._build_spec(intent, discovery, workflow)

        approval = {
            "status": "pending",
            "message": (
                "Autonomous Specification Discovery complete. "
                "Review the inferred specification and recommended workflow below."
            ),
        }

        # discovery["agents"] is a list of plain strings from the LLM path
        # (per _discovery_prompt's spec) but a list of {"name": ..., "type":
        # ..., "description": ...} dicts from _heuristic_discover's fallback
        # — normalize to strings for the ', '.join() below either way.
        agent_list = [a["name"] if isinstance(a, dict) else str(a) for a in discovery.get("agents", [])]
        workflow_steps = [s["name"] for s in workflow.get("steps", [])]

        self._reward(discovery.get("confidence", 0) >= 0.5, 0.4)
        return self._result(
            payload={
                "discovered": True,
                "intent": intent,
                "discovery": discovery,
                "workflow": workflow,
                "specification": spec,
                "approval": approval,
            },
            observations=[
                f"Discovered {len(agent_list)} agents: {', '.join(agent_list)}",
                f"Discovered {len(discovery.get('providers', []))} providers",
                f"Discovered {len(discovery.get('knowledge_sources', []))} knowledge sources",
                f"Workflow: {len(workflow_steps)} steps — {' → '.join(workflow_steps[:5])}{'...' if len(workflow_steps) > 5 else ''}",
                f"Confidence: {discovery.get('confidence', 0):.0%}",
                f"Requires approval: {approval['status']}",
            ],
        )

    async def _discover(self, intent: str, context: dict) -> dict:
        """Multi-phase discovery using LLM analysis."""
        if self._llm is not None:
            try:
                raw = await self._llm.generate(
                    self._discovery_prompt(intent),
                    system=self._system_prompt(),
                )
                m = re.search(r"\{.*\}", raw, re.DOTALL)
                if m:
                    return json.loads(m.group(0))
            except Exception as e:
                logger.warning("[spec_discovery] LLM discovery failed: %s — falling back to heuristic", e)

        return self._heuristic_discover(intent)

    def _discovery_prompt(self, intent: str) -> str:
        return (
            f"Analyze this user intent and produce a comprehensive specification discovery:\n\n"
            f'"{intent}"\n\n'
            "Produce a JSON object with:\n"
            '1. "goal": what the user wants to achieve\n'
            '2. "domain": the application domain (e.g., "document_writing", "data_analysis", "web_automation")\n'
            '3. "platforms": external platforms/APIs needed (e.g., ["google_docs", "google_drive"])\n'
            '4. "capabilities": required capabilities as objects with "name", "type" ("agent"|"provider"|"tool"), "description", "required" (bool)\n'
            '5. "knowledge_sources": where to find domain knowledge (e.g., ["user_documents", "style_guide", "previous_work"])\n'
            '6. "agents": specific agent roles needed (e.g., ["writer", "reviewer", "style_analyzer"])\n'
            '7. "providers": external service providers needed (e.g., ["google_docs_api", "llm_provider"])\n'
            '8. "security": required permissions and auth (e.g., ["oauth2_google", "drive_read_write"])\n'
            '9. "constraints": non-functional requirements (e.g., ["no_data_retention", "user_style_matching"])\n'
            '10. "confidence": 0.0-1.0 how confident you are in this discovery\n'
            '11. "open_questions": list of questions that need human clarification\n'
            '12. "risks": list of potential risks or concerns\n\n'
            "Reply with ONLY the JSON object."
        )

    def _system_prompt(self) -> str:
        return (
            "You are a systems analyst for the MonkeyBrain Cognitive Operating System. "
            "Your job is to analyze a user's natural language intent and discover "
            "everything needed to build a software system that fulfills it. "
            "Think like a senior architect: consider integrations, data sources, "
            "security, compliance, and edge cases. Be specific about providers "
            "and agent roles. Output structured JSON only."
        )

    def _heuristic_discover(self, intent: str) -> dict:
        """Fallback when LLM is unavailable — keyword-based discovery."""
        intent_lower = intent.lower()

        platforms = []
        if any(w in intent_lower for w in ["google doc", "gdoc", "google docs"]):
            platforms.append("google_docs")
        if any(w in intent_lower for w in ["google drive", "gdrive"]):
            platforms.append("google_drive")
        if any(w in intent_lower for w in ["github", "git repo"]):
            platforms.append("github")
        if any(w in intent_lower for w in ["slack", "discord"]):
            platforms.append("messaging")
        if any(w in intent_lower for w in ["email", "gmail", "outlook"]):
            platforms.append("email")
        if any(w in intent_lower for w in ["database", "sql", "postgres", "mongo"]):
            platforms.append("database")
        if any(w in intent_lower for w in ["calendar", "google calendar"]):
            platforms.append("google_calendar")
        if any(w in intent_lower for w in ["sheet", "spreadsheet", "google sheet"]):
            platforms.append("google_sheets")
        if any(w in intent_lower for w in ["twitter", "x.com", "tweet"]):
            platforms.append("twitter")
        if any(w in intent_lower for w in ["notion"]):
            platforms.append("notion")

        agents = []
        if any(w in intent_lower for w in ["write", "essay", "blog", "post", "article", "content"]):
            agents.append({"name": "writer", "type": "agent", "description": "Content generation agent"})
        if any(w in intent_lower for w in ["review", "edit", "proofread", "grammar"]):
            agents.append({"name": "reviewer", "type": "agent", "description": "Content review and editing agent"})
        if any(w in intent_lower for w in ["style", "voice", "tone", "my writing"]):
            agents.append({"name": "style_analyzer", "type": "agent", "description": "Writing style analysis agent"})
        if any(w in intent_lower for w in ["search", "find", "lookup", "research"]):
            agents.append({"name": "researcher", "type": "agent", "description": "Research and information gathering agent"})
        if any(w in intent_lower for w in ["image", "diagram", "chart", "visual"]):
            agents.append({"name": "visualizer", "type": "agent", "description": "Visual content generation agent"})
        if any(w in intent_lower for w in ["data", "analyze", "analysis", "metrics"]):
            agents.append({"name": "analyst", "type": "agent", "description": "Data analysis agent"})
        if any(w in intent_lower for w in ["automate", "workflow", "pipeline"]):
            agents.append({"name": "orchestrator", "type": "agent", "description": "Workflow orchestration agent"})
        if any(w in intent_lower for w in ["schedule", "calendar", "meeting"]):
            agents.append({"name": "scheduler", "type": "agent", "description": "Scheduling and calendar agent"})
        if any(w in intent_lower for w in ["summarize", "summary", "tldr"]):
            agents.append({"name": "summarizer", "type": "agent", "description": "Content summarization agent"})

        if not agents:
            agents.append({"name": "assistant", "type": "agent", "description": "General-purpose assistant agent"})

        providers = []
        for p in platforms:
            providers.append({"name": f"{p}_api", "type": "provider", "description": f"{p} API integration"})
        providers.append({"name": "llm_provider", "type": "provider", "description": "Language model provider (Ollama local or cloud)"})

        knowledge_sources = []
        if any(w in intent_lower for w in ["style", "voice", "my writing", "previous"]):
            knowledge_sources.append("user_documents")
        if any(w in intent_lower for w in ["history", "previous", "past"]):
            knowledge_sources.append("conversation_history")
        if any(w in intent_lower for w in ["data", "database", "records"]):
            knowledge_sources.append("database_records")
        knowledge_sources.append("domain_knowledge")

        return {
            "goal": intent,
            "domain": self._infer_domain(intent_lower),
            "platforms": platforms,
            "capabilities": agents + providers,
            "knowledge_sources": knowledge_sources,
            "agents": [a["name"] for a in agents],
            "providers": [p["name"] for p in providers],
            "security": [f"oauth2_{p}" for p in platforms] if platforms else [],
            "constraints": [],
            "confidence": 0.4 if platforms else 0.3,
            "open_questions": ["Is this specification complete?"],
            "risks": [],
        }

    def _infer_domain(self, intent_lower: str) -> str:
        if any(w in intent_lower for w in ["write", "essay", "blog", "content", "article"]):
            return "content_generation"
        if any(w in intent_lower for w in ["data", "analyze", "analytics", "metrics"]):
            return "data_analysis"
        if any(w in intent_lower for w in ["automate", "workflow", "pipeline", "orchestrat"]):
            return "workflow_automation"
        if any(w in intent_lower for w in ["search", "find", "research", "lookup"]):
            return "information_retrieval"
        if any(w in intent_lower for w in ["build", "create", "generate", "develop"]):
            return "software_generation"
        if any(w in intent_lower for w in ["schedule", "calendar", "meeting"]):
            return "scheduling"
        return "general_assistant"

    def _build_workflow(self, intent: str, discovery: dict) -> dict:
        """Build a recommended execution workflow (DAG) from discovery results.

        Returns a workflow with ordered steps, dependencies, and the
        agent/capability assigned to each step.
        """
        # discovery["agents"] should be plain strings (both _discovery_prompt's
        # schema and _heuristic_discover's return value agree on that), but an
        # LLM response that doesn't follow the requested schema could still
        # hand back {"name": ...} dicts here — normalize defensively since
        # every check below is a plain `"x" in agents` membership test that
        # would silently always miss against un-normalized dicts.
        agents = [a["name"] if isinstance(a, dict) else str(a) for a in discovery.get("agents", [])]
        platforms = discovery.get("platforms", [])
        knowledge = discovery.get("knowledge_sources", [])

        steps = []

        step_id = 0

        if knowledge:
            steps.append({
                "id": step_id,
                "name": "knowledge_acquisition",
                "type": "discovery",
                "agent": "knowledge_retrieval",
                "description": f"Retrieve knowledge from: {', '.join(knowledge)}",
                "depends_on": [],
                "outputs": ["knowledge_context"],
            })
            step_id += 1

        if "style_analyzer" in agents:
            steps.append({
                "id": step_id,
                "name": "style_analysis",
                "type": "analysis",
                "agent": "style_analyzer",
                "description": "Analyze user's writing style from historical documents",
                "depends_on": [s["name"] for s in steps] if steps else [],
                "outputs": ["style_profile"],
            })
            step_id += 1

        if "researcher" in agents:
            steps.append({
                "id": step_id,
                "name": "research",
                "type": "discovery",
                "agent": "researcher",
                "description": "Research topic and gather supporting information",
                "depends_on": [],
                "outputs": ["research_material"],
            })
            step_id += 1

        if "analyst" in agents:
            steps.append({
                "id": step_id,
                "name": "data_analysis",
                "type": "analysis",
                "agent": "analyst",
                "description": "Analyze data and extract insights",
                "depends_on": [s["name"] for s in steps] if steps else [],
                "outputs": ["analysis_results"],
            })
            step_id += 1

        if "writer" in agents:
            write_deps = []
            if "style_analyzer" in agents:
                write_deps.append("style_analysis")
            if "researcher" in agents:
                write_deps.append("research")
            if "analyst" in agents:
                write_deps.append("data_analysis")
            if not write_deps and steps:
                write_deps = [steps[-1]["name"]]

            steps.append({
                "id": step_id,
                "name": "content_generation",
                "type": "execution",
                "agent": "writer",
                "description": "Generate content based on research, style, and analysis",
                "depends_on": write_deps,
                "outputs": ["draft_content"],
            })
            step_id += 1

        if "visualizer" in agents:
            steps.append({
                "id": step_id,
                "name": "visual_generation",
                "type": "execution",
                "agent": "visualizer",
                "description": "Generate visual assets (charts, diagrams, images)",
                "depends_on": ["content_generation"] if any(s["name"] == "content_generation" for s in steps) else [],
                "outputs": ["visual_assets"],
            })
            step_id += 1

        if "reviewer" in agents:
            review_deps = []
            if any(s["name"] == "content_generation" for s in steps):
                review_deps.append("content_generation")
            if any(s["name"] == "visual_generation" for s in steps):
                review_deps.append("visual_generation")

            steps.append({
                "id": step_id,
                "name": "review",
                "type": "validation",
                "agent": "reviewer",
                "description": "Review content for quality, grammar, and style consistency",
                "depends_on": review_deps,
                "outputs": ["reviewed_content"],
            })
            step_id += 1

        if "summarizer" in agents:
            steps.append({
                "id": step_id,
                "name": "summarize",
                "type": "execution",
                "agent": "summarizer",
                "description": "Create summary/TLDR of the final content",
                "depends_on": ["review"] if any(s["name"] == "review" for s in steps) else [],
                "outputs": ["summary"],
            })
            step_id += 1

        platform_steps = []
        for platform in platforms:
            steps.append({
                "id": step_id,
                "name": f"publish_{platform}",
                "type": "integration",
                "agent": f"{platform}_provider",
                "description": f"Publish/deliver content to {platform}",
                "depends_on": ["review"] if any(s["name"] == "review" for s in steps) else [],
                "outputs": [f"{platform}_result"],
            })
            platform_steps.append(f"publish_{platform}")
            step_id += 1

        if not steps:
            steps.append({
                "id": 0,
                "name": "execute",
                "type": "execution",
                "agent": agents[0] if agents else "assistant",
                "description": "Execute the primary task",
                "depends_on": [],
                "outputs": ["result"],
            })

        if "orchestrator" in agents and len(steps) > 1:
            steps.append({
                "id": step_id,
                "name": "orchestrate",
                "type": "coordination",
                "agent": "orchestrator",
                "description": "Coordinate all steps and handle failures",
                "depends_on": [s["name"] for s in steps],
                "outputs": ["final_result"],
            })

        return {
            "total_steps": len(steps),
            "steps": steps,
            "execution_order": self._topological_sort(steps),
            "parallel_groups": self._find_parallel_groups(steps),
            "estimated_complexity": "high" if len(steps) > 6 else "medium" if len(steps) > 3 else "low",
        }

    def _topological_sort(self, steps: list[dict]) -> list[list[str]]:
        """Return execution order as groups of concurrently runnable step names."""
        completed = set()
        order = []

        for _ in range(len(steps) + 1):
            ready = [
                s["name"] for s in steps
                if s["name"] not in completed
                and all(d in completed for d in s.get("depends_on", []))
            ]
            if not ready:
                break
            order.append(ready)
            completed.update(ready)

        return order

    def _find_parallel_groups(self, steps: list[dict]) -> list[list[str]]:
        """Find groups of steps that can run in parallel (no dependencies between them)."""
        visited = set()
        groups = []

        def _bfs(start: str) -> set[str]:
            queue = [start]
            reachable = set()
            while queue:
                current = queue.pop(0)
                if current in reachable:
                    continue
                reachable.add(current)
                for s in steps:
                    if current in s.get("depends_on", []):
                        queue.append(s["name"])
            return reachable

        for s in steps:
            if s["name"] in visited:
                continue
            descendants = _bfs(s["name"])
            parallel = [
                other["name"] for other in steps
                if other["name"] not in visited
                and other["name"] not in descendants
                and not any(other["name"] in d.get("depends_on", []) for d in [s])
                and not any(s["name"] in d.get("depends_on", []) for d in [other])
            ]
            if parallel:
                groups.append(parallel)
                visited.update(parallel)
            else:
                groups.append([s["name"]])
                visited.add(s["name"])

        return groups

    def _build_spec(self, intent: str, discovery: dict, workflow: dict) -> dict:
        """Build a structured specification from discovery + workflow results."""
        return {
            "specification": {
                "version": "1.0",
                "source": "autonomous_discovery",
                "intent": intent,
            },
            "application": {
                "domain": discovery.get("domain", "general"),
                "platforms": discovery.get("platforms", []),
                "description": discovery.get("goal", intent),
            },
            "providers": discovery.get("providers", []),
            "agents": discovery.get("agents", []),
            "capabilities": discovery.get("capabilities", []),
            "knowledge": {
                "sources": discovery.get("knowledge_sources", []),
                "memory_required": bool(discovery.get("knowledge_sources")),
            },
            "security": {
                "permissions": discovery.get("security", []),
                "auth_required": bool(discovery.get("security")),
            },
            "constraints": discovery.get("constraints", []),
            "workflow": workflow,
            "metadata": {
                "confidence": discovery.get("confidence", 0.0),
                "open_questions": discovery.get("open_questions", []),
                "risks": discovery.get("risks", []),
                "discovery_source": "SpecificationDiscoveryAgent",
                "llm_provider": type(self._llm).__name__ if self._llm else "heuristic",
            },
        }
