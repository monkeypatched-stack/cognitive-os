from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi.encoders import jsonable_encoder

from services.common.compliance import document_record_id, utc_now

SUPPLY_CHAIN_COLLECTIONS = {
    "sales_orders",
    "purchase_orders",
    "goods_receipts",
    "inventory_allocations",
    "inventory_transactions",
    "inventory_item_quantities",
    "product_inventory",
    "supplier_inventory",
    "work_orders",
    "material_inward_logs",
    "warehouse_dispensing_records",
    "inventory_disposition_records",
    "warehouse_cycle_counts",
    "stock_adjustment_requests",
    "shipping_information",
    "delivery_notes",
    "packages",
    "pallets",
    "routes",
    "waybills",
}


STATUS_EVENT_PREFIX = {
    "sales_orders": "sales_order",
    "purchase_orders": "purchase_order",
    "goods_receipts": "goods_receipt",
    "inventory_allocations": "inventory",
    "work_orders": "work_order",
    "material_inward_logs": "material_inward",
    "warehouse_dispensing_records": "warehouse_dispensing",
    "inventory_disposition_records": "inventory_disposition",
    "warehouse_cycle_counts": "warehouse_cycle_count",
    "stock_adjustment_requests": "stock_adjustment",
    "shipping_information": "shipping",
    "delivery_notes": "delivery_note",
    "packages": "package",
    "pallets": "pallet",
    "routes": "route",
    "waybills": "waybill",
}


STATUS_FIELD_BY_COLLECTION = {
    "shipping_information": "shipment_status",
}


STATUS_EVENT_ALIASES = {
    ("inventory_allocations", "allocated"): "inventory_allocated",
    ("inventory_allocations", "picked"): "inventory_picked",
    ("inventory_allocations", "delivered"): "inventory_delivered",
    ("goods_receipts", "released"): "goods_receipt_released",
    ("goods_receipts", "qc-hold"): "goods_receipt_qc_hold",
    ("goods_receipts", "rejected"): "goods_receipt_rejected",
    ("sales_orders", "confirmed"): "sales_order_confirmed",
    ("sales_orders", "allocated"): "sales_order_allocated",
    ("sales_orders", "shipped"): "sales_order_shipped",
    ("work_orders", "completed"): "work_order_completed",
    ("work_orders", "done"): "work_order_completed",
    ("delivery_notes", "dispatched"): "dispatch_confirmed",
    ("shipping_information", "dispatched"): "dispatch_confirmed",
    ("shipping_information", "shipped"): "dispatch_confirmed",
}


INTERESTING_STATUS_VALUES = {
    "approved",
    "allocated",
    "cancelled",
    "closed",
    "completed",
    "confirmed",
    "delivered",
    "dispatched",
    "done",
    "in-progress",
    "in-transit",
    "issued",
    "open",
    "partial",
    "partially-delivered",
    "picked",
    "qc-hold",
    "ready-for-next-stage",
    "received",
    "rejected",
    "released",
    "reserved",
    "shipped",
    "submitted",
}


INVENTORY_TRANSACTION_EVENT_ALIASES = {
    "receipt": "inventory_receipt_recorded",
    "shipment": "inventory_shipment_recorded",
    "reservation": "inventory_reserved",
    "release": "inventory_released",
    "transfer-in": "inventory_transfer_recorded",
    "transfer-out": "inventory_transfer_recorded",
    "adjustment": "inventory_adjustment_recorded",
    "cycle-count": "inventory_cycle_count_recorded",
    "return": "inventory_return_recorded",
    "write-off": "inventory_write_off_recorded",
}


WAREHOUSE_WORKFLOW_ACTIONS = {
    "sales_order_confirmed": "warehouse task",
    "sales_order_allocated": "pick pack",
    "goods_receipt_released": "quarantine release rejection",
    "goods_receipt_qc_hold": "quarantine release rejection",
    "goods_receipt_rejected": "quarantine release rejection",
    "inventory_allocated": "pick pack",
    "inventory_picked": "dispatch handoff",
    "inventory_reserved": "inventory allocation",
    "inventory_released": "dispensing record",
    "inventory_receipt_recorded": "receiving",
    "inventory_shipment_recorded": "dispatch handoff",
    "inventory_transfer_recorded": "inventory transaction",
    "inventory_adjustment_recorded": "stock adjustment",
    "inventory_cycle_count_recorded": "cycle count",
    "inventory_transaction_recorded": "inventory transaction",
    "inventory_transaction_posted": "inventory transaction",
    "material_inward_received": "material inward log",
    "warehouse_dispensing_released": "dispensing record",
    "inventory_disposition_release": "quarantine release rejection",
    "inventory_disposition_rejection": "quarantine release rejection",
    "warehouse_cycle_count_completed": "cycle count",
    "stock_adjustment_approved": "stock adjustment",
    "work_order_completed": "warehouse task",
}


