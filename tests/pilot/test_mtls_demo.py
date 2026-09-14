"""Runs the two-process live mTLS exchange demo and asserts it passes end-to-end.

Self-contained (spawns its own uvicorn receiver + sender subprocesses, no external services),
so it can gate the whole wired path in CI.
"""

from __future__ import annotations

import sys
from pathlib import Path

_DEMO = Path(__file__).resolve().parent.parent.parent / "demo" / "mtls_exchange"


def test_two_process_mtls_exchange():
    sys.path.insert(0, str(_DEMO))
    import run_demo

    assert run_demo.main() == 0  # receiver accepts CA-signed proposal; cert-less client rejected
