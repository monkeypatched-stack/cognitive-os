#!/usr/bin/env python3
"""Seed the demo DB with more grounded data following existing schemas."""

import random
import pymongo
from datetime import datetime, timedelta

MONGO_URI = "mongodb://localhost:27017"
DB_NAME = "demo"

c = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
db = c[DB_NAME]

# Existing IDs for cross-referencing
MACHINES = [d["machine_id"] for d in db.pharmaceutical_machines.find({}, {"machine_id": 1, "_id": 0})]
LINES = list(
    {d.get("line_id") for d in db.pharmaceutical_machines.find({}, {"line_id": 1, "_id": 0}) if d.get("line_id")}
)
WORKSTATIONS = [d.get("workstation_id") for d in db.workstations.find({}, {"workstation_id": 1, "_id": 0})]
WORKER_IDS = [d["worker_id"] for d in db.workers.find({}, {"worker_id": 1, "_id": 0})]
WORKER_NAMES = {d["worker_id"]: d["name"] for d in db.workers.find({}, {"worker_id": 1, "name": 1, "_id": 0})}
DEPT_IDS = [d["department_id"] for d in db.departments.find({}, {"department_id": 1, "_id": 0})]
DEPT_NAMES = [d["name"] for d in db.departments.find({}, {"name": 1, "_id": 0})]
PRODUCTS = list(
    {d["product_name"] for d in db.production_batches.find({}, {"product_name": 1, "_id": 0}) if d.get("product_name")}
)
EXISTING_WO = set(d["work_order_id"] for d in db.work_orders.find({}, {"work_order_id": 1, "_id": 0}))
EXISTING_BATCH = set(d["batch_id"] for d in db.production_batches.find({}, {"batch_id": 1, "_id": 0}))

WO_TYPES = ["Maintenance", "Calibration", "Inspection", "Repair", "Installation"]
WO_STATUSES = ["Todo", "In Progress", "Completed", "On Hold"]
WO_PRIORITIES = ["High", "Medium", "Low"]
BATCH_STATUSES = ["Created", "In Progress", "Approved", "Rejected", "On Hold"]
APPROVAL_STATUSES = ["Pending", "Approved", "Rejected"]
CC_STATUSES = ["Open", "Under Review", "Approved", "Rejected"]
CC_TYPES = ["Process Change", "Equipment Change", "Document Change", "System Change"]


def rand_id(prefix, n=8):
    return f"{prefix}-{''.join(random.choices('0123456789ABCDEF', k=n))}"


def rand_date(days_back=30):
    d = datetime.utcnow() - timedelta(days=random.randint(0, days_back), hours=random.randint(0, 23))
    return d.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def pick(lst):
    return random.choice(lst) if lst else None


# ── Work Orders ──────────────────────────────────────────────────────────────

WO_TITLES = [
    "Quarterly PM",
    "Monthly PM",
    "Annual Calibration",
    "Safety Inspection",
    "Emergency Repair",
    "Preventive Maintenance",
    "Corrective Maintenance",
    "Routine Inspection",
    "Overhaul",
    "Bearing Replacement",
    "Seal Replacement",
    "Motor Service",
    "Filter Replacement",
    "Valve Maintenance",
    "Sensor Calibration",
    "pH Meter Calibration",
    "Temperature Controller Calibration",
    "Pressure Gauge Calibration",
    "Weighing Scale Calibration",
    "Documentation Review",
    "SOP Update",
    "Training Session",
]

WO_TARGET = 200
wo_count = db.work_orders.count_documents({})
wo_to_add = WO_TARGET - wo_count
print(f"Work Orders: {wo_count} -> adding {wo_to_add}")

wo_docs = []
for i in range(wo_to_add):
    wo_id = f"WO-{i + wo_count + 1:05d}"
    while wo_id in EXISTING_WO:
        wo_id = rand_id("WO")
    EXISTING_WO.add(wo_id)

    machine = pick(MACHINES) or "MACH-GRAN-001"
    worker = pick(WORKER_IDS) or "WRK-WS-010"
    status = pick(WO_STATUSES)
    created = rand_date(60)

    wo_docs.append(
        {
            "work_order_id": wo_id,
            "title": f"{pick(WO_TYPES)} - {pick(WO_TITLES)}",
            "message": f"Scheduled {pick(WO_TITLES).lower()} for {machine}",
            "work_order_type": pick(WO_TYPES),
            "status": status,
            "priority": pick(WO_PRIORITIES),
            "machine_id": machine,
            "line_id": pick(LINES),
            "workstation_id": pick(WORKSTATIONS),
            "assigned_to": worker,
            "created_at": created,
            "updated_at": created,
            "remarks": "",
            "attachments": [],
            "expected_completion": (
                datetime.strptime(created[:19], "%Y-%m-%d %H:%M:%S") + timedelta(days=random.randint(7, 90))
            ).strftime("%Y-%m-%d %H:%M:%S.000"),
            "assigned_to_name": WORKER_NAMES.get(worker, "Unknown"),
        }
    )