WAREHOUSE_ACTION_NODES = {
    "warehouse task": ("wh-task", "Warehouse Task Intake"),
    "receiving": ("wh-receiving", "Receiving"),
    "material inward log": ("wh-inward", "Material Inward Logs"),
    "quarantine release rejection": ("wh-disposition", "Quarantine Release Rejection"),
    "dispensing record": ("wh-dispensing", "Dispensing Records"),
    "inventory allocation": ("wh-allocation", "Inventory Allocation"),
    "inventory transaction": ("wh-allocation", "Inventory Allocation"),
    "pick pack": ("wh-pick-pack", "Pick Pack Execute"),
    "cycle count": ("wh-cycle-count", "Cycle Count Check"),
    "stock adjustment": ("wh-adjust", "Stock Adjustment"),
    "dispatch handoff": ("wh-dispatch", "Dispatch Handoff"),
}


WAREHOUSE_NODE_CONFIGS = {
    "wh-queue": {
        "source": "external_nats_queue",
        "subject": "indus.warehouse.queue",
        "queue": "indus-warehouse-websocket",
        "exception_subject": "indus.warehouse.exceptions",
        "exception_queue": "indus-warehouse-exceptions",
        "websocket_path": "/api/v1/ws/events",
        "target_node": "Warehouse Task Intake",
    },
    "wh-task": {
        "source": "warehouse_operations",
        "collections": [
            "goods_receipts",
            "inventory_items",
            "sales_orders",
            "work_orders",
        ],
        "output": "warehouse_task",
    },
    "wh-products": {"endpoint": "/api/v1/products", "output": "product_context"},
    "wh-bom": {"endpoint": "/api/v1/boms", "output": "bom_context"},
    "wh-components": {
        "endpoint": "/api/v1/products/components",
        "output": "component_context",
    },
    "wh-storage-req": {
        "endpoint": "/api/v1/warehouse/special-storage-requirements",
        "temperature_control_check": True,
        "segregation_check": True,
        "output": "storage_requirements",
    },
    "wh-documents": {"endpoint": "/api/v1/documents", "output": "warehouse_documents"},
    "wh-assets": {
        "machine_endpoint": "/api/v1/machines",
        "robot_filter": {"machine_type": "Industrial Robot"},
        "output": "warehouse_machines_robots",
    },
    "wh-equipment": {
        "equipment_endpoint": "/api/v1/equipment",
        "output": "warehouse_equipment",
    },
    "wh-receiving": {
        "endpoint": "/api/v1/goods-receipts",
        "dock_check": True,
        "output": "receiving_event",
    },
    "wh-grn": {
        "endpoint": "/api/v1/goods-receipts",
        "record_type": "GRN",
        "output": "grn_record",
    },
    "wh-inward": {
        "endpoint": "/api/v1/warehouse/material-inward-logs",
        "output": "material_inward_log",
    },
    "wh-labels": {
        "endpoint": "/api/v1/warehouse/status-labels",
        "labels": ["Quarantine", "Release", "Rejection"],
        "print_required": True,
        "output": "status_label",
    },
    "wh-disposition": {
        "endpoint": "/api/v1/warehouse/quarantine-release-rejection-records",
        "signature_required": True,
        "qa_review_required": True,
        "output": "material_disposition",
    },
    "wh-dispensing": {
        "endpoint": "/api/v1/warehouse/dispensing-records",
        "signature_required": True,
        "scale_required": True,
        "output": "dispensing_record",
    },
    "wh-allocation": {
        "endpoint": "/api/v1/inventory-allocations",
        "output": "allocated_inventory",
        "reservation_required": True,
    },
    "wh-location": {
        "endpoint": "/api/v1/warehouse-locations",
        "location_hierarchy": ["zone", "area", "aisle", "rack", "bin"],
        "temperature_control_check": True,
    },
    "wh-pick-pack": {
        "scan_required": True,
        "device_type": "Warehouse Scanner",
        "output": "picked_inventory",
    },
    "wh-cycle-count": {
        "endpoint": "/api/v1/warehouse-cycle-counts",
        "variance_check": True,
        "output": "cycle_count_result",
    },
    "wh-adjust": {
        "endpoint": "/api/v1/stock-adjustment-requests",
        "approval_required": True,
        "output": "stock_adjustment_request",
    },
    "wh-dispatch": {
        "endpoint": "/api/v1/shipping-information",
        "handoff_to": "shipping",
        "output": "dispatch_ready",
    },
}


