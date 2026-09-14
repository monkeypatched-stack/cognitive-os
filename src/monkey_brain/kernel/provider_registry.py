"""ProviderRegistry — manages external agent providers (OpenClaw, n8n, NANDA).

When an agent is not found locally, the system checks registered providers
for remote agents. This enables discovery of agents from external ecosystems.

Architecture:
    Local agents (Broca registry)
        ↓ not found?
    ProviderRegistry (OpenClaw, n8n, NANDA)
        ↓ found?
    Register locally → next time found locally
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger("agentos.provider_registry")


class Provider:
    """External agent provider (OpenClaw, n8n, NANDA)."""

    def __init__(self, name: str, url: str = "", available: bool = False):
        import time as _time

        self.name = name
        self.url = url
        self.available = available
        self._agents: dict[str, dict] = {}
        # Real registration moment (this process's boot) — the frontend's
        # own contract audit (ProvidersPanel.tsx) found no discovery
        # timestamp anywhere on this registry; this is the honest one
        # available (when THIS instance was constructed), not a persisted
        # first-ever-discovered time, which this in-memory registry has
        # no durable store to track across restarts.
        self.created_at: float = _time.time()
        # Health is UNKNOWN until check_health() actually runs once — never
        # inferred from `available` (a registration/config flag, not a
        # live reachability signal; see this class's own `available`
        # semantics above, e.g. NandaProvider.available is just "is the
        # env var set", not "did we ever reach it").
        self.last_health_check_at: float | None = None
        self.last_health_status: str = "unknown"
        self.last_latency_ms: float | None = None
        self.last_health_error: str = ""
        # Real, live trust score for THIS provider — starts neutral (0.5,
        # matching AffiliationManager's own default for an unscored
        # target) and only ever moves via an actual recorded execution
        # outcome (record_outcome()), never inferred from `available` or
        # `health_status`. Deliberately a separate mechanism from
        # AffiliationManager/TrustEngine (kernel/affiliations/), which is
        # actor-to-actor and has no notion of a Provider as a target —
        # forcing Providers into that actor-scoped graph would be a
        # fabricated integration, not a real one. Same asymmetric
        # growth/decay shape as TrustEngine (trust falls faster than it
        # rises) for consistency, applied to a provider-scoped subject.
        self.trust_score: float = 0.5
        self._outcome_count: int = 0

    def record_outcome(self, success: bool) -> None:
        """Update trust_score from one real execution outcome. Called by
        ProviderRegistry.execute_agent() after every real dispatch through
        this provider — never on a synthetic or simulated result."""
        self._outcome_count += 1
        delta = 0.05 if success else -0.08
        self.trust_score = max(0.0, min(1.0, self.trust_score + delta))

    async def discover_agents(self, query: str = "") -> list[dict]:
        """Discover agents from this provider."""
        return []

    async def check_health(self) -> dict:
        """Real reachability check — GET self.url with a short timeout and
        record the result. A subclass without a URL (e.g. a CLI-backed
        provider) overrides this with its own real check rather than
        inheriting a check that would always report "no endpoint".
        Never infers healthy from `available` — this is the one place
        that actually reaches out."""
        import time as _time

        if not self.url:
            self.last_health_check_at = _time.time()
            self.last_health_status = "unknown"
            self.last_latency_ms = None
            self.last_health_error = "no endpoint configured for this provider"
            return self._health_result()

        started = _time.monotonic()
        try:
            import httpx

            async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
                resp = await client.get(self.url)
            self.last_latency_ms = round((_time.monotonic() - started) * 1000, 1)
            # Any real HTTP response (even 401/404) proves the endpoint is
            # actually reachable — that's what "healthy" means here, not
            # "returns 200 for this exact unauthenticated GET".
            self.last_health_status = "healthy" if resp.status_code < 500 else "unhealthy"
            self.last_health_error = "" if resp.status_code < 500 else f"HTTP {resp.status_code}"
        except Exception as exc:
            self.last_latency_ms = round((_time.monotonic() - started) * 1000, 1)
            self.last_health_status = "unhealthy"
            self.last_health_error = str(exc)
        self.last_health_check_at = _time.time()
        return self._health_result()

    def _health_result(self) -> dict:
        return {
            "name": self.name,
            "status": self.last_health_status,
            "checked_at": self.last_health_check_at,
            "latency_ms": self.last_latency_ms,
            "error": self.last_health_error,
        }

    async def execute(self, agent_name: str, state: dict) -> dict:
        """Execute an agent via this provider's transport.

        Returns: {"answer": str, "success": bool, ...}
        Override in subclasses for provider-specific execution.
        """
        return {
            "answer": f"Provider {self.name} does not support execution",
            "success": False,
        }

    def register_agent(self, agent_info: dict) -> None:
        agent_name = agent_info.get("name", "")
        if agent_name:
            self._agents[agent_name] = agent_info

    def has_agent(self, agent_name: str) -> bool:
        return agent_name in self._agents

    def get_agent(self, agent_name: str) -> dict | None:
        return self._agents.get(agent_name)

    def list_agents(self) -> list[dict]:
        return list(self._agents.values())


class OpenClawProvider(Provider):
    """OpenClaw — agent-to-agent communication provider.

    Tries API first, falls back to CLI gateway.
    """

    def __init__(self):
        url = os.environ.get("OPENCLAW_API_URL", "")
        super().__init__(name="openclaw", url=url, available=bool(url) or _openclaw_cli_available())

    async def check_health(self) -> dict:
        """OpenClaw has no URL by default (CLI-based) — the base class's
        URL-only check would always report "unknown" for it, when a real
        check (is the CLI actually on PATH right now) does exist. Falls
        through to the base HTTP check when OPENCLAW_API_URL is set."""
        if self.url:
            return await super().check_health()
        import time as _time

        self.last_health_check_at = _time.time()
        cli_ok = _openclaw_cli_available()
        self.last_health_status = "healthy" if cli_ok else "unhealthy"
        self.last_latency_ms = None
        self.last_health_error = "" if cli_ok else "openclaw CLI not found on PATH"
        return self._health_result()

    async def discover_agents(self, query: str = "") -> list[dict]:
        # Try API first
        if self.url:
            try:
                import httpx

                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(f"{self.url}/agents", params={"q": query})
                    if resp.status_code == 200:
                        agents = resp.json().get("agents", [])
                        for agent in agents:
                            self.register_agent(agent)
                        return agents
            except Exception as e:
                logger.debug("[openclaw] API failed, trying CLI: %s", e)

        # Fallback to CLI
        return await self._discover_via_cli()

    async def _discover_via_cli(self) -> list[dict]:
        """Discover agents via openclaw CLI."""
        try:
            import asyncio

            proc = await asyncio.create_subprocess_exec(
                "openclaw",
                "agents",
                "list",
                "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode == 0:
                data = json.loads(stdout.decode())
                # CLI returns list of agent objects with "id" field
                agents_list = data if isinstance(data, list) else data.get("agents", [])
                agents = []
                for agent in agents_list:
                    agent_id = agent.get("id", agent.get("name", ""))
                    if not agent_id:
                        continue
                    # Read agent config for description
                    desc = self._read_agent_description(agent.get("agentDir", ""))
                    entry = {
                        "name": agent_id,
                        "description": desc or f"OpenClaw agent: {agent_id}",
                        "provider": "openclaw",
                        "model": agent.get("model", ""),
                        "workspace": agent.get("workspace", ""),
                    }
                    agents.append(entry)
                    self.register_agent(entry)
                return agents
        except Exception as e:
            logger.debug("[openclaw] CLI discovery failed: %s", e)

        # No static fallback — return empty if CLI fails
        return []

    def _read_agent_description(self, agent_dir: str) -> str:
        """Try to read description from agent's config files."""
        if not agent_dir:
            return ""
        import pathlib

        # Check for BOOTSTRAP.md or similar
        for fname in ("BOOTSTRAP.md", "README.md", "agent.json", "config.json"):
            fpath = pathlib.Path(agent_dir) / fname
            if fpath.exists():
                try:
                    content = fpath.read_text()[:500]
                    # Extract first meaningful line
                    for line in content.splitlines():
                        line = line.strip()
                        if line and not line.startswith("#") and not line.startswith("---"):
                            return line[:200]
                except Exception:
                    logger.debug("_read_agent_description: suppressed exception", exc_info=True)
        return ""

    async def execute(self, agent_name: str, state: dict) -> dict:
        """Execute an OpenClaw agent via API or CLI."""
        question = state.get("question", "")

        # Try API first
        if self.url:
            try:
                import httpx

                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.post(
                        f"{self.url}/agents/{agent_name}/execute",
                        json={"question": question, "state": state},
                    )
                    if resp.status_code == 200:
                        result = resp.json()
                        return {
                            "answer": result.get("answer", result.get("response", "")),
                            "success": True,
                            "provider": "openclaw",
                            "agent": agent_name,
                        }
            except Exception as e:
                logger.debug("[openclaw] API execution failed: %s", e)

        # Fallback to CLI
        try:
            import asyncio

            proc = await asyncio.create_subprocess_exec(
                "openclaw",
                "agents",
                "run",
                agent_name,
                "--question",
                question,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode == 0:
                return {
                    "answer": stdout.decode().strip(),
                    "success": True,
                    "provider": "openclaw",
                    "agent": agent_name,
                }
        except Exception as e:
            logger.debug("[openclaw] CLI execution failed: %s", e)

        return {
            "answer": f"OpenClaw agent {agent_name} execution failed",
            "success": False,
        }


def _openclaw_cli_available() -> bool:
    """Check if openclaw CLI is installed."""
    try:
        import shutil

        return shutil.which("openclaw") is not None
    except Exception:
        return False


class N8nProvider(Provider):
    """n8n — workflow automation provider.

    Tries API first, falls back to local SQLite database.
    """

    def __init__(self):
        url = os.environ.get("N8N_BASE_URL", "")
        super().__init__(name="n8n", url=url, available=bool(url))

    async def discover_agents(self, query: str = "") -> list[dict]:
        # Try API first (requires API key)
        api_key = os.environ.get("N8N_API_KEY", "")
        if self.url and api_key:
            try:
                import httpx

                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(
                        f"{self.url}/api/v1/workflows",
                        headers={"X-N8N-API-KEY": api_key},
                    )
                    if resp.status_code == 200:
                        workflows = resp.json().get("data", [])
                        agents = [
                            {
                                "name": wf.get("name", ""),
                                "description": wf.get("description", ""),
                                "provider": "n8n",
                                "workflow_id": wf.get("id"),
                            }
                            for wf in workflows
                            if wf.get("active")
                        ]
                        for agent in agents:
                            self.register_agent(agent)
                        return agents
            except Exception as e:
                logger.debug("[n8n] API failed: %s", e)

        # Fallback: read from local SQLite database
        return self._discover_from_db()

    def _discover_from_db(self) -> list[dict]:
        """Read active workflows directly from n8n SQLite database."""
        import pathlib

        db_path = pathlib.Path.home() / ".n8n" / "database.sqlite"
        if not db_path.exists():
            return []
        try:
            import sqlite3

            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT id, name, active FROM workflow_entity WHERE active = 1")
            agents = []
            for row in cursor:
                name = row["name"]
                agents.append(
                    {
                        "name": name,
                        "description": f"n8n workflow: {name}",
                        "provider": "n8n",
                        "workflow_id": row["id"],
                    }
                )
                self.register_agent(agents[-1])
            conn.close()
            return agents
        except Exception as e:
            logger.debug("[n8n] DB fallback failed: %s", e)
            return []

    async def execute(self, agent_name: str, state: dict) -> dict:
        """Execute an n8n workflow via API webhook."""
        question = state.get("question", "")
        agent_info = self.get_agent(agent_name)
        # Webhooks are addressed by the path configured on the workflow's
        # Webhook node, not by the workflow's internal id — using workflow_id
        # here 404s and always returns the "execution failed" fallback below,
        # even when the workflow itself is fine. webhook_path is only present
        # on agents this resolver auto-created; older/manually-added n8n
        # agents fall back to workflow_id, matching the prior behavior.
        webhook_path = (agent_info.get("webhook_path") or agent_info.get("workflow_id", "")) if agent_info else ""

        if self.url and webhook_path:
            api_key = os.environ.get("N8N_API_KEY", "")
            try:
                import httpx

                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.post(
                        f"{self.url}/webhook/{webhook_path}",
                        json={"question": question, "state": state},
                        headers={"X-N8N-API-KEY": api_key} if api_key else {},
                    )
                    if resp.status_code == 200:
                        result = resp.json()
                        # Workflows this resolver generates respond via
                        # `={{ $json }}` — whatever shape the Code node
                        # returned (e.g. {"result": {...}}), not a top-level
                        # "output"/"answer" key. Falling back to "" here
                        # discarded that data entirely; raw_output carries it
                        # through for AgentMiddleware's LLM-reformat step
                        # even when there's no "answer" key to find.
                        answer = result.get("output") or result.get("answer") or ""
                        return {
                            "answer": answer,
                            "raw_output": result,
                            "success": True,
                            "provider": "n8n",
                            "agent": agent_name,
                        }
            except Exception as e:
                logger.debug("[n8n] API execution failed: %s", e)

        return {
            "answer": f"n8n workflow {agent_name} execution failed",
            "success": False,
        }


class NandaProvider(Provider):
    """NANDA — decentralized agent registry provider."""

    def __init__(self):
        url = os.environ.get("NANDA_REGISTRY_URL", "")
        super().__init__(name="nanda", url=url, available=bool(url))

    async def discover_agents(self, query: str = "") -> list[dict]:
        if not self.available:
            return []

        try:
            from cerebellum.capabilities.agent.nanda import NANDACapability

            nanda = NANDACapability()
            if nanda._available:
                result = await nanda.execute({"operation": "discover", "domain": query})
                agents = result.get("agents", [])
                for agent in agents:
                    self.register_agent(agent)
                return agents
        except Exception as e:
            logger.debug("[nanda] discovery failed: %s", e)

        return []

    async def execute(self, agent_name: str, state: dict) -> dict:
        """Execute a NANDA agent via the NANDA capability."""
        question = state.get("question", "")

        try:
            from cerebellum.capabilities.agent.nanda import NANDACapability

            nanda = NANDACapability()
            if nanda._available:
                result = await nanda.execute(
                    {
                        "operation": "execute",
                        "agent_id": agent_name,
                        "question": question,
                        "state": state,
                    }
                )
                return {
                    "answer": result.get("answer", result.get("response", "")),
                    "success": result.get("success", True),
                    "provider": "nanda",
                    "agent": agent_name,
                }
        except Exception as e:
            logger.debug("[nanda] execution failed: %s", e)

        return {
            "answer": f"NANDA agent {agent_name} execution failed",
            "success": False,
        }


class ARDProvider(Provider):
    """Agentic Resource Discovery — federated agent/tool/skill discovery.

    Implements the ARD specification (https://agenticresourcediscovery.org/).
    Searches federated registries for agents, tools, and skills matching
    natural language queries.

    HuggingFace reference implementation:
        POST https://huggingface-hf-discover.hf.space/search
    """

    def __init__(self):
        url = os.environ.get("ARD_REGISTRY_URL", "https://huggingface-hf-discover.hf.space")
        super().__init__(name="ard", url=url, available=bool(url))

    async def discover_agents(self, query: str = "") -> list[dict]:
        """Search ARD registry for agents/tools/skills matching the query."""
        if not self.available or not query:
            return []

        try:
            import httpx

            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{self.url}/search",
                    json={
                        "query": {"text": query},
                        "pageSize": 10,
                    },
                    headers={"Content-Type": "application/json"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    results = data.get("results", data.get("items", []))
                    agents = []
                    for item in results:
                        agent = {
                            "name": item.get("name", item.get("id", "")),
                            "description": item.get("description", ""),
                            "provider": "ard",
                            "url": item.get("url", item.get("endpoint", "")),
                            "type": item.get("type", "application/ai-skill"),
                            "publisher": item.get("publisher", {}),
                            "tags": item.get("tags", []),
                        }
                        if agent["name"]:
                            agents.append(agent)
                            self.register_agent(agent)
                    return agents
        except Exception as e:
            logger.debug("[ard] discovery failed: %s", e)

        return []

    async def execute(self, agent_name: str, state: dict) -> dict:
        """Execute an ARD-discovered agent/tool.

        For MCP servers: calls the MCP endpoint.
        For skills: loads the skill and executes.
        For APIs: calls the endpoint directly.
        """
        question = state.get("question", "")
        agent_info = self.get_agent(agent_name)
        agent_url = agent_info.get("url", "") if agent_info else ""
        agent_type = agent_info.get("type", "") if agent_info else ""

        if not agent_url:
            return {
                "answer": f"ARD agent {agent_name} has no endpoint URL",
                "success": False,
            }

        try:
            import httpx

            async with httpx.AsyncClient(timeout=30) as client:
                # MCP server execution
                if "mcp" in agent_type.lower():
                    resp = await client.post(
                        agent_url,
                        json={
                            "jsonrpc": "2.0",
                            "method": "tools/call",
                            "params": {
                                "name": agent_name,
                                "arguments": {"question": question, "state": state},
                            },
                            "id": 1,
                        },
                    )
                    if resp.status_code == 200:
                        result = resp.json().get("result", {})
                        content = result.get("content", [{}])
                        answer = content[0].get("text", "") if content else ""
                        return {
                            "answer": answer or f"MCP agent {agent_name} executed",
                            "success": True,
                            "provider": "ard",
                            "agent": agent_name,
                        }

                # Generic HTTP endpoint
                resp = await client.post(
                    agent_url,
                    json={"question": question, "state": state},
                )
                if resp.status_code == 200:
                    result = (
                        resp.json()
                        if resp.headers.get("content-type", "").startswith("application/json")
                        else {"answer": resp.text}
                    )
                    return {
                        "answer": result.get("answer", result.get("output", resp.text[:500])),
                        "success": True,
                        "provider": "ard",
                        "agent": agent_name,
                    }
        except Exception as e:
            logger.debug("[ard] execution failed for %s: %s", agent_name, e)

        return {"answer": f"ARD agent {agent_name} execution failed", "success": False}


class ProviderRegistry:
    """Manages all external agent providers.

    When an agent is not found locally, check registered providers.
    If found, register locally for future discovery.
    """

    def __init__(self) -> None:
        self._providers: dict[str, Provider] = {}

    def register_provider(self, provider: Provider) -> None:
        self._providers[provider.name] = provider
        logger.info(
            "[provider_registry] Registered provider: %s (available=%s)",
            provider.name,
            provider.available,
        )

    def get_provider(self, name: str) -> Provider | None:
        return self._providers.get(name)

    def list_providers(self) -> list[dict]:
        return [
            {
                "name": p.name,
                "url": p.url,
                "available": p.available,
                "agents": len(p.list_agents()),
                "created_at": p.created_at,
                "health_status": p.last_health_status,
                "health_checked_at": p.last_health_check_at,
                "latency_ms": p.last_latency_ms,
                "health_error": p.last_health_error,
                "trust_score": round(p.trust_score, 3),
                "trust_outcomes": p._outcome_count,
            }
            for p in self._providers.values()
        ]

    def find_provider_for_agent(self, agent_name: str) -> Provider | None:
        """Which provider owns this agent, or None. Backs
        AgentBus.get_provider_for() — find_agent() below returns the agent
        dict but not which provider it came from, so AgentBus had no real
        way to answer that question."""
        for provider in self._providers.values():
            if provider.has_agent(agent_name):
                return provider
        return None

    async def check_provider_health(self, name: str) -> dict | None:
        """Runs the one real check_health() implementation each Provider
        already has (base class = URL reachability; OpenClawProvider
        overrides with a CLI-availability check) and returns its result.
        None means the name isn't a registered provider at all — a 404,
        not a fabricated "unknown" health record for something that
        doesn't exist."""
        provider = self._providers.get(name)
        if provider is None:
            return None
        return await provider.check_health()

    async def discover_from_providers(self, query: str = "") -> list[dict]:
        """Query all providers for agents matching the query, ranked by
        each owning provider's real trust_score (highest first) — the
        literal "trust-aware discovery" this registry previously had no
        mechanism to back. Ties (equal trust, e.g. two never-yet-scored
        providers at the neutral 0.5 default) keep provider registration
        order, so ranking never reorders agents from equally-untested
        providers based on nothing."""
        scored: list[tuple[float, dict]] = []
        for provider in self._providers.values():
            if provider.available:
                try:
                    agents = await provider.discover_agents(query)
                    scored.extend((provider.trust_score, a) for a in agents)
                except Exception as e:
                    logger.debug("[provider_registry] %s discovery failed: %s", provider.name, e)
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [agent for _, agent in scored]

    def find_agent(self, agent_name: str) -> dict | None:
        """Find an agent by name across all providers."""
        for provider in self._providers.values():
            agent = provider.get_agent(agent_name)
            if agent:
                return agent
        return None

    async def execute_agent(self, agent_name: str, state: dict) -> dict:
        """Execute an agent via its provider's transport.

        Finds the provider that owns the agent, then delegates execution.
        Returns: {"answer": str, "success": bool, "provider": str, ...}
        """
        for provider in self._providers.values():
            if provider.has_agent(agent_name):
                try:
                    result = await provider.execute(agent_name, state)
                    provider.record_outcome(bool(result.get("success", False)))
                    return result
                except Exception as e:
                    logger.error(
                        "[provider_registry] %s execution failed for %s: %s",
                        provider.name,
                        agent_name,
                        e,
                    )
                    provider.record_outcome(False)
                    return {
                        "answer": f"Provider {provider.name} execution failed: {e}",
                        "success": False,
                    }
        return {"answer": f"No provider found for agent {agent_name}", "success": False}

    def register_discovered_agent(self, agent_info: dict) -> None:
        """Register a discovered agent locally for future use."""
        provider_name = agent_info.get("provider", "")
        if provider_name in self._providers:
            self._providers[provider_name].register_agent(agent_info)


_provider_registry: ProviderRegistry | None = None


def init_providers() -> ProviderRegistry:
    """Initialize all known providers from environment. Returns singleton."""
    global _provider_registry
    if _provider_registry is not None:
        return _provider_registry

    registry = ProviderRegistry()

    openclaw = OpenClawProvider()
    registry.register_provider(openclaw)

    n8n = N8nProvider()
    registry.register_provider(n8n)

    nanda = NandaProvider()
    registry.register_provider(nanda)

    ard = ARDProvider()
    registry.register_provider(ard)

    _provider_registry = registry
    return registry


def set_providers(registry: ProviderRegistry) -> None:
    """Replace the singleton with an already-initialized registry (called by kernel boot)."""
    global _provider_registry
    _provider_registry = registry
