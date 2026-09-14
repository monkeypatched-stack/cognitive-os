"""ServeAgent — launches a generated DDD service with uvicorn.

Writes PID to ~/.monkeybrain/pids/<service>.pid and notifies MotorCortexAgent.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from ._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.serve_agent")

_REPO = Path(os.environ.get("MONKEYBRAIN_REPO", str(Path(__file__).parents[4])))


class ServeAgent(BaseETASSAgent):
    agent_type = "serve"
    description = "Launches a generated DDD service with uvicorn and records the PID"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict) -> dict:
        service_slug = str(context.get("service_slug", "")).strip()
        output_dir = Path(str(context.get("output_dir", _REPO / "generated")))
        port: int = int(context.get("port", 8090))
        host: str = str(context.get("host", "127.0.0.1"))
        background: bool = bool(context.get("background", True))

        if not service_slug:
            self._reward(False, 0.0)
            return self._result(payload={"running": False}, observations=["no service_slug"])

        out_base = output_dir if output_dir.is_absolute() else _REPO / output_dir
        svc_dir = out_base / service_slug

        if not svc_dir.exists():
            self._reward(False, 0.0)
            return self._result(
                payload={
                    "running": False,
                    "error": f"service dir not found: {svc_dir}",
                },
                observations=[f"run codegen first: monkeypatched make codegen {service_slug}"],
            )

        main_py = svc_dir / "main.py"
        if not main_py.exists():
            nested = svc_dir / service_slug / "main.py"
            if nested.exists():
                main_py = nested
                svc_dir = svc_dir / service_slug
            else:
                self._reward(False, 0.0)
                return self._result(
                    payload={"running": False, "error": "main.py not found"},
                    observations=[f"main.py not found in {svc_dir}"],
                )

        app_module = f"{service_slug}.main:app"
        pids_dir = Path.home() / ".monkeybrain" / "pids"
        pids_dir.mkdir(parents=True, exist_ok=True)
        pid_file = pids_dir / f"{service_slug}.pid"

        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            app_module,
            "--host",
            host,
            "--port",
            str(port),
        ]
        pid: int | None = None

        try:
            if background:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(out_base),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                pid = proc.pid
                pid_file.write_text(str(pid))
            else:
                subprocess.run(cmd, cwd=str(out_base))
        except Exception as e:
            self._reward(False, 0.2)
            return self._result(
                payload={"running": False, "error": repr(e)},
                observations=[f"uvicorn failed: {e}"],
            )

        # Notify MotorCortexAgent (best-effort)
        try:
            from broca.agents.motor_cortex import MotorCortexAgent

            mca = MotorCortexAgent()
            await mca.handle(
                {
                    "action": "serve",
                    "service": service_slug,
                    "host": host,
                    "port": port,
                    "pid": str(pid or ""),
                }
            )
        except Exception as e:
            logger.debug("[serve] MotorCortexAgent notify failed: %s", e)

        logger.info("[serve] %s running at http://%s:%d (pid=%s)", service_slug, host, port, pid)
        self._reward(True, 0.9)
        return self._result(
            payload={
                "running": True,
                "service_slug": service_slug,
                "url": f"http://{host}:{port}",
                "docs_url": f"http://{host}:{port}/docs",
                "pid": pid,
                "pid_file": str(pid_file),
            },
            observations=[f"{service_slug} → http://{host}:{port} (pid={pid})"],
        )
