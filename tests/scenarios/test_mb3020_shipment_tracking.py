"""MB-3020 Shipment Tracking — customer status-request scenario.

Customer requests: "Where is my order?"

No way to answer this existed: get_shipment() (MB-3019) requires a
shipment_id, but a customer only knows their order_id, and an order can
ship as more than one shipment. Built
kernel/domains/logistics.py::track_order() for this — finds every
shipment persisted against an order_id and reports each one's status
plus a single overall_status (the LEAST advanced status among them,
since the order isn't "delivered" until every shipment is).

Same three layers as MB-3002 (the precedent for a free-text "Customer
requests" ticket):

  1. Deterministic, no LLM — track_order() invoked directly against a
     seeded shipment.
  2. "context published" (no LLM required) — the customer's literal
     request published as a Context Stream OBSERVATION event, regardless
     of what the planner does with it.
  3. Full pipeline through a real local LLM (Ollama). As MB-3002's own
     investigation established, PlanetaryRuntime.execute_actor_request()
     runs the ActionExecutor with no capability bus wired in — every
     plan step, whatever capability name the model invents, executes as
     a simulated no-op success. So this layer proves the planner
     genuinely reasons about "where is my order" (a real, non-empty plan
     about tracking/status), not that track_order() itself gets invoked
     end-to-end — same scope MB-3002 verified for "search"/"laptop".
     Skipped if no local Ollama server is reachable.
"""

from __future__ import annotations

import httpx
import pytest

from src.monkey_brain.kernel.domains.logistics import (
    LogisticsCapability,
    create_shipment,
    track_order,
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
REQUEST_TEXT = "Where is my order?"
ORDER_ID = "ORD-TRACK-1"


def _ollama_reachable(base_url: str = "http://localhost:11434") -> bool:
    try:
        return httpx.get(f"{base_url}/api/tags", timeout=2.0).status_code == 200
    except Exception:
        return False


def test_mb3020_track_order_reports_shipment_status():
    kg = KnowledgeGraph()
    create_shipment(kg, ORDER_ID, packages=[{"product_id": "p1", "name": "Apples", "qty": 3}])

    result = track_order(kg, ORDER_ID)

    assert result["success"] is True
    assert result["status"] == "created"
    assert result["shipment_count"] == 1


def test_mb3020_track_order_via_capability():
    kg = KnowledgeGraph()
    create_shipment(kg, ORDER_ID, packages=[{"product_id": "p1", "name": "Apples", "qty": 3}])
    cap = LogisticsCapability()

    assert cap.can_handle("track_order")
    assert cap.invoke("track_order", kg, ORDER_ID) == track_order(kg, ORDER_ID)


def test_mb3020_no_shipment_for_order_is_an_honest_failure():
    kg = KnowledgeGraph()

    result = track_order(kg, "does-not-exist")

    assert result["success"] is False
    assert "no shipment found" in result["error"]


@pytest.mark.asyncio
async def test_mb3020_context_published_for_customer_request():
    marketplace = PlanetaryRuntime()
    profile = ActorProfile(identity=ActorIdentity(name=CUSTOMER_NAME, actor_type=ActorType.HUMAN))
    alice = marketplace.register_actor(profile)

    events_before = marketplace.context_stream.event_count
    await marketplace.execute_actor_request(alice.actor_id, {"question": REQUEST_TEXT})
    new_events = marketplace.context_stream.events(limit=marketplace.context_stream.event_count - events_before)

    # context published.
    assert any(e.event_type is ContextEventType.OBSERVATION and e.actor_id == alice.actor_id for e in new_events)


@pytest.mark.asyncio
async def test_mb3020_order_status_reasoned_about_via_real_local_llm(monkeypatch):
    if not _ollama_reachable():
        pytest.skip("no local Ollama server reachable at localhost:11434")

    from src.monkey_brain.kernel.execute.provider import (
        model_backend as model_backend_module,
    )

    # Patching get_backend() itself (not _default_backend) is required:
    # tests/conftest.py's autouse _default_test_llm_backend fixture ALREADY
    # patches get_backend to unconditionally return a fake, deterministic
    # backend, ignoring _default_backend entirely -- see
    # test_mb3002_browse_catalog.py's identical fix for the full account of
    # this (confirmed live, this session: this exact test previously
    # crashed with "'str' object can't be awaited" -- LLMPlanner still got
    # the fake, synchronous backend regardless of this patch).
    monkeypatch.setattr(
        model_backend_module,
        "get_backend",
        lambda: model_backend_module.ModelBackend(provider="ollama"),
    )

    marketplace = PlanetaryRuntime()
    profile = ActorProfile(identity=ActorIdentity(name=CUSTOMER_NAME, actor_type=ActorType.HUMAN))
    alice = marketplace.register_actor(profile)

    result = await marketplace.execute_actor_request(alice.actor_id, {"question": REQUEST_TEXT})

    assert result.plan.planner == "llm"
    assert result.plan.confidence > 0.0
    assert len(result.plan.steps) > 0
    plan_text = " ".join(f"{step.action} {step.description}".lower() for step in result.plan.steps)
    assert any(kw in plan_text for kw in ("order", "track", "ship", "status", "deliver"))

    assert result.actual_outcome is not None
    assert result.actual_outcome["actions_executed"] > 0
    assert result.actual_outcome["failure_count"] == 0
