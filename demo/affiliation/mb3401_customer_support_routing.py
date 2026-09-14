"""MB-3401 — Customer Support Routing: a Customer must NEVER be able to
reach a Warehouse Worker directly — there is no shared Affiliation and
they are in different Societies. Reaching the Warehouse only works by
relaying through Support Agent, who genuinely holds affiliations on
BOTH sides. The Customer never sees or chooses that internal hop; this
script plays the role every real client already plays (asking Support,
receiving Support's own reply) — the relay itself happens because
Support Agent's OWN next real ask is a second, independent call this
script also makes, exactly as demo/conversation established.
"""

from __future__ import annotations

import sys

from _common import ApiError, ask_actor, banner, client, kv, section
from bootstrap_mb3401 import bootstrap_world


def main() -> int:
    banner("MB-3401 — Customer Support Routing")
    c = client()
    try:
        world = bootstrap_world(c)
        actors = world["actors"]
        customer_id, agent_id, worker_id = (
            actors["Customer"],
            actors["Support Agent"],
            actors["Warehouse Worker"],
        )

        section("Customer attempts to address the Warehouse Worker directly")
        status, body = ask_actor(c, customer_id, "Customer", worker_id, "Where is my order?")
        kv("HTTP status", status)
        kv("Denied", status == 403)
        kv("Reason", body.get("detail", ""))
        direct_denied = status == 403

        section("Customer asks Support (shared customer_support affiliation)")
        status, body = ask_actor(c, customer_id, "Customer", agent_id, "Where is my order?")
        kv("HTTP status", status)
        kv("Answer", body.get("answer", "")[:120])
        customer_to_support_ok = status < 300

        section("Support Agent relays to the Warehouse (shared warehouse_team affiliation)")
        status, body = ask_actor(
            c,
            agent_id,
            "Support Agent",
            worker_id,
            "A customer is asking where their order is — what is its packing status?",
        )
        kv("HTTP status", status)
        kv("Answer", body.get("answer", "")[:120])
        support_to_warehouse_ok = status < 300

        section("Verification")
        checks = [
            ("Customer -> Warehouse Worker directly is DENIED", direct_denied),
            ("Customer -> Support Agent is ALLOWED", customer_to_support_ok),
            ("Support Agent -> Warehouse Worker is ALLOWED", support_to_warehouse_ok),
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
