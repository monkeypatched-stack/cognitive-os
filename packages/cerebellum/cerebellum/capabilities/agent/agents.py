"""Agent Capabilities — agent-to-agent communication."""

from __future__ import annotations

from cerebellum.capability import Capability


class OpenClawCapability(Capability):
    """OpenClaw agent capability.

    Two execution modes:
    - CLI mode (default, no api_url): invokes `openclaw agent` subprocess via the
      local gateway at ws://127.0.0.1:18789. Uses OPENCLAW_AGENT (default "main")
      and OPENCLAW_MODEL (default "claude-cli/claude-sonnet-4-6").
    - HTTP mode (api_url set): POSTs to a remote OpenClaw REST API server.
    """

    def __init__(self, api_url: str = ""):
        super().__init__(name="openclaw")
        self._api_url = api_url.strip()

    async def execute(self, state, **kwargs):
        message = state.get("message") or state.get("task") or state.get("prompt") or str(state)
        agent = state.get("agent", "main")
        model = state.get("model", "claude-cli/claude-sonnet-4-6")
        timeout = float(state.get("timeout", 120))

        if self._api_url:
            return await self._execute_http(state)

        return await self._execute_cli(message, agent, model, timeout)

    async def _execute_cli(self, message: str, agent: str, model: str, timeout: float):
        import asyncio
        import json
        import shutil

        if not shutil.which("openclaw"):
            return {
                "status": "unavailable",
                "agent": "openclaw",
                "reason": "openclaw CLI not found in PATH",
            }
        try:
            proc = await asyncio.create_subprocess_exec(
                "openclaw",
                "agent",
                "--agent",
                agent,
                "--model",
                model,
                "--message",
                message,
                "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                return {
                    "status": "timeout",
                    "agent": "openclaw",
                    "error": f"CLI timed out after {timeout}s",
                }
            if proc.returncode != 0:
                return {
                    "status": "cli_error",
                    "agent": "openclaw",
                    "error": stderr.decode("utf-8", errors="replace").strip(),
                    "returncode": proc.returncode,
                }
            raw = stdout.decode("utf-8", errors="replace").strip()
            try:
                result = json.loads(raw)
            except json.JSONDecodeError:
                result = {"raw": raw}
            payloads = result.get("result", {}).get("payloads", [])
            reply = payloads[0].get("text", "") if payloads else ""
            return {
                "status": "ok",
                "agent": "openclaw",
                "mode": "cli",
                "run_id": result.get("runId"),
                "reply": reply,
                "model": result.get("result", {}).get("meta", {}).get("executionTrace", {}).get("winnerModel", model),
            }
        except Exception as e:
            return {"status": "exception", "agent": "openclaw", "error": str(e)}

    async def _execute_http(self, state: dict):
        try:
            import httpx

            async with httpx.AsyncClient(timeout=30.0) as client:
                try:
                    response = await client.post(
                        f"{self._api_url}/agent/execute",
                        json=state,
                        headers={"Content-Type": "application/json"},
                    )
                    response.raise_for_status()
                    return response.json()
                except httpx.HTTPStatusError as e:
                    return {
                        "status": "http_error",
                        "agent": "openclaw",
                        "error": str(e),
                        "http_status": e.response.status_code,
                    }
                except httpx.TimeoutException:
                    return {
                        "status": "timeout",
                        "agent": "openclaw",
                        "error": "request timed out",
                    }
                except httpx.NetworkError as e:
                    return {
                        "status": "network_error",
                        "agent": "openclaw",
                        "error": str(e),
                    }
        except Exception as e:
            return {"status": "exception", "agent": "openclaw", "error": str(e)}


class ExternalAgentCapability(Capability):
    def __init__(self, agent_url: str = ""):
        super().__init__(name="external_agent")
        self._agent_url = agent_url
        self._is_available = bool(agent_url.strip()) if agent_url else False

    async def execute(self, state, **kwargs):
        if not self._is_available:
            return {
                "status": "unavailable",
                "agent": "external_agent",
                "reason": "agent_url not configured",
                "state": state,
            }

        try:
            import httpx

            async with httpx.AsyncClient(timeout=30.0) as client:
                try:
                    response = await client.post(self._agent_url, json=state)
                    response.raise_for_status()
                    return response.json()
                except httpx.HTTPStatusError as e:
                    return {
                        "status": "http_error",
                        "agent": "external_agent",
                        "error": str(e),
                        "http_status": e.response.status_code,
                        "state": state,
                    }
                except httpx.TimeoutException:
                    return {
                        "status": "timeout",
                        "agent": "external_agent",
                        "error": "request timed out",
                        "state": state,
                    }
                except httpx.NetworkError as e:
                    return {
                        "status": "network_error",
                        "agent": "external_agent",
                        "error": str(e),
                        "state": state,
                    }
        except Exception as e:
            return {
                "status": "exception",
                "agent": "external_agent",
                "error": str(e),
                "state": state,
            }


