#!/usr/bin/env python3
"""Generate 100k grounded training questions from manufacturing DB data."""

import random
import pymongo
import json

MONGO_URI = "mongodb://localhost:27017"
DB_NAME = "demo"

random.seed(42)

c = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
db = c[DB_NAME]

# ── Load ALL data ────────────────────────────────────────────────────────────

work_orders = list(db.work_orders.find({}, {"_id": 0}))
batches = list(db.production_batches.find({}, {"_id": 0}))
approvals = list(db.approvals.find({}, {"_id": 0}))
change_ctrls = list(db.change_controls.find({}, {"_id": 0}))
instruments = list(db.instruments.find({}, {"_id": 0}))
sops = list(db.sops.find({}, {"_id": 0}))
departments = list(db.departments.find({}, {"_id": 0}))
workers = list(db.workers.find({}, {"_id": 0}))
bers = list(db.batch_production_execution_records.find({}, {"_id": 0}))
users = list(db.users.find({}, {"_id": 0}))
machines = list(db.pharmaceutical_machines.find({}, {"_id": 0}))

print(
    f"Loaded: {len(work_orders)} WOs, {len(batches)} batches, {len(approvals)} approvals, "
    f"{len(change_ctrls)} CCs, {len(instruments)} instruments, {len(sops)} SOPs, "
    f"{len(departments)} depts, {len(workers)} workers, {len(bers)} BERs, "
    f"{len(users)} users, {len(machines)} machines"
)


def pick(lst):
    return random.choice(lst) if lst else {}


def pickv(lst, key, default="UNKNOWN"):
    d = pick(lst)
    return d.get(key, default) if isinstance(d, dict) else default


# ── Unique values for variety ────────────────────────────────────────────────

ALL_WO_IDS = [wo["work_order_id"] for wo in work_orders if wo.get("work_order_id")]
ALL_BATCH_NUMS = [b["batch_number"] for b in batches if b.get("batch_number")]
ALL_BATCH_IDS = [b["batch_id"] for b in batches if b.get("batch_id")]
ALL_PRODUCTS = list({b["product_name"] for b in batches if b.get("product_name")})
ALL_WO_TITLES = list({wo["title"] for wo in work_orders if wo.get("title")})
ALL_WO_STATUSES = list({wo["status"] for wo in work_orders if wo.get("status")})
ALL_WO_PRIORITIES = list({wo.get("priority", "High") for wo in work_orders})
ALL_WO_TYPES = list({wo.get("work_order_type", "Maintenance") for wo in work_orders})
ALL_BATCH_STATUSES = list({b["status"] for b in batches if b.get("status")})
ALL_APR_IDS = [a["approval_id"] for a in approvals if a.get("approval_id")]
ALL_APR_TITLES = list({a["title"] for a in approvals if a.get("title")})
ALL_APR_STATUSES = list({a["status"] for a in approvals if a.get("status")})
ALL_CC_IDS = [cc["change_control_id"] for cc in change_ctrls if cc.get("change_control_id")]
ALL_CC_TITLES = list({cc["title"] for cc in change_ctrls if cc.get("title")})
ALL_CC_STATUSES = list({cc["status"] for cc in change_ctrls if cc.get("status")})
ALL_INST_NAMES = [i["name"] for i in instruments if i.get("name")]
ALL_SOP_IDS = [s["id"] for s in sops if s.get("id")]
ALL_SOP_TITLES = [s["title"] for s in sops if s.get("title")]
ALL_DEPT_NAMES = [d["name"] for d in departments if d.get("name")]
ALL_WORKER_NAMES = [w["name"] for w in workers if w.get("name")]
ALL_WORKER_TITLES = list({w.get("title", "Operator") for w in workers})
ALL_BER_IDS = [b["batch_execution_record_id"] for b in bers if b.get("batch_execution_record_id")]
ALL_MACHINE_NAMES = [m.get("name", "Machine") for m in machines if m.get("name")]
ALL_LINE_IDS = list({m.get("line_id") for m in machines if m.get("line_id")})
ALL_USER_NAMES = [u["name"] for u in users if u.get("name")]


