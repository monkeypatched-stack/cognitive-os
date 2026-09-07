#!/usr/bin/env python3
"""Run a deterministic grocery demo pass: bridge autoanswer + POST /prompt.

Requires MODEL_BACKEND=dev_bridge on agentos and AGENTOS_AUTH_REQUIRED=false.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import os

CONTAINER = "monkeypatched-agentos-1"
REDIS_CONTAINER = "monkeypatched-redis-1"
BRIDGE_DIR = "/tmp/mb-llm-bridge"
BASE = "http://localhost:8031/api/v1/agentos"
PRIYA = os.environ.get("DEMO_PRIYA_ACTOR_ID", "0c3cf78910424a15817ea1477cf4edec")
# Product UUIDs are minted fresh by every scripts/seed_world.py reseed --
# same env-override pattern as scripts/grocery_bridge_autoanswer.py's
# DEMO_MILK_PRODUCT_ID, so this stays in sync with whichever product a
# fresh reseed actually created instead of silently going stale.
MILK_ID = os.environ.get("DEMO_MILK_PRODUCT_ID", "product_5cac29e2d0ef4ef0bd31a0352bf26baf")

PLAN = {
    "steps": [
        {
            "action": "ProductSelection",
            "description": "Select 1 liter of milk",
            "expected_outcome": "Milk selected",
            "cost": 3.49,
            "confidence": 0.92,
            "required_permission": "",
            "parameters": {"selection": [{"id": MILK_ID, "qty": 1}]},
            "depends_on": [],
        },
        {
            "action": "OrderCreation",
            "description": "Create order for milk",
            "expected_outcome": "Order created",
            "cost": 0.0,
            "confidence": 0.9,
            "required_permission": "",
            "parameters": {},
            "depends_on": [0],
        },
        {
            "action": "PaymentConfirmation",
            "description": "Confirm payment authorization for milk order",
            "expected_outcome": "Payment authorized",
            "cost": 0.0,
            "confidence": 0.9,
            "required_permission": "",
            "parameters": {},
            "depends_on": [1],
        },
        {
            "action": "Payment",
            "description": "Pay for milk order",
            "expected_outcome": "Payment captured",
            "cost": 3.49,
            "confidence": 0.88,
            "required_permission": "",
            "parameters": {},
            "depends_on": [2],
        },
        {
            "action": "OrderConfirmation",
            "description": "Confirm milk order",
            "expected_outcome": "Order confirmed",
            "cost": 0.0,
            "confidence": 0.9,
            "required_permission": "",
            "parameters": {},
            "depends_on": [3],
        },
    ],
    "summary": "Buy 1 liter of milk",
    "confidence": 0.88,
}


def _docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "exec", CONTAINER, *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _redis(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "exec", REDIS_CONTAINER, "redis-cli", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _api(method: str, path: str, body: dict | None = None, timeout: int = 600) -> dict:
    req = urllib.request.Request(
        f"{BASE}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("X-User-ID", PRIYA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def reset_priya_learning_state() -> None:
    """Clear stale transition models and plan hysteresis that block execution."""
    _redis("DEL", f"monkeybrain:transition_model:{PRIYA}", f"monkeybrain:transition_model:{PRIYA}:meta")
    scan = subprocess.run(
        ["docker", "exec", REDIS_CONTAINER, "redis-cli", "--scan", "--pattern", f"monkeybrain:current_plan:{PRIYA}:*"],
        capture_output=True,
        text=True,
        check=False,
    )
    for key in scan.stdout.strip().splitlines():
        if key:
            _redis("DEL", key)
    # In-memory transition model is loaded at boot — restart to pick up clean Redis.
    subprocess.run(["docker", "restart", CONTAINER], check=True, capture_output=True)
    for _ in range(60):
        try:
            urllib.request.urlopen(f"{BASE.replace('/api/v1/agentos', '')}/health", timeout=3)
            break
        except Exception:
            time.sleep(5)
    print("cleared Priya learning state and restarted agentos")


def ensure_debit_wallet(balance: float = 500.0) -> None:
    """Add a debit wallet so Payment uses synchronous wallet debit, not UPI pause."""
    try:
        _api("POST", "/wallets", {
            "owner": PRIYA,
            "balance": balance,
            "account_type": "debit",
            "name": "Priya Sharma Debit Wallet",
        })
        print(f"created debit wallet with balance ${balance:.2f}")
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 409, 422):
            print("debit wallet already exists (ok)")
        else:
            raise


def _bridge_watch(stop: threading.Event, answered: list[str]) -> None:
    seen: set[str] = set()
    plan_text = json.dumps(PLAN, indent=2)
    while not stop.is_set():
        ls = _docker("sh", "-c", f"ls {BRIDGE_DIR}/*.request.json 2>/dev/null || true")
        for path in ls.stdout.strip().split():
            if not path or path in seen:
                continue
            rid = Path(path).name.replace(".request.json", "")
            resp = f"{BRIDGE_DIR}/{rid}.response.txt"
            if _docker("test", "-f", resp).returncode == 0:
                seen.add(path)
                continue
            subprocess.run(
                ["docker", "exec", "-i", CONTAINER, "tee", resp],
                input=plan_text,
                text=True,
                capture_output=True,
                check=True,
            )
            seen.add(path)
            answered.append(rid)
            print(f"bridge answered {rid}")
        time.sleep(0.25)


def main() -> int:
    question = "buy 1 liter of milk"
    reset_priya_learning_state()
    ensure_debit_wallet()
    _docker("sh", "-c", f"mkdir -p {BRIDGE_DIR} && rm -f {BRIDGE_DIR}/*")

    stop = threading.Event()
    answered: list[str] = []
    watcher = threading.Thread(target=_bridge_watch, args=(stop, answered), daemon=True)
    watcher.start()
    time.sleep(0.5)

    req = urllib.request.Request(
        f"{BASE}/prompt",
        method="POST",
        data=json.dumps({"question": question, "run_simulate": False}).encode(),
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("X-User-ID", PRIYA)
    req.add_header("Idempotency-Key", str(uuid.uuid4()))

    print(f"POST /prompt as Priya Sharma — {question!r}")
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as resp:
        result = json.loads(resp.read())
    stop.set()
    watcher.join(timeout=2)
    elapsed = time.time() - t0

    qr = result.get("query_result", {})
    ae = qr.get("actor_execution", {})
    outcome = ae.get("observations", {}).get("outcome", {})
    plan = ae.get("plan", {})
    actions = ae.get("actions", [])
    goal = outcome.get("goal_achieved")

    if goal is None:
        hist = _api("GET", f"/actors/{PRIYA}/execution-history", timeout=30)
        ex = hist.get("executions", [{}])[0]
        goal = ex.get("outcome") == "success"

    print(f"Completed in {elapsed:.1f}s | bridge_answers={len(answered)} | goal_achieved={goal}")
    print("plan:", [s.get("action") if isinstance(s, dict) else s for s in plan.get("steps", [])])
    for i, a in enumerate(actions):
        if isinstance(a, dict):
            print(f"  {i+1}. {a.get('action')} success={a.get('success')}")

    hist = _api("GET", f"/actors/{PRIYA}/execution-history", timeout=30)
    ex = hist.get("executions", [{}])[0]
    print(f"execution: outcome={ex.get('outcome')} plan={ex.get('plan_summary')}")
    for wc in (ex.get("metadata") or {}).get("world_changes", [])[:8]:
        print(f"  - {wc}")

    return 0 if goal else 1


if __name__ == "__main__":
    raise SystemExit(main())