if wo_docs:
    db.work_orders.insert_many(wo_docs)
    print(f"  Inserted {len(wo_docs)} work orders")

# ── Production Batches ───────────────────────────────────────────────────────

BATCH_TARGET = 200
batch_count = db.production_batches.count_documents({})
batch_to_add = BATCH_TARGET - batch_count
print(f"Production Batches: {batch_count} -> adding {batch_to_add}")

batch_docs = []
ber_docs = []
for i in range(batch_to_add):
    bid = f"PB-{rand_id('', 8)}"
    while bid in EXISTING_BATCH:
        bid = f"PB-{rand_id('', 8)}"
    EXISTING_BATCH.add(bid)

    batch_num = f"BATCH-{batch_count + i + 1:03d}"
    product = pick(PRODUCTS) or "Cetirizine 10mg Tablets"
    status = pick(BATCH_STATUSES)
    created = rand_date(45)
    line = pick(LINES) or "LINE-TAB-001"
    wo = pick(list(EXISTING_WO)) or "PM-RMG-001"

    ber_id = f"BER-{rand_id('', 8)}"

    batch_docs.append(
        {
            "batch_id": bid,
            "batch_number": batch_num,
            "product_id": f"PRD-TAB-{random.randint(1, 20):03d}",
            "product_name": product,
            "process_definition_id": f"PD-{random.choice(['CAP', 'TAB', 'GRN', 'BLD'])}-{random.randint(1, 10):03d}",
            "work_order_id": wo,
            "plant_id": "PLANT-TBL-IN-001",
            "line_id": line,
            "current_stage_id": f"STAGE-{random.randint(1, 15):02d}",
            "current_workstation_id": pick(WORKSTATIONS) or "WS-TAB-01-01",
            "status": status,
            "qa_reviewer_id": None,
            "lifecycle_events": [
                {
                    "event_id": f"BLE-{rand_id('', 8)}",
                    "event_type": "Created",
                    "status_to": "Created",
                    "timestamp": created,
                    "user": "admin@monkeypatched.com",
                }
            ],
            "batch_execution_record_ids": [ber_id],
            "planned_start_at": created,
            "planned_end_at": (
                datetime.strptime(created[:19], "%Y-%m-%d %H:%M:%S") + timedelta(days=random.randint(3, 14))
            ).isoformat(),
            "started_at": created if status != "Created" else None,
            "completed_at": (
                (
                    datetime.strptime(created[:19], "%Y-%m-%d %H:%M:%S") + timedelta(days=random.randint(3, 14))
                ).isoformat()
                if status == "Approved"
                else None
            ),
            "released_at": None,
            "metadata": {
                "seed": True,
                "estimated_quantity": random.randint(500, 10000),
            },
            "created_at": created,
            "updated_at": created,
            "active_batch_execution_record_id": ber_id,
        }
    )

    ber_docs.append(
        {
            "batch_execution_record_id": ber_id,
            "batch_id": bid,
            "process_definition_id": batch_docs[-1]["process_definition_id"],
            "plant_id": "PLANT-TBL-IN-001",
            "line_id": line,
            "status": status,
            "batch_step_execution_ids": [],
            "metadata": {"seed": True},
            "created_at": created,
            "updated_at": created,
        }
    )

if batch_docs:
    db.production_batches.insert_many(batch_docs)
    db.batch_production_execution_records.insert_many(ber_docs)
    print(f"  Inserted {len(batch_docs)} batches + {len(ber_docs)} execution records")

# ── Approvals ────────────────────────────────────────────────────────────────

APPROVAL_TARGET = 100
approval_count = db.approvals.count_documents({})
approval_to_add = APPROVAL_TARGET - approval_count
print(f"Approvals: {approval_count} -> adding {approval_to_add}")

approval_docs = []
for i in range(approval_to_add):
    created = rand_date(30)
    approval_docs.append(
        {
            "approval_id": f"APR-{i + approval_count + 1:04d}",
            "title": pick(
                [
                    "Batch Release",
                    "Process Change",
                    "Equipment Qualification",
                    "SOP Approval",
                    "Deviation Closure",
                    "CAPA Approval",
                    "Change Control Review",
                ]
            ),
            "status": pick(APPROVAL_STATUSES),
            "type": pick(["Batch", "Process", "Equipment", "Document", "Deviation"]),
            "requested_by": pick(WORKER_IDS) or "WRK-WS-010",
            "requested_by_name": WORKER_NAMES.get(pick(WORKER_IDS) or "WRK-WS-010", "Unknown"),
            "department_id": pick(DEPT_IDS),
            "department_name": pick(DEPT_NAMES),
            "batch_id": pick(list(EXISTING_BATCH)),
            "created_at": created,
            "updated_at": created,
            "description": f"Approval request for {pick(['batch release', 'process change', 'equipment qualification', 'SOP update'])}",
        }
    )

if approval_docs:
    db.approvals.insert_many(approval_docs)
    print(f"  Inserted {len(approval_docs)} approvals")