def pick_from(lst):
    return random.choice(lst) if lst else ""


# ── Question templates per intent ────────────────────────────────────────────

# Each template is a function that returns a question string
# We'll generate many variations


def gen_batch_record(n):
    questions = []
    for _ in range(n):
        q = random.choice(
            [
                # Specific batch lookups
                f"What is the status of batch {pick_from(ALL_BATCH_NUMS)}?",
                f"Show me the batch record for {pick_from(ALL_BATCH_NUMS)}",
                f"Tell me about batch {pick_from(ALL_BATCH_NUMS)}",
                f"Batch {pick_from(ALL_BATCH_NUMS)} details",
                f"Get batch record for {pick_from(ALL_BATCH_NUMS)}",
                f"How is batch {pick_from(ALL_BATCH_NUMS)} doing?",
                f"Status of production batch {pick_from(ALL_BATCH_NUMS)}",
                f"Show batch {pick_from(ALL_BATCH_NUMS)}",
                f"Display batch record {pick_from(ALL_BATCH_NUMS)}",
                f"What happened to batch {pick_from(ALL_BATCH_NUMS)}?",
                f"Fetch batch details for {pick_from(ALL_BATCH_NUMS)}",
                f"Give me info on batch {pick_from(ALL_BATCH_NUMS)}",
                f"Open batch {pick_from(ALL_BATCH_NUMS)}",
                f"View batch {pick_from(ALL_BATCH_NUMS)}",
                f"Batch {pick_from(ALL_BATCH_NUMS)} summary",
                # Product-based queries
                f"List all batches for {pick_from(ALL_PRODUCTS)}",
                f"What batches are there for {pick_from(ALL_PRODUCTS)}?",
                f"Find batches for product {pick_from(ALL_PRODUCTS)}",
                f"Show all {pick_from(ALL_PRODUCTS)} batches",
                f"Which batches contain {pick_from(ALL_PRODUCTS)}?",
                f"Batches producing {pick_from(ALL_PRODUCTS)}",
                # Status-based queries
                f"Show batches in {pick_from(ALL_BATCH_STATUSES)} status",
                f"What batches are {pick_from(ALL_BATCH_STATUSES).lower()}?",
                f"List batches with status {pick_from(ALL_BATCH_STATUSES)}",
                f"Find all {pick_from(ALL_BATCH_STATUSES).lower()} batches",
                # Count/aggregation
                "How many production batches do we have?",
                "Count total batches",
                "Show me all production batches",
                "List all batches in the system",
                "Total number of batches",
                "How many batches are there?",
                "What is the batch count?",
                "Give me a batch summary",
                "Show batch statistics",
                "Batch overview",
                # BER queries
                f"Show batch execution record {pick_from(ALL_BER_IDS)}",
                f"BER {pick_from(ALL_BER_IDS)} status",
                f"What is the status of BER {pick_from(ALL_BER_IDS)}?",
                f"Execution record {pick_from(ALL_BER_IDS)}",
                # Yield / quality
                f"What is the yield for batch {pick_from(ALL_BATCH_NUMS)}?",
                f"Show yield for {pick_from(ALL_BATCH_NUMS)}",
                f"Quality metrics for batch {pick_from(ALL_BATCH_NUMS)}",
                f"Batch {pick_from(ALL_BATCH_NUMS)} quality data",
                f"Deviations for batch {pick_from(ALL_BATCH_NUMS)}",
                # Line-based
                f"What batches are on line {pick_from(ALL_LINE_IDS)}?",
                f"Show batches for line {pick_from(ALL_LINE_IDS)}",
                f"Batches on production line {pick_from(ALL_LINE_IDS)}",
                # Time-based
                "Show batches created this week",
                "List recent batches",
                "What batches were started today?",
                "Show batches from last month",
                "Batches completed this week",
                "Recent batch activity",
                # Release
                "List all released batches",
                "Show released batches this month",
                "What batches have been approved?",
                "List approved batches",
                # Hold
                "What batches are on hold?",
                "Show held batches",
                "List batches that are suspended",
                "Batches awaiting release",
                # In progress
                "Show in-progress batches",
                "What batches are currently running?",
                "Active production batches",
                "Batches in production right now",
            ]
        )
        questions.append(q)
    return questions


