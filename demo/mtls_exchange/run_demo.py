"""Two-process live mTLS demo — the whole path booting and talking.

  1. Generate a CA + server/client TLS certs and a shared exchange CA store.
  2. Start the RECEIVER as its own process: the exchange server over mutual TLS.
  3. Run the SENDER as its own process: publish a CA-signed proposal and push it over mTLS.
  4. Verify the receiver ACCEPTED it (and rejects a client with no cert).

Run:  python3 demo/mtls_exchange/run_demo.py
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_tls_ready(port: int, ca: str, client_cert: str, client_key: str, timeout: float = 25.0) -> bool:
    """Poll until the receiver's mTLS listener completes a handshake."""
    ctx = ssl.create_default_context(cafile=ca)
    ctx.load_cert_chain(client_cert, client_key)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2) as raw:
                with ctx.wrap_socket(raw, server_hostname="localhost"):
                    return True
        except Exception:
            time.sleep(0.4)
    return False


def _no_cert_rejected(port: int, ca: str) -> bool:
    """A client trusting the server CA but presenting NO client cert must be rejected.

    Under TLS 1.3 the client-auth failure surfaces on the first application read, not at the
    handshake, so we make a real request and expect it to fail."""
    ctx = ssl.create_default_context(cafile=ca)
    try:
        req = urllib.request.Request(f"https://localhost:{port}/api/v1/agentos/exchange/stats")
        urllib.request.urlopen(req, context=ctx, timeout=5).read()
        return False  # got a response → mTLS NOT enforced
    except Exception:
        return True  # rejected — mTLS enforced


def main() -> int:
    demo = tempfile.mkdtemp(prefix="mtls_demo_")
    print(f"[demo] workspace: {demo}")

    sys.path.insert(0, str(HERE))
    import certs

    tls = certs.generate(os.path.join(demo, "tls"))
    port = _free_port()

    env = {
        **os.environ,
        # NOTE: do NOT override HOME — it would break user site-packages resolution. Both
        # processes already share ~/.monkeybrain/keys, so they share the exchange CA key.
        "PYTHONPATH": str(REPO),
        "AGENTOS_CA_STORE": os.path.join(demo, "ca.json"),
        "AGENTOS_EXCHANGE_TOKEN": "demo-transport-token",
        "AGENTOS_REQUIRE_MTLS": "1",
        "AGENTOS_TENANT_ID": "org:receiver",
        "DEMO_SENDER": "org:sender",
        "PORT": str(port),
        "TLS_CA": tls["ca"],
        "TLS_SERVER_CERT": tls["server_cert"],
        "TLS_SERVER_KEY": tls["server_key"],
        "TLS_CLIENT_CERT": tls["client_cert"],
        "TLS_CLIENT_KEY": tls["client_key"],
    }

    print(f"[demo] starting RECEIVER (mTLS) on https://localhost:{port} ...")
    receiver = subprocess.Popen(
        [sys.executable, str(HERE / "receiver.py")],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        if not _wait_tls_ready(port, tls["ca"], tls["client_cert"], tls["client_key"]):
            print("[demo] FAIL: receiver did not become ready")
            print((receiver.stderr.read() or "")[-1500:])
            return 1
        print("[demo] receiver up; mTLS handshake OK")

        # Negative control: a client with no cert is refused at the TLS layer.
        rejected = _no_cert_rejected(port, tls["ca"])
        print(f"[demo] client WITHOUT cert rejected by mTLS: {rejected}")

        print("[demo] running SENDER (publish + push over mTLS) ...")
        sender = subprocess.run(
            [sys.executable, str(HERE / "sender.py")],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        sys.stderr.write(sender.stderr)
        result_line = next((l for l in sender.stdout.splitlines() if l.startswith("RESULT:")), "")
        result = json.loads(result_line[len("RESULT:") :]) if result_line else {}
        print(f"[demo] receiver response: {result}")

        ok = result.get("status") == "accepted" and rejected
        print(
            "\n[demo] " + ("PASS ✅  two runtimes exchanged a CA-signed proposal over live mTLS" if ok else "FAIL ❌")
        )
        return 0 if ok else 1
    finally:
        receiver.terminate()
        try:
            receiver.wait(timeout=5)
        except Exception:
            receiver.kill()


if __name__ == "__main__":
    raise SystemExit(main())
