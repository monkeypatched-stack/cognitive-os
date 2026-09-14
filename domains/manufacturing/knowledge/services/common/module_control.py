from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, Request, status
from motor.motor_asyncio import AsyncIOMotorDatabase

POLICY_COLLECTION = "module_control_policy"
MODULE_AUDIT_COLLECTION = "module_control_audit"

LIMIT_COLLECTIONS = {
    "max_nodes": ["process_definition_nodes", "workflow_nodes"],
    "max_users": ["users"],
    "max_lines": ["industrial_lines"],
    "max_stages": ["industrial_stages"],
    "max_workstations": ["workstations"],
    "max_machines": ["pharmaceutical_machines"],
    "max_equipment": ["equipment"],
    "max_workorders": ["work_orders"],
}

ENDPOINT_MODULE_PREFIXES = {
    "/api/v1/plants": "facilities",
    "/api/v1/lines": "facilities",
    "/api/v1/stages": "facilities",
    "/api/v1/workstations": "facilities",
    "/api/v1/workstation-status": "workstation_status",
    "/api/v1/workstation-live-state": "workstation_status",
    "/api/v1/workstation-live-states": "workstation_status",
    "/api/v1/machines": "assets",
    "/api/v1/equipment": "assets",
    "/api/v1/devices": "assets",
    "/api/v1/ble-devices": "iot",
    "/api/v1/sensors": "iot",
    "/api/v1/servers": "iot",
    "/api/v1/uwb-devices": "iot",
    "/api/v1/maintenance": "maintenance",
    "/api/v1/calibrations": "maintenance",
    "/api/v1/cleaning": "maintenance",
    "/api/v1/workorders": "workorders",
    "/api/v1/process-definitions": "process_definitions",
    "/api/v1/document-metadata": "documents",
    "/api/v1/documents": "documents",
    "/api/v1/document-workflows": "documents",
    "/api/v1/report-templates": "documents",
    "/api/v1/products": "product_management",
    "/api/v1/boms": "product_management",
    "/api/v1/product-research": "product_management",
    "/api/v1/tag-groups": "product_management",
    "/api/v1/customer-details": "customers",
    "/api/v1/customer-metadata": "customers",
    "/api/v1/customer-order-metrics": "customers",
    "/api/v1/customer-payment-data": "customers",
    "/api/v1/inventory-items": "wms",
    "/api/v1/inventory-item-counts": "wms",
    "/api/v1/inventory-item-quantities": "wms",
    "/api/v1/inventory-stage-outputs": "wms",
    "/api/v1/inventory-transactions": "wms",
    "/api/v1/inventory-allocations": "wms",
    "/api/v1/stock-adjustment-requests": "wms",
    "/api/v1/warehouse": "wms",
    "/api/v1/warehouse-cycle-counts": "wms",
    "/api/v1/warehouse-locations": "wms",
    "/api/v1/warehouse-execution": "wms",
    "/api/v1/orders": "order_management",
    "/api/v1/invoices": "order_management",
    "/api/v1/order-details": "order_management",
    "/api/v1/order-customer-metrics": "order_management",
    "/api/v1/order-metadata": "order_management",
    "/api/v1/order-payment-metadata": "order_management",
    "/api/v1/sales-orders": "order_management",
    "/api/v1/purchase-orders": "supply_chain",
    "/api/v1/purchase-order-shipping-information": "supply_chain",
    "/api/v1/goods-receipts": "supply_chain",
    "/api/v1/sampling": "supply_chain",
    "/api/v1/quality-control": "supply_chain",
    "/api/v1/replenishment": "supply_chain",
    "/api/v1/supply-allocation": "supply_chain",
    "/api/v1/shipping-information": "supply_chain",
    "/api/v1/carriers": "supply_chain",
    "/api/v1/vehicles": "supply_chain",
    "/api/v1/pallets": "supply_chain",
    "/api/v1/packages": "supply_chain",
    "/api/v1/delivery-notes": "supply_chain",
    "/api/v1/routes": "supply_chain",
    "/api/v1/customs-declarations": "supply_chain",
    "/api/v1/shipping-providers": "supply_chain",
    "/api/v1/shipping-provider-metadata": "supply_chain",
    "/api/v1/waybills": "supply_chain",
    "/api/v1/supplier-details": "suppliers",
    "/api/v1/supplier-capabilities": "suppliers",
    "/api/v1/supplier-certifications": "suppliers",
    "/api/v1/supplier-financials": "suppliers",
    "/api/v1/supplier-inventory": "suppliers",
    "/api/v1/supplier-locations": "suppliers",
    "/api/v1/supplier-pricing": "suppliers",
    "/api/v1/supplier-quality": "suppliers",
    "/api/v1/supplier-shipping": "suppliers",
    "/api/v1/shift-templates": "shift_management",
    "/api/v1/shifts": "shift_management",
    "/api/v1/timesheets": "shift_management",
    "/api/v1/changeovers": "changeover_management",
    "/api/v1/agentos": "agentos",
    "/api/v1/module-control": "module_control",
}

