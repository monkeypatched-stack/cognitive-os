"""Smoke script to exercise OrchestratorPipeline._execute_thread enrichment without pytest/coverage.

Run with: python scripts/smoke_orchestrator.py
"""

import asyncio
from types import SimpleNamespace

# Monkeypatch a fake helper and PipelineBootstrap similar to tests
import services.agentos.intents.helpers as helpers


async def fake_helper(client, question, force=False):
    return ("SMOKE SUMMARY", [SimpleNamespace(id="SMOKE_HIT")], [], False)


setattr(helpers, "test_pipeline_question_answer", fake_helper)


class FakePipeline:
    def __init__(self):
        self.received_payload = None

    async def execute(self, payload):
        self.received_payload = payload
        return SimpleNamespace(answer="OK", semantic_hits=[], graph_paths=[], expert="test")


fake_pipeline = FakePipeline()


class FakeEntry:
    def __init__(self, pipeline):
        self.pipeline = pipeline


class FakeRegistry:
    def get_pipeline(self, pipeline_id):
        if pipeline_id == "test_pipeline":
            return FakeEntry(fake_pipeline)
        return None


class FakeBootstrap:
    def __init__(self, db=None):
        pass

    def get_registry(self):
        return FakeRegistry()


# Monkeypatch PipelineBootstrap
import services.agentos.pipeline.pipeline_bootstrap as pb

pb.PipelineBootstrap = FakeBootstrap

# Run the orchestrator
from services.agentos.pipeline.pipeline_orchestrator import (
    OrchestratorPipeline,
    ThreadSpec,
)


async def main():
    orch = OrchestratorPipeline(engine=None, store=None, db="FAKE_DB")
    spec = ThreadSpec("test_pipeline", question="Original question", priority=2, context={})
    res = await orch._execute_thread(spec, {})
    print("Result:", res)
    print("Received payload question:", fake_pipeline.received_payload.cognitive.question)
    print("Received payload metadata:", fake_pipeline.received_payload.cognitive.metadata)


if __name__ == "__main__":
    asyncio.run(main())
