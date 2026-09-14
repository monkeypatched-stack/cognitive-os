"""GraphGeneratorAgent — uses LLM to dynamically generate an ExecutionGraph
from a specification. This is the recursive loop:

    Specification → LLM → ExecutionGraph → Agent Discovery → Execution

If agents don't exist in the registry, they are auto-generated before execution.

The graph compiler owns its own validation. It takes a candidate graph from the
LLM and produces either a valid executable DAG or a convergence failure.
Nothing in between leaks out of the compiler.
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
from dataclasses import dataclass, field
from typing import Any

from ._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.graph_generator")

MAX_REPAIR_ATTEMPTS = 3
CANDIDATE_COUNT = 3


# ── Edge kinds ────────────────────────────────────────────────────────────

# Control/feedback edges are NOT execution flow. They are deliberate back-edges
# (report → analyze, monitor → deploy) and must be excluded from anything that
# reasons about execution ORDER: acyclicity, topological sort, order validation.
# Same convention as GraphManager._topological_order and the CSR compiler's
# _FEEDBACK_REL. Keeping the definition in one place stops the three validators
# below from disagreeing about it — which is exactly how they came to.
FEEDBACK_REL = "feedback"


def _execution_edges(edges: list[dict]) -> list[dict]:
    """The edges that impose execution order — feedback/control edges removed."""
    return [e for e in edges if e.get("type") != FEEDBACK_REL]


# ── Exceptions ────────────────────────────────────────────────────────────


@dataclass
class GraphValidationError(Exception):
    """A single validation error in a candidate graph.

    Used both as a return value from _validate_graph() and as an
    exception when _validate_order() or _topological_order() detects
    a violation.
    """

    code: str = ""
    message: str = ""
    detail: dict = field(default_factory=dict)

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


class PlannerConvergenceError(Exception):
    """Raised when the graph compiler cannot produce a valid DAG after
    MAX_REPAIR_ATTEMPTS. The caller must not produce an execution order,
    compile an IntentIR, or register the graph with the Bellman policy.
    """

    def __init__(self, errors: list[GraphValidationError], attempts: int):
        self.errors = errors
        self.attempts = attempts
        codes = ", ".join(e.code for e in errors)
        super().__init__(f"Graph validation failed after {attempts} attempt(s): {codes}")


# ── LLM providers ─────────────────────────────────────────────────────────


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


class OllamaClient:
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
            resp = await client.post(
                f"{self.base_url}/api/chat",
                json={"model": self.model, "messages": messages, "stream": False},
            )
            resp.raise_for_status()
            return resp.json()["message"]["content"]


class OpenRouterClient:
    """OpenRouter (multi-model proxy) — the primary provider (see
    _select_provider below); Claude/Ollama remain fallbacks."""

    DEFAULT_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")

    def __init__(self, model: str | None = None, api_key: str = ""):
        self.model = model or self.DEFAULT_MODEL
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        self.base_url = os.environ.get("OPENROUTER_API_BASE_URL") or os.environ.get(
            "OPENROUTER_API_URL",
            "https://openrouter.ai/api/v1",
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
                json={
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": ClaudeClient.MAX_TOKENS,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        if "error" in data:
            raise RuntimeError(f"OpenRouter error: {data['error']}")
        return data["choices"][0]["message"]["content"] or ""


class ClaudeClient:
    # claude-sonnet-4-6 is a valid, active model. Override with GRAPH_GENERATOR_MODEL
    # (e.g. claude-opus-4-8 for the hardest graphs, claude-sonnet-5 for the current
    # Sonnet tier) — the graph compiler is an intelligence-sensitive structured-output
    # task, so the model is worth tuning per deployment rather than hardcoding.
    DEFAULT_MODEL = os.environ.get("GRAPH_GENERATOR_MODEL", "claude-sonnet-4-6")

    # The planner emits a whole execution graph as JSON. At 4096 a large graph is
    # truncated mid-object: the response comes back with stop_reason="max_tokens",
    # json.loads fails on the fragment, and the failure surfaces as an unexplained
    # PlannerConvergenceError rather than "the model ran out of output tokens".
    MAX_TOKENS = int(os.environ.get("GRAPH_GENERATOR_MAX_TOKENS", "16000"))

    def __init__(self, model: str | None = None):
        self.model = model or self.DEFAULT_MODEL
        self._client: Any = None

    async def generate(self, prompt: str, system: str = "") -> str:
        import anthropic

        # AsyncAnthropic, not Anthropic. The synchronous client's .create() blocks the
        # thread — and this is an `async def` on the request path, so a graph-generation
        # call (many seconds) blocked the event loop and stalled EVERY concurrent request
        # in the server for its whole duration. Reuse one client so connections pool.
        if self._client is None:
            self._client = anthropic.AsyncAnthropic()

        msg = await self._client.messages.create(
            model=self.model,
            max_tokens=self.MAX_TOKENS,
            system=system or "You are a software architect.",
            messages=[{"role": "user", "content": prompt}],
        )

        # `msg.content[0].text` assumed the first block is always text. It is not:
        # content is a list of typed blocks (thinking, tool_use, text, …), and on a
        # safety refusal it is EMPTY — so the old code raised IndexError/AttributeError
        # and the real reason was lost. Check stop_reason, then take the text blocks.
        if msg.stop_reason == "refusal":
            raise RuntimeError(
                f"Claude declined the graph-generation request (stop_reason=refusal, "
                f"category={getattr(getattr(msg, 'stop_details', None), 'category', None)})"
            )

        text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
        if not text:
            raise RuntimeError(
                f"Claude returned no text content (stop_reason={msg.stop_reason!r}, "
                f"blocks={[getattr(b, 'type', '?') for b in msg.content]})"
            )
        if msg.stop_reason == "max_tokens":
            raise RuntimeError(
                f"Claude hit max_tokens ({self.MAX_TOKENS}) — the execution graph JSON is "
                f"truncated and cannot be parsed. Raise GRAPH_GENERATOR_MAX_TOKENS."
            )
        return text


# ── Graph compiler ─────────────────────────────────────────────────────────


class GraphGeneratorAgent(BaseETASSAgent):
    """Generates an ExecutionGraph from a specification using LLM analysis.

    The graph compiler owns its full pipeline:

        Translation → Validation → Repair → Topological Order → Verification

    It either produces a valid executable DAG or raises
    PlannerConvergenceError. Nothing in between leaks out.
    """

    agent_type = "graph_generator"
    description = "Generates a dynamic ExecutionGraph from a specification using LLM analysis"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._llm = self._select_provider()

    def _select_provider(self):
        from ._llm_bridge import dev_bridge_client
        from ._llm_cache import maybe_cache

        bridge = dev_bridge_client("graph_generator")
        if bridge is not None:
            return maybe_cache(bridge, "graph_generator")
        if os.environ.get("SPEC_DISCOVERY_PROVIDER", "").lower() == "claude" and _claude_available():
            return maybe_cache(ClaudeClient(), "graph_generator")
        if _ollama_available():
            return maybe_cache(
                OllamaClient(model=os.environ.get("OLLAMA_MODEL", "gemma3:latest")),
                "graph_generator",
            )
        if _claude_available():
            return maybe_cache(ClaudeClient(), "graph_generator")
        return None

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict):
        # Accept GoalIR if provided (new pipeline), else fall back to raw intent
        goal_ir = context.get("goal_ir")
        if goal_ir is not None:
            return await self._impl_from_goal_ir(goal_ir, context)

        # Legacy path: raw intent without GoalIR
        spec = context.get("specification") or context.get("spec") or {}
        discovery = context.get("discovery") or {}
        knowledge = context.get("knowledge") or {}
        intent = context.get("intent") or context.get("question") or ""

        # Step 0: Extract domain name via LLM
        domain = await self._extract_domain(intent, spec)

        # Step 1: Discover reusable software agents from intent via LLM. `knowledge` is the
        # semantic-memory retrieval for this question — feeding it here is what makes the PLAN
        # change based on available knowledge (it used to be fetched and dropped).
        agents = await self._discover_agents(spec, discovery, intent, knowledge)

        # Step 2: Group discovered agents under the domain
        agent_map = self._map_features_to_agents(agents, domain)

        # Step 3: Build temporary nodes for dependency planning
        planning_nodes = []
        seen = set()

        for agent_list in agent_map.values():
            for agent in agent_list:
                agent = agent.strip()
                if not agent or agent in seen:
                    continue
                seen.add(agent)
                planning_nodes.append(
                    {
                        "id": f"node_{len(planning_nodes) + 1}",
                        "agent": agent,
                    }
                )

        # Step 4: Generate multiple candidate graphs
        # Each call to _infer_execution_dependencies() may produce different
        # edges due to LLM stochasticity. The graph compiler validates and
        # repairs each candidate. The Bellman policy selects the best.
        candidates = []
        for i in range(CANDIDATE_COUNT):
            try:
                dependency_edges = await self._infer_execution_dependencies(planning_nodes)
                graph = await self._build_graph(
                    agent_map,
                    domain,
                    dependency_edges,
                    intent,
                    planning_nodes,
                )
                candidates.append(graph)
            except PlannerConvergenceError:
                logger.warning("[graph_gen] Candidate %d failed validation, skipping", i)
                continue

        if not candidates:
            self._reward(False, 0.0)
            raise PlannerConvergenceError(
                errors=[
                    GraphValidationError(
                        code="NO_VALID_CANDIDATES",
                        message="No valid graph candidates generated",
                    )
                ],
                attempts=CANDIDATE_COUNT,
            )

        # Step 5: Return all candidates for policy selection
        # The caller (cognitive_runtime) uses the Bellman policy to select.
        self._reward(True, 0.5)
        return self._result(
            payload={
                "candidates": [
                    {
                        "graph": c.graph_data,
                        "execution_graph": c,
                        "execution_graph_id": c.id,
                    }
                    for c in candidates
                ],
                "agents": agents,
                "agent_map": agent_map,
                "node_count": (len(candidates[0].graph_data.get("nodes", [])) if candidates else 0),
                "edge_count": (len(candidates[0].graph_data.get("edges", [])) if candidates else 0),
            },
            observations=[
                f"Discovered {len(agents)} agents from intent",
                f"Generated {len(candidates)} valid candidate(s) from {CANDIDATE_COUNT} attempt(s)",
            ],
        )

    async def _impl_from_goal_ir(self, goal_ir, context: dict):
        """Generate execution graph from structured Goal IR (new pipeline path)."""

        spec = context.get("specification") or context.get("spec") or {}
        knowledge = context.get("knowledge") or {}
        intent = goal_ir.goal

        # Use domain from GoalIR (already extracted by Intent Compiler)
        domain = goal_ir.domain.capitalize()

        # Discover agents using GoalIR entities and constraints
        agents = await self._discover_agents_from_goal_ir(goal_ir, spec, knowledge)

        # Group discovered agents under the domain
        agent_map = self._map_features_to_agents(agents, domain)

        # Build temporary nodes for dependency planning
        planning_nodes = []
        seen = set()

        for agent_list in agent_map.values():
            for agent in agent_list:
                agent = agent.strip()
                if not agent or agent in seen:
                    continue
                seen.add(agent)
                planning_nodes.append(
                    {
                        "id": f"node_{len(planning_nodes) + 1}",
                        "agent": agent,
                    }
                )

        # Generate multiple candidate graphs
        candidates = []
        for i in range(CANDIDATE_COUNT):
            try:
                dependency_edges = await self._infer_execution_dependencies(planning_nodes)
                graph = await self._build_graph(
                    agent_map,
                    domain,
                    dependency_edges,
                    intent,
                    planning_nodes,
                )
                candidates.append(graph)
            except PlannerConvergenceError:
                logger.warning("[graph_gen] Candidate %d failed validation, skipping", i)
                continue

        if not candidates:
            self._reward(False, 0.0)
            raise PlannerConvergenceError(
                errors=[
                    GraphValidationError(
                        code="NO_VALID_CANDIDATES",
                        message="No valid graph candidates generated",
                    )
                ],
                attempts=CANDIDATE_COUNT,
            )

        self._reward(True, 0.5)
        return self._result(
            payload={
                "goal_ir": goal_ir.to_dict(),
                "candidates": [
                    {
                        "graph": c.graph_data,
                        "execution_graph": c,
                        "execution_graph_id": c.id,
                    }
                    for c in candidates
                ],
                "agents": agents,
                "agent_map": agent_map,
                "node_count": (len(candidates[0].graph_data.get("nodes", [])) if candidates else 0),
                "edge_count": (len(candidates[0].graph_data.get("edges", [])) if candidates else 0),
            },
            observations=[
                f"GoalIR: {goal_ir.intent_type}/{goal_ir.domain}, {len(goal_ir.entities)} entities, {len(goal_ir.constraints)} constraints",
                f"Discovered {len(agents)} agents from GoalIR",
                f"Generated {len(candidates)} valid candidate(s) from {CANDIDATE_COUNT} attempt(s)",
            ],
        )

    async def _discover_agents_from_goal_ir(self, goal_ir, spec: dict, knowledge: dict | None = None) -> list[str]:
        """Discover agents using GoalIR entities and constraints."""

        # Build a richer prompt using GoalIR structure
        entities_str = (
            ", ".join(f"{e.name} ({e.entity_type})" for e in goal_ir.entities) if goal_ir.entities else "none specified"
        )
        constraints_str = (
            ", ".join(f"{c.constraint_type}: {c.description}" for c in goal_ir.constraints)
            if goal_ir.constraints
            else "none"
        )
        relationships_str = (
            ", ".join(f"{r.source} {r.relationship_type} {r.target}" for r in goal_ir.relationships)
            if goal_ir.relationships
            else "none"
        )

        if self._llm is not None:
            try:
                prompt = (
                    f"Identify the smallest reusable software agents required to satisfy this request:\n"
                    f'"{goal_ir.goal}"\n\n'
                    f"Intent type: {goal_ir.intent_type}\n"
                    f"Domain: {goal_ir.domain}\n"
                    f"Entities: {entities_str}\n"
                    f"Constraints: {constraints_str}\n"
                    f"Relationships: {relationships_str}\n\n"
                    "Your task is NOT to solve the request. Decompose it into the smallest set of\n"
                    "independent, reusable agents.\n\n"
                    "Rules:\n"
                    "- Each agent has exactly one responsibility.\n"
                    "- Agents communicate only through explicit inputs and outputs.\n"
                    "- Agents should be independently executable and reusable by other workflows.\n"
                    "- Use domain-specific names; prefer existing software concepts.\n"
                    "- Do NOT create agents named Discovery, Analysis, Execution, Planning, Processing\n"
                    "  or Feature unless they are genuinely essential to the domain.\n"
                    "- Maximize opportunities for parallel execution; minimize coupling.\n"
                    "- Return ONLY a JSON array.\n"
                    "- Do not explain your reasoning.\n\n"
                    "Output ONLY the JSON array."
                )
                raw = await self._llm.generate(
                    prompt,
                    system=(
                        "You are an expert software architect and capability planner. "
                        "Return ONLY a JSON array of capability/agent names."
                    ),
                )
                m = re.search(r"\[.*\]", raw, re.DOTALL)
                if m:
                    return json.loads(m.group(0))
            except Exception as e:
                logger.warning("[graph_gen] LLM agent discovery failed: %s", e)

        return []

    # ── Agent discovery ───────────────────────────────────────────────

    async def _discover_agents(
        self,
        spec: dict,
        discovery: dict,
        intent: str,
        knowledge: dict | None = None,
    ) -> list[str]:
        if self._llm is not None:
            try:
                prompt = self._agent_discovery_prompt(spec, discovery, intent, knowledge)
                raw = await self._llm.generate(
                    prompt,
                    system=(
                        "You are an expert software architect and capability planner. "
                        "Return ONLY a JSON array of capability/agent names."
                    ),
                )
                m = re.search(r"\[.*\]", raw, re.DOTALL)
                if m:
                    return json.loads(m.group(0))
            except Exception as e:
                logger.warning("[graph_gen] LLM agent discovery failed: %s", e)

        return []

    @staticmethod
    def _summarize_knowledge(knowledge: dict | None, *, limit: int = 8, width: int = 240) -> str:
        """Condense retrieved semantic-memory knowledge into prompt bullets.

        Returns "" when there is nothing to say, so the discovery prompt is byte-for-byte
        unchanged for the no-knowledge case (backward compatible) and only grows when the
        question actually has retrieved context to plan against.
        """
        if not isinstance(knowledge, dict):
            return ""
        results = knowledge.get("results") or knowledge.get("hits") or []
        if not isinstance(results, list):
            return ""
        bullets: list[str] = []
        for item in results[:limit]:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content") or item.get("summary") or ""
                source = item.get("source") or item.get("collection") or ""
            else:
                text, source = str(item), ""
            text = " ".join(str(text).split())[:width]
            if text:
                bullets.append(f"- {text}" + (f"  (source: {source})" if source else ""))
        return "\n".join(bullets)

    def _agent_discovery_prompt(
        self,
        spec: dict,
        discovery: dict,
        intent: str,
        knowledge: dict | None = None,
    ) -> str:
        # `intent` is the user's RAW question. The planning guidance below used to be prepended
        # to the question by the /plan route and carried around as part of the "intent", which
        # meant every downstream LLM call (domain extraction especially) was fed the rules and
        # the worked examples as if the user had written them. The guidance belongs here, in
        # the prompt of the agent that actually needs it — not inside the intent.
        #
        # The knowledge block is what makes the plan adapt: retrieved facts/prior context steer
        # which agents get chosen (reuse known capabilities, don't invent agents for things the
        # knowledge already covers). Empty when nothing was retrieved, so the prompt is unchanged.
        summary = self._summarize_knowledge(knowledge)
        knowledge_block = (
            (
                "Known facts and prior context retrieved for this request. Use them to choose\n"
                "agents that reuse what is already known, and do NOT invent agents for capabilities\n"
                "the knowledge below already establishes:\n"
                f"{summary}\n\n"
            )
            if summary
            else ""
        )
        return (
            "Identify the smallest reusable software agents required to satisfy this request:\n"
            f'"{intent}"\n\n'
            f"Specification:\n{json.dumps(spec, indent=2)[:1000]}\n\n"
            f"{knowledge_block}"
            "Your task is NOT to solve the request. Decompose it into the smallest set of\n"
            "independent, reusable agents.\n\n"
            "Rules:\n"
            "- Each agent has exactly one responsibility.\n"
            "- Agents communicate only through explicit inputs and outputs.\n"
            "- Agents should be independently executable and reusable by other workflows.\n"
            "- Use domain-specific names; prefer existing software concepts.\n"
            "- Do NOT create agents named Discovery, Analysis, Execution, Planning, Processing\n"
            "  or Feature unless they are genuinely essential to the domain.\n"
            "- If the domain has well-known components (compiler, blog engine, search engine,\n"
            "  database, web crawler), model those components directly.\n"
            "- Maximize opportunities for parallel execution; minimize coupling.\n"
            "- Return ONLY a JSON array.\n"
            "- Do not explain your reasoning.\n\n"
            "Example output:\n"
            '["ProjectAgent", "TaskAgent", "ReminderAgent", "NotificationAgent", "StorageAgent"]\n\n'
            "Output ONLY the JSON array."
        )

    def _map_features_to_agents(self, agents: list[str], domain: str) -> dict[str, list[str]]:
        return {
            f"{domain}Agents": agents,
        }

    async def _extract_domain(self, intent: str, spec: dict) -> str:
        goal = spec.get("goal", intent) or intent

        if self._llm is not None:
            try:
                prompt = f'Extract a single domain name from this intent: "{goal}". Output ONLY the domain name, nothing else. Example: "build a todo app" → "Todo"'
                raw = await self._llm.generate(prompt, system="Output a single word or CamelCase name.")
                domain = raw.strip().strip('"').strip("'")
                if domain and len(domain) < 30:
                    return domain
            except Exception:
                pass

        words = goal.split()
        for i, w in enumerate(words):
            if w.lower() in (
                "build",
                "create",
                "make",
                "develop",
                "write",
            ) and i + 1 < len(words):
                next_word = words[i + 1]
                if next_word.lower() in ("a", "an", "the"):
                    if i + 2 < len(words):
                        return words[i + 2].capitalize()
                return next_word.capitalize()
        return "App"

    # ── Dependency inference ──────────────────────────────────────────

    async def _infer_execution_dependencies(
        self,
        nodes: list[dict],
    ) -> list[dict]:
        """Infer execution dependencies between discovered agents using the LLM."""

        if len(nodes) <= 1:
            return []

        if self._llm is None:
            return []

        agent_names = [n["agent"] for n in nodes]
        agent_list_str = "\n".join(f"- {name}" for name in agent_names)

        prompt = f"""You are an expert software architect.

