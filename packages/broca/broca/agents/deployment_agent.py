"""DeploymentAgent — ships a packaged service to an environment, with rollback.

Distinct from ServeAgent (serve_agent.py): ServeAgent launches a freshly
generated service locally for development/testing, with no notion of
versions or rollback. DeploymentAgent tracks a deploy history per service
(~/.monkeybrain/deploys/<service>.json) so a failed deploy — or a bad release
discovered later — can be rolled back to the last-known-good version, which
is the "release" stage's actual job, not just "start the process."
"""

from __future__ import annotations
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from ._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.deployment")

_REPO = Path(os.environ.get("MONKEYBRAIN_REPO", str(Path(__file__).parents[4])))
_DEPLOYS_DIR = Path.home() / ".monkeybrain" / "deploys"


def _history_path(service_slug: str) -> Path:
    _DEPLOYS_DIR.mkdir(parents=True, exist_ok=True)
    return _DEPLOYS_DIR / f"{service_slug}.json"


def _load_history(service_slug: str) -> list[dict]:
    path = _history_path(service_slug)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except Exception:
        return []


def _save_history(service_slug: str, history: list[dict]) -> None:
    _history_path(service_slug).write_text(json.dumps(history[-20:], indent=2))


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


class DeploymentAgent(BaseETASSAgent):
    agent_type = "deploy"
    description = "Ships a packaged service to an environment and tracks deploy history for rollback"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict):
        action = str(context.get("action", "deploy"))
        if action == "rollback":
            return await self._rollback(context)
        return await self._deploy(context)

    async def _deploy(self, context: dict):
        service_slug = str(context.get("service_slug", "")).strip()
        if not service_slug:
            self._reward(False, 0.0)
            return self._result(payload={"deployed": False}, observations=["no service_slug in context"])

        output_dir = Path(str(context.get("output_dir") or _REPO / "generated"))
        svc_dir = output_dir / service_slug
        if not (svc_dir / "main.py").exists() and not (svc_dir / service_slug / "main.py").exists():
            self._reward(False, 0.1)
            return self._result(
                payload={"deployed": False, "error": f"no main.py under {svc_dir}"},
                observations=["package the service before deploying"],
            )
        if (svc_dir / service_slug / "main.py").exists():
            svc_dir = svc_dir / service_slug

        version = str(context.get("version", "0.1.0"))
        host = str(context.get("host", "0.0.0.0"))
        port = int(context.get("port", 8090))

        history = _load_history(service_slug)
        previous = history[-1] if history else None

        # Stop whatever is currently running for this service before starting the new version.
        if previous and previous.get("pid") and _pid_alive(previous["pid"]):
            self._stop(previous["pid"])

        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            f"{service_slug}.main:app",
            "--host",
            host,
            "--port",
            str(port),
        ]
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(svc_dir.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            self._reward(False, 0.2)
            return self._result(
                payload={"deployed": False, "error": repr(e)},
                observations=[f"deploy failed: {e}"],
            )

        record = {
            "version": version,
            "pid": proc.pid,
            "host": host,
            "port": port,
            "deployed_at": time.time(),
        }
        history.append(record)
        _save_history(service_slug, history)

        self._reward(True, 0.9)
        return self._result(
            payload={
                "deployed": True,
                "version": version,
                "url": f"http://{host}:{port}",
                "pid": proc.pid,
            },
            observations=[f"{service_slug} v{version} deployed → http://{host}:{port} (pid={proc.pid})"],
        )

    async def _rollback(self, context: dict):
        service_slug = str(context.get("service_slug", "")).strip()
        if not service_slug:
            self._reward(False, 0.0)
            return self._result(
                payload={"rolled_back": False},
                observations=["no service_slug in context"],
            )

        history = _load_history(service_slug)
        if len(history) < 2:
            self._reward(False, 0.2)
            return self._result(
                payload={
                    "rolled_back": False,
                    "error": "no prior version to roll back to",
                },
                observations=[f"deploy history for {service_slug} has {len(history)} entries"],
            )

        current = history.pop()
        target = history[-1]
        _save_history(service_slug, history)

        if current.get("pid") and _pid_alive(current["pid"]):
            self._stop(current["pid"])

        output_dir = Path(str(context.get("output_dir") or _REPO / "generated"))
        svc_dir = output_dir / service_slug
        if (svc_dir / service_slug / "main.py").exists():
            svc_dir = svc_dir / service_slug

        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            f"{service_slug}.main:app",
            "--host",
            target.get("host", "0.0.0.0"),
            "--port",
            str(target.get("port", 8090)),
        ]
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(svc_dir.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            self._reward(False, 0.2)
            return self._result(payload={"rolled_back": False, "error": repr(e)})

        target["pid"] = proc.pid
        target["deployed_at"] = time.time()
        history.append(target)
        _save_history(service_slug, history)

        self._reward(True, 0.7)
        return self._result(
            payload={
                "rolled_back": True,
                "version": target.get("version"),
                "pid": proc.pid,
            },
            observations=[f"rolled back {service_slug} to v{target.get('version')} (pid={proc.pid})"],
        )

    def _stop(self, pid: int) -> None:
        try:
            os.kill(pid, 15)  # SIGTERM
        except Exception as e:
            logger.debug("[deploy] failed to stop pid=%s: %s", pid, e)
