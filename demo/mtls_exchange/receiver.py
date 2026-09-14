"""Receiver runtime — serves the knowledge-exchange endpoint over mutual TLS.

Boots the assembled exchange server (CA-backed identity + revocation + transport auth from
configure_exchange) behind uvicorn with client-certificate verification required. Only a peer
presenting a CA-issued client cert can even open the connection; the proposal is then verified
through the full trust pipeline before anything is accepted.
"""

from __future__ import annotations

import os
import ssl

import uvicorn
from fastapi import FastAPI

from src.monkey_brain.kernel.compile.network import (
    configure_exchange,
    secure_mode_preflight,
)
from src.monkey_brain.kernel.compile import Relationship, TrustNetwork


def build_app() -> FastAPI:
    secure_mode_preflight()  # log/verify the security posture at boot
    tenant = os.environ.get("AGENTOS_TENANT_ID", "default")
    # Grant the sender permission to publish beliefs to this runtime.
    net = TrustNetwork()
    net.connect(os.environ["DEMO_SENDER"], tenant, Relationship.MENTOR)
    _ex, server, _flusher = configure_exchange(net)
    app = FastAPI(title="MonkeyBrain Receiver")
    app.include_router(server.create_router(), prefix="/api/v1/agentos")
    return app


if __name__ == "__main__":
    uvicorn.run(
        build_app(),
        host="127.0.0.1",
        port=int(os.environ["PORT"]),
        ssl_certfile=os.environ["TLS_SERVER_CERT"],
        ssl_keyfile=os.environ["TLS_SERVER_KEY"],
        ssl_ca_certs=os.environ["TLS_CA"],
        ssl_cert_reqs=ssl.CERT_REQUIRED,  # mTLS: require a client certificate
        log_level="warning",
    )
