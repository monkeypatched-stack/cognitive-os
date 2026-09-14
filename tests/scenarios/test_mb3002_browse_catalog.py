"""MB-3002 Browse Catalog — customer browse-request scenario.

Customer requests: "Show me laptops."

Verify:
    - catalog queried
    - results returned
    - context published

Three layers:

  1. "catalog queried" / "results returned" (deterministic, no LLM) — the
     commerce domain's actual catalog mechanism (kernel/domains/
     commerce.py::CommerceCapability, "search_products", backed by
     kernel/domains/grocery.py::open_products()) invoked directly with
     the customer's query.
  2. "context published" (no LLM required either way) — the customer's
     literal request routed through the real end-to-end chain
     (PlanetaryRuntime.execute_actor_request() -> ... ->
     CognitiveActor._cognitive_tick()) publishes a Context Stream
     OBSERVATION event unconditionally, regardless of what the planner
     does with the request.
  3. Full pipeline through a real local LLM (Ollama — no ANTHROPIC_API_KEY
     needed, per kernel/execute/provider/model_backend.py's existing
     "ollama" provider) — proves the planner genuinely reasons about the
     request rather than failing closed with an empty plan (the baseline
     documented in test_ec001_simple_purchase.py when no provider is
     configured at all): a real plan with real steps identifying this as
     a search/catalog request, which now actually EXECUTES successfully
     end to end. Skipped if no local Ollama server is reachable, so this
     file still runs on machines/CI without one.

     This layer originally surfaced a real bug: local models commonly
     write the word "none" for "no permission needed" instead of a true
     empty string, and belief_runtime.py's execution-stage gate
     (`if step.required_permission: ...`) treated that non-empty "none"
     as an actual required permission, denying every step of an
     unpermissioned plan. Fixed in kernel/pipeline/llm_planner.py — the
     step-construction boundary where the model's free-text JSON becomes
     a structured PlanStep now normalizes "none"/"n/a"/etc. to "".
"""

from __future__ import annotations

import httpx
import pytest

from src.monkey_brain.kernel.domains.commerce import (
    CommerceCapability,
    CommerceCapabilityBus,
)
from src.monkey_brain.kernel.knowledge_graph import KnowledgeGraph
from src.monkey_brain.kernel.society.context_stream import ContextEventType
from src.monkey_brain.kernel.society.domain import (
    ActorIdentity,
    ActorProfile,
    ActorType,
)
from src.monkey_brain.kernel.society.integration import PlanetaryRuntime

CUSTOMER_NAME = "Alice"
REQUEST_TEXT = "Show me laptops"
QUERY = "laptops"


def _ollama_reachable(base_url: str = "http://localhost:11434") -> bool:
    try:
        return httpx.get(f"{base_url}/api/tags", timeout=2.0).status_code == 200
    except Exception:
        return False


def test_mb3002_catalog_queried_and_results_returned():
    kg = KnowledgeGraph()
    bus = CommerceCapabilityBus([CommerceCapability()])

    found = bus.discover_operation("search_products")
    assert found is not None, "catalog must expose a search_products operation"

    # catalog queried.
    results = bus.invoke("search_products", kg, query=QUERY)

    # results returned — a real list (empty: this marketplace's catalog is
    # grocery-domain seed data, so "laptops" legitimately matches nothing;
    # the point is the query mechanism ran and returned a result, not that
    # a laptop actually exists in a grocery catalog).
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_mb3002_context_published_for_customer_request():
    marketplace = PlanetaryRuntime()
    profile = ActorProfile(identity=ActorIdentity(name=CUSTOMER_NAME, actor_type=ActorType.HUMAN))
    alice = marketplace.register_actor(profile)

    events_before = marketplace.context_stream.event_count
    await marketplace.execute_actor_request(alice.actor_id, {"question": REQUEST_TEXT})
    new_events = marketplace.context_stream.events(limit=marketplace.context_stream.event_count - events_before)

    # context published.
    assert any(e.event_type is ContextEventType.OBSERVATION and e.actor_id == alice.actor_id for e in new_events)


@pytest.mark.asyncio
async def test_mb3002_catalog_queried_via_real_local_llm(monkeypatch):
    if not _ollama_reachable():
        pytest.skip("no local Ollama server reachable at localhost:11434")

    from src.monkey_brain.kernel.execute.provider import (
        model_backend as model_backend_module,
    )

    # Force Ollama for this test regardless of MODEL_BACKEND/import order —
    # ModelBackend's default provider is a module-level constant read once
    # at import time, and get_backend() caches a singleton, so neither a
    # late os.environ["MODEL_BACKEND"] write nor monkeypatch.setenv() is
    # guaranteed to take effect; replacing the cached singleton directly is.
    #
    # Patching get_backend() itself (not _default_backend) is required:
    # tests/conftest.py's autouse _default_test_llm_backend fixture ALREADY
    # patches get_backend to unconditionally return a fake, deterministic
    # backend, ignoring _default_backend entirely — patching only
    # _default_backend here (the original approach) silently had no effect,
    # LLMPlanner still got the fake backend, and this test's real-LLM
    # assertions were unknowingly running against fake "achieve_<word>"
    # output instead. monkeypatch.setattr calls inside a test body override
    # an autouse fixture's earlier patch of the SAME attribute.
    monkeypatch.setattr(
        model_backend_module,
        "get_backend",
        lambda: model_backend_module.ModelBackend(provider="ollama"),
    )

    marketplace = PlanetaryRuntime()
    profile = ActorProfile(identity=ActorIdentity(name=CUSTOMER_NAME, actor_type=ActorType.HUMAN))
    alice = marketplace.register_actor(profile)

    result = await marketplace.execute_actor_request(alice.actor_id, {"question": REQUEST_TEXT})

    # catalog queried: a real local model, given the actual request,
    # produces a non-empty plan whose steps are about searching/showing
    # laptops — not the zero-provider baseline's empty, zero-confidence
    # plan.
    assert result.plan.planner == "llm"
    assert result.plan.confidence > 0.0
    assert len(result.plan.steps) > 0
    plan_text = " ".join(f"{step.action} {step.description}".lower() for step in result.plan.steps)
    assert "laptop" in plan_text or "search" in plan_text

    # results returned — and, now that the "none" permission-normalization
    # bug is fixed, actually executed rather than denied.
    assert result.actual_outcome is not None
    assert result.actual_outcome["actions_executed"] > 0
    assert result.actual_outcome["failure_count"] == 0
    assert all(action["success"] for action in result.actions)
