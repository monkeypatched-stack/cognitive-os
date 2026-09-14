"""MB-3404 — Temporary Affiliation via Presence: the full real
grant-then-revoke cycle. A Contractor with zero configured Affiliations
cannot reach a Warehouse Worker; entering the real Warehouse Space
(POST /actors/{id}/move) grants real TEMPORARY Warehouse Society
membership via MembershipGovernor, making the same ask succeed with no
Affiliation ever created; leaving revokes it and the same ask is denied
again. Exercises PlanetaryRuntime.resolve_communication()'s
multi-society lookup (_societies_for), which is what lets a
temporarily-co-present pair be found as sharing a Society even though
neither one's PERMANENT home society changed.
"""

from __future__ import annotations

import sys

from _common import ApiError, ask_actor, banner, client, kv, move_actor, section
from bootstrap_mb3404 import bootstrap_world


def main() -> int:
    banner("MB-3404 — Temporary Affiliation via Presence")
    c = client()
    try:
        world = bootstrap_world(c)
        actors = world["actors"]
        spaces = world["spaces"]
        contractor_id, worker_id = actors["Contractor"], actors["Warehouse Worker"]

        section("Before presence: Contractor asks Warehouse Worker")
        status, body = ask_actor(c, contractor_id, "Contractor", worker_id, "Can I help out today?")
        kv("HTTP status", status)
        before_denied = status == 403

        section("Contractor physically enters the Warehouse A Space")
        presence = move_actor(c, contractor_id, spaces["warehouse_a"], activity="visiting")
        kv("Presence recorded at", presence.get("space_id", spaces["warehouse_a"]))

        section("While present: Contractor asks Warehouse Worker again")
        status, body = ask_actor(c, contractor_id, "Contractor", worker_id, "Can I help out today?")
        kv("HTTP status", status)
        kv("Answer", body.get("answer", "")[:120])
        during_allowed = status < 300

        section("Contractor leaves the Warehouse (back to the Contractor Pool Office)")
        presence = move_actor(c, contractor_id, spaces["contractor_office"], activity="returning")
        kv(
            "Presence recorded at",
            presence.get("space_id", spaces["contractor_office"]),
        )

        section("After leaving: Contractor asks Warehouse Worker again")
        status, body = ask_actor(c, contractor_id, "Contractor", worker_id, "Can I help out today?")
        kv("HTTP status", status)
        after_denied = status == 403

        section("Verification")
        checks = [
            ("Denied before any presence in the Warehouse", before_denied),
            ("Allowed while physically present (temporary membership)", during_allowed),
            ("Denied again after leaving (temporary membership revoked)", after_denied),
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
