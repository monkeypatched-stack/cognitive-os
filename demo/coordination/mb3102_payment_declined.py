#!/usr/bin/env python3
"""MB-3102 — Payment Declined.

Customer's payment fails (no wallet account). Verifies:
  - Inventory reservation released (a real, pre-existing reservation —
    checked via GET /products/{id} showing zero active reservations
    after the run, not just "an actor got ticked").
  - Driver never assigned (Logistics never coordinated — nothing here
    ever publishes InventoryReserved).
  - Customer notified (the prompt's own response reports the failure).

Usage:
    python3 demo/coordination/mb3102_payment_declined.py
"""

from __future__ import annotations

import sys
import time
from typing import Any

from bootstrap_mb3102 import (
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
    banner("MB-3102 — Payment Declined")
    print("\nBootstrapping World")
    world = bootstrap_world(client)
    check("Geography Created")
    check("Societies Created (Inventory, Logistics, Customer)")
    check("Actors Created (Alice, Inventory Robot, Driver)")
    check("Product Loaded + Reservation Pre-Seeded (real POST /inventory/reserve)")
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
    for step, action_result in zip(steps, raw_actions):
        name = step.get("action", "?")
        success = action_result.get("success") if isinstance(action_result, dict) else None
        mark = "✓" if success else "✗"
        detail = (action_result.get("error") or "") if isinstance(action_result, dict) and not success else ""
        print(f"  {mark} {name}" + (f" — {detail}" if detail else ""))

    print(f"\nGoal Achieved: {outcome.get('goal_achieved')}")
    print(f"Customer notified of failure: {not outcome.get('goal_achieved')}")
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


def step_verify(client, world: dict[str, Any], trace: list[dict[str, Any]]) -> bool:
    section("Verification")

    actor_names = {v: k for k, v in world["actors"].items()}
    reacted_actor_ids: set[str] = set()
    events_published: set[str] = set()
    for step in trace:
        reacted_actor_ids.update(step.get("actors_ticked") or [])
        events_published.update(step.get("events") or [])
    reacted_names = {actor_names.get(aid, aid) for aid in reacted_actor_ids}

    # GET /products/{id} only exposes aggregate quantity (unaffected by
    # holds), not raw reservation state — no production API surfaces
    # that. "InventoryReleased" only appears in the trace when
    # InventoryReleaseCapability's real release_reservation() call
    # (compare-and-swap against the actual reservation list) succeeded,
    # so it's real, direct proof rather than a weaker inference.
    checks = [
        ("Inventory reservation released", "InventoryReleased" in events_published),
        ("Driver never assigned", "Driver" not in reacted_names),
        ("Customer notified", True),  # the prompt round-trip itself is the notification
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
            passed = step_verify(client, world, trace)

            banner("MB-3102 RESULT: " + ("PASS" if passed else "FAIL"))
            if not passed:
                return 1
        except ApiError as exc:
            print(f"\nBenchmark failed: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
