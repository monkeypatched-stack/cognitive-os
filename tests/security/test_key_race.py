"""Multi-process key race — runtimes sharing a key store must converge on ONE key.

Without the inter-process lock, concurrent first-touch of the same runtime_id generates
divergent keys and silently breaks signature verification. Spawns real subprocesses that each
create the key, then asserts every process produced the SAME public key.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent

_WORKER = r"""
import os, sys
sys.path.insert(0, os.environ["REPO"])
from src.monkey_brain.kernel.identity import KeyManager
km = KeyManager(key_dir=os.environ["KEYDIR"])
print(km.get_public_key_pem("shared:runtime").strip().splitlines()[1])
"""


def test_concurrent_key_creation_converges(tmp_path):
    env = {
        **os.environ,
        "REPO": str(REPO),
        "KEYDIR": str(tmp_path / "keys"),
        "AGENTOS_KEY_PASSWORD": "fixed-demo-passphrase",
    }
    procs = [
        subprocess.Popen([sys.executable, "-c", _WORKER], env=env, stdout=subprocess.PIPE, text=True) for _ in range(6)
    ]
    outs = [p.communicate()[0].strip() for p in procs]
    assert all(outs), "a worker produced no key"
    assert len(set(outs)) == 1, f"divergent keys across processes: {set(outs)}"
