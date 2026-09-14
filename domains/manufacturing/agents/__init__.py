"""Manufacturing domain agents that read the real data.

The 300-odd agents in broca.agents.domains are structural stubs — LineAgent.act() returns
{"action": "line.status", "success": True} and touches nothing. That is why every executed
step reported [ok] while the answer had zero evidence behind it: there was no data in the
pipeline to have evidence *of*.

These four are the real thing. They query Mongo, return the rows they actually found, and
cite the collections they read, so `kernel.execute.grounding` can tell a retrieved fact from
an invention. They resolve on tier 2 of AgentResolver (the Broca registry), ahead of the n8n
and SDLC build tiers, so a planner asking for LineResolverAgent gets this rather than a
generated workflow guessing at a schema.

Registered by register_manufacturing_agents(), called from api.bootstrap after the ETASS
agents — importing this package is what triggers @auto_register.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("manufacturing.agents")


def register_manufacturing_agents(runtime: Any = None) -> list[str]:
    """Register the data-backed manufacturing agents into the Broca registry.

    Idempotent: registry.register() is keyed by agent_type, so re-registering replaces the
    entry rather than duplicating it.
    """
    from broca.registry import get_registry

    from domains.manufacturing.agents.calibration import (
        CalibrationDueEvaluatorAgent,
        CalibrationScheduleAgent,
    )
    from domains.manufacturing.agents.equipment import EquipmentInventoryAgent
    from domains.manufacturing.agents.lines import LineResolverAgent

    registry = get_registry()
    agents = [
        LineResolverAgent(runtime=runtime),
        EquipmentInventoryAgent(runtime=runtime),
        CalibrationScheduleAgent(runtime=runtime),
        CalibrationDueEvaluatorAgent(runtime=runtime),
    ]
    for agent in agents:
        registry.register(agent)

    names = [a.agent_type for a in agents]
    logger.info(
        "[manufacturing] registered %d data-backed agents: %s",
        len(names),
        ", ".join(names),
    )
    return names


__all__ = ["register_manufacturing_agents"]
