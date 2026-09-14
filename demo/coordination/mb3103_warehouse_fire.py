#!/usr/bin/env python3
"""MB-3103 — Warehouse Fire Rerouting.

Customer orders normally (nothing reacts yet — this world's societies
don't subscribe to OrderCreated). A fire at Warehouse A evacuates its
worker and publishes a real WarehouseClosed event via POST /events —
which now drives the SAME propagation engine directly (not just the
/prompt path). Verifies:
  - Warehouse A's worker is never coordinated (nothing subscribes to
    OrderCreated, and Warehouse A itself subscribes to nothing).
  - Warehouse B (the alternate) IS coordinated, reacting to
    WarehouseClosed by reserving the pending order's inventory.
  - Driver IS coordinated (InventoryReserved -> Logistics -> Driver),
    proving the reroute reached all the way through to dispatch.

Usage:
    python3 demo/coordination/mb3103_warehouse_fire.py
"""

from __future__ import annotations

import sys
import time
from typing import Any

from bootstrap_mb3103 import (
    ApiError,
    TRACKED_PRODUCT_NAME,
    _call,
    _client,
    bootstrap_world,
)

PROMPT = "Buy a wireless gaming mouse."


def banner(title: str) -> None:
    print("\n" + "=" * 56)
    print(title)
    print("=" * 56)


def section(title: str) -> None:
    print("\n" + "-" * 56)
    print(title)
    print("-" * 56)


def check(label: str) -> None:
    print(f"✓ {label}")


def fail(label: str, detail: str = "") -> None:
    print(f"✗ {label}" + (f" — {detail}" if detail else ""))


def kv(label: str, value: Any, width: int = 28) -> None:
    dots = "." * max(1, width - len(label))
    print(f"{label} {dots} {value}")


def _prompt_with_retry(
    client, actor_id: str, question: str, attempts: int = 3, delay_seconds: float = 5.0
) -> dict[str, Any]:
    last_response: dict[str, Any] = {}
    for attempt in range(attempts):
        response = _call(
            client,
            "POST",
            "/prompt",
            json={"question": question},
            headers={"X-User-ID": actor_id},
        )
        if (response.get("query_result") or {}).get("llm_answered", True):
            return response
        last_response = response
        if attempt < attempts - 1:
            time.sleep(delay_seconds)
    answer = (last_response.get("query_result") or {}).get("answer", "unknown error")
    raise ApiError(f"POST /prompt did not produce an answer after {attempts} attempts: {answer}")


def step_bootstrap(client) -> dict[str, Any]:
    banner("MB-3103 — Warehouse Fire Rerouting")
    print("\nBootstrapping World")
    world = bootstrap_world(client)
    check("Geography Created")
    check("Societies Created (Warehouse A, Warehouse B, Logistics, Customer)")
    check("Actors Created (Alice, Warehouse Worker A, Inventory Robot B, Driver)")
    check("Product Loaded")
    check(f"World Validation {'Passed' if world['verification'].get('ok') else 'FAILED'}")
    if not world["verification"].get("ok"):
        raise ApiError(f"World validation failed: {world['verification']}")
    return world


def step_submit_order(client, actor_id: str) -> dict[str, Any]:
    section("Customer Prompt")
    print(f'"{PROMPT}"')
    response = _prompt_with_retry(client, actor_id, PROMPT)
    execution = (response.get("query_result") or {}).get("actor_execution") or {}
    plan = execution.get("plan") or {}
    steps = plan.get("steps") or []
    outcome = (execution.get("observations") or {}).get("outcome") or {}

    print(f"\nPlan ({len(steps)} steps): " + " -> ".join(s.get("action", "?") for s in steps))
    print(f"Goal Achieved: {outcome.get('goal_achieved')}")
    return execution


def step_inject_fire(client, space_id: str) -> dict[str, Any]:
    section("Inject Event")
    print("Warehouse A Fire")
    result = _call(
        client,
        "POST",
        "/events",
        json={
            "type": "fire",
            "space_id": space_id,
            "description": "Warehouse A Fire",
        },
    )
    kv("Actors Evacuated", len(result.get("evacuated") or []))
    return result


def print_scope_and_trace(label: str, scope: dict[str, Any], trace: list[dict[str, Any]]) -> None:
    section(f"{label} — Propagation")
    kv("Societies Coordinated", scope.get("societies_coordinated"))
    kv("Actors Coordinated", scope.get("actors_coordinated"))
    kv("Termination Reason", scope.get("termination_reason"))

    section(f"{label} — Coordination Trace")
    if not trace:
        print("(empty — no propagation occurred)")
    for step in trace:
        events = ", ".join(step.get("events") or [])
        actors = ", ".join(step.get("actors_ticked") or []) or "(none)"
        print(f"  depth {step.get('depth')}: [{events}] -> {step.get('society_name')} -> actors ticked: {actors}")


def step_verify(world: dict[str, Any], order_execution: dict[str, Any], fire_result: dict[str, Any]) -> bool:
    section("Verification")

    actor_names = {v: k for k, v in world["actors"].items()}
    order_trace = order_execution.get("coordination_trace") or []
    fire_trace = fire_result.get("coordination_trace") or []
    all_trace = list(order_trace) + list(fire_trace)

    reacted_actor_ids: set[str] = set()
    for step in all_trace:
        reacted_actor_ids.update(step.get("actors_ticked") or [])
    reacted_names = {actor_names.get(aid, aid) for aid in reacted_actor_ids}

    # domain_events_seen (not the trace's per-round "events", which only
    # records what TRIGGERED that round) — InventoryReserved is a real
    # event Warehouse B's own reaction publishes as a RESULT, not a
    # trigger, so it would never appear in any trace[i]["events"] entry
    # even on a real success. Same fix MB-3105's false positive needed.
    order_scope = (order_execution.get("execution_scope") or {}).get("propagation") or {}
    events_published = set(order_scope.get("domain_events_seen") or [])
    events_published |= set((fire_result.get("execution_scope") or {}).get("domain_events_seen") or [])

    checks = [
        (
            "Warehouse A worker never coordinated",
            "Warehouse Worker A" not in reacted_names,
        ),
        ("Warehouse B (alternate) coordinated", "Inventory Robot B" in reacted_names),
        (
            "Inventory reserved at alternate warehouse",
            "InventoryReserved" in events_published,
        ),
        ("Driver rerouted/assigned", "Driver" in reacted_names),
    ]
    all_pass = True
    for label, ok in checks:
        if ok:
            check(f"{label} — PASS")
        else:
            fail(label, "FAIL")
            all_pass = False
    return all_pass


def main() -> int:
    with _client() as client:
        try:
            world = step_bootstrap(client)
            customer_id = world["actors"]["Alice"]

            order_execution = step_submit_order(client, customer_id)
            print_scope_and_trace(
                "Order",
                (order_execution.get("execution_scope") or {}).get("propagation") or {},
                order_execution.get("coordination_trace") or [],
            )

            fire_result = step_inject_fire(client, world["spaces"]["warehouse_a"])
            print_scope_and_trace(
                "Fire",
                fire_result.get("execution_scope") or {},
                fire_result.get("coordination_trace") or [],
            )

            passed = step_verify(world, order_execution, fire_result)

            banner("MB-3103 RESULT: " + ("PASS" if passed else "FAIL"))
            if not passed:
                return 1
        except ApiError as exc:
            print(f"\nBenchmark failed: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
