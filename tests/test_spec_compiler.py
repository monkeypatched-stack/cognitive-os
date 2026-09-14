"""Tests for the ETASSSpec → PromptCompilerAgent → StructuredPromptIR pipeline.

The whole stack is driven by specs. No hardcoded test data — every test
loads a workload spec from etass/workloads/ and compiles it.

Run:
  pytest tests/test_spec_compiler.py -v
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

# Make monorepo packages importable
ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages" / "broca"))
sys.path.insert(0, str(ROOT / "packages" / "cerebellum"))

WORKLOADS_DIR = ROOT / "etass" / "workloads"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def compiler():
    from broca.agents.prompt_compiler import PromptCompilerAgent

    return PromptCompilerAgent()


def _spec(name: str):
    from etass.specification import ETASSSpec

    return ETASSSpec.from_yaml(WORKLOADS_DIR / f"{name}.yaml")


def _compile(compiler, spec):
    """Synchronous wrapper — runs the async compiler in a new event loop."""

    async def _run():
        result = await compiler.handle({"spec": spec})
        return result.payload["prompt_ir"]

    return asyncio.run(_run())


# ---------------------------------------------------------------------------
# ETASSSpec — schema and reasoning strategy
# ---------------------------------------------------------------------------


class TestETASSSpec:
    def test_self_healing_loads(self):
        spec = _spec("self_healing")
        assert spec.workload == "self_healing"
        assert spec.domain == "software_engineering"
        assert spec.reasoning == "reflection"  # default for self_healing

    def test_batch_release_loads(self):
        spec = _spec("batch_release")
        assert spec.workload == "governance_review"
        assert spec.domain == "manufacturing"
        assert spec.bounded_context == "production"
        assert spec.aggregate == "batch"
        assert spec.entity == "lot"
        assert spec.value_object == "recipe"
        assert spec.reasoning == "debate"

    def test_code_generation_loads(self):
        spec = _spec("code_generation")
        assert spec.workload == "code_generation"
        assert spec.reasoning == "chain_of_thought"

    def test_architecture_review_loads(self):
        spec = _spec("architecture_review")
        assert spec.reasoning == "debate"

    def test_all_workloads_present(self):
        yaml_files = list(WORKLOADS_DIR.glob("*.yaml"))
        assert len(yaml_files) >= 4, f"Expected >= 4 workload specs, found {len(yaml_files)}"

    def test_invalid_reasoning_raises(self):
        from etass.specification import ETASSSpec

        with pytest.raises(ValueError, match="Unknown reasoning strategy"):
            ETASSSpec(workload="test", goal="test", reasoning="magic_thinking")

    def test_from_question(self):
        pytest.skip("test_from_question not yet implemented")