class AgentToAgentCapability(Capability):
    def __init__(self):
        super().__init__(name="agent_to_agent")

    async def execute(self, state, **kwargs):
        return {"status": "configured", "communication": "agent_to_agent"}


class RemoteAgentExecutionCapability(Capability):
    def __init__(self, remote_url: str = ""):
        super().__init__(name="remote_agent_execution")
        self._remote_url = remote_url
        self._is_available = bool(remote_url.strip()) if remote_url else False

    async def execute(self, state, **kwargs):
        if not self._is_available:
            return {
                "status": "unavailable",
                "agent": "remote_agent_execution",
                "reason": "remote_url not configured",
                "state": state,
            }

        try:
            import httpx

            async with httpx.AsyncClient(timeout=30.0) as client:
                try:
                    response = await client.post(f"{self._remote_url}/execute", json=state)
                    response.raise_for_status()
                    return response.json()
                except httpx.HTTPStatusError as e:
                    return {
                        "status": "http_error",
                        "agent": "remote_agent_execution",
                        "error": str(e),
                        "http_status": e.response.status_code,
                        "state": state,
                    }
                except httpx.TimeoutException:
                    return {
                        "status": "timeout",
                        "agent": "remote_agent_execution",
                        "error": "request timed out",
                        "state": state,
                    }
                except httpx.NetworkError as e:
                    return {
                        "status": "network_error",
                        "agent": "remote_agent_execution",
                        "error": str(e),
                        "state": state,
                    }
        except Exception as e:
            return {
                "status": "exception",
                "agent": "remote_agent_execution",
                "error": str(e),
                "state": state,
            }


class AgentDiscoveryCapability(Capability):
    def __init__(self, registry_url: str = ""):
        super().__init__(name="agent_discovery")
        self._registry_url = registry_url
        self._is_available = bool(registry_url.strip()) if registry_url else False

    async def execute(self, state, **kwargs):
        if not self._is_available:
            return {
                "status": "unavailable",
                "agent": "agent_discovery",
                "reason": "registry_url not configured",
                "state": state,
            }

        try:
            import httpx

            async with httpx.AsyncClient(timeout=30.0) as client:
                try:
                    response = await client.get(f"{self._registry_url}/agents")
                    response.raise_for_status()
                    return response.json()
                except httpx.HTTPStatusError as e:
                    return {
                        "status": "http_error",
                        "agent": "agent_discovery",
                        "error": str(e),
                        "http_status": e.response.status_code,
                        "state": state,
                    }
                except httpx.TimeoutException:
                    return {
                        "status": "timeout",
                        "agent": "agent_discovery",
                        "error": "request timed out",
                        "state": state,
                    }
                except httpx.NetworkError as e:
                    return {
                        "status": "network_error",
                        "agent": "agent_discovery",
                        "error": str(e),
                        "state": state,
                    }
        except Exception as e:
            return {
                "status": "exception",
                "agent": "agent_discovery",
                "error": str(e),
                "state": state,
            }


class AgentRegistryCapability(Capability):
    def __init__(self, registry_url: str = ""):
        super().__init__(name="agent_registry")
        self._registry_url = registry_url
        self._is_available = bool(registry_url.strip()) if registry_url else False

    async def execute(self, state, **kwargs):
        if not self._is_available:
            return {
                "status": "unavailable",
                "agent": "agent_registry",
                "reason": "registry_url not configured",
                "state": state,
            }

        try:
            import httpx

            async with httpx.AsyncClient(timeout=30.0) as client:
                try:
                    response = await client.post(f"{self._registry_url}/register", json=state)
                    response.raise_for_status()
                    return response.json()
                except httpx.HTTPStatusError as e:
                    return {
                        "status": "http_error",
                        "agent": "agent_registry",
                        "error": str(e),
                        "http_status": e.response.status_code,
                        "state": state,
                    }
                except httpx.TimeoutException:
                    return {
                        "status": "timeout",
                        "agent": "agent_registry",
                        "error": "request timed out",
                        "state": state,
                    }
                except httpx.NetworkError as e:
                    return {
                        "status": "network_error",
                        "agent": "agent_registry",
                        "error": str(e),
                        "state": state,
                    }
        except Exception as e:
            return {
                "status": "exception",
                "agent": "agent_registry",
                "error": str(e),
                "state": state,
            }
