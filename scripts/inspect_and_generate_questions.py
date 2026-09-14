#!/usr/bin/env python3
"""
Read actual MongoDB data and generate 500 grounded smoke test questions.

Connects to the running MongoDB, samples each key collection, then
produces config/grounded_smoke_500.json ready for question_smoke_runner.py.
"""

from __future__ import annotations

import asyncio
import json
import sys
import random
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from motor.motor_asyncio import AsyncIOMotorClient
from services.common.config import settings

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_FILE = REPO_ROOT / "config" / "grounded_smoke_500.json"

random.seed(42)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def pick(docs: list[dict], field: str, fallback: str = "") -> str:
    for d in docs:
        v = d.get(field) or d.get("name") or d.get("id") or ""
        if v:
            return str(v)
    return fallback


def all_values(docs: list[dict], field: str) -> list[str]:
    return [str(d[field]) for d in docs if d.get(field)]


def sample(lst: list, n: int) -> list:
    return random.sample(lst, min(n, len(lst)))


# ---------------------------------------------------------------------------
# main inspector + generator
# ---------------------------------------------------------------------------


async def inspect(db) -> dict[str, list[dict]]:
    """Fetch up to 50 docs from each key collection."""
    COLLECTIONS = [
        "industrial_plants",
        "industrial_lines",
        "industrial_stages",
        "industrial_workstations",
        "pharmaceutical_machines",
        "pharmaceutical_equipment",
        "work_orders",
        "production_batches",
        "batch_execution_records",
        "batch_step_executions",
        "sops",
        "industrial_sops",
        "process_definitions",
        "process_steps",
        "process_prechecks",
        "process_postchecks",
        "process_constraints",
        "maintenance_logs",
        "calibration_logs",
        "cleaning_records",
        "machine_parts",
        "raw_materials",
        "users",
        "workers",
        "industrial_workers",
        "shifts",
        "work_orders",
        "sensors",
        "devices",
        "ipc_result_records",
        "yield_reconciliation_records",
        "batch_release_workflows",
        "equipment_usage_ledger",
        "area_room_usage_ledger",
        "gxp_change_controls",
        "plant_locations",
        "families",
        "classes",
        "subclasses",
    ]

    data: dict[str, list[dict]] = {}
    for coll_name in COLLECTIONS:
        coll = db[coll_name]
        count = await coll.count_documents({})
        if count == 0:
            continue
        docs = await coll.find({}, {"_id": 0}).limit(50).to_list(50)
        data[coll_name] = docs
        print(f"  {coll_name}: {count} docs (sampled {len(docs)})")

    return data


