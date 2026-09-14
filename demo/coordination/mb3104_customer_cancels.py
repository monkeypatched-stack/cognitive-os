#!/usr/bin/env python3
"""MB-3104 — Customer Cancels Order.

Customer orders (real OrderCreated propagation, same proven chain as
MB-3100), then asks to cancel it. Verifies real, scoped reactions to
OrderCancelled:
  - Warehouse coordinated again (picking cancelled).
  - Inventory Robot coordinated (reservation released).
  - Driver coordinated (delivery cancelled).
  - Merchant coordinated (payment refund notification).

Usage:
    python3 demo/coordination/mb3104_customer_cancels.py
"""

from __future__ import annotations

import sys
import time
from typing import Any

from bootstrap_mb3104 import (
    ApiError,
    TRACKED_PRODUCT_NAME,
    _call,
    _client,
    bootstrap_world,
)

ORDER_PROMPT = "Buy a wireless gaming mouse."
CANCEL_PROMPT = "Cancel my order."


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
    banner("MB-3104 — Customer Cancels Order")
    print("\nBootstrapping World")
    world = bootstrap_world(client)
    check("Geography Created")
    check("Societies Created (Warehouse, Logistics, Merchant, Customer)")
    check("Actors Created (Alice, Inventory Robot, Driver, Bob)")
    check("Product Loaded")
    check(f"World Validation {'Passed' if world['verification'].get('ok') else 'FAILED'}")
    if not world["verification"].get("ok"):
        raise ApiError(f"World validation failed: {world['verification']}")
    return world


def step_prompt(client, actor_id: str, question: str, label: str) -> dict[str, Any]:
    section(label)
    print(f'"{question}"')
    response = _prompt_with_retry(client, actor_id, question)
    execution = (response.get("query_result") or {}).get("actor_execution") or {}
    plan = execution.get("plan") or {}
    steps = plan.get("steps") or []
    outcome = (execution.get("observations") or {}).get("outcome") or {}

    print(f"\nPlan ({len(steps)} steps): " + " -> ".join(s.get("action", "?") for s in steps))
    print(f"Goal Achieved: {outcome.get('goal_achieved')}")
    return execution


def print_scope_and_trace(label: str, execution: dict[str, Any]) -> list[dict[str, Any]]:
    propagation = (execution.get("execution_scope") or {}).get("propagation") or {}
    section(f"{label} — Propagation")
    kv("Societies Coordinated", propagation.get("societies_coordinated"))
    kv("Actors Coordinated", propagation.get("actors_coordinated"))
    kv("Termination Reason", propagation.get("termination_reason"))

    trace = execution.get("coordination_trace") or []
    section(f"{label} — Coordination Trace")
    if not trace:
        print("(empty — no propagation occurred)")
    for step in trace:
        events = ", ".join(step.get("events") or [])
        actors = ", ".join(step.get("actors_ticked") or []) or "(none)"
        print(f"  depth {step.get('depth')}: [{events}] -> {step.get('society_name')} -> actors ticked: {actors}")
    return trace


def step_verify(
    world: dict[str, Any],
    cancel_execution: dict[str, Any],
    cancel_trace: list[dict[str, Any]],
) -> bool:
    section("Verification")

    actor_names = {v: k for k, v in world["actors"].items()}
    reacted_actor_ids: set[str] = set()
    triggering_events: set[str] = set()
    for step in cancel_trace:
        reacted_actor_ids.update(step.get("actors_ticked") or [])
        triggering_events.update(step.get("events") or [])
    reacted_names = {actor_names.get(aid, aid) for aid in reacted_actor_ids}

    # domain_events_seen (not the trace's per-round "events", which only
    # records what TRIGGERED that round) — InventoryReleased is a real
    # event Inventory Robot's own reaction publishes as a RESULT, not a
    # trigger, so "coordinated" alone doesn't prove it actually ran. Same
    # coordinated-vs-succeeded gap MB-3105's false positive needed fixed.
    domain_events_seen = set(
        ((cancel_execution.get("execution_scope") or {}).get("propagation") or {}).get("domain_events_seen") or []
    )

    checks = [
        (
            "Picking cancelled (Warehouse coordinated)",
            "OrderCancelled" in triggering_events
            and any("Warehouse" in (s.get("society_name") or "") for s in cancel_trace),
        ),
        (
            "Inventory released (real InventoryReleased event)",
            "InventoryReleased" in domain_events_seen,
        ),
        ("Driver cancelled (Logistics coordinated)", "Driver" in reacted_names),
        ("Payment refunded (Merchant notified)", "Bob" in reacted_names),
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

            order_execution = step_prompt(client, customer_id, ORDER_PROMPT, "Customer Prompt (Order)")
            print_scope_and_trace("Order", order_execution)

            cancel_execution = step_prompt(client, customer_id, CANCEL_PROMPT, "Customer Prompt (Cancel)")
            cancel_trace = print_scope_and_trace("Cancel", cancel_execution)

            passed = step_verify(world, cancel_execution, cancel_trace)

            banner("MB-3104 RESULT: " + ("PASS" if passed else "FAIL"))
            if not passed:
                return 1
        except ApiError as exc:
            print(f"\nBenchmark failed: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
