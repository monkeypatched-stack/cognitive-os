#!/usr/bin/env python3
"""Seed InfluxDB — fixed escaping for batch writes."""

import json
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen

INFLUX_URL = os.getenv("INFLUXDB_URL", "http://localhost:8181")
INFLUX_TOKEN = os.getenv("INFLUXDB_TOKEN", "")
INFLUX_DB = os.getenv("INFLUXDB_BUCKET", "events")

if not INFLUX_TOKEN:
    sys.exit("INFLUXDB_TOKEN is not set — export it before running this script.")

MACHINES = [
    {"id": "MACH-BLEND-001", "name": "Bin-Blender", "stage": "Blending"},
    {"id": "MACH-COMP-001", "name": "Tablet-Press", "stage": "Compression"},
    {"id": "MACH-COAT-001", "name": "Coating-Pan", "stage": "Film-Coating"},
    {"id": "MACH-DRY-001", "name": "Fluid-Bed-Dryer", "stage": "Drying"},
    {"id": "MACH-MILL-001", "name": "Cone-Mill", "stage": "Granulation"},
    {"id": "MACH-GRAN-001", "name": "High-Shear-Granulator", "stage": "Granulation"},
    {"id": "MACH-COMP-003", "name": "Metal-Detector", "stage": "Inspection"},
    {"id": "MACH-PACK-002", "name": "Cartoner", "stage": "Packaging"},
    {"id": "MACH-PACK-004", "name": "Checkweigher", "stage": "Packaging"},
]
TECHNICIANS = ["Prashun-Javeri", "Rajiv-Malhotra", "Karan-Trivedi", "Aarav-Mehta"]
SHIFTS = ["Day-Shift", "Evening-Shift", "Night-Shift"]
random.seed(42)
now = datetime.now(timezone.utc)


def safe_json(obj):
    """Escape double quotes for line protocol."""
    return json.dumps(obj).replace('"', '\\"')


def batch_write(lines):
    if not lines:
        return 0
    url = f"{INFLUX_URL}/api/v2/write?org=default&bucket={INFLUX_DB}&precision=ns"
    data = "\n".join(lines).encode("utf-8")
    req = Request(url, data=data, method="POST")
    req.add_header("Content-Type", "text/plain")
    req.add_header("Authorization", f"Bearer {INFLUX_TOKEN}")
    try:
        with urlopen(req, timeout=15) as resp:
            return len(lines) if resp.status in (200, 204) else 0
    except Exception as e:
        print(f"  ERROR: {e}")
        # Try one by one on failure
        ok = 0
        for line in lines:
            try:
                req2 = Request(url, data=line.encode("utf-8"), method="POST")
                req2.add_header("Content-Type", "text/plain")
                req2.add_header("Authorization", f"Bearer {INFLUX_TOKEN}")
                with urlopen(req2, timeout=5) as resp2:
                    if resp2.status in (200, 204):
                        ok += 1
            except Exception:
                pass  # Skip failed writes during seeding
        return ok


def seed_maintenance():
    print("Seeding maintenance_log...", end=" ", flush=True)
    lines = []
    for m in MACHINES:
        for d in range(30):
            ts = now - timedelta(days=d, hours=random.randint(0, 23))
            ts_ns = int(ts.timestamp() * 1e9)
            evt = random.choice(["Preventive", "Corrective", "Calibration"])
            st = random.choice(["Completed", "Completed", "In Progress"])
            tech = random.choice(TECHNICIANS)
            eid = f"MAIN-{random.randint(10000000, 99999999):08X}"
            payload = safe_json(
                {
                    "id": eid,
                    "type": evt,
                    "machine_id": m["id"],
                    "machine_name": m["name"],
                    "stage": m["stage"],
                    "status": st,
                    "technician": tech,
                    "date": ts.strftime("%Y-%m-%d"),
                }
            )
            tags = f"machine_id={m['id']},machine_name={m['name']},stage={m['stage']},event_type={evt},status={st},technician={tech}"
            lines.append(f'maintenance_log,{tags} payload_json="{payload}",payload_type="json" {ts_ns}')
    n = batch_write(lines)
    print(f"{n} records")
    return n


def seed_downtime():
    print("Seeding downtime_log...", end=" ", flush=True)
    lines = []
    for m in MACHINES:
        for d in range(30):
            if random.random() > 0.35:
                continue
            ts = now - timedelta(days=d, hours=random.randint(0, 23))
            ts_ns = int(ts.timestamp() * 1e9)
            dur = random.randint(10, 480)
            reason = random.choice(["Mechanical-Failure", "Electrical-Fault", "Operator-Error"])
            impact = random.choice(["Production-Halt", "Reduced-Output", "Quality-Hold"])
            eid = f"DT-{random.randint(10000000, 99999999):08X}"
            payload = safe_json(
                {
                    "id": eid,
                    "type": "downtime",
                    "machine_id": m["id"],
                    "machine_name": m["name"],
                    "stage": m["stage"],
                    "duration_min": dur,
                    "reason": reason,
                    "impact": impact,
                    "date": ts.strftime("%Y-%m-%d"),
                }
            )
            tags = f"machine_id={m['id']},machine_name={m['name']},stage={m['stage']},reason={reason},impact={impact}"
            lines.append(f'downtime_log,{tags} payload_json="{payload}",payload_type="json" {ts_ns}')
    n = batch_write(lines)
    print(f"{n} records")
    return n


