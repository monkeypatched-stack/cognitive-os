"""MB-3056 Lemon Metrics — verify planetary/society/movement/interaction
metrics and business KPIs.

Verify: planetary metrics, society metrics, movement metrics,
interaction metrics, business KPIs.

Like MB-3010/3015/3028/3032/3046/3047/3053/3054/3055, no new code was
needed for the first four categories: kernel/society/integration.py::
PlanetaryRuntime._publish_lemon_metrics() (Prompt 8's Lemon
Observability, from earlier in this session) already publishes real
metrics for every one of them on every cycle() call, routed through
kernel/compile/_obs.py — the same Lemon integration point every other
subsystem in this codebase uses. Verified here by installing _obs.py's
own Recorder test sink and running a real cycle():

  - planetary metrics:   "planetary.*"   (cycle duration, entities/
    societies ticked, actors observed, context events published, ...)
  - society metrics:     "governance.*"  (permanent/temporary
    memberships, effective-membership calculations, space-society
    associations)
  - movement metrics:    "presence.*"    (presence updates, actor
    movements, timeline entries)
  - interaction metrics: "planetary.interactions_routed"

Per explicit design choice ("verify + report the gap"): business KPIs
(orders, revenue, shipments, tickets, ...) are NOT instrumented at
all — none of the ~50 domain functions built across MB-3019-3055
(grocery.py, commerce.py, logistics.py, finance.py, supply_chain.py,
support.py) ever call _obs.counter()/gauge()/event(). Verified
behaviorally below by running a real merchant-onboarding -> product-
listing -> order-creation flow with the Recorder sink installed and
confirming it captures nothing — a genuine gap, not fixed in this
ticket.
"""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.compile import _obs
from src.monkey_brain.kernel.domains.commerce import list_product, onboard_merchant
from src.monkey_brain.kernel.domains.grocery import OrderCreationCapability
from src.monkey_brain.kernel.knowledge_graph import KnowledgeGraph
from src.monkey_brain.kernel.society.domain import (
    ActorIdentity,
    ActorProfile,
    ActorType,
)
from src.monkey_brain.kernel.society.integration import PlanetaryRuntime


async def _run_a_real_cycle_and_capture_metrics() -> _obs.Recorder:
    recorder = _obs.Recorder()
    _obs.set_sink(recorder)
    try:
        marketplace = PlanetaryRuntime()
        marketplace.register_actor(ActorProfile(identity=ActorIdentity(name="Alice", actor_type=ActorType.HUMAN)))
        marketplace.register_actor(ActorProfile(identity=ActorIdentity(name="Bob", actor_type=ActorType.HUMAN)))
        await marketplace.cycle()
        return recorder
    finally:
        _obs.clear_sink()


@pytest.mark.asyncio
async def test_mb3056_planetary_metrics_are_published():
    recorder = await _run_a_real_cycle_and_capture_metrics()

    planetary = {n for n in recorder.names() if n.startswith("planetary.")}

    assert {
        "planetary.cycle_duration_ms",
        "planetary.entities_ticked",
        "planetary.societies_ticked",
        "planetary.actors_observed",
        "planetary.context_events_published",
        "planetary.simulation_time_seconds",
        "planetary.peak_queue_depth",
    } <= planetary


@pytest.mark.asyncio
async def test_mb3056_society_metrics_are_published():
    recorder = await _run_a_real_cycle_and_capture_metrics()

    governance = {n for n in recorder.names() if n.startswith("governance.")}

    assert {
        "governance.permanent_memberships",
        "governance.temporary_memberships_created",
        "governance.temporary_memberships_revoked",
        "governance.effective_membership_calculations",
        "governance.membership_events_published",
        "governance.spaces_with_societies",
        "governance.spaces_without_societies",
        "governance.societies_without_spaces",
    } <= governance


@pytest.mark.asyncio
async def test_mb3056_movement_metrics_are_published():
    recorder = await _run_a_real_cycle_and_capture_metrics()

    presence = {n for n in recorder.names() if n.startswith("presence.")}

    assert {
        "presence.updates",
        "presence.actor_movements",
        "presence.timeline_entries",
    } <= presence


@pytest.mark.asyncio
async def test_mb3056_interaction_metrics_are_published():
    recorder = await _run_a_real_cycle_and_capture_metrics()

    assert "planetary.interactions_routed" in recorder.names()


def test_mb3056_business_kpis_are_not_yet_instrumented():
    """Documents the real gap: a full, real business flow (merchant
    onboarding -> product listing -> order creation) emits ZERO Lemon
    metrics. Not a bug fixed here — see module docstring."""
    recorder = _obs.Recorder()
    _obs.set_sink(recorder)
    try:
        kg = KnowledgeGraph()
        store_id = onboard_merchant(kg, "merchant_bob", "Bob's Store")["store_id"]
        product_id = list_product(kg, store_id, "merchant_bob", "Oat Milk", price=4.5, quantity=10)["product_id"]
        cap = OrderCreationCapability()
        result = cap.handle(
            {
                "context": {
                    "knowledge_graph": kg,
                    "actor_id": "alice",
                    "selected_product": [
                        {
                            "id": product_id,
                            "name": "Oat Milk",
                            "price": 4.5,
                            "qty": 1,
                            "store_id": store_id,
                            "store_name": "Bob's Store",
                        }
                    ],
                }
            }
        )

        assert result["success"] is True
        assert recorder.names() == set()
    finally:
        _obs.clear_sink()
