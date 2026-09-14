"""Sender runtime — publishes a CA-signed knowledge proposal and pushes it over mTLS.

Shares the exchange CA with the receiver (same AGENTOS_CA_STORE + key store), so the proposal
certificate chains to a root the receiver trusts. Presents its own TLS client certificate to
satisfy the receiver's mutual-TLS requirement.
"""

from __future__ import annotations

import json
import os
import sys

from src.monkey_brain.kernel.compile.network import (
    configure_exchange,
    ExchangeClient,
    build_client_ssl_context,
)


def main() -> int:
    origin = os.environ["DEMO_SENDER"]
    tenant = os.environ.get("AGENTOS_TENANT_ID", "default")
    port = os.environ["PORT"]

    ex, _server, _flusher = configure_exchange()  # same CA store → same root anchor
    proposal = ex.publish(origin, "belief", "api", [("Observe", "Decide", 1.0), ("Decide", "Act", 1.0)])
    if proposal is None:
        print("SENDER: publish blocked", file=sys.stderr)
        return 2
    print(
        f"SENDER: published proposal (sig={proposal.signature.split(':')[0]}, "
        f"chain_len={len(proposal.certificate['chain'])})",
        file=sys.stderr,
    )

    ctx = build_client_ssl_context(
        os.environ["TLS_CLIENT_CERT"],
        os.environ["TLS_CLIENT_KEY"],
        os.environ["TLS_CA"],
    )
    client = ExchangeClient(ssl_context=ctx)
    result = client.send_proposal(proposal, f"https://localhost:{port}", recipient_tenant=tenant)
    print("RESULT:" + json.dumps(result))
    return 0 if result.get("status") == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