def gen_work_order_query(n):
    questions = []
    for _ in range(n):
        q = random.choice(
            [
                # Specific WO lookups
                f"What is the status of work order {pick_from(ALL_WO_IDS)}?",
                f"Show me work order {pick_from(ALL_WO_IDS)}",
                f"Tell me about work order {pick_from(ALL_WO_IDS)}",
                f"Work order {pick_from(ALL_WO_IDS)} details",
                f"How is work order {pick_from(ALL_WO_IDS)}?",
                f"Get details for {pick_from(ALL_WO_IDS)}",
                f"Show details for work order {pick_from(ALL_WO_IDS)}",
                f"Status of {pick_from(ALL_WO_IDS)}",
                f"What is {pick_from(ALL_WO_IDS)}?",
                f"Open {pick_from(ALL_WO_IDS)}",
                f"View work order {pick_from(ALL_WO_IDS)}",
                f"Display {pick_from(ALL_WO_IDS)}",
                f"Fetch {pick_from(ALL_WO_IDS)}",
                # Generic queries
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
                "Work order summary",
                "Show work order list",
                "All work orders",
                # Status-based
                f"Show work orders with status {pick_from(ALL_WO_STATUSES)}",
                f"List work orders in {pick_from(ALL_WO_STATUSES)} state",
                f"Find work orders that are {pick_from(ALL_WO_STATUSES).lower()}",
                f"What work orders have status {pick_from(ALL_WO_STATUSES)}?",
                # Type-based
                f"List all {pick_from(ALL_WO_TYPES).lower()} work orders",
                f"Show {pick_from(ALL_WO_TYPES).lower()} work orders",
                f"What {pick_from(ALL_WO_TYPES).lower()} work orders do we have?",
                f"Filter work orders by type {pick_from(ALL_WO_TYPES)}",
                # Department-based
                f"Find work orders for department {pick_from(ALL_DEPT_NAMES)}",
                f"Show work orders in {pick_from(ALL_DEPT_NAMES)}",
                f"What work orders belong to {pick_from(ALL_DEPT_NAMES)}?",
                f"List {pick_from(ALL_DEPT_NAMES)} work orders",
                # Worker-based
                f"What work orders are assigned to {pick_from(ALL_WORKER_NAMES)}?",
                f"Show me tasks for {pick_from(ALL_WORKER_NAMES)}",
                f"Work orders assigned to {pick_from(ALL_WORKER_NAMES)}",
                f"What is {pick_from(ALL_WORKER_NAMES)} working on?",
                f"Show {pick_from(ALL_WORKER_NAMES)}'s assignments",
                f"List tasks for {pick_from(ALL_WORKER_NAMES)}",
                f"What has {pick_from(ALL_WORKER_NAMES)} been assigned?",
                # Machine-based
                f"Show work orders for {pick_from(ALL_MACHINE_NAMES)}",
                f"What work orders are for {pick_from(ALL_MACHINE_NAMES)}?",
                f"Maintenance orders for {pick_from(ALL_MACHINE_NAMES)}",
                # Priority
                "Show critical work orders",
                "List medium priority work orders",
                "What low priority work orders exist?",
                "Show work orders by priority",
            ]
        )
        questions.append(q)
    return questions


