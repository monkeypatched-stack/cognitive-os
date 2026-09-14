"""Runtime Capabilities — code execution integrations.

SECURITY: these capabilities run caller-supplied code/commands. They are
disabled by default and refuse to execute unless code execution is explicitly
enabled via MONKEYBRAIN_ALLOW_CODE_EXECUTION=true. Never enable this on a host
that processes untrusted request state — doing so is remote code execution by
design. They are not registered by cerebellum.providers.load_all_providers();
wiring them in is an explicit, deliberate act.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys

from cerebellum.capability import Capability


def _code_execution_enabled() -> bool:
    """True only when code execution is explicitly opted into via env."""
    return os.getenv("MONKEYBRAIN_ALLOW_CODE_EXECUTION", "false").strip().lower() in (
        "true",
        "1",
        "yes",
        "on",
    )


_DISABLED_RESULT = {
    "error": (
        "code execution disabled — set MONKEYBRAIN_ALLOW_CODE_EXECUTION=true to enable "
        "(never on a host processing untrusted input)"
    ),
    "success": False,
}


class PythonCapability(Capability):
    """Python code execution capability (disabled unless explicitly enabled)."""

    def __init__(self):
        super().__init__(name="python")

    async def execute(self, state, **kwargs):
        if not _code_execution_enabled():
            return dict(_DISABLED_RESULT)
        code = state.get("code", "")
        try:
            result = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=state.get("timeout", 30),
            )
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
                "success": result.returncode == 0,
            }
        except subprocess.TimeoutExpired:
            return {"error": "Execution timed out", "success": False}
        except Exception as e:
            return {"error": str(e), "success": False}


class ShellCapability(Capability):
    """Shell command execution capability (disabled unless explicitly enabled).

    Runs without shell=True: the command is tokenized with shlex and executed
    directly, so shell metacharacters (;, |, &&, $(), backticks) are not
    interpreted and cannot chain or inject additional commands.
    """

    def __init__(self):
        super().__init__(name="shell")

    async def execute(self, state, **kwargs):
        if not _code_execution_enabled():
            return dict(_DISABLED_RESULT)
        command = state.get("command", "")
        argv = command if isinstance(command, list) else shlex.split(str(command))
        if not argv:
            return {"error": "empty command", "success": False}
        try:
            result = subprocess.run(
                argv,
                shell=False,
                capture_output=True,
                text=True,
                timeout=state.get("timeout", 30),
            )
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
                "success": result.returncode == 0,
            }
        except subprocess.TimeoutExpired:
            return {"error": "Execution timed out", "success": False}
        except Exception as e:
            return {"error": str(e), "success": False}


class ProcessCapability(Capability):
    """Process execution capability (disabled unless explicitly enabled)."""

    def __init__(self):
        super().__init__(name="process")

    async def execute(self, state, **kwargs):
        if not _code_execution_enabled():
            return dict(_DISABLED_RESULT)
        command = state.get("command", [])
        if not isinstance(command, list) or not command:
            return {
                "error": "process command must be a non-empty argument list",
                "success": False,
            }
        try:
            result = subprocess.run(
                command,
                shell=False,
                capture_output=True,
                text=True,
                timeout=state.get("timeout", 30),
            )
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
                "success": result.returncode == 0,
            }
        except subprocess.TimeoutExpired:
            return {"error": "Execution timed out", "success": False}
        except Exception as e:
            return {"error": str(e), "success": False}