# ── Change Controls ──────────────────────────────────────────────────────────

CC_TARGET = 50
cc_count = db.change_controls.count_documents({})
cc_to_add = CC_TARGET - cc_count
print(f"Change Controls: {cc_count} -> adding {cc_to_add}")

cc_docs = []
for i in range(cc_to_add):
    created = rand_date(45)
    worker = pick(WORKER_IDS) or "WRK-RAHUL-001"
    cc_docs.append(
        {
            "change_control_id": f"CC-2026-{i + cc_count + 1:04d}",
            "title": pick(
                [
                    "Increase Granulation Speed",
                    "Modify Compression Force",
                    "Update Coating Parameters",
                    "Change Filter Type",
                    "Replace Seal Material",
                    "Modify Drying Temperature",
                    "Update Blending Time",
                    "Change Discharge Valve",
                    "Modify Spray Rate",
                    "Update Cleaning Procedure",
                    "Change Raw Material Supplier",
                    "Modify Packaging Line Speed",
                    "Update Environmental Monitoring",
                    "Change Water System Parameters",
                    "Modify HVAC Settings",
                ]
            ),
            "status": pick(CC_STATUSES),
            "type": pick(CC_TYPES),
            "line_id": pick(LINES) or "LINE-TAB-001",
            "line_name": "Tablet Line A",
            "stage_id": f"STAGE-{random.randint(1, 10):02d}",
            "stage_name": pick(
                [
                    "Granulation",
                    "Compression",
                    "Coating",
                    "Packaging",
                    "Dispensing",
                    "Blending",
                ]
            ),
            "description": f"Proposed change to improve {pick(['efficiency', 'quality', 'safety', 'compliance'])}",
            "requested_by": worker,
            "requested_by_name": WORKER_NAMES.get(worker, "Unknown"),
            "affected_batches": [pick(list(EXISTING_BATCH)) for _ in range(random.randint(0, 3))],
            "created_at": created,
        }
    )

if cc_docs:
    db.change_controls.insert_many(cc_docs)
    print(f"  Inserted {len(cc_docs)} change controls")

# ── Workers ──────────────────────────────────────────────────────────────────

WORKER_TARGET = 50
worker_count = db.workers.count_documents({})
worker_to_add = WORKER_TARGET - worker_count
print(f"Workers: {worker_count} -> adding {worker_to_add}")

INDIAN_NAMES = [
    "Amit Singh",
    "Priya Patel",
    "Rahul Sharma",
    "Neha Gupta",
    "Vikram Desai",
    "Anjali Reddy",
    "Suresh Kumar",
    "Meera Iyer",
    "Rajesh Nair",
    "Pooja Verma",
    "Arjun Mehta",
    "Kavita Joshi",
    "Sanjay Rao",
    "Deepa Menon",
    "Rohan Bhat",
    "Shruti Kulkarni",
    "Manish Tiwari",
    "Divya Pandey",
    "Karthik Subramanian",
    "Nisha Agarwal",
    "Arun Choudhary",
    "Lata Hegde",
    "Vivek Malhotra",
    "Sunita Kulkarni",
    "Aditya Bose",
    "Rekha Pillai",
    "Nitin Saxena",
    "Ashwini Kulkarni",
    "Gaurav Mishra",
    "Swati Deshmukh",
    "Prasad Gokhale",
    "Mamta Banerjee",
    "Tarun Chopra",
    "Ritu Saxena",
    "Sachin Kulkarni",
]

WORKER_ROLES = ["operator", "technician", "engineer", "supervisor", "safety", "qa"]
TITLES = [
    "Operator",
    "Senior Technician",
    "Engineer",
    "Supervisor",
    "Safety Officer",
    "QA Analyst",
]

worker_docs = []
for i in range(worker_to_add):
    wid = f"WRK-WS-{worker_count + i + 1:03d}"
    name = INDIAN_NAMES[i % len(INDIAN_NAMES)]
    role = pick(WORKER_ROLES)
    worker_docs.append(
        {
            "worker_id": wid,
            "name": name,
            "title": pick(TITLES),
            "role": role,
            "reports_to": pick(WORKER_IDS) or "WRK-WS-010",
            "workstation_id": pick(WORKSTATIONS) or "WS-TAB-01-01",
            "status": "Active",
            "created_at": rand_date(60),
            "updated_at": rand_date(30),
            "reports_to_name": "Priya Sharma",
            "user_id": wid,
        }
    )

if worker_docs:
    db.workers.insert_many(worker_docs)
    print(f"  Inserted {len(worker_docs)} workers")

# ── Summary ──────────────────────────────────────────────────────────────────

print("\n=== Final Counts ===")
for coll in [
    "work_orders",
    "production_batches",
    "batch_production_execution_records",
    "approvals",
    "change_controls",
    "workers",
    "instruments",
    "sops",
    "pharmaceutical_machines",
    "users",
    "departments",
]:
    print(f"  {coll:<45} {db[coll].count_documents({}):>5}")

c.close()
print("\nDone.")
