#!/usr/bin/env python3
"""MB-3101 — Inventory Unavailable.

Customer orders a genuinely out-of-stock product (real quantity=0, not
simulated). Verifies:
  - Warehouse Worker does NOT get coordinated (no OrderCreated fires —
    OrderCreationCapability's real MB-3031 backorder path reports
    InventoryUnavailable instead, since every item backordered).
  - Customer's own execution shows a real backorder in the result.
  - Merchant Society (subscribed to InventoryUnavailable) IS coordinated.

Usage:
    python3 demo/coordination/mb3101_inventory_unavailable.py
"""

from __future__ import annotations

import sys
import time
from typing import Any

from bootstrap_mb3101 import (
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
    banner("MB-3101 — Inventory Unavailable")
    print("\nBootstrapping World")
    world = bootstrap_world(client)
    check("Geography Created")
    check("Societies Created (Warehouse, Merchant, Customer)")
    check("Actors Created (Alice, Warehouse Worker, Bob)")
    check(f"Product Loaded (quantity=0: genuinely out of stock)")
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
    raw_actions = execution.get("actions") or []

    print(f"\nPlan ({len(steps)} steps):")
    for i, step in enumerate(steps, 1):
        print(f"  {i}. {step.get('action')} — {step.get('description', '')}")

    print("\nExecution:")
    backordered_seen = False
    for step, action_result in zip(steps, raw_actions):
        name = step.get("action", "?")
        success = action_result.get("success") if isinstance(action_result, dict) else None
        mark = "✓" if success else "✗"
        result = action_result.get("result") if isinstance(action_result, dict) else None
        detail = ""
        if isinstance(result, dict) and result.get("backordered"):
            backordered_seen = True
            detail = f"backordered={result['backordered']}"
        elif not success and isinstance(action_result, dict):
            detail = action_result.get("error") or ""
        print(f"  {mark} {name}" + (f" — {detail}" if detail else ""))

    print(f"\nGoal Achieved: {outcome.get('goal_achieved')}")
    print(f"Customer received backorder: {backordered_seen}")
    return execution


def print_scope_and_trace(execution: dict[str, Any]) -> list[dict[str, Any]]:
    scope = execution.get("execution_scope") or {}
    propagation = scope.get("propagation") or {}
    section("Propagation")
    kv("Societies Coordinated", propagation.get("societies_coordinated"))
    kv("Actors Coordinated", propagation.get("actors_coordinated"))
    kv("Termination Reason", propagation.get("termination_reason"))

    trace = execution.get("coordination_trace") or []
    section("Coordination Trace")
    if not trace:
        print("(empty — no propagation occurred)")
    for step in trace:
        events = ", ".join(step.get("events") or [])
        actors = ", ".join(step.get("actors_ticked") or []) or "(none)"
        print(f"  depth {step.get('depth')}: [{events}] -> {step.get('society_name')} -> actors ticked: {actors}")
    return trace


def step_verify(world: dict[str, Any], execution: dict[str, Any], trace: list[dict[str, Any]]) -> bool:
    section("Verification")

    actor_names = {v: k for k, v in world["actors"].items()}
    reacted_actor_ids: set[str] = set()
    events_published: set[str] = set()
    for step in trace:
        reacted_actor_ids.update(step.get("actors_ticked") or [])
        events_published.update(step.get("events") or [])
    reacted_names = {actor_names.get(aid, aid) for aid in reacted_actor_ids}

    raw_actions = execution.get("actions") or []
    plan_steps = (execution.get("plan") or {}).get("steps") or []
    backordered = any(
        isinstance(a.get("result"), dict) and a["result"].get("backordered") for a in raw_actions if isinstance(a, dict)
    )

    checks = [
        ("Warehouse Worker does not pick", "Warehouse Worker" not in reacted_names),
        ("Customer receives backorder", backordered),
        (
            "Merchant notified",
            "Bob" in reacted_names or "InventoryUnavailable" in events_published,
        ),
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
            execution = step_submit_order(client, customer_id)
            trace = print_scope_and_trace(execution)
            passed = step_verify(world, execution, trace)

            banner("MB-3101 RESULT: " + ("PASS" if passed else "FAIL"))
            if not passed:
                return 1
        except ApiError as exc:
            print(f"\nBenchmark failed: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
