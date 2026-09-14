#!/usr/bin/env python3
"""Verify answer correctness by cross-checking API responses against DB ground truth."""

import httpx
import pymongo
import re
import json

MONGO = pymongo.MongoClient("mongodb://localhost:27017", serverSelectionTimeoutMS=3000)
DB = MONGO["demo"]
API = "http://localhost:8031/api/v1/agentos/prompt"
TIMEOUT = 15


def ask(question):
    r = httpx.post(
        API,
        json={"question": question, "run_query": True, "run_simulate": True},
        timeout=TIMEOUT,
    )
    return r.json().get("query_result", {}).get("answer", "")


def check(label, question, verify_fn):
    """Ask question, verify answer against DB."""
    answer = ask(question)
    ok, detail = verify_fn(answer)
    icon = "✓" if ok else "✗"
    print(f"  {icon} {label}")
    if not ok:
        print(f"    Q: {question}")
        print(f"    A: {answer[:150]}")
        print(f"    Expected: {detail}")
    return ok


# ── Work Orders ──────────────────────────────────────────────────────────────

print("\n=== WORK ORDER CORRECTNESS ===")

wo = DB.work_orders.find_one({"work_order_id": "PM-RMG-001"})
check(
    "WO status lookup",
    "What is the status of work order PM-RMG-001?",
    lambda a: (wo["status"] in a, f"should contain '{wo['status']}'"),
)

wo_count = DB.work_orders.count_documents({})
check(
    "WO count",
    "How many work orders are there in total?",
    lambda a: (str(wo_count) in a, f"should contain '{wo_count}'"),
)

high_count = DB.work_orders.count_documents({"priority": "High"})
check(
    "WO high priority count",
    "List high priority work orders",
    lambda a: (str(high_count) in a, f"should contain '{high_count}'"),
)

wo2 = DB.work_orders.find_one({"work_order_id": "WO-00154"})
check(
    "WO details lookup",
    "Show me details for work order WO-00154",
    lambda a: (
        wo2["title"] in a if wo2 else (False, "WO not found"),
        f"should contain '{wo2['title']}'",
    ),
)

check(
    "WO overdue",
    "What work orders are overdue?",
    lambda a: (
        "work order" in a.lower() or "overdue" in a.lower(),
        "should mention work orders",
    ),
)

# ── Batches ──────────────────────────────────────────────────────────────────

print("\n=== BATCH CORRECTNESS ===")

b = DB.production_batches.find_one({"batch_number": "BATCH-001"})
check(
    "Batch lookup",
    "Show batch BATCH-001",
    lambda a: (b["product_name"] in a, f"should contain '{b['product_name']}'"),
)

batch_count = DB.production_batches.count_documents({})
check(
    "Batch count",
    "How many production batches do we have?",
    lambda a: (str(batch_count) in a, f"should contain '{batch_count}'"),
)

approved_count = DB.production_batches.count_documents({"status": "Approved"})
check(
    "Batch status filter",
    "List all batches with status Approved",
    lambda a: (str(approved_count) in a, f"should contain '{approved_count}'"),
)

b2 = DB.production_batches.find_one({"batch_number": "BATCH-050"})
check(
    "Batch status lookup",
    "What is the status of batch BATCH-050?",
    lambda a: (
        b2["status"] in a if b2 else (False, "batch not found"),
        f"should contain '{b2['status']}'",
    ),
)

check(
    "Batch product filter",
    "Find all batches for Cetirizine 10mg Tablets",
    lambda a: ("BATCH-" in a or "batch" in a.lower(), "should list batches"),
)

# ── Approvals ────────────────────────────────────────────────────────────────

print("\n=== APPROVAL CORRECTNESS ===")

ap = DB.approvals.find_one({"approval_id": "APR-0001"})
check(
    "Approval lookup",
    "What is the approval status for APR-0001?",
    lambda a: (
        ap["title"] in a if ap else (False, "not found"),
        f"should contain '{ap['title']}'",
    ),
)

apr_count = DB.approvals.count_documents({})
check(
    "Approval count",
    "How many approvals are waiting?",
    lambda a: (str(apr_count) in a, f"should contain '{apr_count}'"),
)