def gen_approval_query(n):
    questions = []
    for _ in range(n):
        q = random.choice(
            [
                # Specific approval lookups
                f"What is the approval status for {pick_from(ALL_APR_IDS)}?",
                f"Show me approval {pick_from(ALL_APR_IDS)}",
                f"Approval {pick_from(ALL_APR_IDS)} details",
                f"Status of approval {pick_from(ALL_APR_IDS)}",
                f"Tell me about {pick_from(ALL_APR_IDS)}",
                f"Open approval {pick_from(ALL_APR_IDS)}",
                f"View {pick_from(ALL_APR_IDS)}",
                # Title-based
                f"Who approved {pick_from(ALL_APR_TITLES)}?",
                f"Show pending approvals for {pick_from(ALL_APR_TITLES)}",
                f"What is the status of {pick_from(ALL_APR_TITLES)}?",
                f"Approval history for {pick_from(ALL_APR_TITLES)}",
                # Generic
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
                "Pending approval list",
                "Approval queue",
                "What needs to be approved?",
                "Show me what needs approval",
                "Approvals dashboard",
                "Recent approvals",
                "Approval summary",
                "All approval requests",
                # Status-based
                f"Show approvals with status {pick_from(ALL_APR_STATUSES)}",
                f"List {pick_from(ALL_APR_STATUSES).lower()} approvals",
                f"What approvals are {pick_from(ALL_APR_STATUSES).lower()}?",
                # Department-based
                f"Show pending approvals in {pick_from(ALL_DEPT_NAMES)}",
                f"List approvals for {pick_from(ALL_DEPT_NAMES)}",
                f"What approvals are in {pick_from(ALL_DEPT_NAMES)}?",
                f"Approval requests from {pick_from(ALL_DEPT_NAMES)}",
            ]
        )
        questions.append(q)
    return questions


def gen_change_control(n):
    questions = []
    for _ in range(n):
        q = random.choice(
            [
                # Specific CC lookups
                f"What is the status of change control {pick_from(ALL_CC_IDS)}?",
                f"Show me change control {pick_from(ALL_CC_IDS)}",
                f"Change control {pick_from(ALL_CC_IDS)} details",
                f"Tell me about {pick_from(ALL_CC_IDS)}",
                f"Status of {pick_from(ALL_CC_IDS)}",
                f"Open {pick_from(ALL_CC_IDS)}",
                f"View change control {pick_from(ALL_CC_IDS)}",
                f"Show details for {pick_from(ALL_CC_IDS)}",
                # Title-based
                f"What is the status of '{pick_from(ALL_CC_TITLES)}'?",
                f"Show change control '{pick_from(ALL_CC_TITLES)}'",
                f"Details for '{pick_from(ALL_CC_TITLES)}'",
                # Generic
                "List all open change controls",
                "Show change control requests",
                "What change controls are pending?",
                "Show all change controls",
                "List approved change controls",
                "Change control summary",
                "What changes are being reviewed?",
                "Show change control history",
                "Open change controls",
                "Pending change controls",
                "All change control requests",
                "Change control dashboard",
                "Recent change controls",
                # Status-based
                f"Show change controls with status {pick_from(ALL_CC_STATUSES)}",
                f"List {pick_from(ALL_CC_STATUSES).lower()} change controls",
                # Department-based
                f"Show change controls open in {pick_from(ALL_DEPT_NAMES)}",
                f"List change controls for {pick_from(ALL_DEPT_NAMES)}",
                f"What change controls are in {pick_from(ALL_DEPT_NAMES)}?",
            ]
        )
        questions.append(q)
    return questions