def seed_calibration():
    print("Seeding calibration_log...", end=" ", flush=True)
    EQUIP = [
        {"id": "EQP-001", "name": "Level-Sensor", "mid": "MACH-BLEND-001"},
        {"id": "EQP-002", "name": "Temperature-Sensor", "mid": "MACH-GRAN-001"},
        {"id": "EQP-003", "name": "Pressure-Gauge", "mid": "MACH-DRY-001"},
        {"id": "EQP-004", "name": "Vibration-Sensor", "mid": "MACH-BLEND-001"},
        {"id": "EQP-005", "name": "Flow-Meter", "mid": "MACH-COAT-001"},
    ]
    lines = []
    for eq in EQUIP:
        for d in range(60):
            if random.random() > 0.4:
                continue
            ts = now - timedelta(days=d, hours=random.randint(0, 23))
            ts_ns = int(ts.timestamp() * 1e9)
            st = random.choice(["Completed", "Completed", "Overdue"])
            res = random.choice(["Within-Tolerance", "Within-Tolerance", "Out-of-Tolerance"])
            tech = random.choice(TECHNICIANS)
            eid = f"CAL-{random.randint(10000000, 99999999):08X}"
            payload = safe_json(
                {
                    "id": eid,
                    "type": "calibration",
                    "equipment_id": eq["id"],
                    "equipment_name": eq["name"],
                    "machine_id": eq["mid"],
                    "status": st,
                    "result": res,
                    "technician": tech,
                    "date": ts.strftime("%Y-%m-%d"),
                }
            )
            tags = f"equipment_id={eq['id']},equipment_name={eq['name']},machine_id={eq['mid']},status={st},result={res},technician={tech}"
            lines.append(f'calibration_log,{tags} payload_json="{payload}",payload_type="json" {ts_ns}')
    n = batch_write(lines)
    print(f"{n} records")
    return n


def seed_cleaning():
    print("Seeding cleaning_log...", end=" ", flush=True)
    lines = []
    for m in MACHINES:
        for d in range(30):
            if random.random() > 0.5:
                continue
            ts = now - timedelta(days=d, hours=random.randint(0, 23))
            ts_ns = int(ts.timestamp() * 1e9)
            ct = random.choice(["CIP", "Manual", "SOP-Based"])
            st = random.choice(["Completed", "Completed", "Pending"])
            tech = random.choice(TECHNICIANS)
            eid = f"CLN-{random.randint(10000000, 99999999):08X}"
            payload = safe_json(
                {
                    "id": eid,
                    "type": "cleaning",
                    "machine_id": m["id"],
                    "machine_name": m["name"],
                    "stage": m["stage"],
                    "clean_type": ct,
                    "status": st,
                    "technician": tech,
                    "date": ts.strftime("%Y-%m-%d"),
                }
            )
            tags = f"machine_id={m['id']},machine_name={m['name']},stage={m['stage']},clean_type={ct},status={st},technician={tech}"
            lines.append(f'cleaning_log,{tags} payload_json="{payload}",payload_type="json" {ts_ns}')
    n = batch_write(lines)
    print(f"{n} records")
    return n


def seed_telemetry():
    print("Seeding telemetry...", end=" ", flush=True)
    lines = []
    for m in MACHINES:
        bt = random.uniform(20, 80)
        bv = random.uniform(0.1, 5.0)
        for h in range(168):
            ts = now - timedelta(hours=h)
            ts_ns = int(ts.timestamp() * 1e9)
            t = round(bt + random.gauss(0, 2) + (5 if h % 24 < 8 else 0), 2)
            v = round(bv + random.gauss(0, 0.3) + (1.5 if random.random() < 0.05 else 0), 3)
            p = round(random.uniform(1.0, 10.0) + random.gauss(0, 0.2), 3)
            u = round(min(100, max(0, random.uniform(60, 95) + random.gauss(0, 5))), 1)
            s = round(random.uniform(100, 500), 1)
            pw = round(random.uniform(5, 50), 2)
            tags = f"machine_id={m['id']},machine_name={m['name']},stage={m['stage']}"
            lines.append(
                f"telemetry,{tags} temperature={t},vibration={v},pressure={p},utilization={u},speed={s},power={pw} {ts_ns}"
            )
    n = batch_write(lines)
    print(f"{n} records")
    return n


if __name__ == "__main__":
    t0 = time.time()
    total = seed_maintenance() + seed_downtime() + seed_calibration() + seed_cleaning() + seed_telemetry()
    elapsed = time.time() - t0
    print(f"\nDone: {total} records in {elapsed:.1f}s")
