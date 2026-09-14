"""MB-3402 — Cross-Affiliation Chain: exercises
PlanetaryRuntime.resolve_communication()'s cross-society path
specifically (three different SocietyRuntimes, no two of these actors
share one). Two real, direct hops (Merchant<->Logistics,
Logistics<->Warehouse) must each succeed on their own affiliation, and
must NOT compose into a third, transitive Merchant<->Warehouse reach —
the router has no notion of affiliation chains, only direct, real,
shared ones.
"""

from __future__ import annotations

import sys

from _common import ApiError, ask_actor, banner, client, kv, section
from bootstrap_mb3402 import bootstrap_world


def main() -> int:
    banner("MB-3402 — Cross-Affiliation Chain")
    c = client()
    try:
        world = bootstrap_world(c)
        actors = world["actors"]
        merchant_id, logistics_id, warehouse_id = (
            actors["Merchant"],
            actors["Logistics Provider"],
            actors["Warehouse Worker"],
        )

        section("Merchant -> Logistics Provider (shared merchant_logistics)")
        status, body = ask_actor(
            c,
            merchant_id,
            "Merchant",
            logistics_id,
            "Can you ship 20 units to the East Coast this week?",
        )
        kv("HTTP status", status)
        hop1_ok = status < 300

        section("Logistics Provider -> Warehouse Worker (shared logistics_warehouse)")
        status, body = ask_actor(
            c,
            logistics_id,
            "Logistics Provider",
            warehouse_id,
            "Do you have 20 units ready for pickup?",
        )
        kv("HTTP status", status)
        hop2_ok = status < 300

        section("Merchant -> Warehouse Worker directly (no shared affiliation, no transitivity)")
        status, body = ask_actor(
            c,
            merchant_id,
            "Merchant",
            warehouse_id,
            "Do you have 20 units ready for pickup?",
        )
        kv("HTTP status", status)
        kv("Reason", body.get("detail", ""))
        hop3_denied = status == 403

        section("Verification")
        checks = [
            ("Merchant -> Logistics Provider is ALLOWED", hop1_ok),
            ("Logistics Provider -> Warehouse Worker is ALLOWED", hop2_ok),
            (
                "Merchant -> Warehouse Worker directly is DENIED (no transitive reach)",
                hop3_denied,
            ),
        ]
        ok = True
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
