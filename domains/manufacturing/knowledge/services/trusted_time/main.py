"""Trusted Time — Layer 5 (Trust & Infrastructure): a small, dedicated
signing service that issues HMAC-signed timestamps, so signed/hash-chained
structures elsewhere in this codebase can attest to when they were created
instead of trusting each process's own local clock outright.

Deliberately has NO database, no message bus, no dependency on any other
service — unlike every other services/* microservice in this tree. Its only
job is "sign this timestamp with a shared secret," so it has nothing to
persist and nothing else to be down for. Consumed by
src/monkey_brain/kernel/trusted_time.py's client (currently: only
kernel/process/checkpoint.py's Checkpoint.created_at — see that module's
docstring for why it, specifically, needed this).

Shares its signing key with callers via TRUSTED_TIME_SIGNING_KEY (falls back
to AGENTOS_MASTER_KEY, then an insecure dev-only constant) — the same
env-var-driven HMAC pattern kernel/plan/goals/intent_ir.py and
kernel/process/checkpoint.py already use for their own signatures. This is
a shared-secret trust model (consistent with the rest of this codebase's
signing), not a PKI — no asymmetric crypto, no cert issuance, on purpose,
for an MVP scope.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time

from fastapi import FastAPI

app = FastAPI(title="Trusted Time", version="1.0.0")

KEY_ID = os.environ.get("TRUSTED_TIME_KEY_ID", "trusted-time-dev")


def _signing_key() -> bytes:
    key = os.environ.get("TRUSTED_TIME_SIGNING_KEY", "") or os.environ.get("AGENTOS_MASTER_KEY", "")
    if not key:
        key = "insecure-dev-only-trusted-time-key"
    return key.encode("utf-8")


def sign_timestamp(ts: float, key_id: str) -> str:
    return hmac.new(_signing_key(), f"{ts:.6f}|{key_id}".encode("utf-8"), hashlib.sha256).hexdigest()


@app.get("/timestamp")
def issue_timestamp() -> dict:
    ts = time.time()
    return {"timestamp": ts, "signature": sign_timestamp(ts, KEY_ID), "key_id": KEY_ID}


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/live")
def live() -> dict:
    return {"status": "ok"}


@app.get("/ready")
def ready() -> dict:
    return {"status": "ok"}