def generate_questions(data: dict[str, list[dict]]) -> list[dict]:
    """Generate 500 grounded questions from the sampled data."""
    questions: list[dict] = []
    q_id = 0

    def add(category: str, question: str, expect_any: list[str]) -> None:
        nonlocal q_id
        q_id += 1
        questions.append(
            {
                "id": f"q{q_id:04d}_{category}",
                "category": category,
                "question": question,
                "expect_any": expect_any,
            }
        )

    # -----------------------------------------------------------------------
    # Plants
    # -----------------------------------------------------------------------
    plants = data.get("industrial_plants", [])
    for p in sample(plants, 8):
        name = p.get("name") or p.get("plant_name") or ""
        pid = p.get("plant_id") or p.get("id") or ""
        loc = p.get("location") or p.get("city") or p.get("country") or ""
        if name:
            add(
                "shop_floor_topology",
                f"What is the status of {name}?",
                [name, "status", "plant"],
            )
            add(
                "shop_floor_topology",
                f"What lines are configured in {name}?",
                [name, "line", "lines"],
            )
            add(
                "shop_floor_topology",
                f"Where is {name} located?",
                [name, "location", loc] if loc else [name, "location", "plant"],
            )
            add(
                "shop_floor_topology",
                f"Describe the production layout of {name}.",
                [name, "plant", "line"],
            )

    if plants:
        add(
            "shop_floor_topology",
            "How many plants are in the system?",
            ["plant", "plants"],
        )
        add("shop_floor_topology", "List all plants in the system.", ["plant", "plants"])

    # -----------------------------------------------------------------------
    # Lines
    # -----------------------------------------------------------------------
    lines = data.get("industrial_lines", [])
    for l in sample(lines, 10):
        name = l.get("name") or l.get("line_name") or ""
        plant = l.get("plant_id") or l.get("plant_name") or ""
        if name:
            add(
                "shop_floor_topology",
                f"What stages are in {name}?",
                [name, "stage", "stages"],
            )
            add(
                "shop_floor_topology",
                f"What is the status of {name}?",
                [name, "status", "line"],
            )
            add(
                "shop_floor_topology",
                f"What is the throughput of {name}?",
                [name, "throughput", "line"],
            )
            add(
                "shop_floor_topology",
                f"How many workstations are in {name}?",
                [name, "workstation", "workstations"],
            )

    if lines:
        add(
            "shop_floor_topology",
            "How many production lines are configured?",
            ["line", "lines"],
        )
        add("shop_floor_topology", "List all production lines.", ["line", "lines"])

    # -----------------------------------------------------------------------
    # Stages
    # -----------------------------------------------------------------------
    stages = data.get("industrial_stages", [])
    for s in sample(stages, 12):
        name = s.get("name") or s.get("stage_name") or ""
        line_id = s.get("line_id") or s.get("line_name") or ""
        takt = s.get("takt_time")
        eff = s.get("efficiency")
        status = s.get("status") or ""
        if name:
            add(
                "shop_floor_topology",
                f"What workstations are in the {name} stage?",
                [name, "workstation"],
            )
            add(
                "shop_floor_topology",
                f"What is the status of the {name} stage?",
                [name, "status"] if status else [name, "stage"],
            )
            if takt:
                add(
                    "production_kpi",
                    f"What is the takt time of {name}?",
                    [name, str(takt)[:4]],
                )
            if eff:
                add(
                    "production_kpi",
                    f"What is the efficiency of {name}?",
                    [name, str(eff)[:2]],
                )
            add(
                "shop_floor_topology",
                f"Which line does the {name} stage belong to?",
                [name, "line"],
            )

    if stages:
        add(
            "shop_floor_topology",
            "How many stages are configured?",
            ["stage", "stages"],
        )
        add(
            "production_kpi",
            "Which stages have efficiency below 90%?",
            ["stage", "efficiency"],
        )
        add(
            "production_kpi",
            "Which stage has the longest takt time?",
            ["stage", "takt"],
        )

    # -----------------------------------------------------------------------
    # Workstations
    # -----------------------------------------------------------------------
    workstations = data.get("industrial_workstations", [])
    for ws in sample(workstations, 12):
        name = ws.get("name") or ws.get("workstation_name") or ""
        stage_id = ws.get("stage_id") or ws.get("stage_name") or ""
        line_id = ws.get("line_id") or ws.get("line_name") or ""
        if name:
            add("assets", f"What machines are assigned to {name}?", [name, "machine"])
            add("assets", f"What equipment is at {name}?", [name, "equipment"])
            add(
                "shop_floor_topology",
                f"Which stage does {name} belong to?",
                [name, "stage"],
            )
            add("workers", f"Who is assigned to {name}?", [name, "worker", "assigned"])

    if workstations:
        add(
            "shop_floor_topology",
            "How many workstations are configured?",
            ["workstation", "workstations"],
        )

    # -----------------------------------------------------------------------
    # Machines
    # -----------------------------------------------------------------------
    machines = data.get("pharmaceutical_machines", [])
    for m in sample(machines, 15):
        name = m.get("name") or m.get("machine_name") or ""
        mid = m.get("machine_id") or m.get("id") or ""
        ws = m.get("workstation_id") or m.get("workstation_name") or ""
        mtype = m.get("machine_type") or m.get("type") or ""
        status = m.get("status") or m.get("operational_status") or ""
        if name:
            add(
                "assets",
                f"What is the status of {name}?",
                [name, "status"] if status else [name, "machine"],
            )
            add(
                "assets",
                f"Which workstation is {name} assigned to?",
                [name, "workstation"],
            )
            add("workorders", f"Show work orders for {name}.", [name, "work order"])
            add(
                "workorders",
                f"What is the latest work order for {name}?",
                [name, "work order", "latest"],
            )
            add(
                "maintenance",
                f"Show maintenance events for {name}.",
                [name, "maintenance"],
            )
            if mtype:
                add("assets", f"What type of machine is {name}?", [name, mtype])

    if machines:
        add("assets", "How many machines are in the system?", ["machine", "machines"])
        add("assets", "List all machines.", ["machine", "machines"])
        add(
            "assets",
            "Which machines are operational?",
            ["machine", "operational", "status"],
        )

    # -----------------------------------------------------------------------
    # Equipment
    # -----------------------------------------------------------------------
    equipment = data.get("pharmaceutical_equipment", [])
    for e in sample(equipment, 10):
        name = e.get("name") or e.get("equipment_name") or ""
        status = e.get("status") or ""
        ws = e.get("workstation_id") or e.get("workstation_name") or ""
        if name:
            add(
                "assets",
                f"What is the status of {name}?",
                [name, "status"] if status else [name, "equipment"],
            )
            add(
                "assets",
                f"Which workstation is {name} assigned to?",
                [name, "workstation"],
            )
            add(
                "maintenance",
                f"What calibration records exist for {name}?",
                [name, "calibration"],
            )

    if equipment:
        add("assets", "How many equipment records are in the system?", ["equipment"])
        add(
            "assets",
            "Which equipment is due for maintenance?",
            ["equipment", "maintenance", "due"],
        )

    # -----------------------------------------------------------------------
    # Work Orders
    # -----------------------------------------------------------------------
    work_orders = data.get("work_orders", [])
    for wo in sample(work_orders, 15):
        woid = wo.get("work_order_id") or wo.get("id") or ""
        status = wo.get("status") or ""
        mname = wo.get("machine_name") or wo.get("machine_id") or ""
        wtype = wo.get("work_order_type") or wo.get("type") or ""
        if woid:
            add(
                "workorders",
                f"What is the status of work order {woid}?",
                [woid, "status"],
            )
            add(
                "workorders",
                f"Show details for work order {woid}.",
                [woid, "work order"],
            )
            if status:
                add(
                    "workorders",
                    f"Is work order {woid} {status.lower()}?",
                    [woid, status],
                )

    if work_orders:
        add("workorders", "How many work orders are open?", ["work order", "open"])
        add("workorders", "List all pending work orders.", ["work order", "pending"])
        add(
            "workorders",
            "What statuses are valid for a work order?",
            ["status", "valid", "work order"],
        )
        add(
            "workorders",
            "What events were created for the latest work order?",
            ["event", "work order"],
        )

    # -----------------------------------------------------------------------
    # Production Batches
    # -----------------------------------------------------------------------
    batches = data.get("production_batches", [])
    for b in sample(batches, 10):
        bid = b.get("batch_id") or b.get("id") or ""
        bname = b.get("name") or b.get("batch_name") or bid
        product = b.get("product_name") or b.get("product_id") or ""
        status = b.get("status") or ""
        if bid:
            add(
                "batch_records",
                f"Show batch execution records for {bname}.",
                [bname, "batch", "execution"] if bname else [bid, "batch"],
            )
            add(
                "batch_records",
                f"What is the status of batch {bid}?",
                [bid, "status"] if status else [bid, "batch"],
            )
            if product:
                add(
                    "batch_records",
                    f"Which product is batch {bid} for?",
                    [bid, product],
                )

    if batches:
        add("batch_records", "List recent batch records.", ["batch", "record"])
        add(
            "batch_records",
            "Show batch execution records for the latest production batch.",
            ["batch", "execution"],
        )
        add(
            "batch_records",
            "Which batch release workflows are pending?",
            ["batch", "release", "pending"],
        )
        add(
            "batch_records",
            "Show yield reconciliation for the latest batch.",
            ["yield", "reconciliation", "batch"],
        )

    # -----------------------------------------------------------------------
    # SOPs
    # -----------------------------------------------------------------------
    sops = data.get("sops", []) + data.get("industrial_sops", [])
    for sop in sample(sops, 12):
        title = sop.get("title") or sop.get("name") or sop.get("sop_name") or ""
        sid = sop.get("sop_id") or sop.get("id") or ""
        ws = sop.get("workstation_id") or sop.get("workstation_name") or ""
        if title:
            add("process_and_sop", f"Summarize the SOP: {title}.", [title[:30], "SOP"])
            add(
                "process_and_sop",
                f"What are the steps in the {title} SOP?",
                [title[:30], "step"],
            )
            add(
                "process_and_sop",
                f"Who approves the {title} SOP?",
                [title[:30], "approval"],
            )

    # -----------------------------------------------------------------------
    # Process Definitions
    # -----------------------------------------------------------------------
    proc_defs = data.get("process_definitions", [])
    for pd in sample(proc_defs, 8):
        name = pd.get("name") or pd.get("process_definition_name") or ""
        if name:
            add(
                "process_and_sop",
                f"What steps are in the {name} process?",
                [name, "step"],
            )
            add(
                "process_and_sop",
                f"What constraints apply to {name}?",
                [name, "constraint"],
            )

    # -----------------------------------------------------------------------
    # Maintenance Logs
    # -----------------------------------------------------------------------
    maint = data.get("maintenance_logs", [])
    for m in sample(maint, 10):
        machine = m.get("machine_name") or m.get("machine_id") or ""
        mtype = m.get("maintenance_type") or m.get("type") or ""
        status = m.get("status") or ""
        date = m.get("scheduled_date") or m.get("date") or ""
        if machine:
            add(
                "maintenance",
                f"Show maintenance history for {machine}.",
                [machine, "maintenance"],
            )
            if mtype:
                add(
                    "maintenance",
                    f"What {mtype} maintenance was done on {machine}?",
                    [machine, mtype, "maintenance"],
                )

    if maint:
        add(
            "maintenance",
            "Which maintenance tasks are overdue?",
            ["maintenance", "overdue", "due"],
        )
        add(
            "maintenance",
            "Show all scheduled maintenance events.",
            ["maintenance", "scheduled"],
        )

    # -----------------------------------------------------------------------
    # Calibration Logs
    # -----------------------------------------------------------------------
    calib = data.get("calibration_logs", [])
    for c in sample(calib, 8):
        machine = c.get("machine_name") or c.get("machine_id") or c.get("equipment_id") or ""
        status = c.get("status") or c.get("calibration_status") or ""
        if machine:
            add(
                "maintenance",
                f"What calibration records exist for {machine}?",
                [machine, "calibration"],
            )
            if status:
                add(
                    "maintenance",
                    f"What is the calibration status of {machine}?",
                    [machine, "calibration", status],
                )

    if calib:
        add(
            "maintenance",
            "Which calibration records are due or pending?",
            ["calibration", "due", "pending"],
        )
        add(
            "maintenance",
            "Which equipment requires re-calibration?",
            ["calibration", "equipment"],
        )

    # -----------------------------------------------------------------------
    # Cleaning Records
    # -----------------------------------------------------------------------
    cleaning = data.get("cleaning_records", [])
    for c in sample(cleaning, 6):
        machine = c.get("machine_name") or c.get("machine_id") or ""
        status = c.get("status") or ""
        if machine:
            add(
                "maintenance",
                f"Show cleaning records for {machine}.",
                [machine, "cleaning"],
            )

    if cleaning:
        add(
            "maintenance",
            "Which cleaning records are linked to process execution?",
            ["cleaning", "process", "execution"],
        )
        add("maintenance", "List recent cleaning records.", ["cleaning", "record"])

    # -----------------------------------------------------------------------
    # Machine Parts
    # -----------------------------------------------------------------------
    parts = data.get("machine_parts", [])
    for p in sample(parts, 8):
        name = p.get("name") or p.get("part_name") or ""
        qty = p.get("quantity") or p.get("stock_quantity") or 0
        mname = p.get("machine_name") or p.get("machine_id") or ""
        if name:
            add("assets", f"What is the stock level for {name}?", [name, "stock"])
            if mname:
                add("assets", f"Which parts are used in {mname}?", [mname, "part"])

    if parts:
        add("assets", "Which machine parts are low stock?", ["part", "stock", "machine"])
        add("assets", "List all machine parts.", ["part", "machine"])

    # -----------------------------------------------------------------------
    # IPC Results
    # -----------------------------------------------------------------------
    ipc = data.get("ipc_result_records", [])
    for i in sample(ipc, 6):
        batch = i.get("batch_id") or i.get("batch_name") or ""
        param = i.get("parameter") or i.get("test_parameter") or ""
        if param:
            add("quality", f"Show IPC results for {param}.", [param, "IPC", "result"])
        if batch:
            add(
                "quality",
                f"Show IPC result records for batch {batch}.",
                [batch, "IPC", "result"],
            )

    if ipc:
        add(
            "quality",
            "Show IPC result records for content uniformity.",
            ["IPC", "content uniformity", "result"],
        )
        add(
            "quality",
            "Which IPC checks failed in the last batch?",
            ["IPC", "failed", "batch"],
        )

    # -----------------------------------------------------------------------
    # Yield Reconciliation
    # -----------------------------------------------------------------------
    yield_rec = data.get("yield_reconciliation_records", [])
    if yield_rec:
        add(
            "batch_records",
            "Show yield reconciliation for the latest batch.",
            ["yield", "reconciliation"],
        )
        add(
            "batch_records",
            "What is the yield rate for the latest production run?",
            ["yield", "rate", "production"],
        )

    # -----------------------------------------------------------------------
    # Workers / Users
    # -----------------------------------------------------------------------
    workers = data.get("workers", []) + data.get("industrial_workers", [])
    for w in sample(workers, 10):
        name = w.get("name") or w.get("worker_name") or ""
        role = w.get("role") or w.get("position") or ""
        stage = w.get("stage_id") or w.get("stage_name") or ""
        ws = w.get("workstation_id") or w.get("workstation_name") or ""
        if name:
            add(
                "workers",
                f"What is the role of {name}?",
                [name, "role"] if role else [name, "worker"],
            )
            if stage:
                add(
                    "workers",
                    f"Which workers are at the {stage} stage?",
                    [stage, "worker"],
                )

    if workers:
        add(
            "workers",
            "Who reports to the line manager?",
            ["manager", "reports", "worker"],
        )
        add(
            "workers",
            "Which workers are assigned to the blending stage?",
            ["worker", "blending", "assigned"],
        )
        add("workers", "List all workers in the system.", ["worker", "workers"])

    # -----------------------------------------------------------------------
    # Sensors / IoT
    # -----------------------------------------------------------------------
    sensors = data.get("sensors", [])
    for s in sample(sensors, 8):
        name = s.get("name") or s.get("sensor_name") or ""
        stype = s.get("sensor_type") or s.get("type") or ""
        ws = s.get("workstation_id") or s.get("workstation_name") or ""
        if name:
            add(
                "telemetry_optional",
                f"What is the latest reading for {name}?",
                [name, "reading", "sensor"],
            )
            if ws:
                add("telemetry_optional", f"Which sensors are at {ws}?", [ws, "sensor"])
            if stype:
                add(
                    "telemetry_optional",
                    f"What happens if the {name} exceeds threshold?",
                    [name, stype, "threshold"],
                )

    if sensors:
        add(
            "telemetry_optional",
            "What is the latest sensor reading for the blender?",
            ["sensor", "reading", "blender"],
        )
        add(
            "telemetry_optional",
            "List all sensors in the system.",
            ["sensor", "sensors"],
        )

    # -----------------------------------------------------------------------
    # Compliance / GXP
    # -----------------------------------------------------------------------
    change_controls = data.get("gxp_change_controls", [])
    for cc in sample(change_controls, 8):
        ccid = cc.get("change_control_id") or cc.get("id") or ""
        title = cc.get("title") or cc.get("name") or ""
        status = cc.get("status") or ""
        if title:
            add(
                "compliance",
                f"What is the status of change control: {title[:40]}?",
                ([title[:30], "change control"] if status else [title[:30], "compliance"]),
            )
        if ccid:
            add(
                "compliance",
                f"Show details for change control {ccid}.",
                [ccid, "change control"],
            )

    add("compliance", "Show pending approvals.", ["approval", "pending"])
    add("compliance", "Show recent Part 11 audit events.", ["audit", "Part 11"])
    add("compliance", "Which change controls are open?", ["change control", "open"])
    add("compliance", "List all compliance records.", ["compliance", "record"])

    # -----------------------------------------------------------------------
    # Integration / World model / negative
    # -----------------------------------------------------------------------
    add(
        "integration_backed",
        "What warehouse inventory movements are visible from WMS integration?",
        ["warehouse", "inventory", "WMS"],
    )
    add(
        "integration_backed",
        "What maintenance events came from CMMS integration?",
        ["maintenance", "CMMS"],
    )
    add(
        "integration_backed",
        "What controlled documents came from the document management integration?",
        ["document", "integration"],
    )
    add(
        "integration_backed",
        "What LIMS sample results are available for the latest batch?",
        ["LIMS", "sample", "batch"],
    )

    add(
        "world_model_reasoning",
        "If I increase blender speed by 10%, what impact will it have on content uniformity?",
        ["Recommendation", "Throughput", "Quality", "Approval"],
    )
    add(
        "world_model_reasoning",
        "What happens if a machine is taken offline for maintenance during a batch run?",
        ["impact", "batch", "maintenance"],
    )
    add(
        "world_model_reasoning",
        "What is the cascading impact of a blending stage failure?",
        ["impact", "failure", "stage"],
    )
    add(
        "world_model_reasoning",
        "Can we add a preventive work order for a high-temperature scenario?",
        ["preventive", "work order", "approval"],
    )

    add(
        "negative_controls",
        "Who won the cricket match yesterday?",
        ["not have", "unsupported", "local system", "graph intent", "cannot"],
    )
    add(
        "negative_controls",
        "What is the weather in Mumbai?",
        ["not have", "unsupported", "local system", "graph intent", "cannot"],
    )
    add("negative_controls", "yes", ["full graph question", "ask the full", "clarify"])

    # -----------------------------------------------------------------------
    # Batch Release
    # -----------------------------------------------------------------------
    batch_release = data.get("batch_release_workflows", [])
    for br in sample(batch_release, 6):
        bid = br.get("batch_id") or br.get("id") or ""
        status = br.get("status") or ""
        if bid:
            add(
                "batch_records",
                f"Show batch release workflow for batch {bid}.",
                [bid, "release", "workflow"],
            )

    if batch_release:
        add(
            "batch_records",
            "Which batch release workflows are pending approval?",
            ["batch", "release", "pending"],
        )

    # -----------------------------------------------------------------------
    # Equipment Usage Ledger
    # -----------------------------------------------------------------------
    eq_ledger = data.get("equipment_usage_ledger", [])
    for el in sample(eq_ledger, 6):
        ename = el.get("equipment_name") or el.get("equipment_id") or ""
        if ename:
            add(
                "batch_records",
                f"Show equipment usage for {ename}.",
                [ename, "usage", "equipment"],
            )

    if eq_ledger:
        add(
            "batch_records",
            "Show equipment usage ledger for the latest batch.",
            ["equipment", "usage", "ledger"],
        )

    # -----------------------------------------------------------------------
    # Plant Locations
    # -----------------------------------------------------------------------
    locations = data.get("plant_locations", [])
    for loc in sample(locations, 6):
        name = loc.get("name") or loc.get("location_name") or ""
        ltype = loc.get("location_type") or loc.get("type") or ""
        if name:
            add("shop_floor_topology", f"What is located at {name}?", [name, "location"])
            if ltype:
                add(
                    "shop_floor_topology",
                    f"What {ltype}s are in the system?",
                    [ltype, "location"],
                )

    if locations:
        add("shop_floor_topology", "List all plant locations.", ["location", "plant"])

    # -----------------------------------------------------------------------
    # Taxonomy (families, classes, subclasses)
    # -----------------------------------------------------------------------
    families = data.get("families", []) + data.get("equipment_families", [])
    for f in sample(families, 4):
        name = f.get("name") or ""
        if name:
            add(
                "assets",
                f"What machines are in the {name} family?",
                [name, "family", "machine"],
            )

    classes = data.get("classes", []) + data.get("equipment_classes", [])
    for c in sample(classes, 4):
        name = c.get("name") or ""
        if name:
            add(
                "assets",
                f"What equipment belongs to class {name}?",
                [name, "class", "equipment"],
            )

    # -----------------------------------------------------------------------
    # Production KPIs (generic)
    # -----------------------------------------------------------------------
    add("production_kpi", "Show Quality and Cost KPIs.", ["Quality", "KPI", "Cost"])
    add(
        "production_kpi",
        "What is the overall equipment effectiveness (OEE)?",
        ["OEE", "effectiveness", "equipment"],
    )
    add(
        "production_kpi",
        "What is the throughput rate for the latest production run?",
        ["throughput", "production"],
    )
    add(
        "production_kpi",
        "Which production line has the highest efficiency?",
        ["line", "efficiency"],
    )
    add(
        "production_kpi",
        "What is the yield rate for the latest OSD production?",
        ["yield", "OSD", "production"],
    )

    # -----------------------------------------------------------------------
    # Documents / Research
    # -----------------------------------------------------------------------
    add(
        "documents",
        "Summarize the indexed blender speed SOP document.",
        ["SOP", "blender", "document"],
    )
    add(
        "documents",
        "What uploaded document evidence supports blender speed changes?",
        ["document", "evidence", "blender"],
    )
    add(
        "documents",
        "List all controlled documents in the system.",
        ["document", "controlled"],
    )

    # -----------------------------------------------------------------------
    # Module control / system meta
    # -----------------------------------------------------------------------
    add(
        "module_control_boundary",
        "Are warehouse, IoT, order modules hidden unless enabled?",
        ["hidden", "enabled", "Module Control"],
    )
    add(
        "module_control_boundary",
        "Which integrations can add nodes to the graph?",
        ["SAP", "Oracle", "Veeva", "LIMS", "WMS", "CMMS", "integration"],
    )

    # -----------------------------------------------------------------------
    # Trim / Pad to 500
    # -----------------------------------------------------------------------
    print(f"\nGenerated {len(questions)} questions before trimming.")

    # Deduplicate by question text
    seen: set[str] = set()
    unique: list[dict] = []
    for q in questions:
        if q["question"] not in seen:
            seen.add(q["question"])
            unique.append(q)

    # If over 500, sample 500 keeping category balance
    if len(unique) > 500:
        # Stratified sample
        from collections import defaultdict

        by_cat: dict[str, list] = defaultdict(list)
        for q in unique:
            by_cat[q["category"]].append(q)
        final: list[dict] = []
        while len(final) < 500 and any(by_cat.values()):
            for cat in list(by_cat.keys()):
                if by_cat[cat] and len(final) < 500:
                    final.append(by_cat[cat].pop(0))
        unique = final

    # Renumber IDs
    for i, q in enumerate(unique, 1):
        q["id"] = f"q{i:04d}_{q['category']}"

    return unique