def gen_production_kpi(n):
    questions = []
    templates = [
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
        "Manufacturing KPIs",
        "Production efficiency report",
        "Show cycle time data",
        "What is the scrap rate?",
        "Show OEE breakdown",
        "Machine availability report",
        "Production output summary",
        "Quality rate for this week",
        "Performance rate summary",
        "Downtime analysis",
        "Overall plant performance",
        "Show me manufacturing metrics",
        "Production targets vs actual",
        "What is the first pass yield?",
        "Show equipment effectiveness",
        "Batch success rate",
        "Process capability indices",
    ]
    dept_templates = [
        f"Show production KPIs for {pick_from(ALL_DEPT_NAMES)}",
        f"What are the KPIs for {pick_from(ALL_DEPT_NAMES)}?",
        f"{pick_from(ALL_DEPT_NAMES)} performance metrics",
        f"Production efficiency in {pick_from(ALL_DEPT_NAMES)}",
    ]
    for _ in range(n):
        q = random.choice(templates + dept_templates)
        questions.append(q)
    return questions


def gen_sop_compliance(n):
    questions = []
    for _ in range(n):
        q = random.choice(
            [
                # Specific SOP lookups
                f"Show me SOP {pick_from(ALL_SOP_IDS)}",
                f"What is the SOP for {pick_from(ALL_SOP_TITLES)}?",
                f"Is SOP {pick_from(ALL_SOP_IDS)} current?",
                f"Who approved SOP {pick_from(ALL_SOP_IDS)}?",
                f"SOP {pick_from(ALL_SOP_IDS)} details",
                f"Show {pick_from(ALL_SOP_TITLES)} SOP",
                f"Tell me about {pick_from(ALL_SOP_TITLES)} procedure",
                f"View SOP {pick_from(ALL_SOP_IDS)}",
                f"Open SOP {pick_from(ALL_SOP_IDS)}",
                f"Display SOP {pick_from(ALL_SOP_IDS)}",
                # Generic
                "List all SOPs",
                "Show me all standard operating procedures",
                "What SOPs do we have?",
                "Show SOP compliance status",
                "List SOPs that need review",
                "What SOPs are expiring?",
                "Show recent SOP updates",
                "SOP library overview",
                "All standard operating procedures",
                "SOP summary",
                "Recent SOP changes",
                "SOP review schedule",
                # Department-based
                f"List SOPs for {pick_from(ALL_DEPT_NAMES)}",
                f"What SOPs are in {pick_from(ALL_DEPT_NAMES)}?",
                f"Show {pick_from(ALL_DEPT_NAMES)} SOPs",
            ]
        )
        questions.append(q)
    return questions


def gen_warehouse_shipping(n):
    questions = []
    templates = [
        "List warehouse shipments for today",
        "What is the status of outbound freight?",
        "Show inbound shipments this week",
        "List pending dispatch orders",
        "Show me shipment tracking",
        "What shipments are pending?",
        "List all shipments",
        "Show warehouse status",
        "What is in the warehouse?",
        "Show dispatch schedule",
        "Warehouse inventory report",
        "Outbound shipment list",
        "Inbound receipt log",
        "Shipping queue",
        "Pending deliveries",
        "Warehouse stock levels",
        "Dispatch summary",
        "Shipment tracking dashboard",
        "Freight status report",
        "Logistics overview",
    ]
    for _ in range(n):
        if random.random() < 0.4 and ALL_PRODUCTS:
            q = random.choice(
                [
                    f"Show warehouse inventory for {pick_from(ALL_PRODUCTS)}",
                    f"What is the shipping status for batch {pick_from(ALL_BATCH_NUMS)}?",
                    f"Inventory for {pick_from(ALL_PRODUCTS)}",
                    f"Stock status for {pick_from(ALL_PRODUCTS)}",
                ]
            )
        else:
            q = random.choice(templates)
        questions.append(q)
    return questions


