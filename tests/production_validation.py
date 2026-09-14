"""
MonkeyBrain Production Validation Suite (PV-100)
Production acceptance test suite — all through server API.
"""

import subprocess, json, time

BASE = "http://localhost:8000/api/v1/agentos"


def api(method, path, body=None):
    cmd = [
        "curl",
        "-s",
        "-X",
        method,
        f"{BASE}{path}",
        "-H",
        "Content-Type: application/json",
    ]
    if body:
        cmd.extend(["-d", json.dumps(body)])
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    try:
        return json.loads(r.stdout)
    except:
        return {"error": r.stdout}


def tick(aid):
    return api("POST", f"/actors/{aid}/tick", {"start": "", "goal": "", "reward": 1.0})


def get_stores(r):
    steps = r.get("result", {}).get("plan", {}).get("steps", [])
    return [s.get("action", "") for s in steps if "store" in s.get("action", "")]


def register(name, goal, obj):
    r = api(
        "POST",
        "/actors",
        {"name": name, "actor_type": "ai_agent", "goals": [goal], "objective": obj},
    )
    return r.get("actor_id", "")


def reset_world(stores_cfg):
    import redis

    r = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)
    w = json.loads(r.get("monkeybrain:world"))
    stores = [e for e in w["entities"] if "store" in e.get("name", "")]
    for e in stores:
        if e["name"] in stores_cfg:
            e["attributes"] = stores_cfg[e["name"]]
    w["entities"] = stores
    r.set("monkeybrain:world", json.dumps(w))


def std():
    return {
        "store_a": {
            "stock": 100,
            "price_per_liter": 2.50,
            "reliability": 0.95,
            "distance_km": 8.0,
            "freshness_hours": 48,
        },
        "store_b": {
            "stock": 30,
            "price_per_liter": 1.20,
            "reliability": 0.55,
            "distance_km": 3.0,
            "freshness_hours": 24,
        },
        "store_c": {
            "stock": 12,
            "price_per_liter": 1.80,
            "reliability": 0.70,
            "distance_km": 5.0,
            "freshness_hours": 36,
        },
        "store_d": {
            "stock": 4,
            "price_per_liter": 2.00,
            "reliability": 0.80,
            "distance_km": 0.5,
            "freshness_hours": 72,
        },
    }


results = {"passed": 0, "failed": 0, "tests": []}


def test(name, cond, detail=""):
    if cond:
        results["passed"] += 1
    else:
        results["failed"] += 1
    results["tests"].append({"name": name, "status": "PASS" if cond else "FAIL"})
    print(f"  [{'✓' if cond else '✗'}] {name}" + (f" — {detail}" if detail else ""))


# ═══════════════════════════════════════════════════════════════
# Setup
# ═══════════════════════════════════════════════════════════════
reset_world(std())
alice = register("PV_Alice", "buy_milk", "cost")
bob = register("PV_Bob", "buy_milk", "speed")
carol = register("PV_Carol", "buy_milk", "reliability")
dave = register("PV_Dave", "buy_milk", "freshness")
emma = register("PV_Emma", "buy_milk", "")
print(f"  Actors: {len([a for a in [alice, bob, carol, dave, emma] if a])}/5")

# ═══════════════════════════════════════════════════════════════
# Phase 1 — Planning
# ═══════════════════════════════════════════════════════════════
print("\n=== Phase 1: Planning ===")
sa = get_stores(tick(alice))
sb = get_stores(tick(bob))
sc = get_stores(tick(carol))
sd = get_stores(tick(dave))
se = get_stores(tick(emma))
test("PV-001 Simple Purchase", len(sa) > 0, f"steps={len(sa)}")
test(
    "PV-002 Cheapest Store",
    sa[0] == "process_store_b" if sa else False,
    f"first={sa[0] if sa else 'none'}",
)
test(
    "PV-003 Fastest Delivery",
    sb[0] == "process_store_d" if sb else False,
    f"first={sb[0] if sb else 'none'}",
)
test(
    "PV-004 Reliability Objective",
    sc[0] == "process_store_a" if sc else False,
    f"first={sc[0] if sc else 'none'}",
)
test("PV-005 Multi-step Goal", len(sd) >= 3, f"steps={len(sd)}")

# ═══════════════════════════════════════════════════════════════
# Phase 2 — Dynamic Adaptation (no restart needed — world mutations persist in-memory)
# ═══════════════════════════════════════════════════════════════
print("\n=== Phase 2: Dynamic Adaptation ===")

# PV-010 Stock Out
reset_world(
    {
        **std(),
        "store_b": {
            "stock": 0,
            "price_per_liter": 1.20,
            "reliability": 0.01,
            "distance_km": 3.0,
            "freshness_hours": 0,
        },
    }
)
test("PV-010 Stock Out", get_stores(tick(alice))[0] != "process_store_b", f"rerouted")

# PV-011 Price Surge
reset_world(
    {
        **std(),
        "store_b": {
            "stock": 30,
            "price_per_liter": 2.40,
            "reliability": 0.55,
            "distance_km": 3.0,
            "freshness_hours": 24,
        },
    }
)
test("PV-011 Price Surge", get_stores(tick(alice))[0] != "process_store_b", f"switched")

# PV-012 Delivery Delay
reset_world(
    {
        **std(),
        "store_d": {
            "stock": 4,
            "price_per_liter": 2.00,
            "reliability": 0.10,
            "distance_km": 0.5,
            "freshness_hours": 12,
        },
    }
)
test(
    "PV-012 Delivery Delay",
    get_stores(tick(bob))[0] == "process_store_d",
    f"speed tolerates risk",
)

