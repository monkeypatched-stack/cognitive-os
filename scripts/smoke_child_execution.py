"""Smoke script: verify child pipeline executions dispatch actions to NATSReducerStore.

This script creates an in-memory NATSReducerStore (no NATS, no mem0), registers a
simple parent pipeline that spawns a child, runs the parent via PipelineRuntime,
and prints store action log to show thread.completed/answer.set entries.
"""

import asyncio
import time
import sys
from pathlib import Path

# Ensure repo root is on sys.path so `services` imports resolve when running from scripts/
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.agentos.events.reducer import (
    NATSReducerStore,
    create_thread_reducer,
    create_answer_reducer,
)
from services.agentos.pipeline.pipeline_bootstrap import (
    PipelineBootstrap,
    build_registry,
)
from services.agentos.pipeline.pipeline_interfaces import (
    ContextPayload,
    PhysicalState,
    CognitiveState,
)
from services.agentos.pipeline.pipeline_runtime import PipelineRuntime
from services.agentos.pipeline.pipeline_engine import PipelineEngine
from services.agentos.pipeline.pipeline_registry import PipelineRegistry
from services.agentos.pipeline.pipeline_interfaces import (
    ExecutionResult,
    PipelineStatus,
    IPipeline,
)


class ChildOperator(IPipeline):
    entity_type = "dummy_child"

    @property
    def capabilities(self):
        return []

    def validate_contracts(self, payload: ContextPayload) -> bool:
        return True

    async def execute(self, payload: ContextPayload) -> ExecutionResult:
        await asyncio.sleep(0.01)
        return ExecutionResult(
            status=PipelineStatus.COMPLETED,
            answer="child answer",
            semantic_hits=[],
            graph_paths=[],
            llm_answered=False,
            expert="dummy_child",
        )


class ParentOperator(IPipeline):
    entity_type = "parent"

    @property
    def capabilities(self):
        return []

    def validate_contracts(self, payload: ContextPayload) -> bool:
        return True

    async def execute(self, payload):
        # spawn child via runtime — in real code this would be done inside the operator,
        # but for this smoke we just delegate and return a combined answer
        from services.agentos.pipeline.pipeline_bootstrap import PipelineBootstrap

        bootstrap = PipelineBootstrap(db=payload.physical.db)
        runtime = bootstrap.get_runtime()
        child_payload = ContextPayload(
            physical=PhysicalState(
                entity_id="dummy",
                entity_type="dummy_child",
                parent_id="parent",
                db=payload.physical.db,
            ),
            cognitive=payload.cognitive,
            transaction_id=payload.transaction_id,
        )
        r = await runtime.execute_pipeline("dummy_child", child_payload)
        return ExecutionResult(status=PipelineStatus.COMPLETED, answer=f"parent saw: {r.answer}")


async def main():
    # Build store
    store = NATSReducerStore(nats_client=None, mem0_client=None)
    store.add_reducer(create_thread_reducer())
    store.add_reducer(create_answer_reducer())

    # Create a bootstrap with our runtime/engine wired to the store
    from types import SimpleNamespace

    fake_db = SimpleNamespace()
    bootstrap = PipelineBootstrap(db=fake_db, store=store)

    # Register dummy pipelines into the registry
    registry = bootstrap.get_registry()
    registry.register_pipeline("dummy_child", ChildOperator())
    registry.register_pipeline("parent", ParentOperator())

    # Ensure event publisher wiring (no-op here)
    breg, runtime, publisher, subscriber = bootstrap.build_runtime()

    # Build payload and execute parent via runtime
    payload = ContextPayload(
        physical=PhysicalState(entity_id="parent", entity_type="parent", parent_id=None, db=fake_db),
        cognitive=CognitiveState(question="run child", metadata={"store": store}),
        transaction_id="tx-smoke",
    )

    res = await runtime.execute_pipeline("parent", payload)
    print("Parent result:", res.answer)

    # Show store action log
    print("Store action log:")
    for a in store.get_action_log():
        print(a)


if __name__ == "__main__":
    asyncio.run(main())
