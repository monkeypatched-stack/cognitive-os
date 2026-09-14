"""World Graph Builder / Cognitive Reasoning separation — REST Setup +
/prompt Reasoning scenario.

Principle: APIs build the world. /prompt reasons over the world. Never
use /prompt to create entities.

Every benchmark now has two phases:

  Phase 1 — Setup (API): build the graph entirely through REST routes.
    No reasoning, no planning, no cognition — deterministic mutations
    of PlanetaryRuntime.knowledge_graph (api/routes/commerce.py,
    orders.py, fulfillment.py, events.py — new in this pass; planet.py/
    societies.py/actors.py/memberships.py already existed and were
    comprehensive).

  Phase 2 — Reasoning (Prompt): ask a real question via POST /prompt.
    CognitiveOS reasons over the graph Phase 1 already built.

Closes two real gaps found investigating this:

  1. There was no REST surface for commerce at all. A generic
     /world/entities CRUD endpoint existed, but it writes to
     pr.world (kernel/society/world.py's separate WorldEntity/
     SharedWorld system) — NOT the KnowledgeGraph every commerce
     function (grocery.py/commerce.py/logistics.py/finance.py) actually
     reads and writes. Building on it would have silently produced a
     world /prompt's reasoning could never see. Per explicit design
     choice, the new commerce/orders/fulfillment/events routes target
     pr.knowledge_graph directly instead.
  2. Actors created via the real POST /actors route were never wired
     with a real capability_bus or a KnowledgeGraph-pointed context
     (MB-3060's finding) — every action would have simulated regardless
     of what Phase 1 built. Per explicit design choice, real actor
     creation now wires the same business-capable engine MB-3060 proved
     works (build_runtime_engine() + ContextConstructionEngine), by
     default, for every actor — not a one-off test construction.

Verified live, end to end, in one real app boot: every Setup-phase call
below actually succeeded (actor, merchant, product, wallet, cart, order,
payment, pack, shipment, tracking, event), and the /prompt call that
followed genuinely read the exact product Phase 1 created — correct
price, and quantity correctly reflecting the earlier order's real
reservation — proving REST-built world and prompt-driven reasoning now
share the same graph, not two disconnected ones.
"""

from __future__ import annotations

import os

os.environ.setdefault("AGENTOS_AUTH_REQUIRED", "false")

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.monkey_brain.api.main import app  # noqa: E402


def _ollama_reachable(base_url: str = "http://localhost:11434") -> bool:
    try:
        return httpx.get(f"{base_url}/api/tags", timeout=2.0).status_code == 200
    except Exception:
        return False


