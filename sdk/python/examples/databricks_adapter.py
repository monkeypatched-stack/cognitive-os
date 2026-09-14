"""
DataBricks Adapter – Reference Implementation
==============================================

Pattern: API Token + HTTP REST

Capabilities:
    run_databricks_job  – Trigger a DataBricks job run and wait for result
    query_databricks    – Execute a SQL query via the SQL Warehouses API

Demonstrates:
    - Long-polling / async job tracking
    - Bearer token auth (API Token as Bearer)
    - Structured result mapping
"""

import asyncio
from typing import Any, Dict

from monkeypatched_sdk import (
    AdapterContext,
    AdapterResponse,
    APIKeyHandler,
    CapabilityAdapter,
    HTTPConnectionPool,
    capability_adapter,
)
from monkeypatched_sdk.contracts.exceptions import AdapterInitializationError

# DataBricks job run terminal states
_TERMINAL_STATES = {"TERMINATED", "INTERNAL_ERROR", "SKIPPED"}
_SUCCESS_STATE = "TERMINATED"
_SUCCESS_RESULT = "SUCCESS"

# Max seconds to wait for a job run
_DEFAULT_JOB_TIMEOUT = 600


@capability_adapter(
    capability_id="run_databricks_job",
    version="1.0.0",
    tags=["databricks", "analytics", "ml", "etl"],
)
class DataBricksJobAdapter(CapabilityAdapter):
    """
    Triggers a DataBricks job run and polls until completion.

    Configuration (config.yaml)
    ---------------------------
    adapters:
      run_databricks_job:
        enabled: true
        endpoint: https://<workspace>.azuredatabricks.net
        credentials:
          api_key: ${DATABRICKS_TOKEN}
        timeout: 600
        extra:
          poll_interval: 10
    """

    def __init__(self, adapter_id: str, capability_id: str) -> None:
        super().__init__(adapter_id, capability_id)
        self._pool: HTTPConnectionPool | None = None

    async def initialize(self) -> None:
        endpoint = self.config.get("endpoint")
        api_token = self.config.get("credentials", {}).get("api_key")

        if not endpoint:
            raise AdapterInitializationError("DataBricks endpoint not configured.")
        if not api_token:
            raise AdapterInitializationError("DataBricks API token not configured.")

        auth = APIKeyHandler(
            api_key=api_token,
            header_name="Authorization",
            header_prefix="Bearer ",
        )

        self._pool = HTTPConnectionPool(
            base_url=endpoint,
            auth_handler=auth,
            headers={"Content-Type": "application/json"},
            pool_size=3,
            timeout=30,
            health_path="/api/2.0/clusters/list",
        )
        await self._pool.initialize()
        self.log("info", "DataBricksJobAdapter initialized.", endpoint=endpoint)

    async def execute(
        self,
        context: AdapterContext,
        inputs: Dict[str, Any],
    ) -> AdapterResponse:
        """
        Trigger a DataBricks job and wait for it to finish.

        Required inputs
        ---------------
        job_id : int | str  – DataBricks job ID

        Optional inputs
        ---------------
        parameters     : dict – notebook parameters
        job_timeout    : int  – override max wait seconds (default 600)
        """
        job_id = inputs.get("job_id")
        if not job_id:
            return AdapterResponse.fail("Missing required input: job_id")

        parameters = inputs.get("parameters", {})
        job_timeout = int(inputs.get("job_timeout", self.config.get("timeout", _DEFAULT_JOB_TIMEOUT)))
        poll_interval = int(self.config.get("poll_interval", 10))

        try:
            async with await self.telemetry.start_span("databricks_job_run") as span:
                span.set_tag("job_id", str(job_id))

                # Trigger the job run
                run_id = await self._trigger_run(job_id, parameters)
                if run_id is None:
                    return AdapterResponse.fail("Failed to trigger DataBricks job run.")

                span.set_tag("run_id", str(run_id))
                self.log("info", f"DataBricks run {run_id} started for job {job_id}.")

                # Poll until terminal state
                elapsed = 0
                while elapsed < job_timeout:
                    await asyncio.sleep(poll_interval)
                    elapsed += poll_interval

                    state, result_state, output = await self._poll_run(run_id)

                    if state in _TERMINAL_STATES:
                        success = state == _SUCCESS_STATE and result_state == _SUCCESS_RESULT
                        if success:
                            await self.emit_event(
                                "databricks_job_completed",
                                {"job_id": job_id, "run_id": run_id},
                            )
                            await self.emit_metric("databricks_jobs_succeeded", 1.0)
                            return AdapterResponse.ok(
                                {
                                    "run_id": run_id,
                                    "state": state,
                                    "result_state": result_state,
                                    "output": output,
                                }
                            )
                        else:
                            await self.emit_metric("databricks_jobs_failed", 1.0)
                            return AdapterResponse.fail(f"DataBricks job failed: state={state}, result={result_state}")

                return AdapterResponse.fail(f"DataBricks job run {run_id} timed out after {job_timeout}s.")

        except Exception as exc:
            self.log("error", f"DataBricks execute failed: {exc}")
            return AdapterResponse.fail(str(exc))

    async def _trigger_run(self, job_id: Any, parameters: dict) -> Any:
        """POST to /api/2.0/jobs/run-now and return the run_id."""
        payload: Dict[str, Any] = {"job_id": int(job_id)}
        if parameters:
            payload["notebook_params"] = parameters

        async with self._pool.connection() as client:
            response = await client.post("/api/2.0/jobs/run-now", json=payload)

        if response.status_code == 200:
            return response.json().get("run_id")
        self.log(
            "error",
            f"DataBricks trigger failed: {response.status_code} {response.text}",
        )
        return None

    async def _poll_run(self, run_id: Any) -> tuple:
        """GET run state. Returns (life_cycle_state, result_state, output)."""
        async with self._pool.connection() as client:
            response = await client.get(
                "/api/2.0/jobs/runs/get",
                params={"run_id": run_id},
            )

        if response.status_code != 200:
            return "UNKNOWN", "", {}

        data = response.json()
        state = data.get("state", {})
        return (
            state.get("life_cycle_state", "UNKNOWN"),
            state.get("result_state", ""),
            data.get("output", {}),
        )

    async def health_check(self) -> bool:
        if self._pool is None:
            return False
        try:
            async with self._pool.connection() as client:
                r = await client.get("/api/2.0/clusters/list", timeout=5)
                return r.status_code == 200
        except Exception:
            return False

    async def shutdown(self) -> None:
        if self._pool is not None:
            await self._pool.shutdown()
        self.log("info", "DataBricksJobAdapter shut down.")
