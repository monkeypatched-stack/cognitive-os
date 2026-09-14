"""
SAP Adapter – Reference Implementation
========================================

Pattern: OAuth2 Client Credentials + HTTP REST

Capabilities:
    create_purchase_order   – Create a PO in SAP MM
    get_material_stock      – Read stock levels from SAP MM/WM

This adapter demonstrates:
    - OAuth2 with automatic token refresh
    - Multiple capabilities via multiple adapters (one class per capability)
    - SAP OData API integration pattern
    - Rich input validation
"""

from typing import Any, Dict

from monkeypatched_sdk import (
    AdapterContext,
    AdapterResponse,
    AuthFactory,
    AuthStrategy,
    CapabilityAdapter,
    HTTPConnectionPool,
    capability_adapter,
)
from monkeypatched_sdk.contracts.exceptions import AdapterInitializationError


@capability_adapter(
    capability_id="create_purchase_order",
    version="1.0.0",
    tags=["sap", "erp", "procurement"],
)
class SAPPurchaseOrderAdapter(CapabilityAdapter):
    """
    Creates Purchase Orders in SAP via the OData API.

    Configuration (config.yaml)
    ---------------------------
    adapters:
      create_purchase_order:
        enabled: true
        endpoint: https://sap.example.com
        credentials:
          oauth_endpoint: https://sap.example.com/oauth/token
          client_id: ${SAP_CLIENT_ID}
          client_secret: ${SAP_CLIENT_SECRET}
          scope: MM.PurchaseOrders.Write
        timeout: 60
        extra:
          company_code: "1000"
          purchasing_org: "1000"
          purchasing_group: "001"
    """

    def __init__(self, adapter_id: str, capability_id: str) -> None:
        super().__init__(adapter_id, capability_id)
        self._pool: HTTPConnectionPool | None = None

    async def initialize(self) -> None:
        endpoint = self.config.get("endpoint")
        credentials = self.config.get("credentials", {})

        if not endpoint:
            raise AdapterInitializationError("SAP endpoint not configured.")

        # Auto-detect OAuth2 from credentials dict
        auth_handler = AuthFactory.from_config(credentials)

        self._pool = HTTPConnectionPool(
            base_url=endpoint,
            auth_handler=auth_handler,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            pool_size=5,
            timeout=int(self.config.get("timeout", 60)),
            health_path="/sap/opu/odata/sap/HEALTH_CHECK_SRV/",
        )
        await self._pool.initialize()
        self.log("info", "SAPPurchaseOrderAdapter initialized.", endpoint=endpoint)

    async def execute(
        self,
        context: AdapterContext,
        inputs: Dict[str, Any],
    ) -> AdapterResponse:
        """
        Create a SAP Purchase Order.

        Required inputs
        ---------------
        vendor_id   : str  – SAP vendor number
        line_items  : list – [{material, quantity, unit, price}]

        Optional inputs
        ---------------
        delivery_date : str  – ISO 8601 date
        payment_terms : str  – SAP payment terms key
        """
        vendor_id = inputs.get("vendor_id")
        line_items = inputs.get("line_items", [])

        if not vendor_id:
            return AdapterResponse.fail("Missing required input: vendor_id")
        if not line_items:
            return AdapterResponse.fail("Missing required input: line_items")

        company_code = self.config.get("company_code", "1000")
        purchasing_org = self.config.get("purchasing_org", "1000")
        purchasing_group = self.config.get("purchasing_group", "001")

        payload = {
            "CompanyCode": company_code,
            "PurchasingOrganization": purchasing_org,
            "PurchasingGroup": purchasing_group,
            "Vendor": vendor_id,
            "DocumentDate": inputs.get("delivery_date", ""),
            "PaymentTerms": inputs.get("payment_terms", ""),
            "to_PurchaseOrderItem": {
                "results": [
                    {
                        "Material": item.get("material"),
                        "OrderQuantity": str(item.get("quantity", 1)),
                        "OrderQuantityUnit": item.get("unit", "EA"),
                        "NetPriceAmount": str(item.get("price", 0)),
                    }
                    for item in line_items
                ]
            },
        }

        try:
            async with await self.telemetry.start_span("sap_create_po") as span:
                span.set_tag("vendor_id", vendor_id)
                span.set_tag("line_item_count", len(line_items))

                async with self._pool.connection() as client:
                    response = await client.post(
                        "/sap/opu/odata/sap/MM_PURCH_REQ_MNGT_SRV/A_PurchaseOrder",
                        json={"d": payload},
                        headers={"X-CSRF-Token": await self._get_csrf_token(client)},
                    )

                if response.status_code in (200, 201):
                    data = response.json().get("d", {})

                    await self.emit_event(
                        "purchase_order_created",
                        {
                            "po_number": data.get("PurchaseOrder"),
                            "vendor_id": vendor_id,
                            "workflow_id": context.workflow_id,
                        },
                    )
                    await self.emit_metric("purchase_orders_created", 1.0)

                    return AdapterResponse.ok(
                        {
                            "po_number": data.get("PurchaseOrder"),
                            "status": "created",
                        }
                    )

                return AdapterResponse.fail(f"SAP API error {response.status_code}: {response.text}")

        except Exception as exc:
            self.log("error", f"PO creation failed: {exc}")
            await self.emit_metric("purchase_order_errors", 1.0)
            return AdapterResponse.fail(str(exc))

    async def _get_csrf_token(self, client) -> str:
        """Fetch SAP CSRF token (required for write operations)."""
        try:
            response = await client.get(
                "/sap/opu/odata/sap/MM_PURCH_REQ_MNGT_SRV/",
                headers={"X-CSRF-Token": "Fetch"},
            )
            return response.headers.get("x-csrf-token", "")
        except Exception:
            return ""

    async def health_check(self) -> bool:
        if self._pool is None:
            return False
        try:
            async with self._pool.connection() as client:
                response = await client.get(
                    "/sap/opu/odata/sap/CATALOGSERVICE/",
                    timeout=5,
                )
                return response.status_code < 500
        except Exception:
            return False

    async def shutdown(self) -> None:
        if self._pool is not None:
            await self._pool.shutdown()
        self.log("info", "SAPPurchaseOrderAdapter shut down.")