def test_rest_setup_builds_a_real_commerce_graph_and_prompt_reasons_over_it(
    monkeypatch,
):
    if not _ollama_reachable():
        pytest.skip("no local Ollama server reachable at localhost:11434")

    from src.monkey_brain.kernel.execute.provider import (
        model_backend as model_backend_module,
    )

    # Patching get_backend() itself (not _default_backend) is required —
    # see test_mb3002_browse_catalog.py's fix for the full account: the
    # autouse _default_test_llm_backend fixture already patches get_backend
    # to a fake, synchronous backend, which _default_backend alone never
    # overrides.
    monkeypatch.setattr(
        model_backend_module,
        "get_backend",
        lambda: model_backend_module.ModelBackend(provider="ollama"),
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        # ── Phase 1: Setup (API) — no reasoning, deterministic world building ──
        r = client.post("/api/v1/agentos/actors", json={"name": "Alice", "actor_type": "human"})
        assert r.status_code == 200, r.text
        actor_id = r.json()["actor_id"]

        r = client.post(
            "/api/v1/agentos/merchants",
            json={
                "merchant_id": "merchant_bob",
                "store_name": "Bob's Store",
                "delivery_fee": 0.0,
            },
        )
        assert r.status_code == 200, r.text
        store_id = r.json()["store_id"]

        r = client.post(
            "/api/v1/agentos/products",
            json={
                "store_id": store_id,
                "merchant_id": "merchant_bob",
                "name": "Wireless Gaming Mouse",
                "price": 59.99,
                "quantity": 25,
            },
        )
        assert r.status_code == 200, r.text
        product_id = r.json()["product_id"]

        r = client.post(
            "/api/v1/agentos/wallets",
            json={
                "owner": actor_id,
                "balance": 500.0,
                "account_type": "debit",
            },
        )
        assert r.status_code == 200, r.text

        r = client.get("/api/v1/agentos/products", params={"query": "gaming mouse"})
        assert r.status_code == 200, r.text
        assert any(p["id"] == product_id for p in r.json()["products"])

        r = client.get(f"/api/v1/agentos/products/{product_id}")
        assert r.status_code == 200, r.text
        assert r.json()["inventory"] == 25

        r = client.post(
            f"/api/v1/agentos/cart/{actor_id}/items",
            json={"product_id": product_id, "quantity": 1},
        )
        assert r.status_code == 200, r.text
        assert r.json()["lines"][0]["product_id"] == product_id

        r = client.post(
            "/api/v1/agentos/orders",
            json={
                "actor_id": actor_id,
                "items": [
                    {
                        "id": product_id,
                        "name": "Wireless Gaming Mouse",
                        "price": 59.99,
                        "qty": 1,
                        "store_id": store_id,
                        "store_name": "Bob's Store",
                    }
                ],
            },
        )
        assert r.status_code == 200, r.text
        order_id = r.json()["order_id"]
        assert r.json()["total"] == 64.79

        r = client.post(f"/api/v1/agentos/orders/{order_id}/payment", json={"actor_id": actor_id})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "completed"
        assert r.json()["wallet_balance_after"] == 435.21

        r = client.post(
            "/api/v1/agentos/fulfillment/pack",
            json={
                "items": [{"id": product_id, "name": "Wireless Gaming Mouse", "qty": 1}],
            },
        )
        assert r.status_code == 200, r.text
        packages = r.json()["packages"]

        r = client.post(
            "/api/v1/agentos/shipments",
            json={"order_id": order_id, "packages": packages},
        )
        assert r.status_code == 200, r.text
        shipment_id = r.json()["shipment_id"]

        r = client.get(f"/api/v1/agentos/orders/{order_id}/tracking")
        assert r.status_code == 200, r.text
        assert r.json()["shipment_count"] == 1
        assert r.json()["shipments"][0]["shipment_id"] == shipment_id

        r = client.post(
            "/api/v1/agentos/events",
            json={"type": "promotion", "description": "Flash sale on gaming mice"},
        )
        assert r.status_code == 200, r.text

        # ── Phase 2: Reasoning (Prompt) — the graph already exists ──
        r = client.post(
            "/api/v1/agentos/prompt",
            json={"question": "I need a wireless gaming mouse under $100 with next-day delivery."},
            headers={"X-User-ID": actor_id},
        )
        assert r.status_code == 200, r.text
        body = r.json()

        # Intent resolution / Planning correctness: a real LLM plan.
        execution = body["query_result"]["actor_execution"]
        plan = execution["plan"]
        assert plan["planner"] == "llm"
        assert plan["confidence"] > 0.0
        assert len(plan["steps"]) > 0

        # The reasoning genuinely read the SAME graph Phase 1 built — not
        # a simulated or disconnected one. product_id (created via REST)
        # appears in a real ProductSelection result, with the real price.
        # ProductSelectionCapability.handle()'s real success shape
        # (grocery.py) is a flat {"success": True, "selected": [{"id":
        # ..., ...}], ...} — not the nested candidates -> products
        # structure this previously checked, which doesn't match any
        # current branch of that capability (same stale-assertion bug
        # test_mb3060_end_to_end_cognitive_os_prompt.py's identical
        # check had, fixed the same way there).
        actions = execution["actions"]
        product_selection_results = [
            a["result"] for a in actions if isinstance(a.get("result"), dict) and a["result"].get("selected")
        ]
        assert product_selection_results, "no step returned a real product selection"
        found = {p["id"]: p for outcome in product_selection_results for p in outcome["selected"]}
        assert product_id in found
        assert found[product_id]["price"] == 59.99
