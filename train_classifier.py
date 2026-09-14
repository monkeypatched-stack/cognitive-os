#!/usr/bin/env python3
"""Train the intent classifier with grounded DB data, then invalidate cache."""

import pymongo
import random

MONGO_URI = "mongodb://localhost:27017"
DB_NAME = "demo"

c = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
db = c[DB_NAME]

# ── Load grounded data ───────────────────────────────────────────────────────

work_orders = list(
    db.work_orders.find(
        {},
        {
            "work_order_id": 1,
            "title": 1,
            "status": 1,
            "priority": 1,
            "work_order_type": 1,
            "_id": 0,
        },
    )
)
batches = list(
    db.production_batches.find({}, {"batch_id": 1, "batch_number": 1, "product_name": 1, "status": 1, "_id": 0})
)
approvals = list(db.approvals.find({}, {"approval_id": 1, "title": 1, "status": 1, "type": 1, "_id": 0}))
change_ctrls = list(db.change_controls.find({}, {"change_control_id": 1, "title": 1, "status": 1, "type": 1, "_id": 0}))
instruments = list(db.instruments.find({}, {"name": 1, "serial_number": 1, "_id": 0}))
sops = list(db.sops.find({}, {"id": 1, "title": 1, "entity_name": 1, "_id": 0}))
departments = list(db.departments.find({}, {"name": 1, "_id": 0}))
workers = list(db.workers.find({}, {"name": 1, "title": 1, "role": 1, "_id": 0}))
bers = list(db.batch_production_execution_records.find({}, {"batch_execution_record_id": 1, "status": 1, "_id": 0}))


def pick(lst):
    return random.choice(lst) if lst else {}


def pick_name(lst, key):
    d = pick(lst)
    return d.get(key, "UNKNOWN")


print(
    f"Loaded: {len(work_orders)} WOs, {len(batches)} batches, {len(approvals)} approvals, "
    f"{len(change_ctrls)} CCs, {len(instruments)} instruments, {len(sops)} SOPs, "
    f"{len(departments)} depts, {len(workers)} workers, {len(bers)} BERs"
)

# ── Build grounded examples ──────────────────────────────────────────────────

grounded = {
    # ── Batch Record ──────────────────────────────────────────────────────────
    "batch_record": [],
    # ── Work Order Query ───────────────────────────────────────────────────────
    "work_order_query": [],
    # ── Work Order Create ──────────────────────────────────────────────────────
    "work_order_create": [],
    # ── Approval Query ─────────────────────────────────────────────────────────
    "approval_query": [],
    # ── Change Control ─────────────────────────────────────────────────────────
    "change_control": [],
    "change_control_query": [],
    # ── Production KPI ─────────────────────────────────────────────────────────
    "production_kpi": [],
    # ── SOP Compliance ─────────────────────────────────────────────────────────
    "sop_compliance": [],
    # ── Warehouse Shipping ─────────────────────────────────────────────────────
    "warehouse_shipping": [],
    # ── Drug Research ──────────────────────────────────────────────────────────
    "drug_research": [],
    # ── Worker ─────────────────────────────────────────────────────────────────
    "worker": [],
    # ── Audit Log ──────────────────────────────────────────────────────────────
    "audit_log": [],
    # ── Decision Intelligence ──────────────────────────────────────────────────
    "decision_intelligence": [],
}

# Batch Record examples
for b in batches[:30]:
    bn = b.get("batch_number", "BATCH-001")
    pn = b.get("product_name", "Tablets")
    bs = b.get("status", "Created")
    grounded["batch_record"].extend(
        [
            f"What is the status of batch {bn}?",
            f"Show me the batch record for {bn}",
            f"Show batch {bn}",
            f"Tell me about batch {bn}",
            f"Batch {bn} details",
            f"What is batch {bn} status?",
            f"Get batch record for {bn}",
            f"Show batch record for lot {bn}",
            f"How is batch {bn} doing?",
            f"Status of production batch {bn}",
            f"List all batches for {pn}",
            f"What batches are there for {pn}?",
            f"Find batches for product {pn}",
            f"Show all {pn} batches",
            f"How many batches do we have?",
            f"List all production batches",
            f"Show me all batches",
            f"Count total batches",
            f"What batches are in {bs} status?",
            f"List batches with status {bs}",
            f"Show batches in progress",
            f"What batches are on hold?",
            f"List all in-progress batches",
            f"Show batch execution record {pick_name(bers, 'batch_execution_record_id')}",
            f"What is the yield for batch {bn}?",
            f"Show batch history for {bn}",
            f"When was batch {bn} created?",
            f"What product is in batch {bn}?",
            f"Which line is batch {bn} on?",
        ]
    )

# Work Order Query examples
wo_statuses = list({wo.get("status", "Todo") for wo in work_orders})
wo_priorities = list({wo.get("priority", "High") for wo in work_orders})
wo_types = list({wo.get("work_order_type", "Maintenance") for wo in work_orders})