def gen_drug_research(n):
    questions = []
    templates = [
        "Tell me about drug research findings",
        "What drug formulations are in research?",
        "List active drug research projects",
        "Show me R&D data",
        "What drugs are we researching?",
        "Show formulation studies",
        "Tell me about our research pipeline",
        "What are the active research projects?",
        "Research and development overview",
        "Drug development pipeline",
        "Formulation development status",
        "Clinical trial progress",
        "API research data",
        "Excipient studies",
        "Stability study results",
        "Bioequivalence data",
    ]
    for _ in range(n):
        if random.random() < 0.3 and ALL_PRODUCTS:
            q = random.choice(
                [
                    f"What is the research status for {pick_from(ALL_PRODUCTS)}?",
                    f"Show formulation data for {pick_from(ALL_PRODUCTS)}",
                    f"Research progress for {pick_from(ALL_PRODUCTS)}",
                    f"Development status of {pick_from(ALL_PRODUCTS)}",
                ]
            )
        else:
            q = random.choice(templates)
        questions.append(q)
    return questions


def gen_worker(n):
    questions = []
    for _ in range(n):
        q = random.choice(
            [
                # Specific worker lookups
                f"What work orders are assigned to {pick_from(ALL_WORKER_NAMES)}?",
                f"Show me tasks for {pick_from(ALL_WORKER_NAMES)}",
                f"What is {pick_from(ALL_WORKER_NAMES)}'s role?",
                f"Tell me about {pick_from(ALL_WORKER_NAMES)}",
                f"Show {pick_from(ALL_WORKER_NAMES)}'s assignments",
                f"What does {pick_from(ALL_WORKER_NAMES)} do?",
                f"Show {pick_from(ALL_WORKER_NAMES)} profile",
                f"Workload for {pick_from(ALL_WORKER_NAMES)}",
                f"Tasks assigned to {pick_from(ALL_WORKER_NAMES)}",
                f"Schedule for {pick_from(ALL_WORKER_NAMES)}",
                # Generic
                "Show me the workers",
                "Who works here?",
                "List all workers",
                "Show team members",
                "All employees",
                "Worker directory",
                "Staff list",
                "Team roster",
                "Personnel directory",
                "Show active workers",
                # Department-based
                f"List all operators in {pick_from(ALL_DEPT_NAMES)}",
                f"Who is the manager of {pick_from(ALL_DEPT_NAMES)}?",
                f"Show {pick_from(ALL_DEPT_NAMES)} team",
                f"Workers in {pick_from(ALL_DEPT_NAMES)}",
                f"Staff of {pick_from(ALL_DEPT_NAMES)}",
                # Role-based
                f"Show all {pick_from(ALL_WORKER_TITLES).lower()}s",
                f"List {pick_from(ALL_WORKER_TITLES).lower()}s",
            ]
        )
        questions.append(q)
    return questions


def gen_audit_log(n):
    questions = []
    templates = [
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
        "Audit records for today",
        "Compliance audit log",
        "Part 11 compliance report",
        "Data integrity audit",
        "Electronic signature log",
        "System access audit",
        "User activity log",
        "Change audit trail",
        "Regulatory compliance events",
        "GxP audit records",
        "Recent system changes",
        "Who accessed what?",
        "System activity report",
        "Audit summary",
        "Compliance dashboard",
    ]
    for _ in range(n):
        q = random.choice(templates)
        questions.append(q)
    return questions


def gen_decision_intelligence(n):
    questions = []
    templates = [
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
        "Decision support overview",
        "Critical path analysis",
        "Priority recommendations",
        "What is most urgent?",
        "Executive summary",
        "Situational awareness report",
        "Alert summary",
        "What requires my decision?",
        "Decision matrix",
        "Recommended actions",
    ]
    for _ in range(n):
        q = random.choice(templates)
        questions.append(q)
    return questions


