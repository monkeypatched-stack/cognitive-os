"""AutoAgentGenerator — generates agents on-the-fly when they don't exist
in the Broca registry. Uses the LLM to create agent implementations from
the agent name and description, then registers them.

Flow:
    Agent not found → LLM generates agent code → Register → Execute
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
from typing import Any

from ._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.auto_agent_generator")


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


class ClaudeClient:
    def __init__(self, model: str = "claude-sonnet-4-6"):
        self.model = model

    async def generate(self, prompt: str, system: str = "") -> str:
        import anthropic

        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system or "You are a Python developer.",
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text


class OpenRouterClient:
    """OpenRouter (multi-model proxy) — the primary provider (see
    _select_provider below); Claude/Ollama remain fallbacks."""

    def __init__(self, model: str = "", api_key: str = ""):
        self.model = model or os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")
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
                json={"model": self.model, "messages": messages, "max_tokens": 4096},
            )
            resp.raise_for_status()
            data = resp.json()
        if "error" in data:
            raise RuntimeError(f"OpenRouter error: {data['error']}")
        return data["choices"][0]["message"]["content"] or ""


class AutoAgentGenerator(BaseETASSAgent):
    """Generates and registers agents on-the-fly when they don't exist.

    Flow:
        Agent not found → LLM generates implementation → Register → Return
    """

    agent_type = "auto_agent_generator"
    description = "Generates missing agents on-the-fly using LLM and registers them into the Broca registry"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._llm = self._select_provider()

    def _select_provider(self):
        from ._llm_bridge import dev_bridge_client
        from ._llm_cache import maybe_cache

        bridge = dev_bridge_client("auto_agent_generator")
        if bridge is not None:
            return maybe_cache(bridge, "auto_agent_generator")
        if os.environ.get("SPEC_DISCOVERY_PROVIDER", "").lower() == "claude" and _claude_available():
            return maybe_cache(ClaudeClient(), "auto_agent_generator")
        if _ollama_available():
            return maybe_cache(
                OllamaClient(model=os.environ.get("OLLAMA_MODEL", "gemma3:latest")),
                "auto_agent_generator",
            )
        if _claude_available():
            return maybe_cache(ClaudeClient(), "auto_agent_generator")
        return None

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict):
        agent_name = context.get("agent_name", "")
        agent_description = context.get("agent_description", "")
        spec = context.get("specification", {})
        discovery = context.get("discovery", {})

        if not agent_name:
            self._reward(False, 0.0)
            return self._result(payload={"generated": False}, observations=["no agent_name in context"])

        existing = self._find_existing_agent(agent_name)
        if existing:
            self._reward(True, 0.8)
            return self._result(
                payload={
                    "generated": False,
                    "agent_name": agent_name,
                    "found": True,
                    "source": "registry",
                },
                observations=[f"Agent {agent_name} already exists in registry"],
            )

        agent_code = await self._generate_agent(agent_name, agent_description, spec, discovery)

        registered = self._register_agent(agent_name, agent_code)

        self._reward(registered, 0.6)
        return self._result(
            payload={
                "generated": registered,
                "agent_name": agent_name,
                "agent_code_length": len(agent_code) if agent_code else 0,
                "source": "llm_generated" if registered else "generation_failed",
            },
            observations=[
                f"Agent {agent_name}: {'generated and registered' if registered else 'generation failed'}",
            ],
        )

    def _find_existing_agent(self, agent_name: str) -> bool:
        try:
            from broca.registry import get_registry

            registry = get_registry()
            # Exact match
            if registry.discover(agent_name) is not None:
                return True
            # Fuzzy match: check if any registered agent type contains the name
            agent_lower = agent_name.lower().replace("-", " ").replace("_", " ")
            for registered in registry._agents.values() if hasattr(registry, "_agents") else []:
                reg_type = getattr(registered, "agent_type", "").lower().replace("-", " ").replace("_", " ")
                if agent_lower in reg_type or reg_type in agent_lower:
                    return True
            return False
        except Exception:
            return False

    async def _generate_agent(self, name: str, description: str, spec: dict, discovery: dict) -> str:
        if self._llm is not None:
            try:
                raw = await self._llm.generate(
                    self._agent_prompt(name, description, spec, discovery),
                    system=self._system_prompt(),
                )
                m = re.search(r"```python\s*\n(.*?)```", raw, re.DOTALL)
                if m:
                    return m.group(1).strip()
                if "class " in raw and "BaseETASSAgent" in raw:
                    return raw.strip()
            except Exception as e:
                logger.warning("[auto_agent] LLM generation failed: %s", e)

        return self._heuristic_agent(name, description)

    def _agent_prompt(self, name: str, description: str, spec: dict, discovery: dict) -> str:
        agents_list = discovery.get("agents", [])
        platforms = discovery.get("platforms", [])
        return (
            f"Generate a Python agent class for the MonkeyBrain Cognitive Operating System.\n\n"
            f"Agent name: {name}\n"
            f"Description: {description}\n"
            f"Available platforms: {', '.join(platforms)}\n"
            f"Other agents in the system: {', '.join(agents_list)}\n"
            f"Specification: {json.dumps(spec, indent=2)[:1000]}\n\n"
            "The agent must:\n"
            "1. Extend BaseETASSAgent from broca.agents._base\n"
            '2. Have agent_type = "{name}"\n'
            "3. Have a handle() method that returns an AgentResult\n"
            "4. Use self._run(context, self._impl) pattern\n"
            "5. Return self._result(payload={...}, observations=[...])\n\n"
            "Output ONLY the Python code. No markdown fences. No explanation."
        )

    def _system_prompt(self) -> str:
        return (
            "You are a Python developer for the MonkeyBrain Cognitive Operating System. "
            "You generate agent implementations that follow the BaseETASSAgent pattern. "
            "Output valid Python code only."
        )

    def _heuristic_agent(self, name: str, description: str) -> str:
        # name/description are request-supplied and were interpolated RAW into Python source:
        # a quote in either escaped the string literal and injected code. Sanitise both — the
        # identifier for the class name, and a repr() for the string literals.
        from broca.agents._codegen_guard import safe_identifier

        ident = safe_identifier(name)
        name_lit = repr(str(name))
        desc_lit = repr(str(description))
        return f'''"""Auto-generated agent: {ident}"""
from __future__ import annotations
from typing import Any
from broca.agents._base import BaseETASSAgent


class AutoGen_{ident}(BaseETASSAgent):
    agent_type = {name_lit}
    description = {desc_lit}

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict):
        query = context.get("question", context.get("query", ""))
        result = f"Auto-generated agent {{self.agent_type}} processed: {{query}}"
        self._reward(True, 0.5)
        return self._result(
            payload={{"result": result, "agent": self.agent_type}},
            observations=[f"{{self.agent_type}}: processed query"],
        )
'''

    def _register_agent(self, name: str, agent_code: str) -> bool:
        if not agent_code:
            return False

        from broca.agents._codegen_guard import (
            SAFE_BUILTINS,
            UnsafeGeneratedCode,
            exec_enabled,
            validate_agent_code,
        )

        # Executing model-written source is RCE by design — make it a deliberate choice.
        if not exec_enabled():
            logger.error(
                "[auto_agent] refusing to exec generated code for %r: set BROCA_AUTO_AGENT_EXEC=1 "
                "to enable (this executes LLM-written Python in-process)",
                name,
            )
            return False

        # The source is influenced by request-supplied agent_name/agent_description (prompt
        # injection), and models hallucinate. Validate before it ever reaches exec().
        try:
            validate_agent_code(agent_code)
        except UnsafeGeneratedCode as exc:
            logger.error("[auto_agent] rejected unsafe generated code for %r: %s", name, exc)
            return False

        try:
            from broca.registry import get_registry

            registry = get_registry()

            namespace = {
                "__name__": "auto_generated",
                # Minimal builtins — the previous full __builtins__ handed generated code
                # os/subprocess/open via a single import.
                "__builtins__": SAFE_BUILTINS,
            }
            try:
                from broca.agents._base import BaseETASSAgent

                namespace["BaseETASSAgent"] = BaseETASSAgent
                # Ensure broca.agents has BaseETASSAgent even if cached from earlier
                import broca.agents as _ba

                if not hasattr(_ba, "BaseETASSAgent"):
                    _ba.BaseETASSAgent = BaseETASSAgent
                # Also patch sys.modules entry
                import sys as _sys

                _sm = _sys.modules.get("broca.agents")
                if _sm is not None and not hasattr(_sm, "BaseETASSAgent"):
                    _sm.BaseETASSAgent = BaseETASSAgent
            except ImportError:
                pass
            exec(agent_code, namespace)

            agent_class = None
            for obj in namespace.values():
                if isinstance(obj, type) and hasattr(obj, "agent_type") and hasattr(obj, "handle"):
                    agent_class = obj
                    break

            if agent_class is None:
                logger.warning("[auto_agent] No valid agent class found in generated code")
                return False

            agent_instance = agent_class()
            registry.register(agent_instance)
            logger.info("[auto_agent] Auto-generated and registered agent: %s", name)
            return True

        except Exception as e:
            logger.warning("[auto_agent] Registration failed for %s: %s", name, e)
            return False