Given these agents, construct a dependency graph describing the order in which
they must execute.

Agents:

{agent_list_str}

Rules:

- Output ONLY valid JSON. No prose, no markdown fences.
- Use ONLY the agent names supplied above.
- Do NOT invent agents.
- The graph MUST be acyclic.
- Every edge means "from" must complete before "to" begins:

    from
        ↓
    to

- An agent that PROVIDES something (storage, a database, a connection, a schema, a
  configuration, a fetched resource) is a PREREQUISITE of every agent that uses it. It comes
  FIRST, with edges pointing OUT of it to its consumers. It is never the last node: nothing
  downstream can consume what a terminal node produces.
- Prefer maximum parallel execution — add a dependency only when "to" genuinely cannot start
  until "from" has finished. Agents that only share a prerequisite, and do not feed each
  other, must NOT be chained: give them each an edge from the prerequisite instead. A chain
  of every agent in a line is almost always wrong.

Return a JSON array of edges in this exact shape:

[
  {{"from": "AgentA", "to": "AgentB"}}
]

Example — note the shape: the provider (StorageAgent) runs FIRST and fans out to the three
agents that need it; those three are independent of one another and therefore run in
parallel; only the agent that genuinely needs all of them waits for them:

[
  {{"from": "StorageAgent", "to": "TaskCreateAgent"}},
  {{"from": "StorageAgent", "to": "TaskQueryAgent"}},
  {{"from": "StorageAgent", "to": "TaskCompleteAgent"}},
  {{"from": "TaskCreateAgent", "to": "NotifyAgent"}},
  {{"from": "TaskCompleteAgent", "to": "NotifyAgent"}}
]