@capability_adapter(
    capability_id="get_material_stock",
    version="1.0.0",
    tags=["sap", "erp", "inventory"],
)
class SAPMaterialStockAdapter(CapabilityAdapter):
    """
    Reads material stock levels from SAP.

    Configuration
    -------------
    Same as SAPPurchaseOrderAdapter – shares credentials format.
    """

    def __init__(self, adapter_id: str, capability_id: str) -> None:
        super().__init__(adapter_id, capability_id)
        self._pool: HTTPConnectionPool | None = None

    async def initialize(self) -> None:
        endpoint = self.config.get("endpoint")
        credentials = self.config.get("credentials", {})

        if not endpoint:
            raise AdapterInitializationError("SAP endpoint not configured.")

        auth = AuthFactory.from_config(credentials)
        self._pool = HTTPConnectionPool(
            base_url=endpoint,
            auth_handler=auth,
            headers={"Accept": "application/json"},
            pool_size=3,
            timeout=30,
        )
        await self._pool.initialize()

    async def execute(
        self,
        context: AdapterContext,
        inputs: Dict[str, Any],
    ) -> AdapterResponse:
        """
        Read stock for a material.

        Required inputs
        ---------------
        material_id : str
        plant       : str  – SAP plant code
        """
        material_id = inputs.get("material_id")
        plant = inputs.get("plant")

        if not material_id or not plant:
            return AdapterResponse.fail("Missing required inputs: material_id, plant")

        try:
            async with self._pool.connection() as client:
                response = await client.get(
                    f"/sap/opu/odata/sap/ZSTOCK_SRV/StockSet(Material='{material_id}',Plant='{plant}')",
                )

            if response.status_code == 200:
                data = response.json().get("d", {})
                return AdapterResponse.ok(
                    {
                        "material_id": material_id,
                        "plant": plant,
                        "unrestricted_stock": float(data.get("UnrestrictedStock", 0)),
                        "unit": data.get("BaseUnit", "EA"),
                    }
                )

            return AdapterResponse.fail(f"SAP stock query failed: {response.status_code}")

        except Exception as exc:
            return AdapterResponse.fail(str(exc))

    async def health_check(self) -> bool:
        if self._pool is None:
            return False
        try:
            async with self._pool.connection() as client:
                r = await client.get("/sap/opu/odata/sap/ZSTOCK_SRV/", timeout=5)
                return r.status_code < 500
        except Exception:
            return False

    async def shutdown(self) -> None:
        if self._pool is not None:
            await self._pool.shutdown()