def gen_work_order_create(n):
    questions = []
    templates = [
        "Create a new work order",
        "Open a work order for pump inspection",
        "Create a maintenance work order",
        "I need to create a work order",
        "New work order for equipment",
        "Start a new work order",
        "Initiate a work order",
        "Raise a work order",
        "Submit a new work order request",
        "Create calibration work order",
    ]
    for _ in range(n):
        if random.random() < 0.5:
            q = random.choice(
                [
                    f"Create a work order for {pick_from(ALL_MACHINE_NAMES)} calibration",
                    f"Open a {pick_from(ALL_WO_TYPES).lower()} work order",
                    f"Create a work order for {pick_from(ALL_DEPT_NAMES)}",
                    f"New work order for {pick_from(ALL_INST_NAMES)}",
                    f"Open work order for {pick_from(ALL_MACHINE_NAMES)} maintenance",
                    f"Create a preventive maintenance work order for {pick_from(ALL_MACHINE_NAMES)}",
                ]
            )
        else:
            q = random.choice(templates)
        questions.append(q)
    return questions


# ── Generate all ─────────────────────────────────────────────────────────────

TARGET_PER_INTENT = {
    "batch_record": 15000,
    "work_order_query": 15000,
    "approval_query": 8000,
    "change_control": 5000,
    "change_control_query": 5000,
    "production_kpi": 5000,
    "sop_compliance": 5000,
    "warehouse_shipping": 5000,
    "drug_research": 5000,
    "worker": 5000,
    "audit_log": 5000,
    "decision_intelligence": 3000,
    "work_order_create": 4000,
}

generators = {
    "batch_record": gen_batch_record,
    "work_order_query": gen_work_order_query,
    "approval_query": gen_approval_query,
    "change_control": gen_change_control,
    "change_control_query": gen_change_control,
    "production_kpi": gen_production_kpi,
    "sop_compliance": gen_sop_compliance,
    "warehouse_shipping": gen_warehouse_shipping,
    "drug_research": gen_drug_research,
    "worker": gen_worker,
    "audit_log": gen_audit_log,
    "decision_intelligence": gen_decision_intelligence,
    "work_order_create": gen_work_order_create,
}

all_examples = {}

for intent, count in TARGET_PER_INTENT.items():
    gen = generators[intent]
    examples = gen(count)
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for e in examples:
        if e not in seen:
            seen.add(e)
            unique.append(e)
    all_examples[intent] = unique
    print(f"  {intent:<30} generated={count:>6}  unique={len(unique):>6}")

# ── Also keep original small examples for rare intents ───────────────────────

keep_intents = [
    "plant_topology",
    "line",
    "stage",
    "workstation",
    "asset_management",
    "count",
    "simulation",
    "sittingface_pipeline",
    "web_search",
    "knowledge_base",
]

for intent in keep_intents:
    # Import current examples
    try:
        from src.monkey_brain.kernel.classifier.intent_examples import INTENT_EXAMPLES

        if intent in INTENT_EXAMPLES:
            all_examples[intent] = INTENT_EXAMPLES[intent]
    except ImportError:
        pass  # Module not available, skip

total = sum(len(v) for v in all_examples.values())
print(f"\nTotal unique examples: {total}")

# ── Write intent_examples.py ─────────────────────────────────────────────────

lines = [
    '"""Intent examples for the embedding classifier — trained with 100k grounded manufacturing data."""',
    "",
    "INTENT_EXAMPLES: dict[str, list[str]] = {",
]

for intent in sorted(all_examples.keys()):
    examples = all_examples[intent]
    lines.append(f'    "{intent}": [')
    for ex in examples:
        escaped = ex.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'        "{escaped}",')
    lines.append("    ],")

lines.append("}")
lines.append("")

output_path = "src/monkey_brain/kernel/classifier/intent_examples.py"
with open(output_path, "w") as f:
    f.write("\n".join(lines))

print(f"\nWrote {output_path}")

# ── Invalidate classifier ────────────────────────────────────────────────────

try:
    import src.monkey_brain.kernel.classifier.embed_classifier as mod

    mod._classifier = None
    print("Classifier cache invalidated")
except ImportError:
    pass  # Module not available, skip cache invalidation

c.close()
print("Done.")
