"""MB-3403 — Unauthorized Communication: "Message the CEO directly"
must be DENIED with a real, specific reason — not a generic error, and
not a silently-empty or fabricated reply. Verifies the real HTTP
POST /actors/{id}/ask route itself now enforces resolve_communication()
(the second, previously-unguarded code path this suite closes, distinct
from the LLM-planner-driven AskActorCapability MB-3400/3401/3402
exercise).
"""

from __future__ import annotations

import sys

from _common import ApiError, ask_actor, banner, client, kv, section
from bootstrap_mb3403 import bootstrap_world


def main() -> int:
    banner("MB-3403 — Unauthorized Communication")
    c = client()
    try:
        world = bootstrap_world(c)
        actors = world["actors"]
        customer_id, ceo_id = actors["Customer"], actors["CEO"]

        section('Customer attempts: "Message the CEO directly"')
        status, body = ask_actor(c, customer_id, "Customer", ceo_id, "I need a refund approved right now.")
        kv("HTTP status", status)
        kv("Denied", status == 403)
        reason = body.get("detail", "")
        kv("Reason", reason)

        section("Verification")
        checks = [
            ("Request is DENIED (403)", status == 403),
            (
                "Denial includes a real, specific reason",
                bool(reason) and reason != "denied",
            ),
            (
                "Reason names the actual gap (no shared affiliation/society)",
                "affiliation" in reason.lower() or "society" in reason.lower(),
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
