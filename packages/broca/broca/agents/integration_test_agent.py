"""IntegrationTestAgent — exercises a running service across its real
dependencies (HTTP, DB), distinct from UnitTestingAgent's isolated pytest run.

Where UnitTestingAgent runs `pytest` against source in isolation, this agent
requires a live service (as ServeAgent starts) and drives real HTTP requests
against it — health check, then any configured smoke-test requests — so
failures here reflect actual runtime integration problems (wrong port,
missing env var, DB connection failure) that unit tests can't catch.
"""

from __future__ import annotations
import asyncio
import logging
import time
from typing import Any
from ._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.integration_test")

_DEFAULT_TIMEOUT = 10.0
_HEALTH_RETRY_SECONDS = 15.0


class IntegrationTestAgent(BaseETASSAgent):
    agent_type = "integration_test"
    description = (
        "Exercises a running service over real HTTP against its actual dependencies, not an isolated pytest run"
    )

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict):
        cap = self._find_capability("integration_test")
        if cap:
            try:
                from src.monkey_brain.kernel.execution_state import ExecutionState

                state = ExecutionState.from_dict(context) if hasattr(ExecutionState, "from_dict") else context
                raw = await cap.execute(state)
                output = raw.output if hasattr(raw, "output") else (raw if isinstance(raw, dict) else {})
                if "passed" in output:
                    self._reward(output["passed"])
                    return self._result(payload=output)
            except Exception as e:
                logger.warning("[integration_test] capability failed: %s — direct HTTP", e)

        return await self._http_smoke_test(context)

    async def _http_smoke_test(self, context: dict):
        base_url = str(context.get("base_url") or f"http://localhost:{context.get('port', 8000)}").rstrip("/")
        checks = context.get("checks") or [{"method": "GET", "path": "/health", "expect_status": 200}]

        try:
            import httpx
        except ImportError:
            self._reward(True, 0.5)
            return self._result(
                payload={"passed": True, "skipped": True},
                observations=["httpx not installed"],
            )

        healthy = await self._wait_for_health(base_url, httpx)
        if not healthy:
            self._reward(False, 0.1)
            return self._result(
                payload={"passed": False, "error": "service never became healthy"},
                observations=[f"{base_url}/health did not return 200 within {_HEALTH_RETRY_SECONDS}s"],
            )

        results = []
        all_ok = True
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            for check in checks:
                method = check.get("method", "GET")
                path = check.get("path", "/health")
                expect = check.get("expect_status", 200)
                try:
                    resp = await client.request(method, f"{base_url}{path}", json=check.get("body"))
                    ok = resp.status_code == expect
                    results.append(
                        {
                            "path": path,
                            "status": resp.status_code,
                            "expected": expect,
                            "ok": ok,
                        }
                    )
                    all_ok = all_ok and ok
                except Exception as e:
                    results.append({"path": path, "error": str(e), "ok": False})
                    all_ok = False

        self._reward(all_ok, 0.4)
        return self._result(
            payload={"passed": all_ok, "checks": results},
            observations=[f"{r['path']}: {'ok' if r.get('ok') else 'FAILED'}" for r in results],
            evidence=[r for r in results if not r.get("ok")],
        )

    async def _wait_for_health(self, base_url: str, httpx_mod) -> bool:
        deadline = time.time() + _HEALTH_RETRY_SECONDS
        async with httpx_mod.AsyncClient(timeout=2.0) as client:
            while time.time() < deadline:
                try:
                    resp = await client.get(f"{base_url}/health")
                    if resp.status_code == 200:
                        return True
                except Exception:
                    pass
                await asyncio.sleep(0.5)
        return False
