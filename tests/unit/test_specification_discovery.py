"""Tests for SpecificationDiscoveryAgent — Autonomous Specification Discovery.

Verifies:
1. Intent analysis and domain inference
2. Agent and provider discovery
3. Workflow DAG generation
4. Specification structure with workflow
5. Approval gate
6. Ollama provider selection
7. Heuristic fallback
"""

import asyncio
import json
import sys
import os

_repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Resolve `broca` to packages/broca/broca (the full package with `agents/`), not the
# src/broca stub. packages/broca must sit AHEAD of src on the path, else src/broca shadows
# it and `broca.agents` fails to import.
_broca_pkg = os.path.join(_repo, "packages", "broca")
for _p in (os.path.join(_repo, "src"), _repo, _broca_pkg):
    if _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)

import pytest

# These drive a real SpecificationDiscoveryAgent that reaches a local Ollama server. Skip the
# whole module by default — BEFORE importing broca — so CI is deterministic and never depends
# on Ollama or on broca-package import ordering. Set RUN_OLLAMA_TESTS=1 to exercise them.
if not os.getenv("RUN_OLLAMA_TESTS"):
    pytest.skip(
        "requires a local Ollama server; set RUN_OLLAMA_TESTS=1 to run",
        allow_module_level=True,
    )

# Evict a wrongly-resolved `broca` (the src/broca stub) cached by an earlier test so the full
# package loads from packages/broca.
for _m in [k for k in list(sys.modules) if k == "broca" or k.startswith("broca.")]:
    if not hasattr(sys.modules[_m], "agents"):
        del sys.modules[_m]

from broca.agents.specification_discovery_agent import (
    SpecificationDiscoveryAgent,
    OllamaClient,
    _ollama_available,
)


def test_empty_intent_returns_not_discovered():
    async def scenario():
        agent = SpecificationDiscoveryAgent()
        result = await agent.handle({})
        assert result.payload["discovered"] is False
        assert result.reward < 0.5

    asyncio.run(scenario())


def test_google_docs_essay_discovers_agents():
    """Heuristic discovers correct agents for a writing task."""

    async def scenario():
        agent = SpecificationDiscoveryAgent()
        result = await agent.handle(
            {
                "question": "Given a Google Doc, if I give a topic, write an essay in my writing style.",
            }
        )
        discovery = result.payload["discovery"]

        assert result.payload["discovered"] is True
        assert "google_docs" in discovery["platforms"]
        assert discovery["domain"] == "document_writing"
        assert "writer" in discovery["agents"]
        assert "style_analyzer" in discovery["agents"]
        assert "user_documents" in discovery["knowledge_sources"]
        assert "oauth2_google" in discovery["security"]

    asyncio.run(scenario())


def test_workflow_generation():
    """Workflow DAG is generated with correct steps and dependencies."""

    async def scenario():
        agent = SpecificationDiscoveryAgent()
        result = await agent.handle(
            {
                "question": "Given a Google Doc, if I give a topic, write an essay in my writing style.",
            }
        )
        workflow = result.payload["workflow"]

        assert "steps" in workflow
        assert "execution_order" in workflow
        assert "total_steps" in workflow
        assert "parallel_groups" in workflow
        assert "estimated_complexity" in workflow

        step_names = [s["name"] for s in workflow["steps"]]
        assert "content_generation" in step_names
        assert "style_analysis" in step_names

        style_step = next(s for s in workflow["steps"] if s["name"] == "style_analysis")
        assert style_step["agent"] == "style_analyzer"
        assert style_step["type"] == "analysis"

        write_step = next(s for s in workflow["steps"] if s["name"] == "content_generation")
        assert "style_analysis" in write_step["depends_on"]
        assert write_step["agent"] == "writer"

    asyncio.run(scenario())


def test_workflow_topological_order():
    """Execution order respects dependencies."""

    async def scenario():
        agent = SpecificationDiscoveryAgent()
        result = await agent.handle(
            {
                "question": "Research a topic, write a blog post, then review it before publishing to Google Docs.",
            }
        )
        workflow = result.payload["workflow"]
        order = workflow["execution_order"]

        all_steps_in_order = [name for group in order for name in group]
        assert len(all_steps_in_order) == workflow["total_steps"]

        name_to_idx = {name: i for i, name in enumerate(all_steps_in_order)}
        for step in workflow["steps"]:
            for dep in step.get("depends_on", []):
                if dep in name_to_idx:
                    assert name_to_idx[dep] < name_to_idx[step["name"]], f"{dep} must come before {step['name']}"

    asyncio.run(scenario())


def test_specification_includes_workflow():
    """The specification output contains the workflow."""

    async def scenario():
        agent = SpecificationDiscoveryAgent()
        result = await agent.handle(
            {
                "question": "Write an essay about climate change in Google Docs.",
            }
        )
        spec = result.payload["specification"]

        assert "workflow" in spec
        assert "steps" in spec["workflow"]
        assert "total_steps" in spec["workflow"]
        assert spec["workflow"]["total_steps"] >= 1

    asyncio.run(scenario())


