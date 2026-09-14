"""MB-3400 — Warehouse Team: an UNDIRECTED request ("can someone help
pack Order 123?") must reach every real, currently-eligible participant
of the sender's shared "warehouse_team" Affiliation, and NO ONE else —
not even an unaffiliated colleague sharing the same Society. Verifies
BroadcastToAffiliationCapability end to end against the real
SocietyRuntime.broadcast_message()/AffiliationCommunicationRouter, not
a scripted recipient list.
"""

from __future__ import annotations

import sys

from _common import ApiError, banner, client, first_result, force_round, kv, section
from bootstrap_mb3400 import bootstrap_world


def main() -> int:
    banner("MB-3400 — Warehouse Team (Affiliation-Scoped Broadcast)")
    c = client()
    try:
        world = bootstrap_world(c)
        actors = world["actors"]
        worker_id = actors["Warehouse Worker"]
        packer1_id, packer2_id, cashier_id = (
            actors["Packer One"],
            actors["Packer Two"],
            actors["Cashier"],
        )

        section("Warehouse Worker broadcasts to the warehouse_team affiliation")
        steps, actions = force_round(
            c,
            worker_id,
            "Warehouse Worker",
            "BroadcastToAffiliation",
            'Say: "Can someone help pack Order 123?"',
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
            ("Packer One received it", packer1_id in recipients),
            ("Packer Two received it", packer2_id in recipients),
            (
                "Cashier did NOT receive it (different affiliation)",
                cashier_id not in recipients,
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