for wo in work_orders[:30]:
    wid = wo.get("work_order_id", "WO-001")
    wt = wo.get("title", "PM")
    ws = wo.get("status", "Todo")
    grounded["work_order_query"].extend(
        [
            f"What is the status of work order {wid}?",
            f"Show me work order {wid}",
            f"Tell me about work order {wid}",
            f"Work order {wid} details",
            f"How is work order {wid}?",
            f"Get details for {wid}",
            f"Show details for work order {wid}",
            f"Status of {wid}",
            f"What is {wid}?",
            f"Work order {wid} status",
        ]
    )

# Generic work order queries
grounded["work_order_query"].extend(
    [
        "Show me all work orders",
        "List all open work orders",
        "What work orders are there?",
        "How many work orders do we have?",
        "Count total work orders",
        "Show work orders with high priority",
        "List high priority work orders",
        "What are the urgent work orders?",
        "Show me work orders that are overdue",
        "What work orders are overdue?",
        "List all maintenance work orders",
        "Show me calibration work orders",
        "What work orders are in progress?",
        "List completed work orders",
        "Show all todo work orders",
        f"Show work orders with status {pick_name(work_orders, 'status')}",
        f"List all {pick_name(work_orders, 'work_order_type')} work orders",
        f"Find work orders for department {pick_name(departments, 'name')}",
        f"What work orders are assigned to {pick_name(workers, 'name')}",
        f"Show tasks for {pick_name(workers, 'name')}",
    ]
)

# Work Order Create examples
grounded["work_order_create"].extend(
    [
        "Create a new work order",
        "Open a work order for pump inspection",
        "Create a maintenance work order",
        "I need to create a work order",
        "New work order for equipment",
        f"Create a work order for {pick_name(instruments, 'name')} calibration",
        f"Open a {random.choice(wo_types).lower()} work order",
        f"Create a work order for {pick_name(departments, 'name')}",
    ]
)

# Approval Query examples
for apr in approvals[:20]:
    aid = apr.get("approval_id", "APR-001")
    at = apr.get("title", "Batch Release")
    grounded["approval_query"].extend(
        [
            f"What is the approval status for {aid}?",
            f"Show me approval {aid}",
            f"Approval {aid} details",
            f"Status of approval {aid}",
            f"Who approved {at}?",
            f"Show pending approvals for {at}",
        ]
    )

grounded["approval_query"].extend(
    [
        "Show all pending approvals",
        "List approval requests",
        "How many approvals are waiting?",
        "What approvals need my attention?",
        "Show me open approvals",
        "List all approvals",
        "What is waiting for approval?",
        "Show approval history",
        "List approved items",
        "Show rejected approvals",
        f"Show pending approvals in {pick_name(departments, 'name')}",
        f"List approvals for {pick_name(departments, 'name')}",
    ]
)

# Change Control examples
for cc in change_ctrls:
    ccid = cc.get("change_control_id", "CC-2026-0001")
    cct = cc.get("title", "Process Change")
    grounded["change_control"].extend(
        [
            f"What is the status of change control {ccid}?",
            f"Show me change control {ccid}",
            f"Change control {ccid} details",
            f"Tell me about {ccid}",
            f"Status of {ccid}",
        ]
    )
    grounded["change_control_query"].extend(
        [
            f"What is the status of change control {ccid}?",
            f"Show change control {ccid}",
            f"Details for {ccid}",
        ]
    )

grounded["change_control"].extend(
    [
        "List all open change controls",
        "Show change control requests",
        "What change controls are pending?",
        "Show all change controls",
        "List approved change controls",
        f"Show change controls open in {pick_name(departments, 'name')}",
        "What changes are being reviewed?",
        "Show change control history",
    ]
)
grounded["change_control_query"].extend(
    [
        "Show change controls",
        "List all change controls",
        "What change controls exist?",
        f"Show change controls in {pick_name(departments, 'name')}",
    ]
)

# Production KPI examples
grounded["production_kpi"].extend(
    [
        "What is the OEE for production line 1?",
        "Show me the KPIs for this shift",
        "What is the MTBF for our machines?",
        "Show production yield for today",
        "What is the overall equipment effectiveness?",
        "Show production metrics",
        "What is the batch rejection rate?",
        "Show downtime report for this week",
        "List all deviations this month",
        "What are the quality metrics?",
        "Show production performance",
        "What is the throughput?",
        "Show yield trend",
        f"Show production KPIs for {pick_name(departments, 'name')}",
        f"What are the KPIs for {pick_name(departments, 'name')}?",
        "Show me manufacturing KPIs",
        "What is the production efficiency?",
        "Show cycle time data",
        "What is the scrap rate?",
    ]
)

# SOP Compliance examples
for sop in sops[:15]:
    sid = sop.get("id", "SOP-001")
    st = sop.get("title", "Dispensing")
    grounded["sop_compliance"].extend(
        [
            f"Show me SOP {sid}",
            f"What is the SOP for {st}?",
            f"Is SOP {sid} current?",
            f"Who approved SOP {sid}?",
            f"SOP {sid} details",
            f"Show {st} SOP",
            f"Tell me about {st} procedure",
        ]
    )

