"""ClientCapabilityRegisterAgent — instantiates a ClientCapabilityAgent from a
compiled client capability chart and registers it into the Broca registry,
making it discoverable as "{service_slug}-client-agent" for the rest of the
process lifetime.

Distinct from CompileAgentAgent (compile_agent_agent.py), which only compiles
the chart into a .prompt.md — this is the registration half of "monkeypatched
make create-agent" (soma.py's soma_create_agent does this same
instantiate+register inline, not via a Broca agent), wrapped here so the SDLC
graph can drive it as a capability node.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.client_capability_register")

_REPO = Path(__file__).resolve().parents[4]


class ClientCapabilityRegisterAgent(BaseETASSAgent):
    agent_type = "client_capability_register"
    description = "Registers a compiled client capability chart as a discoverable Broca agent"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict) -> dict:
        service_slug = str(context.get("service_slug", "")).strip()
        if not service_slug:
            self._reward(False, 0.0)
            return self._result(payload={"registered": False}, observations=["no service_slug"])

        chart_path = (
            Path(str(context["chart_path"]))
            if context.get("chart_path")
            else _REPO / "somatic" / "charts" / f"{service_slug}-client" / "values.yaml"
        )
        if not chart_path.exists():
            self._reward(False, 0.0)
            return self._result(
                payload={
                    "registered": False,
                    "error": f"chart not found: {chart_path}",
                },
                observations=[f"no compiled client chart for {service_slug}"],
            )

        try:
            from broca.agents.client_capability_agent import ClientCapabilityAgent
            from broca.registry import get_registry

            agent = ClientCapabilityAgent(service_slug=service_slug, chart_path=chart_path, runtime=self._runtime)
            get_registry().register(agent)
        except Exception as e:
            self._reward(False, 0.0)
            return self._result(
                payload={"registered": False, "error": str(e)},
                observations=[f"registration failed: {e}"],
            )

        self._reward(True, 0.9)
        return self._result(
            payload={
                "registered": True,
                "agent_type": agent.agent_type,
                "operations": agent.operations(),
            },
            observations=[f"registered {agent.agent_type} with {len(agent.operations())} operations"],
        )