async def main() -> None:
    print(f"Connecting to MongoDB at {settings.MONGODB_URL} / {settings.DB_NAME} ...")
    client = AsyncIOMotorClient(settings.MONGODB_URL, serverSelectionTimeoutMS=3000)
    db = client[settings.DB_NAME]

    print("\nInspecting collections...")
    data = await inspect(db)

    if not data:
        print("ERROR: No collections found or MongoDB is empty. Make sure the DB is seeded.")
        sys.exit(1)

    client.close()

    print("\nGenerating questions...")
    questions = generate_questions(data)
    print(f"Final question count: {len(questions)}")

    # Write output
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "2.0.0",
        "description": "Auto-generated grounded smoke test suite from live MongoDB data.",
        "defaults": {
            "target_count": len(questions),
            "endpoint": "/api/v1/agentos/query",
            "top_k": 8,
            "traversal_depth": 1,
            "path_limit_per_hit": 5,
            "role": "admin",
            "user_id": "grounded-smoke-runner",
            "use_retrieval": False,
            "use_memory": False,
            "bypass_cache": True,
        },
        "questions": questions,
    }

    OUTPUT_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"\nWritten to {OUTPUT_FILE}")

    # Print category breakdown
    from collections import Counter

    cats = Counter(q["category"] for q in questions)
    print("\nCategory breakdown:")
    for cat, count in sorted(cats.items(), key=lambda x: -x[1]):
        print(f"  {cat:40s} {count:3d}")


if __name__ == "__main__":
    asyncio.run(main())
