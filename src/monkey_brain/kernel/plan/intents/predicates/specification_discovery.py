"""Specification Discovery — routes open-ended build requests through
the SpecificationDiscoveryAgent for autonomous spec inference."""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("agentos.spec_discovery")

_SPEC_DISCOVERY_KEYWORDS = [
    "build me",
    "create a",
    "make me",
    "develop a",
    "write me",
    "i need an",
    "i want a",
    "can you build",
    "can you create",
    "help me build",
    "help me create",
    "design a",
    "generate a",
    "write an app",
    "build an app",
    "build a system",
    "build a tool",
    "write a tool",
    "create a tool",
    "build an assistant",
    "write an assistant",
    "create an assistant",
]


def is_specification_discovery_question(question: str) -> bool:
    """Check if question is an open-ended build request that needs spec discovery."""
    q = question.lower().strip()
    if any(kw in q for kw in _SPEC_DISCOVERY_KEYWORDS):
        return True
    if re.search(
        r"\b(build|create|make|develop|write|design|generate)\b.*\b(app|tool|system|assistant|bot|service|agent|workflow|pipeline|integration)\b",
        q,
    ):
        return True
    return False


async def specification_discovery_question_answer(
    client: Any,
    question: str,
    force: bool = False,
) -> str:
    """Route through SpecificationDiscoveryAgent for autonomous spec inference."""
    from broca.agents.specification_discovery_agent import SpecificationDiscoveryAgent

    agent = SpecificationDiscoveryAgent()
    result = await agent.handle({"question": question})

    payload = result.payload
    if not payload.get("discovered"):
        return "I was unable to discover a specification from your request. Please provide more details."

    discovery = payload["discovery"]
    workflow = payload["workflow"]
    approval = payload["approval"]

    lines = [
        "## Autonomous Specification Discovery",
        "",
        f"**Goal:** {discovery.get('goal', question)}",
        f"**Domain:** {discovery.get('domain', 'general')}",
        f"**Confidence:** {discovery.get('confidence', 0):.0%}",
        "",
        "### Required Agents",
    ]
    for agent_name in discovery.get("agents", []):
        lines.append(f"- {agent_name}")

    lines.append("")
    lines.append("### Required Providers")
    for provider in discovery.get("providers", []):
        lines.append(f"- {provider}")

    lines.append("")
    lines.append("### Knowledge Sources")
    for source in discovery.get("knowledge_sources", []):
        lines.append(f"- {source}")

    lines.append("")
    lines.append("### Recommended Workflow")
    for step in workflow.get("steps", []):
        deps = f" (after: {', '.join(step['depends_on'])})" if step.get("depends_on") else ""
        lines.append(f"{step['id'] + 1}. **{step['name']}** [{step['type']}] — {step['description']}{deps}")

    lines.append("")
    lines.append(f"**Complexity:** {workflow.get('estimated_complexity', 'unknown')}")
    lines.append(f"**Total Steps:** {workflow.get('total_steps', 0)}")

    if discovery.get("open_questions"):
        lines.append("")
        lines.append("### Open Questions")
        for q in discovery["open_questions"]:
            lines.append(f"- {q}")

    lines.append("")
    lines.append(f"**Approval Status:** {approval['status']}")

    return "\n".join(lines)