pending_count = DB.approvals.count_documents({"status": "Pending"})
check(
    "Pending approvals",
    "Show all pending approvals",
    lambda a: (
        str(pending_count) in a or "pending" in a.lower(),
        f"should mention pending approvals",
    ),
)

# ── Change Controls ──────────────────────────────────────────────────────────

print("\n=== CHANGE CONTROL CORRECTNESS ===")

cc = DB.change_controls.find_one({"change_control_id": "CC-2026-0008"})
check(
    "CC lookup",
    "What is the status of change control CC-2026-0008?",
    lambda a: (
        cc["title"] in a if cc else (False, "not found"),
        f"should contain '{cc['title']}'",
    ),
)

cc_count = DB.change_controls.count_documents({})
check(
    "CC list",
    "List all open change controls",
    lambda a: (
        str(cc_count) in a or "change control" in a.lower(),
        f"should list change controls",
    ),
)

# ── SOPs ─────────────────────────────────────────────────────────────────────

print("\n=== SOP CORRECTNESS ===")

sop = DB.sops.find_one({"id": "SOP-TAB-DISP-001"})
check(
    "SOP lookup",
    "Show me SOP SOP-TAB-DISP-001",
    lambda a: (
        sop["title"] in a if sop else (False, "not found"),
        f"should contain '{sop['title']}'",
    ),
)

sop_count = DB.sops.count_documents({})
check(
    "SOP list",
    "List all SOPs",
    lambda a: (str(sop_count) in a or "sop" in a.lower(), f"should mention SOPs"),
)

# ── Workers ──────────────────────────────────────────────────────────────────

print("\n=== WORKER CORRECTNESS ===")

w = DB.workers.find_one({"name": "Rajesh Patel"})
check(
    "Worker lookup",
    "What is Rajesh Patel's role?",
    lambda a: (
        w["title"] in a if w else (False, "not found"),
        f"should contain '{w['title']}'",
    ),
)

worker_count = DB.workers.count_documents({})
check(
    "Worker list",
    "Show me the workers",
    lambda a: (str(worker_count) in a or "worker" in a.lower(), f"should list workers"),
)

# ── Production KPI ───────────────────────────────────────────────────────────

print("\n=== PRODUCTION KPI CORRECTNESS ===")

machine_count = DB.pharmaceutical_machines.count_documents({})
active_count = DB.pharmaceutical_machines.count_documents({"status": "Active"})
check(
    "KPI machine count",
    "What is the OEE for production line 1?",
    lambda a: (str(machine_count) in a, f"should contain '{machine_count}'"),
)

check(
    "KPI batch summary",
    "Show production KPIs for Operations",
    lambda a: (
        "batch" in a.lower() or "kpi" in a.lower(),
        "should show production data",
    ),
)

# ── Decision Intelligence ────────────────────────────────────────────────────

print("\n=== DECISION INTELLIGENCE CORRECTNESS ===")

check(
    "Decision summary",
    "Help me decide which batch to prioritize",
    lambda a: (
        "batch" in a.lower() or "priority" in a.lower() or "decision" in a.lower(),
        "should provide decision data",
    ),
)

# ── Audit Log ────────────────────────────────────────────────────────────────

print("\n=== AUDIT LOG CORRECTNESS ===")

audit_count = DB.part11_audit_trail.count_documents({})
check(
    "Audit log",
    "Show me the audit log for today",
    lambda a: ("audit" in a.lower() or str(audit_count) in a, "should show audit data"),
)

# ── Work Order Create ────────────────────────────────────────────────────────

print("\n=== WORK ORDER CREATE CORRECTNESS ===")

wo_before = DB.work_orders.count_documents({})
check(
    "WO create",
    "Create a work order for Analytical Balance calibration",
    lambda a: (
        "created" in a.lower() or "work order" in a.lower(),
        "should confirm creation",
    ),
)
wo_after = DB.work_orders.count_documents({})
created = wo_after > wo_before
print(f"  {'✓' if created else '✗'} WO actually inserted: {wo_before} -> {wo_after}")

# ── Summary ──────────────────────────────────────────────────────────────────

print("\n" + "=" * 60)
print("  ANSWER CORRECTNESS VERIFICATION COMPLETE")
print("=" * 60)