WAREHOUSE_MAIN_PATH = [
    "Warehouse Queue",
    "Warehouse Task Intake",
    "Products",
    "BOM",
    "Components",
    "Special Storage Requirements",
    "Receiving",
    "GRN Records",
    "Material Inward Logs",
    "Status Labels",
    "Quarantine Release Rejection",
    "Dispensing Records",
    "Inventory Allocation",
    "Location Assignment",
    "Pick Pack Execute",
    "Cycle Count Check",
]


WAREHOUSE_CANVAS_PATHS = {
    "warehouse task": ["Warehouse Queue", "Warehouse Task Intake"],
    "receiving": WAREHOUSE_MAIN_PATH[:7],
    "material inward log": WAREHOUSE_MAIN_PATH[:9],
    "quarantine release rejection": WAREHOUSE_MAIN_PATH[:11],
    "dispensing record": WAREHOUSE_MAIN_PATH[:12],
    "inventory allocation": WAREHOUSE_MAIN_PATH[:13],
    "inventory transaction": WAREHOUSE_MAIN_PATH[:13],
    "pick pack": WAREHOUSE_MAIN_PATH[:15],
    "cycle count": WAREHOUSE_MAIN_PATH[:16],
    "stock adjustment": [*WAREHOUSE_MAIN_PATH[:16], "Stock Adjustment"],
    "dispatch handoff": [*WAREHOUSE_MAIN_PATH[:16], "Dispatch Handoff"],
}


WAREHOUSE_SUPPORTING_NODES = {
    "receiving": ["Warehouse Documents"],
    "material inward log": ["Warehouse Documents"],
    "quarantine release rejection": ["Warehouse Documents"],
    "dispensing record": ["Warehouse Documents", "Machines Robots", "Equipment"],
    "inventory allocation": ["Warehouse Documents", "Machines Robots", "Equipment"],
    "inventory transaction": ["Warehouse Documents", "Machines Robots", "Equipment"],
    "pick pack": ["Warehouse Documents", "Machines Robots", "Equipment"],
    "cycle count": ["Warehouse Documents", "Machines Robots", "Equipment"],
    "stock adjustment": ["Warehouse Documents", "Machines Robots", "Equipment"],
    "dispatch handoff": ["Warehouse Documents", "Machines Robots", "Equipment"],
}


SHIPPING_WORKFLOW_ACTIONS = {
    "dispatch_confirmed": "dispatch confirmation",
    "delivery_note_dispatched": "delivery note",
    "delivery_note_delivered": "dispatch confirmation",
    "package_dispatched": "package",
    "package_delivered": "package",
    "pallet_dispatched": "pallet",
    "route_completed": "shipping route",
    "waybill_issued": "waybill",
    "waybill_in_transit": "waybill",
    "waybill_delivered": "dispatch confirmation",
    "shipping_dispatched": "dispatch confirmation",
    "shipping_shipped": "dispatch confirmation",
    "shipping_in_transit": "shipping information",
    "shipping_delivered": "dispatch confirmation",
}


SHIPPING_ACTION_NODES = {
    "shipping information": ("ship-info", "Shipping Information"),
    "pallet": ("ship-pallet", "Pallet"),
    "package": ("ship-package", "Package"),
    "delivery note": ("ship-delivery-note", "Delivery Note"),
    "shipping route": ("ship-route", "Shipping Route"),
    "waybill": ("ship-waybill", "Waybill"),
    "dispatch confirmation": ("ship-confirm", "Dispatch Confirmation"),
}


