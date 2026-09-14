"""MB-3405 — Broadcast Scoping: "Everyone in Warehouse A stop
operations immediately" must reach every Warehouse A participant and
NO ONE outside Warehouse A's own Society — including a Distribution
Worker who, deliberately, holds the exact same "warehouse_a_ops"
Affiliation string but lives in a different Society entirely. This is
the honest architectural boundary of BroadcastToAffiliationCapability
today: it is Society-scoped (SocietyRuntime.broadcast_message only
consults its own active_actors()), not a global affiliation-wide fan-
out — documented here as a real, verified fact, not an assumption.
"""

from __future__ import annotations

import sys

from _common import ApiError, banner, client, first_result, force_round, kv, section
from bootstrap_mb3405 import bootstrap_world


def main() -> int:
    banner("MB-3405 — Broadcast Scoping")
    c = client()
    try:
        world = bootstrap_world(c)
        actors = world["actors"]
        manager_id = actors["Warehouse Manager"]
        worker1_id, worker2_id = actors["Floor Worker One"], actors["Floor Worker Two"]
        distribution_id = actors["Distribution Worker"]

        section("Warehouse Manager broadcasts a stop-operations order")
        steps, actions = force_round(
            c,
            manager_id,
            "Warehouse Manager",
            "BroadcastToAffiliation",
            'Say: "Everyone in Warehouse A stop operations immediately."',
        )
        result = first_result("BroadcastToAffiliation", steps, actions)
        if result is None:
            print("FAIL: BroadcastToAffiliation step did not run", file=sys.stderr)
            return 1
        if not result.get("success"):
            print(f"FAIL: broadcast was not successful: {result}", file=sys.stderr)
            return 1

        recipients = set(result.get("recipients", []))
        kv("Delivered count", result.get("delivered_count"))
        kv("Recipients", sorted(recipients))

        checks = [
            ("Floor Worker One received it", worker1_id in recipients),
            ("Floor Worker Two received it", worker2_id in recipients),
            (
                "Distribution Worker did NOT receive it (different Society)",
                distribution_id not in recipients,
            ),
        ]
        ok = True
        section("Verification")
        for label, passed in checks:
            kv(label, "PASS" if passed else "FAIL")
            ok = ok and passed

        banner("RESULT: " + ("PASS" if ok else "FAIL"))
        return 0 if ok else 1
    except ApiError as exc:
        print(f"Demo failed: {exc}", file=sys.stderr)
        return 1
    finally:
        c.close()


if __name__ == "__main__":
    sys.exit(main())