grounded["sop_compliance"].extend(
    [
        "List all SOPs",
        "Show me all standard operating procedures",
        "What SOPs do we have?",
        "Show SOP compliance status",
        "List SOPs that need review",
        "What SOPs are expiring?",
        "Show recent SOP updates",
        f"List SOPs for {pick_name(departments, 'name')}",
        "Show me the SOP library",
    ]
)

# Warehouse Shipping examples
grounded["warehouse_shipping"].extend(
    [
        "List warehouse shipments for today",
        "What is the status of outbound freight?",
        "Show inbound shipments this week",
        "List pending dispatch orders",
        f"Show warehouse inventory for {pick_name(batches, 'product_name')}",
        f"What is the shipping status for batch {pick_name(batches, 'batch_number')}?",
        "Show me shipment tracking",
        "What shipments are pending?",
        "List all shipments",
        "Show warehouse status",
        "What is in the warehouse?",
        "Show dispatch schedule",
    ]
)

# Drug Research examples
grounded["drug_research"].extend(
    [
        "Tell me about drug research findings",
        "What drug formulations are in research?",
        f"What is the research status for {pick_name(batches, 'product_name')}?",
        f"Show formulation data for {pick_name(batches, 'product_name')}",
        "List active drug research projects",
        "Show me R&D data",
        "What drugs are we researching?",
        "Show formulation studies",
        "Tell me about our research pipeline",
        "What are the active research projects?",
    ]
)

# Worker examples
for w in workers[:15]:
    wn = w.get("name", "Unknown")
    grounded["worker"].extend(
        [
            f"What work orders are assigned to {wn}?",
            f"Show me tasks for {wn}",
            f"What is {wn}'s role?",
            f"Tell me about {wn}",
            f"Show {wn}'s assignments",
        ]
    )

grounded["worker"].extend(
    [
        "Show me the workers",
        "Who works here?",
        "List all workers",
        "Show team members",
        f"List all operators in {pick_name(departments, 'name')}",
        f"Who is the manager of {pick_name(departments, 'name')}?",
        "Show worker details",
        "List active workers",
        "Who is available?",
        "Show staffing information",
    ]
)

# Audit Log examples
grounded["audit_log"].extend(
    [
        "Show me the audit log for today",
        "What compliance events happened this week?",
        "Show audit trail",
        "List recent audit events",
        "What changes were made today?",
        "Show part 11 audit events",
        "List all compliance events",
        "Show me recent changes",
        "What was modified?",
        "Show change history",
        "List audit records for today",
        "Show compliance audit log",
    ]
)

# Decision Intelligence examples
grounded["decision_intelligence"].extend(
    [
        "Help me decide which batch to prioritize",
        "What should I focus on today?",
        "Give me a decision summary",
        "What are the critical issues?",
        "Help me prioritize my work",
        "What needs immediate attention?",
        "Show me decision support data",
        "What are the key decisions to make?",
        "Help me analyze the situation",
        "What action should I take?",
    ]
)

# ── Merge with existing INTENT_EXAMPLES ──────────────────────────────────────

from src.monkey_brain.kernel.classifier.intent_examples import INTENT_EXAMPLES

# Merge grounded examples into INTENT_EXAMPLES
for intent, examples in grounded.items():
    if intent in INTENT_EXAMPLES:
        # Add grounded examples, deduplicate
        existing = set(INTENT_EXAMPLES[intent])
        new = [e for e in examples if e not in existing]
        INTENT_EXAMPLES[intent] = INTENT_EXAMPLES[intent] + new
    else:
        INTENT_EXAMPLES[intent] = examples

# Print summary
print("\n=== Training Examples per Intent ===")
total = 0
for intent, examples in sorted(INTENT_EXAMPLES.items()):
    print(f"  {intent:<30} {len(examples):>4} examples")
    total += len(examples)
print(f"  {'TOTAL':<30} {total:>4} examples")

# ── Write updated intent_examples.py ─────────────────────────────────────────

import inspect

lines = [
    '"""Intent examples for the embedding classifier — trained with grounded DB data."""',
    "",
    "INTENT_EXAMPLES: dict[str, list[str]] = {",
]

for intent in sorted(INTENT_EXAMPLES.keys()):
    examples = INTENT_EXAMPLES[intent]
    lines.append(f'    "{intent}": [')
    for ex in examples:
        # Escape quotes in examples
        escaped = ex.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'        "{escaped}",')
    lines.append("    ],")

lines.append("}")
lines.append("")

output_path = "src/monkey_brain/kernel/classifier/intent_examples.py"
with open(output_path, "w") as f:
    f.write("\n".join(lines))

print(f"\nWrote {output_path} with {total} total examples")

# ── Invalidate classifier cache ──────────────────────────────────────────────

from src.monkey_brain.kernel.classifier.embed_classifier import (
    get_classifier,
    _classifier,
)
import src.monkey_brain.kernel.classifier.embed_classifier as mod

# Reset the module-level singleton so it retrains
mod._classifier = None
print("Classifier cache invalidated — will retrain on next request")

c.close()
print("Done.")