MODULE_CATALOG = {
    "data_layer": {"tier": "core", "surface": "platform"},
    "agentos": {"tier": "core", "surface": "reasoning"},
    "world_model_reasoning": {"tier": "core", "surface": "reasoning"},
    "simulation": {"tier": "core", "surface": "simulation"},
    "action_layer": {"tier": "core", "surface": "approved_actions"},
    "agents": {"tier": "core", "surface": "approved_agents"},
    "feedback": {"tier": "core", "surface": "learning"},
    "facilities": {"tier": "core", "surface": "production_shop_floor"},
    "assets": {"tier": "core", "surface": "production_shop_floor"},
    "maintenance": {"tier": "core", "surface": "production_shop_floor"},
    "workorders": {"tier": "core", "surface": "production_shop_floor"},
    "process_definitions": {"tier": "core", "surface": "production_shop_floor"},
    "documents": {"tier": "core", "surface": "production_shop_floor"},
    "module_control": {"tier": "core", "surface": "control_plane"},
    "wms": {"tier": "add_on", "surface": "warehouse"},
    "iot": {"tier": "add_on", "surface": "iot"},
    "changeover_management": {"tier": "add_on", "surface": "changeover_management"},
    "shift_management": {"tier": "add_on", "surface": "shift_management"},
    "workstation_status": {"tier": "add_on", "surface": "workstation_status"},
    "order_management": {"tier": "add_on", "surface": "order_management"},
    "product_management": {"tier": "add_on", "surface": "product_management"},
    "customers": {"tier": "add_on", "surface": "customer_management"},
    "suppliers": {"tier": "add_on", "surface": "supplier_management"},
    "supply_chain": {"tier": "add_on", "surface": "supply_chain"},
}

DEFAULT_MODULES = {
    "data_layer": True,
    "agentos": True,
    "world_model_reasoning": True,
    "simulation": True,
    "action_layer": True,
    "agents": True,
    "feedback": True,
    "facilities": True,
    "assets": True,
    "maintenance": True,
    "workorders": True,
    "process_definitions": True,
    "documents": True,
    "module_control": True,
    "wms": False,
    "iot": False,
    "changeover_management": False,
    "shift_management": False,
    "workstation_status": False,
    "order_management": False,
    "product_management": False,
    "customers": False,
    "suppliers": False,
    "supply_chain": False,
}

INTEGRATION_CATALOG = (
    {"id": "sap_erp", "name": "SAP", "category": "ERP / MES"},
    {"id": "oracle_erp", "name": "Oracle", "category": "ERP / SCM"},
    {
        "id": "microsoft_dynamics_365",
        "name": "Microsoft Dynamics 365",
        "category": "ERP / CRM",
    },
    {"id": "salesforce", "name": "Salesforce", "category": "CRM"},
    {"id": "servicenow", "name": "ServiceNow", "category": "ITSM / Workflow"},
    {"id": "workday", "name": "Workday", "category": "HRIS / Finance"},
    {"id": "netsuite", "name": "NetSuite", "category": "ERP"},
    {"id": "odoo", "name": "Odoo", "category": "ERP"},
    {"id": "epicor", "name": "Epicor", "category": "Manufacturing ERP"},
    {"id": "infor", "name": "Infor", "category": "ERP / WMS"},
    {"id": "ifs", "name": "IFS", "category": "EAM / ERP"},
    {"id": "siemens_teamcenter", "name": "Siemens Teamcenter", "category": "PLM"},
    {"id": "siemens_opcenter", "name": "Siemens Opcenter", "category": "MES"},
    {
        "id": "rockwell_factorytalk",
        "name": "Rockwell FactoryTalk",
        "category": "SCADA / MES",
    },
    {"id": "ptc_thingworx", "name": "PTC ThingWorx", "category": "IIoT"},
    {"id": "aveva_pi", "name": "AVEVA PI", "category": "Historian"},
    {"id": "ignition", "name": "Ignition", "category": "SCADA"},
    {"id": "ge_proficy", "name": "GE Proficy", "category": "MES / Historian"},
    {"id": "honeywell_forge", "name": "Honeywell Forge", "category": "Industrial IoT"},
    {"id": "kepware", "name": "Kepware", "category": "OPC / PLC Gateway"},
    {"id": "aws_iot", "name": "AWS IoT", "category": "Cloud IoT"},
    {"id": "azure_iot", "name": "Azure IoT", "category": "Cloud IoT"},
    {"id": "google_cloud", "name": "Google Cloud", "category": "Cloud / Data"},
    {"id": "snowflake", "name": "Snowflake", "category": "Data Warehouse"},
    {"id": "databricks", "name": "Databricks", "category": "Lakehouse"},
    {"id": "power_bi", "name": "Power BI", "category": "Analytics"},
    {"id": "tableau", "name": "Tableau", "category": "Analytics"},
    {"id": "looker", "name": "Looker", "category": "Analytics"},
    {"id": "qlik", "name": "Qlik", "category": "Analytics"},
    {"id": "mulesoft", "name": "MuleSoft", "category": "iPaaS"},
    {"id": "boomi", "name": "Boomi", "category": "iPaaS"},
    {"id": "workato", "name": "Workato", "category": "Automation"},
    {"id": "zapier", "name": "Zapier", "category": "Automation"},
    {"id": "n8n", "name": "n8n", "category": "Workflow Automation"},
    {"id": "node_red", "name": "Node-RED", "category": "Flow Automation"},
    {"id": "apache_kafka", "name": "Apache Kafka", "category": "Event Streaming"},
    {"id": "rabbitmq", "name": "RabbitMQ", "category": "Messaging"},
    {"id": "nats", "name": "NATS", "category": "Messaging"},
    {"id": "slack", "name": "Slack", "category": "Collaboration"},
    {"id": "microsoft_teams", "name": "Microsoft Teams", "category": "Collaboration"},
    {"id": "gmail", "name": "Gmail", "category": "Email"},
    {"id": "whatsapp", "name": "WhatsApp", "category": "Messaging"},
    {"id": "twilio", "name": "Twilio", "category": "SMS / Voice"},
    {"id": "jira", "name": "Jira", "category": "Work Management"},
    {"id": "github", "name": "GitHub", "category": "Developer Platform"},
    {"id": "gitlab", "name": "GitLab", "category": "DevOps"},
    {"id": "zendesk", "name": "Zendesk", "category": "Support"},
    {"id": "shopify", "name": "Shopify", "category": "Commerce"},
    {"id": "stripe", "name": "Stripe", "category": "Payments"},
    {"id": "fedex", "name": "FedEx", "category": "Carrier"},
    {"id": "ups", "name": "UPS", "category": "Carrier"},
    {"id": "dhl", "name": "DHL", "category": "Carrier"},
)

