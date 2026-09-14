#!/usr/bin/env python3
"""Auto-answer LLM dev_bridge requests for grocery buy-milk plans.

Watches LLM_BRIDGE_DIR (default /tmp/mb-llm-bridge) for *.request.json files
and writes a valid multi-step grocery plan to the matching .response.txt file.
Used for deterministic demo passes when MODEL_BACKEND=dev_bridge.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

BRIDGE_DIR = Path(os.environ.get("LLM_BRIDGE_DIR", "/tmp/mb-llm-bridge"))
# Cheapest milk in the seeded world (Trader Joe's 2% Milk)
MILK_PRODUCT_ID = os.environ.get("DEMO_MILK_PRODUCT_ID", "product_5cac29e2d0ef4ef0bd31a0352bf26baf")

PLAN_TEMPLATE = {
    "steps": [
        {
            "action": "ProductSelection",
            "description": "Select 1 liter of milk",
            "expected_outcome": "Milk product selected from catalog",
            "cost": 3.49,
            "confidence": 0.92,
            "required_permission": "",
            "parameters": {"selection": [{"id": MILK_PRODUCT_ID, "qty": 1}]},
            "depends_on": [],
        },
        {
            "action": "OrderCreation",
            "description": "Create grocery order for selected milk",
            "expected_outcome": "Order created with line items",
            "cost": 0.0,
            "confidence": 0.9,
            "required_permission": "",
            "parameters": {},
            "depends_on": [0],
        },
        {
            "action": "Payment",
            "description": "Pay for the milk order from wallet",
            "expected_outcome": "Payment captured",
            "cost": 3.49,
            "confidence": 0.88,
            "required_permission": "",
            "parameters": {},
            "depends_on": [1],
        },
        {
            "action": "OrderConfirmation",
            "description": "Confirm the milk order",
            "expected_outcome": "Order confirmed for fulfillment",
            "cost": 0.0,
            "confidence": 0.9,
            "required_permission": "",
            "parameters": {},
            "depends_on": [2],
        },
        {
            "action": "Delivery",
            "description": "Deliver milk to home address",
            "expected_outcome": "Milk delivered",
            "cost": 1.99,
            "confidence": 0.85,
            "required_permission": "",
            "parameters": {},
            "depends_on": [3],
        },
    ],
    "summary": "Buy 1 liter of milk — select, order, pay, confirm, deliver",
    "confidence": 0.88,
}


def _should_answer(prompt: str) -> bool:
    # Match only the CURRENT tick's actual goal line ("Goal: ...", the
    # first line of every llm_planner.py prompt) — not any substring
    # anywhere in the prompt. Priya has multiple standing goals (e.g.
    # "buy groceries efficiently" AND "stay within household budget")
    # that /execute cycles through independently of what was last asked
    # via /prompt; a bare substring match on "buy"+"milk" also matched
    # PAST memory lines ("Relevant experiences: ... Buy 1L of milk ...")
    # on a tick whose real goal was unrelated, feeding this script's
    # hardcoded milk-buying plan into a grounding context that never
    # offered a milk product at all — confirmed live: "planner selected
    # ..., which was never offered in this execution's grounding".
    goal_line = ""
    for line in prompt.splitlines():
        if line.startswith("Goal:"):
            goal_line = line.lower()
            break
    return "grocer" in goal_line or "milk" in goal_line


def main() -> None:
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Watching {BRIDGE_DIR} for grocery planner requests...")
    seen: set[str] = set()
    while True:
        for req in BRIDGE_DIR.glob("*.request.json"):
            rid = req.stem.replace(".request", "")
            if rid in seen:
                continue
            try:
                payload = json.loads(req.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            prompt = payload.get("prompt", "")
            if not _should_answer(prompt):
                continue
            resp = BRIDGE_DIR / f"{rid}.response.txt"
            if resp.exists():
                seen.add(rid)
                continue
            resp.write_text(json.dumps(PLAN_TEMPLATE, indent=2))
            seen.add(rid)
            print(f"answered {rid} ({len(prompt)} char prompt)")
        time.sleep(0.3)


if __name__ == "__main__":
    main()