SHIPPING_CANVAS_PATHS = {
    "shipping information": [
        "Shipping Queue",
        "Shipping Intake",
        "Shipping Information",
    ],
    "pallet": ["Shipping Queue", "Shipping Intake", "Shipping Information", "Pallet"],
    "package": [
        "Shipping Queue",
        "Shipping Intake",
        "Shipping Information",
        "Pallet",
        "Package",
    ],
    "delivery note": [
        "Shipping Queue",
        "Shipping Intake",
        "Shipping Information",
        "Pallet",
        "Package",
        "Delivery Note",
    ],
    "shipping route": [
        "Shipping Queue",
        "Shipping Intake",
        "Shipping Information",
        "Pallet",
        "Package",
        "Delivery Note",
        "Shipping Route",
    ],
    "waybill": [
        "Shipping Queue",
        "Shipping Intake",
        "Shipping Information",
        "Delivery Note",
        "Shipping Route",
        "Customs Declaration",
        "Waybill",
    ],
    "dispatch confirmation": [
        "Shipping Queue",
        "Shipping Intake",
        "Shipping Information",
        "Delivery Note",
        "Shipping Route",
        "Waybill",
        "Dispatch Confirmation",
    ],
}


def _normalize_token(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", "-").replace(" ", "-")


def _document(event: dict[str, Any], key: str) -> dict[str, Any]:
    value = event.get(key)
    return value if isinstance(value, dict) else {}


def _record_id(
    collection: str, document: dict[str, Any], event: dict[str, Any]
) -> str | None:
    return (
        str(event.get("record_id"))
        if event.get("record_id")
        else document_record_id(document) or str(document.get("id") or "") or None
    )


def _workflow_route(event_type: str) -> dict[str, Any]:
    warehouse_action = WAREHOUSE_WORKFLOW_ACTIONS.get(event_type)
    if warehouse_action:
        action_node_id, action_node = WAREHOUSE_ACTION_NODES.get(
            warehouse_action, ("wh-task", "Warehouse Task Intake")
        )
        return {
            "domain": "warehouse",
            "layout_id": "process_definition-warehouse-management-canvas",
            "process_definition_id": "process_definition-warehouse-management",
            "subject": "indus.warehouse.queue",
            "target_node": "Warehouse Task Intake",
            "entry_node": "Warehouse Task Intake",
            "action_node_id": f"process_definition-warehouse-management:{action_node_id}",
            "action_node": action_node,
            "action_node_config": WAREHOUSE_NODE_CONFIGS.get(action_node_id, {}),
            "warehouse_action": warehouse_action,
            "workflow_path": WAREHOUSE_CANVAS_PATHS.get(
                warehouse_action,
                ["Warehouse Queue", "Warehouse Task Intake", action_node],
            ),
            "supporting_nodes": WAREHOUSE_SUPPORTING_NODES.get(warehouse_action, []),
            "audit_node": "Warehouse Audit Log",
            "exception_node": "Warehouse Exception Queue",
        }
    shipping_action = SHIPPING_WORKFLOW_ACTIONS.get(event_type)
    if shipping_action:
        action_node_id, action_node = SHIPPING_ACTION_NODES.get(
            shipping_action, ("ship-intake", "Shipping Intake")
        )
        return {
            "domain": "shipping",
            "layout_id": "process_definition-shipping-management-canvas",
            "process_definition_id": "process_definition-shipping-management",
            "subject": "indus.shipping.queue",
            "target_node": "Shipping Intake",
            "entry_node": "Shipping Intake",
            "action_node_id": f"process_definition-shipping-management:{action_node_id}",
            "action_node": action_node,
            "shipping_action": shipping_action,
            "workflow_path": SHIPPING_CANVAS_PATHS.get(
                shipping_action, ["Shipping Queue", "Shipping Intake", action_node]
            ),
            "audit_node": "Shipping Audit Log",
            "exception_node": "Shipping Exception Queue",
        }
    return {
        "domain": "supply_chain",
        "subject": f"indus.events.{event_type.replace('_', '-')}",
    }


def _changed_by_update_payload(event: dict[str, Any], field: str) -> bool:
    update = event.get("update")
    if not isinstance(update, dict):
        return False
    updated_fields = update.get("updatedFields")
    if isinstance(updated_fields, dict) and field in updated_fields:
        return True
    set_fields = update.get("$set")
    if isinstance(set_fields, dict) and field in set_fields:
        return True
    return field in update


def _status_changed(
    event: dict[str, Any], before: dict[str, Any], after: dict[str, Any], field: str
) -> bool:
    if before:
        return _normalize_token(before.get(field)) != _normalize_token(after.get(field))
    operation = _normalize_token(event.get("operation"))
    if "insert" in operation:
        return bool(after.get(field))
    return _changed_by_update_payload(event, field)


def _domain_event(
    *,
    cdc_event: dict[str, Any],
    event_type: str,
    collection: str,
    record: dict[str, Any],
    action: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    workflow = _workflow_route(event_type)
    payload = {
        "collection": collection,
        "record_id": _record_id(collection, record, cdc_event),
        "action": action,
        "document": record,
        "workflow": workflow,
        **(details or {}),
    }
    event = {
        "event_id": f"supply-chain-{uuid4().hex}",
        "event_type": event_type,
        "source": "cdc-domain-mapper",
        "domain": "supply_chain",
        "subject": workflow["subject"],
        "payload_type": "json",
        "payload": payload,
        "workflow": workflow,
        "source_cdc_event_id": cdc_event.get("event_id"),
        "source_collection": collection,
        "source_operation": cdc_event.get("operation"),
        "record_id": payload["record_id"],
        "occurred_at": utc_now(),
    }
    if workflow.get("target_node"):
        event["target_node"] = workflow["target_node"]
    if workflow.get("warehouse_action"):
        event["warehouse_action"] = workflow["warehouse_action"]
    if workflow.get("shipping_action"):
        event["shipping_action"] = workflow["shipping_action"]
    return jsonable_encoder(event)


def _derive_status_event(
    cdc_event: dict[str, Any],
    collection: str,
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any] | None:
    field = STATUS_FIELD_BY_COLLECTION.get(collection, "status")
    if not after or not _status_changed(cdc_event, before, after, field):
        return None
    status = _normalize_token(after.get(field))
    if not status or status not in INTERESTING_STATUS_VALUES:
        return None

    event_type = STATUS_EVENT_ALIASES.get((collection, status))
    if not event_type:
        prefix = STATUS_EVENT_PREFIX.get(collection)
        if not prefix:
            return None
        event_type = f"{prefix}_{status.replace('-', '_')}"

    return _domain_event(
        cdc_event=cdc_event,
        event_type=event_type,
        collection=collection,
        record=after,
        action=status,
        details={
            "field": field,
            "before_status": before.get(field) if before else None,
            "after_status": after.get(field),
        },
    )


def _derive_inventory_transaction_event(
    cdc_event: dict[str, Any], after: dict[str, Any]
) -> dict[str, Any] | None:
    if not after:
        return None
    operation = _normalize_token(cdc_event.get("operation"))
    if "insert" not in operation and not _changed_by_update_payload(
        cdc_event, "posted"
    ):
        return None

    transaction_type = _normalize_token(after.get("transaction_type"))
    event_type = "inventory_transaction_recorded"
    if after.get("posted") is True:
        event_type = "inventory_transaction_posted"
    elif transaction_type:
        event_type = INVENTORY_TRANSACTION_EVENT_ALIASES.get(
            transaction_type, event_type
        )

    return _domain_event(
        cdc_event=cdc_event,
        event_type=event_type,
        collection="inventory_transactions",
        record=after,
        action=transaction_type or "transaction",
        details={
            "transaction_type": after.get("transaction_type"),
            "direction": after.get("direction"),
            "quantity": after.get("quantity"),
            "reference_type": after.get("reference_type"),
            "reference_id": after.get("reference_id"),
            "posted": after.get("posted"),
        },
    )


def derive_supply_chain_domain_events(
    cdc_event: dict[str, Any],
) -> list[dict[str, Any]]:
    collection = str(cdc_event.get("collection") or "").strip()
    if collection not in SUPPLY_CHAIN_COLLECTIONS:
        return []

    before = _document(cdc_event, "before")
    after = _document(cdc_event, "after")
    if not after:
        return []

    events: list[dict[str, Any]] = []
    if collection == "inventory_transactions":
        event = _derive_inventory_transaction_event(cdc_event, after)
    else:
        event = _derive_status_event(cdc_event, collection, before, after)
    if event:
        events.append(event)
    return events