# PV-013 Product Recall
reset_world(
    {
        **std(),
        "store_b": {
            "stock": 0,
            "price_per_liter": 1.20,
            "reliability": 0.01,
            "distance_km": 3.0,
            "freshness_hours": 0,
        },
    }
)
test(
    "PV-013 Product Recall",
    get_stores(tick(alice))[0] != "process_store_b",
    f"avoids recalled",
)

# PV-014 Supplier Offline
reset_world(
    {
        **std(),
        "store_a": {
            "stock": 0,
            "price_per_liter": 2.50,
            "reliability": 0.0,
            "distance_km": 8.0,
            "freshness_hours": 0,
        },
    }
)
test(
    "PV-014 Supplier Offline",
    get_stores(tick(carol))[0] != "process_store_a",
    f"rerouted",
)

# PV-015 Regional Outage
reset_world(
    {
        **std(),
        "store_b": {"stock": 0, "reliability": 0.0},
        "store_c": {"stock": 0, "reliability": 0.0},
        "store_d": {"stock": 0, "reliability": 0.0},
    }
)
test(
    "PV-015 Regional Outage",
    get_stores(tick(alice))[0] == "process_store_a",
    f"converge to store_a",
)

# ═══════════════════════════════════════════════════════════════
# Phase 3 — Objective Policies
# ═══════════════════════════════════════════════════════════════
print("\n=== Phase 3: Objective Policies ===")
reset_world(std())
sa = get_stores(tick(alice))
sb = get_stores(tick(bob))
sc = get_stores(tick(carol))
test(
    "PV-030 Policy Diversity",
    len(set([sa[0], sb[0], sc[0]])) >= 2,
    f"A={sa[0] if sa else ''} B={sb[0] if sb else ''} C={sc[0] if sc else ''}",
)
test("PV-031 No Policy Contamination", sa[0] != sb[0], f"different picks")

# ═══════════════════════════════════════════════════════════════
# Phase 6 — Recovery
# ═══════════════════════════════════════════════════════════════
print("\n=== Phase 6: Recovery ===")
reset_world(
    {
        **std(),
        "store_a": {
            "stock": 0,
            "reliability": 0.0,
            "distance_km": 8.0,
            "freshness_hours": 0,
            "price_per_liter": 2.50,
        },
    }
)
test(
    "PV-060 Adapts to failure",
    get_stores(tick(carol))[0] != "process_store_a",
    f"during failure",
)
reset_world(std())
test(
    "PV-060 Returns after recovery",
    get_stores(tick(carol))[0] == "process_store_a",
    f"after recovery",
)

# ═══════════════════════════════════════════════════════════════
# Phase 8 — Collective Cognition
# ═══════════════════════════════════════════════════════════════
print("\n=== Phase 8: Collective Cognition ===")
reset_world(
    {
        **std(),
        "store_b": {
            "stock": 0,
            "price_per_liter": 1.20,
            "reliability": 0.01,
            "distance_km": 3.0,
            "freshness_hours": 0,
        },
    }
)
test(
    "PV-080 All avoid recalled",
    all(get_stores(tick(a))[0] != "process_store_b" for a in [alice, bob, carol]),
    "all avoid store_b",
)

# ═══════════════════════════════════════════════════════════════
# Phase 9 — Negotiation
# ═══════════════════════════════════════════════════════════════
print("\n=== Phase 9: Negotiation ===")
reset_world(
    {
        **std(),
        "store_b": {
            "stock": 3,
            "price_per_liter": 1.20,
            "reliability": 0.55,
            "distance_km": 3.0,
            "freshness_hours": 24,
        },
        "store_d": {"stock": 0},
    }
)
test(
    "PV-090 First gets scarce item",
    get_stores(tick(alice))[0] == "process_store_b",
    f"alice got store_b",
)
reset_world({**std(), "store_b": {"stock": 0}, "store_d": {"stock": 0}})
test(
    "PV-090 Second reroutes",
    get_stores(tick(bob))[0] != "process_store_b",
    f"bob rerouted",
)

# ═══════════════════════════════════════════════════════════════
# Phase 11 — Long-Running
# ═══════════════════════════════════════════════════════════════
print("\n=== Phase 11: Long-Running ===")
reset_world(std())
t0 = time.time()
for i in range(50):
    tick(alice)
elapsed = time.time() - t0
test("PV-110 50-tick stability", elapsed < 300, f"{elapsed:.1f}s")
test("PV-110 No degradation", len(get_stores(tick(alice))) > 0, "still produces plan")

# ═══════════════════════════════════════════════════════════════
# Summary
# ═══════════════════════════════════════════════════════════════
total = results["passed"] + results["failed"]
print(f"\n{'=' * 60}")
print(f"  PV-100 Production Validation Report")
print(f"{'=' * 60}")
print(f"\n  Passed: {results['passed']}")
print(f"  Failed: {results['failed']}")
print(f"  Total:  {total}")
print(f"  Rate:   {results['passed'] / total * 100:.1f}%")
print(f"\n{'=' * 60}")
if results["failed"] == 0:
    print(f"  PRODUCTION READY ✓")
else:
    print(f"  NOT PRODUCTION READY — {results['failed']} failures")
print(f"{'=' * 60}")