DIRECT_TRANSFORMER_CATALOG_IDS = {"sap_erp", "oracle_erp"}

LEGACY_ADAPTER_INTEGRATIONS = {
    "veeva_quality": True,
    "generic_lims": True,
    "document_management": True,
    "wms": True,
    "cmms": True,
}

DEFAULT_INTEGRATIONS = {
    **{
        integration["id"]: integration["id"] in DIRECT_TRANSFORMER_CATALOG_IDS
        for integration in INTEGRATION_CATALOG
    },
    **LEGACY_ADAPTER_INTEGRATIONS,
}


def default_integration_catalog() -> list[dict[str, Any]]:
    return [
        {
            **dict(integration),
            "direct_transformer": integration["id"] in DIRECT_TRANSFORMER_CATALOG_IDS,
        }
        for integration in INTEGRATION_CATALOG
    ]


DEFAULT_LIMITS = {
    "max_nodes": 10000,
    "max_users": 1000,
    "max_lines": 100,
    "max_stages": 500,
    "max_workstations": 2000,
    "max_machines": 5000,
    "max_equipment": 10000,
    "max_workorders": 100000,
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def default_module_control_policy() -> dict[str, Any]:
    now = utc_now()
    return {
        "policy_id": "singleton",
        "version": 1,
        "modules": dict(DEFAULT_MODULES),
        "integrations": dict(DEFAULT_INTEGRATIONS),
        "integration_catalog": default_integration_catalog(),
        "module_catalog": dict(MODULE_CATALOG),
        "limits": dict(DEFAULT_LIMITS),
        "endpoint_module_prefixes": dict(ENDPOINT_MODULE_PREFIXES),
        "limit_collections": {
            key: list(value) for key, value in LIMIT_COLLECTIONS.items()
        },
        "created_at": now,
        "updated_at": now,
        "updated_by": "system",
    }


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _serialize(document: dict[str, Any]) -> dict[str, Any]:
    document = dict(document)
    document.pop("_id", None)
    return document


async def get_module_control_policy(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    policy = await db[POLICY_COLLECTION].find_one({"policy_id": "singleton"})
    if policy:
        merged = default_module_control_policy()
        merged.update(_serialize(policy))
        merged["modules"] = {**DEFAULT_MODULES, **(policy.get("modules") or {})}
        merged["integrations"] = {
            **DEFAULT_INTEGRATIONS,
            **(policy.get("integrations") or {}),
        }
        merged["integration_catalog"] = list(
            policy.get("integration_catalog") or default_integration_catalog()
        )
        merged["module_catalog"] = {
            **MODULE_CATALOG,
            **(policy.get("module_catalog") or {}),
        }
        merged["limits"] = {**DEFAULT_LIMITS, **(policy.get("limits") or {})}
        merged["endpoint_module_prefixes"] = {
            **ENDPOINT_MODULE_PREFIXES,
            **(policy.get("endpoint_module_prefixes") or {}),
        }
        merged["limit_collections"] = {
            **LIMIT_COLLECTIONS,
            **(policy.get("limit_collections") or {}),
        }
        return merged
    policy = default_module_control_policy()
    await db[POLICY_COLLECTION].insert_one(policy)
    return _serialize(policy)


async def save_module_control_policy(
    db: AsyncIOMotorDatabase,
    patch: dict[str, Any],
    *,
    actor: str = "system",
) -> dict[str, Any]:
    policy = await get_module_control_policy(db)
    next_policy = dict(policy)
    for key in (
        "modules",
        "integrations",
        "module_catalog",
        "limits",
        "endpoint_module_prefixes",
        "limit_collections",
    ):
        if key in patch and isinstance(patch[key], dict):
            next_policy[key] = {**(next_policy.get(key) or {}), **patch[key]}
    if "integration_catalog" in patch and isinstance(
        patch["integration_catalog"], list
    ):
        next_policy["integration_catalog"] = list(patch["integration_catalog"])
    next_policy["version"] = int(next_policy.get("version") or 1) + 1
    next_policy["updated_at"] = utc_now()
    next_policy["updated_by"] = actor
    await db[POLICY_COLLECTION].replace_one(
        {"policy_id": "singleton"}, next_policy, upsert=True
    )
    await db[MODULE_AUDIT_COLLECTION].insert_one(
        {
            "event_type": "module-control-policy-updated",
            "actor": actor,
            "patch": patch,
            "version": next_policy["version"],
            "created_at": utc_now(),
        }
    )
    return _serialize(next_policy)


def module_for_path(path: str, policy: dict[str, Any]) -> str | None:
    prefixes = policy.get("endpoint_module_prefixes") or {}
    matches = sorted(
        (
            (prefix, module_id)
            for prefix, module_id in prefixes.items()
            if path.startswith(prefix)
        ),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    return str(matches[0][1]) if matches else None


async def evaluate_module_control(
    db: AsyncIOMotorDatabase,
    *,
    module_id: str | None = None,
    path: str | None = None,
    integration_id: str | None = None,
    limit_key: str | None = None,
    collection: str | None = None,
) -> dict[str, Any]:
    policy = await get_module_control_policy(db)
    resolved_module = module_id or (module_for_path(path, policy) if path else None)
    decisions: list[dict[str, Any]] = []
    allowed = True

    if resolved_module:
        enabled = bool((policy.get("modules") or {}).get(resolved_module, False))
        allowed = allowed and enabled
        decisions.append({"kind": "module", "id": resolved_module, "allowed": enabled})

    if integration_id:
        enabled = bool((policy.get("integrations") or {}).get(integration_id, False))
        allowed = allowed and enabled
        decisions.append(
            {"kind": "integration", "id": integration_id, "allowed": enabled}
        )

    if limit_key:
        limit = int((policy.get("limits") or {}).get(limit_key, 0) or 0)
        collections = (
            [collection]
            if collection
            else list((policy.get("limit_collections") or {}).get(limit_key) or [])
        )
        current = 0
        for name in collections:
            current += await db[name].count_documents({})
        within_limit = limit <= 0 or current < limit
        allowed = allowed and within_limit
        decisions.append(
            {
                "kind": "limit",
                "id": limit_key,
                "allowed": within_limit,
                "current": current,
                "limit": limit,
            }
        )

    return {
        "allowed": allowed,
        "module": resolved_module,
        "decisions": decisions,
        "policy_version": policy.get("version"),
    }


async def require_module_control_allowed(
    db: AsyncIOMotorDatabase,
    *,
    module_id: str | None = None,
    path: str | None = None,
    integration_id: str | None = None,
    limit_key: str | None = None,
    collection: str | None = None,
) -> dict[str, Any]:
    evaluation = await evaluate_module_control(
        db,
        module_id=module_id,
        path=path,
        integration_id=integration_id,
        limit_key=limit_key,
        collection=collection,
    )
    if not evaluation["allowed"]:
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail={"message": "Blocked by module control policy", **evaluation},
        )
    return evaluation


def module_control_dependency(
    module_id: str | None = None,
    limit_key: str | None = None,
    collection: str | None = None,
):
    async def _dependency(request: Request):
        db = (
            request.app.state.database
            if hasattr(request.app.state, "database")
            else None
        )
        if db is None:
            return None
        return await require_module_control_allowed(
            db,
            module_id=module_id,
            path=request.url.path,
            limit_key=limit_key,
            collection=collection,
        )

    return _dependency
