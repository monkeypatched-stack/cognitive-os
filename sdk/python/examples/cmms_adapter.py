"""
CMMS Adapter – Reference Implementation
=========================================

Pattern: API Key + HTTP REST + connection pool

Capability:  create_work_order
System:      Any REST-based CMMS (IBM Maximo, SAP PM, Infor EAM, etc.)

This adapter:
    - Uses APIKeyHandler for authentication
    - Uses HTTPConnectionPool for connection reuse
    - Validates inputs before calling the external system
    - Emits events and metrics on success/failure
    - Implements clean health checks
"""

from datetime import datetime, timezone
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


@capability_adapter(
    capability_id="create_work_order",
    version="1.0.0",
    tags=["cmms", "maintenance", "work-orders"],
)
class CMMSAdapter(CapabilityAdapter):
    """
    Creates work orders in a CMMS via REST API.

    Configuration (config.yaml)
    ---------------------------
    adapters:
      create_work_order:
        enabled: true
        endpoint: https://cmms.example.com
        credentials:
          api_key: ${CMMS_API_KEY}
        timeout: 45
        extra:
          work_order_prefix: WO
          company_id: "100"
    """

    def __init__(self, adapter_id: str, capability_id: str) -> None:
        super().__init__(adapter_id, capability_id)
        self._pool: HTTPConnectionPool | None = None
        self._auth: APIKeyHandler | None = None

    async def initialize(self) -> None:
        """Set up HTTP connection pool and validate configuration."""
        endpoint = self.config.get("endpoint")
        if not endpoint:
            raise AdapterInitializationError("CMMSAdapter requires 'endpoint' in configuration.")

        credentials = self.config.get("credentials", {})
        api_key = credentials.get("api_key", "")

        if not api_key:
            raise AdapterInitializationError("CMMSAdapter requires 'credentials.api_key' in configuration.")

        self._auth = APIKeyHandler(api_key=api_key)

        self._pool = HTTPConnectionPool(
            base_url=endpoint,
            auth_handler=self._auth,
            pool_size=5,
            timeout=int(self.config.get("timeout", 45)),
            health_path="/health",
        )
        await self._pool.initialize()
        self.log("info", "CMMSAdapter initialized.", endpoint=endpoint)

    async def execute(
        self,
        context: AdapterContext,
        inputs: Dict[str, Any],
    ) -> AdapterResponse:
        """
        Create a work order in the CMMS.

        Required inputs
        ---------------
        equipment_id : str  – asset to create work order for
        work_type    : str  – "preventive" | "corrective" | "inspection"
        description  : str  – work order description

        Optional inputs
        ---------------
        priority     : str  – "Low" | "Medium" | "High" | "Critical"
        due_date     : str  – ISO 8601 date string
        assigned_to  : str  – technician ID or team name
        """
        # --- Input validation ---
        required = ("equipment_id", "work_type", "description")
        missing = [k for k in required if not inputs.get(k)]
        if missing:
            return AdapterResponse.fail(f"Missing required inputs: {missing}")

        prefix = self.config.get("work_order_prefix", "WO")
        company_id = self.config.get("company_id", "")

        payload = {
            "equipment_id": inputs["equipment_id"],
            "work_type": inputs["work_type"],
            "description": inputs["description"],
            "priority": inputs.get("priority", "Medium"),
            "due_date": inputs.get("due_date"),
            "assigned_to": inputs.get("assigned_to"),
            "workflow_id": context.workflow_id,
            "execution_id": context.execution_id,
            "reference_prefix": prefix,
            "company_id": company_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        try:
            async with await self.telemetry.start_span("cmms_create_work_order") as span:
                span.set_tag("equipment_id", inputs["equipment_id"])
                span.set_tag("work_type", inputs["work_type"])

                async with self._pool.connection() as client:
                    response = await client.post(
                        "/api/work-orders",
                        json=payload,
                    )

                if response.status_code == 201:
                    data = response.json()

                    await self.emit_event(
                        "work_order_created",
                        {
                            "work_order_id": data["id"],
                            "equipment_id": inputs["equipment_id"],
                            "workflow_id": context.workflow_id,
                        },
                    )
                    await self.emit_metric(
                        "work_orders_created",
                        1.0,
                        tags={"equipment": inputs["equipment_id"]},
                    )

                    return AdapterResponse.ok(
                        result={
                            "work_order_id": data["id"],
                            "status": data.get("status", "created"),
                            "reference": data.get("reference"),
                        }
                    )

                error_text = response.text
                await self.emit_metric("work_order_errors", 1.0)
                return AdapterResponse.fail(f"CMMS API error {response.status_code}: {error_text}")

        except Exception as exc:
            self.log("error", f"Work order creation failed: {exc}")
            await self.emit_metric("work_order_errors", 1.0)
            return AdapterResponse.fail(str(exc))

    async def health_check(self) -> bool:
        """Ping the CMMS health endpoint."""
        if self._pool is None:
            return False
        try:
            async with self._pool.connection() as client:
                response = await client.get("/health", timeout=5)
                return response.status_code == 200
        except Exception:
            return False

    async def shutdown(self) -> None:
        """Shut down the HTTP connection pool."""
        if self._pool is not None:
            await self._pool.shutdown()
        self.log("info", "CMMSAdapter shut down.")
