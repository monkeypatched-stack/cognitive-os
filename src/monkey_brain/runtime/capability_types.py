"""Capability type classification — what kind of thing a capability group is,
which determines how the middleware resolves and executes it.

    Native          — a real Broca agent handles it directly (task CRUD, robotics, ...)
    Workflow        — an n8n workflow handles it (communication, ...)
    External API    — a real external system via OpenClaw/ARD (travel booking, ...)
    LLM / Generative — no ground truth to resolve against; the LLM's own
                       output IS the deliverable (summarize, translate)
    Human           — needs a human-in-the-loop approval/response
    Composite       — no single capability satisfies it; it decomposes into
                       an ordered chain of sub-capabilities the middleware
                       executes in sequence, threading each step's real
                       output into the next (see COMPOSITE_RECIPES)

This is intentionally a static map, not a learned/dynamic classifier — it
mirrors capability_router.py's CAPABILITY_GROUPS in scope and maturity.
Extend both together when a new capability group is added.
"""

from __future__ import annotations

from enum import Enum


class CapabilityType(str, Enum):
    NATIVE = "native"
    WORKFLOW = "workflow"
    EXTERNAL_API = "external_api"
    LLM_GENERATIVE = "llm_generative"
    HUMAN = "human"
    COMPOSITE = "composite"


# capability group (from capability_router.CAPABILITY_GROUPS) -> type.
# Unmapped groups default to NATIVE — the original, always-safe cascade
# (Broca -> providers -> n8n auto-create -> SDLC -> CodeGen-advisory).
GROUP_CAPABILITY_TYPES: dict[str, CapabilityType] = {
    "task_management": CapabilityType.NATIVE,
    "content_generation": CapabilityType.COMPOSITE,
    "document_processing": CapabilityType.LLM_GENERATIVE,
    "communication": CapabilityType.WORKFLOW,
    "scheduling": CapabilityType.NATIVE,
    "travel_booking": CapabilityType.EXTERNAL_API,
    "robotics": CapabilityType.NATIVE,
    "knowledge_management": CapabilityType.NATIVE,
}


def capability_type_for_group(group: str) -> CapabilityType:
    return GROUP_CAPABILITY_TYPES.get(group, CapabilityType.NATIVE)


# ── Composite recipes ───────────────────────────────────────────────────────
# A composite capability group decomposes into an ordered chain of steps.
# Each step is either:
#   - a real capability group name (resolved through the normal provider
#     hierarchy — Local Registry -> ARD -> OpenClaw -> n8n -> SDLC)
#   - one of the "__llm_*__" sentinels below, handled directly by an LLM
#     call within the chain (generation/review/formatting steps that have
#     no separate agent to resolve — the step *is* an LLM prompt).
#
# "Write a blog about X" -> research (real knowledge-search capability) ->
# generate (LLM writes the draft, grounded in the research step's real
# output) -> review (LLM proofreads) -> publish (LLM formats as markdown).
# No BlogAgent needs to exist for any of this.
LLM_GENERATE_STEP = "__llm_generate__"
LLM_REVIEW_STEP = "__llm_review__"
LLM_PUBLISH_STEP = "__llm_publish__"

CompositeStep = tuple[str, str]  # (step_name, resolves_as)

COMPOSITE_RECIPES: dict[str, list[CompositeStep]] = {
    "content_generation": [
        ("research", "knowledge_management"),
        ("generate", LLM_GENERATE_STEP),
        ("review", LLM_REVIEW_STEP),
        ("publish", LLM_PUBLISH_STEP),
    ],
}


def composite_recipe_for_group(group: str) -> list[CompositeStep] | None:
    return COMPOSITE_RECIPES.get(group)