def test_specification_has_all_sections():
    """The specification has all required top-level sections."""

    async def scenario():
        agent = SpecificationDiscoveryAgent()
        result = await agent.handle(
            {
                "question": "Automate fetching GitHub PRs and posting summaries to Slack.",
            }
        )
        spec = result.payload["specification"]

        for key in [
            "specification",
            "application",
            "providers",
            "agents",
            "capabilities",
            "knowledge",
            "security",
            "constraints",
            "workflow",
            "metadata",
        ]:
            assert key in spec, f"Missing section: {key}"

        assert spec["specification"]["version"] == "1.0"
        assert spec["specification"]["source"] == "autonomous_discovery"

    asyncio.run(scenario())


def test_approval_gate_pending():
    """Discovery produces a pending approval gate."""

    async def scenario():
        agent = SpecificationDiscoveryAgent()
        result = await agent.handle(
            {
                "question": "Build me a calendar integration.",
            }
        )
        approval = result.payload["approval"]
        assert approval["status"] == "pending"
        assert "specification" in approval["message"].lower() or "workflow" in approval["message"].lower()

    asyncio.run(scenario())


def test_observations_include_workflow():
    """Observations mention the workflow steps."""

    async def scenario():
        agent = SpecificationDiscoveryAgent()
        result = await agent.handle(
            {
                "question": "Write an essay about AI in Google Docs with grammar checking.",
            }
        )
        observations = result.observations
        assert any("agents" in o for o in observations)
        assert any("workflow" in o.lower() for o in observations)
        assert any("Confidence" in o for o in observations)

    asyncio.run(scenario())


def test_data_analysis_workflow():
    """Workflow for a data analysis task includes analyst and visualizer steps."""

    async def scenario():
        agent = SpecificationDiscoveryAgent()
        result = await agent.handle(
            {
                "question": "Analyze my sales data and create a dashboard with charts.",
            }
        )
        workflow = result.payload["workflow"]
        step_names = [s["name"] for s in workflow["steps"]]

        assert "data_analysis" in step_names
        assert "visual_generation" in step_names

    asyncio.run(scenario())


def test_workflow_automation_dag():
    """Workflow automation task produces a multi-step DAG with orchestrator."""

    async def scenario():
        agent = SpecificationDiscoveryAgent()
        result = await agent.handle(
            {
                "question": "Automate fetching GitHub PRs and posting summaries to Slack.",
            }
        )
        workflow = result.payload["workflow"]
        step_names = [s["name"] for s in workflow["steps"]]

        assert "publish_github" in step_names
        assert "publish_messaging" in step_names
        assert "orchestrate" in step_names
        assert workflow["estimated_complexity"] in ("medium", "high")

    asyncio.run(scenario())


def test_parallel_groups_identified():
    """Independent steps are grouped for parallel execution."""

    async def scenario():
        agent = SpecificationDiscoveryAgent()
        result = await agent.handle(
            {
                "question": "Research a topic, analyze data, then write and review.",
            }
        )
        workflow = result.payload["workflow"]
        groups = workflow["parallel_groups"]

        assert len(groups) >= 1
        all_in_groups = [name for g in groups for name in g]
        assert len(all_in_groups) == workflow["total_steps"]

    asyncio.run(scenario())


def test_ollama_provider_selection():
    """Provider selection falls back correctly based on availability."""
    agent = SpecificationDiscoveryAgent()
    if _ollama_available():
        assert isinstance(agent._llm, OllamaClient)
    else:
        assert agent._llm is None or not isinstance(agent._llm, OllamaClient)


def test_generic_intent_low_confidence():
    """Generic intents get lower confidence."""

    async def scenario():
        agent = SpecificationDiscoveryAgent()
        result = await agent.handle(
            {
                "question": "Help me think.",
            }
        )
        assert result.payload["discovery"]["confidence"] <= 0.4

    asyncio.run(scenario())


def test_metadata_includes_llm_provider():
    """Metadata reports which LLM provider was used."""

    async def scenario():
        agent = SpecificationDiscoveryAgent()
        result = await agent.handle(
            {
                "question": "Write a blog post.",
            }
        )
        meta = result.payload["specification"]["metadata"]
        assert "llm_provider" in meta
        assert isinstance(meta["llm_provider"], str)

    asyncio.run(scenario())


if __name__ == "__main__":
    tests = [
        test_empty_intent_returns_not_discovered,
        test_google_docs_essay_discovers_agents,
        test_workflow_generation,
        test_workflow_topological_order,
        test_specification_includes_workflow,
        test_specification_has_all_sections,
        test_approval_gate_pending,
        test_observations_include_workflow,
        test_data_analysis_workflow,
        test_workflow_automation_dag,
        test_parallel_groups_identified,
        test_ollama_provider_selection,
        test_generic_intent_low_confidence,
        test_metadata_includes_llm_provider,
    ]
    ok = 0
    failures = []
    for fn in tests:
        try:
            fn()
            ok += 1
            print(f"  PASS: {fn.__name__}")
        except Exception as e:
            failures.append(f"{fn.__name__}: {e!r}")
            print(f"  FAIL: {fn.__name__}: {e!r}")
    print(f"\nSpecificationDiscovery: {ok}/{len(tests)} passed")
    if failures:
        print("Failures:")
        for f in failures:
            print(f"  {f}")