A strictly linear A -> B -> C -> D chain is correct ONLY when each agent literally consumes
the previous agent's output (e.g. a compiler: Lexer -> Parser -> CodeGen). Do not default to it.
"""

        try:
            response = await self._llm.generate(
                prompt,
                system=("You are an expert software architect. Return ONLY a JSON array of dependency edges."),
            )
            m = re.search(r"\[.*\]", response, re.DOTALL)
            deps = json.loads(m.group(0)) if m else []
        except Exception as e:
            logger.warning("[graph_gen] LLM dependency inference failed: %s", e)
            return []

        id_lookup = {n["agent"]: n["id"] for n in nodes}

        edges = []

        for dep in deps:
            src = id_lookup.get(dep.get("from"))
            dst = id_lookup.get(dep.get("to"))

            if src and dst and src != dst:
                edges.append(
                    {
                        "from": src,
                        "to": dst,
                    }
                )

        return edges

    # ── Graph compilation (the public API) ────────────────────────────

    async def _build_graph(
        self,
        agent_map: dict[str, list[str]],
        domain: str,
        dependency_edges: list[dict] | None = None,
        intent: str = "",
        planning_nodes: list[dict] | None = None,
    ) -> Any:
        """Build a compiled ExecutionGraph from discovered agents and edges.

        This is the graph compiler's public API. It owns the complete pipeline:

            Translation → Validation → Repair → Ordering → Verification → ExecutionGraph

        Returns a compiled ExecutionGraph object with graph_data attached.
        Raises PlannerConvergenceError if the graph cannot be repaired.
        """
        from src.monkey_brain.kernel.execute.graph import (
            ExecutionGraph,
            GraphNode,
            GraphEdge,
        )

        planning_nodes = planning_nodes or []

        # 1. Translate planner output into a raw graph
        graph_data = self._build_raw_graph(agent_map, domain, dependency_edges)

        # 2. Validate and repair loop
        last_errors = self._validate_graph(
            graph_data.get("nodes", []),
            graph_data.get("edges", []),
        )

        for attempt in range(MAX_REPAIR_ATTEMPTS):
            if not last_errors:
                break

            repaired_edges = await self._repair_dependencies(
                graph_data,
                last_errors,
                intent,
                planning_nodes,
            )
            graph_data = self._build_raw_graph(agent_map, domain, repaired_edges)

            last_errors = self._validate_graph(
                graph_data.get("nodes", []),
                graph_data.get("edges", []),
            )

        if last_errors:
            raise PlannerConvergenceError(
                errors=last_errors,
                attempts=min(attempt + 1, MAX_REPAIR_ATTEMPTS),
            )

        # 3. Compute execution order (raises on cycle)
        graph_data["execution_order"] = self._topological_order(
            graph_data["nodes"],
            graph_data["edges"],
        )

        # 4. Verify execution order matches graph
        self._validate_order(graph_data["nodes"], graph_data["edges"], graph_data["execution_order"])

        # 5. Generate ExecutionGraph object
        eg = ExecutionGraph()

        for node in graph_data.get("nodes", []):
            eg.add_node(
                GraphNode(
                    id=node["id"],
                    type="step",
                    label=node["name"],
                    props={
                        "capability": node["agent"],
                        "node_type": node.get("type", "execution"),
                    },
                )
            )

        for edge in graph_data.get("edges", []):
            if edge.get("type") == "feedback":
                continue
            if eg.get_node(edge["from"]) is None or eg.get_node(edge["to"]) is None:
                continue
            eg.add_edge(
                GraphEdge(
                    src=edge["from"],
                    dst=edge["to"],
                    rel="depends_on",
                )
            )

        # Attach raw data for callers that need the dict
        eg.graph_data = graph_data

        return eg

    # ── Raw graph translation (internal) ──────────────────────────────

    def _build_raw_graph(
        self,
        agent_map: dict[str, list[str]],
        domain: str,
        dependency_edges: list[dict] | None = None,
    ) -> dict:
        """Translate planner output into a raw graph dict.

        Pure translation. No validation. No execution order.
        """
        nodes = []
        seen = set()

        for agent_list in agent_map.values():
            for agent in agent_list:
                agent = agent.strip()
                if not agent or agent in seen:
                    continue
                seen.add(agent)
                nodes.append(
                    {
                        "id": f"node_{len(nodes) + 1}",
                        "name": agent,
                        "agent": agent,
                        "type": "execution",
                        "agents": [],
                    }
                )

        if not nodes:
            nodes.append(
                {
                    "id": "node_1",
                    "name": f"{domain}Agent",
                    "agent": f"{domain}Agent",
                    "type": "execution",
                    "agents": [],
                }
            )

        id_lookup = {node["agent"]: node["id"] for node in nodes}
        node_ids = {node["id"] for node in nodes}

        edges = []

        for dep in dependency_edges or []:
            from_val = dep.get("from", "")
            to_val = dep.get("to", "")

            if from_val in node_ids and to_val in node_ids:
                src, dst = from_val, to_val
            else:
                src = id_lookup.get(from_val)
                dst = id_lookup.get(to_val)

            if not src or not dst:
                logger.warning(
                    "[graph] Unknown dependency: %s -> %s",
                    dep.get("from"),
                    dep.get("to"),
                )
                continue

            if src == dst:
                continue

            edges.append(
                {
                    "from": src,
                    "to": dst,
                }
            )

        return {
            "nodes": nodes,
            "edges": edges,
            "execution_order": [],
            "total_steps": len(nodes),
            "remove_nodes": [],
        }

    # ── Graph validation (deterministic, internal) ────────────────────

    def _validate_graph(
        self,
        nodes: list[dict],
        edges: list[dict],
    ) -> list[GraphValidationError]:
        """Deterministic validation of a candidate execution graph.

        Returns a list of GraphValidationError. Empty list means valid.
        Never modifies the graph. Never attempts repairs.
        """
        errors: list[GraphValidationError] = []

        if not nodes:
            errors.append(
                GraphValidationError(
                    code="EMPTY_GRAPH",
                    message="Graph has no nodes",
                )
            )
            return errors

        node_ids = [n["id"] for n in nodes]
        node_id_set = set(node_ids)

        # 1. No duplicate node IDs
        if len(node_ids) != len(node_id_set):
            seen: set[str] = set()
            dupes: list[str] = []
            for nid in node_ids:
                if nid in seen:
                    dupes.append(nid)
                seen.add(nid)
            errors.append(
                GraphValidationError(
                    code="DUPLICATE_NODE_ID",
                    message=f"Duplicate node IDs: {dupes}",
                    detail={"duplicate_ids": dupes},
                )
            )

        # 2. All edges reference existing nodes
        bad_refs: list[dict] = []
        for e in edges:
            src = e.get("from", "")
            dst = e.get("to", "")
            if src not in node_id_set or dst not in node_id_set:
                bad_refs.append(e)
        if bad_refs:
            errors.append(
                GraphValidationError(
                    code="DANGLING_EDGE",
                    message=f"{len(bad_refs)} edge(s) reference non-existent nodes",
                    detail={"edges": bad_refs},
                )
            )

        # 3. No duplicate edges
        edge_tuples = [(e.get("from", ""), e.get("to", "")) for e in edges]
        if len(edge_tuples) != len(set(edge_tuples)):
            seen_edges: set[tuple[str, str]] = set()
            dupes_edges: list[dict] = []
            for e in edges:
                key = (e.get("from", ""), e.get("to", ""))
                if key in seen_edges:
                    dupes_edges.append(e)
                seen_edges.add(key)
            errors.append(
                GraphValidationError(
                    code="DUPLICATE_EDGE",
                    message=f"{len(dupes_edges)} duplicate edge(s)",
                    detail={"edges": dupes_edges},
                )
            )

        # 4. No self-loops
        self_loops = [e for e in edges if e.get("from") == e.get("to")]
        if self_loops:
            errors.append(
                GraphValidationError(
                    code="SELF_LOOP",
                    message=f"{len(self_loops)} self-loop(s) detected",
                    detail={"edges": self_loops},
                )
            )

        # 5. Graph is acyclic (DFS) — over EXECUTION edges only.
        #    A type="feedback" edge is control flow, not execution flow: it is a
        #    deliberate back-edge (report → analyze, monitor → deploy). Every consumer
        #    downstream excludes it from ordering — GraphManager._topological_order,
        #    CognitiveRuntime's dependency computation, the CSR compiler (_FEEDBACK_REL).
        #    This DFS walked ALL edges, so it reported any feedback edge as
        #    CYCLE_DETECTED. Since a cycle is unrepairable, the planner could never emit
        #    a feedback edge at all: it failed convergence and /plan returned 422. The
        #    concept was supported everywhere except in the compiler that produces it.
        if not bad_refs:
            adj: dict[str, list[str]] = {nid: [] for nid in node_id_set}
            for e in _execution_edges(edges):
                src = e.get("from", "")
                dst = e.get("to", "")
                if src in adj:
                    adj[src].append(dst)

            WHITE, GRAY, BLACK = 0, 1, 2
            color: dict[str, int] = {nid: WHITE for nid in node_id_set}
            parent: dict[str, str | None] = {nid: None for nid in node_id_set}
            cycle_path: list[str] | None = None

            def dfs(nid: str) -> bool:
                nonlocal cycle_path
                color[nid] = GRAY
                for neighbor in adj.get(nid, []):
                    if neighbor not in color:
                        continue
                    if color[neighbor] == GRAY:
                        cycle = [neighbor, nid]
                        cur = nid
                        while cur != neighbor:
                            cur = parent.get(cur)
                            if cur is None:
                                break
                            cycle.append(cur)
                        cycle.reverse()
                        cycle_path = cycle
                        return True
                    if color[neighbor] == WHITE:
                        parent[neighbor] = nid
                        if dfs(neighbor):
                            return True
                color[nid] = BLACK
                return False

            for nid in node_id_set:
                if color[nid] == WHITE:
                    if dfs(nid):
                        break

            if cycle_path:
                errors.append(
                    GraphValidationError(
                        code="CYCLE_DETECTED",
                        message=f"Graph contains a cycle: {' → '.join(cycle_path)} → {cycle_path[0]}",
                        detail={"cycle": cycle_path},
                    )
                )

        return errors

    # ── Execution order verification ──────────────────────────────────

    def _validate_order(
        self,
        nodes: list[dict],
        edges: list[dict],
        execution_order: list[list[str]],
    ) -> None:
        """Verify that the execution order is valid for the given graph.

        Raises GraphValidationError if the order is invalid.
        """
        node_ids = {n["id"] for n in nodes}

        # Every node appears exactly once
        flat = [nid for batch in execution_order for nid in batch]
        if len(flat) != len(node_ids):
            raise GraphValidationError(
                code="INVALID_ORDER",
                message=f"Execution order has {len(flat)} nodes but graph has {len(node_ids)}",
                detail={"order_count": len(flat), "node_count": len(node_ids)},
            )

        seen: set[str] = set()
        for nid in flat:
            if nid in seen:
                raise GraphValidationError(
                    code="DUPLICATE_IN_ORDER",
                    message=f"Node {nid} appears multiple times in execution order",
                    detail={"node": nid},
                )
            seen.add(nid)

        if seen != node_ids:
            missing = sorted(node_ids - seen)
            raise GraphValidationError(
                code="MISSING_IN_ORDER",
                message=f"Nodes missing from execution order: {missing}",
                detail={"missing": missing},
            )

        # For every EXECUTION edge (u, v), u appears before v. Feedback edges point
        # backward by definition (report ⇢ analyze), so checking them here rejected the
        # one correct order for any graph that had one.
        position = {nid: i for i, nid in enumerate(flat)}
        for e in _execution_edges(edges):
            src = e.get("from", "")
            dst = e.get("to", "")
            if src in position and dst in position:
                if position[src] >= position[dst]:
                    raise GraphValidationError(
                        code="ORDER_VIOLATES_DEPENDENCY",
                        message=f"Edge {src} → {dst} violates execution order",
                        detail={"from": src, "to": dst},
                    )

    # ── Dependency repair (LLM, internal) ─────────────────────────────

    async def _repair_dependencies(
        self,
        graph_data: dict,
        errors: list[GraphValidationError],
        intent: str,
        nodes: list[dict],
    ) -> list[dict]:
        """Ask the LLM to repair invalid dependency edges.

        Nodes are already correct. Only edges are repaired.
        The repair prompt emphasizes minimal changes.
        """
        if self._llm is None:
            return graph_data.get("edges", [])

        agent_names = [n["agent"] for n in nodes]
        id_lookup = {n["agent"]: n["id"] for n in nodes}
        node_ids = {n["id"] for n in nodes}

        # Build a human-readable edge list with agent names
        id_to_agent = {n["id"]: n["agent"] for n in nodes}
        current_edges_named = []
        for e in graph_data.get("edges", []):
            src_name = id_to_agent.get(e.get("from", ""), e.get("from", ""))
            dst_name = id_to_agent.get(e.get("to", ""), e.get("to", ""))
            current_edges_named.append(f"  {src_name} → {dst_name}")
        edges_str = "\n".join(current_edges_named) if current_edges_named else "  (none)"

        # Build error descriptions with cycle visualization
        error_sections = []
        for err in errors:
            if err.code == "CYCLE_DETECTED":
                cycle = err.detail.get("cycle", [])
                cycle_names = [id_to_agent.get(nid, nid) for nid in cycle]
                viz = " → ".join(cycle_names) + f" → {cycle_names[0]}"
                error_sections.append(
                    f"Cycle detected:\n\n{viz}\n\nRemove the minimum number of edges necessary to produce a valid DAG."
                )
            elif err.code == "SELF_LOOP":
                error_sections.append(f"Self-loop detected: {err.message}\n\nRemove the self-referencing edge.")
            elif err.code == "DANGLING_EDGE":
                error_sections.append(
                    f"Invalid edge references: {err.message}\n\nRemove edges that reference non-existent agents."
                )
            else:
                error_sections.append(f"[{err.code}] {err.message}")

        error_str = "\n\n".join(error_sections)

        prompt = f"""The following dependency graph has validation errors:

Current edges:
{edges_str}

{error_str}

Agents: {", ".join(agent_names)}

Rules:
- Preserve every valid dependency.
- Only modify dependencies that violate graph validation.
- Minimize the number of changed edges.
- Do NOT rename agents.
- Do NOT add agents.
- Do NOT remove agents.
- Only repair the dependency graph.
- The graph MUST be acyclic.

Return ONLY a JSON array of corrected edges:
[{{"from": "AgentA", "to": "AgentB"}}]
"""

        try:
            response = await self._llm.generate(
                prompt,
                system="Return ONLY a JSON array of dependency edges. No prose, no markdown fences.",
            )
            m = re.search(r"\[.*\]", response, re.DOTALL)
            if not m:
                return graph_data.get("edges", [])

            repaired_deps = json.loads(m.group(0))
        except Exception as e:
            logger.warning("[graph_gen] LLM repair failed: %s", e)
            return graph_data.get("edges", [])

        edges = []
        for dep in repaired_deps:
            from_val = dep.get("from", "")
            to_val = dep.get("to", "")
            if from_val in node_ids and to_val in node_ids:
                src, dst = from_val, to_val
            else:
                src = id_lookup.get(from_val)
                dst = id_lookup.get(to_val)
            if src and dst and src != dst:
                edges.append({"from": src, "to": dst})

        return edges

    # ── Topological ordering ──────────────────────────────────────────

    def _topological_order(self, nodes: list[dict], edges: list[dict]) -> list[list[str]]:
        """Compute topological execution order.

        Raises GraphValidationError if the graph is cyclic. A partial
        execution order is never returned.
        """
        real_edges = _execution_edges(edges)
        in_degree = {n["id"]: 0 for n in nodes}
        for e in real_edges:
            # Ignore edges to nodes that don't exist — _validate_graph reports those as
            # DANGLING_EDGE. Counting them here invented phantom entries in `remaining`
            # that no node could ever satisfy.
            if e["to"] in in_degree:
                in_degree[e["to"]] += 1

        order = []
        remaining = set(in_degree.keys())

        for _ in range(len(nodes) + 1):
            if not remaining:
                break
            ready = [nid for nid in remaining if in_degree.get(nid, 0) == 0]
            if not ready:
                remaining_nodes = sorted(remaining)
                raise GraphValidationError(
                    code="CYCLE_DETECTED",
                    message=f"Topological sort failed — cycle involving nodes: {remaining_nodes}",
                    detail={"nodes": remaining_nodes},
                )
            order.append(ready)
            for nid in ready:
                remaining.discard(nid)
                # Decrement over the SAME edge set the in-degree was counted from.
                # This walked `edges` (all of them) while in_degree came from
                # `real_edges`, so every feedback edge decremented a count it had never
                # incremented. Its target's in-degree went NEGATIVE, could never equal
                # zero, and so never became ready — reported as CYCLE_DETECTED on a
                # provably acyclic graph (build → deploy, monitor ⇢ deploy).
                for e in real_edges:
                    if e["from"] == nid and e["to"] in in_degree:
                        in_degree[e["to"]] -= 1

        return order
