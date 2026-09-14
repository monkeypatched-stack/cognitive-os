#!/usr/bin/env python3
"""Seed calibration records into InfluxDB matching the existing calibration_log schema."""

import sys
import json
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.parse import urlencode

sys.path.insert(0, "/Users/prashunjaveri/Code/monkeypatched")

from services.common.config import settings

NOW = datetime.now(timezone.utc)

CALIBRATIONS = [
    {
        "instrument": "Analytical Balance (Weighing Booth)",
        "equipment_id": "EQP-CAL-001",
        "machine_id": "MACH-QC-001",
        "workstation": "Weighing Booth",
        "stage": "Dispensing",
        "frequency": "6-monthly",
        "cal_type": "Linearity check, repeatability test, eccentricity test",
        "standard": "OIML R 76",
        "status": "Completed",
        "result": "Within-Tolerance",
        "technician": "QC-Lab-Technician",
        "due_months_ago": 2,
    },
    {
        "instrument": "Analytical Balance (IPQC Station)",
        "equipment_id": "EQP-CAL-002",
        "machine_id": "MACH-QC-002",
        "workstation": "In-Process QC Station",
        "stage": "Compression",
        "frequency": "6-monthly",
        "cal_type": "Linearity, repeatability, eccentricity",
        "standard": "OIML R 76",
        "status": "Completed",
        "result": "Within-Tolerance",
        "technician": "QC-Lab-Technician",
        "due_months_ago": 1,
    },
    {
        "instrument": "Moisture Analyzer",
        "equipment_id": "EQP-CAL-003",
        "machine_id": "MACH-QC-003",
        "workstation": "Sampling Booth",
        "stage": "Dispensing",
        "frequency": "Annual",
        "cal_type": "Verify against reference standard weights",
        "standard": "USP",
        "status": "Completed",
        "result": "Within-Tolerance",
        "technician": "QC-Lab-Technician",
        "due_months_ago": 4,
    },
    {
        "instrument": "LOD Moisture Analyzer (FBD)",
        "equipment_id": "EQP-CAL-004",
        "machine_id": "MACH-QC-004",
        "workstation": "FBD Unloading Station",
        "stage": "Drying",
        "frequency": "6-monthly",
        "cal_type": "Verify against NIST traceable reference",
        "standard": "USP",
        "status": "Scheduled",
        "result": "Pending",
        "technician": "QC-Lab-Technician",
        "due_months_ago": -1,
    },
    {
        "instrument": "Thermometer (Binder Prep)",
        "equipment_id": "EQP-CAL-005",
        "machine_id": "MACH-QC-005",
        "workstation": "Binder Prep Station",
        "stage": "Granulation",
        "frequency": "Annual",
        "cal_type": "3-point calibration (ambient, 50C, 100C)",
        "standard": "NIST traceable",
        "status": "Completed",
        "result": "Within-Tolerance",
        "technician": "QC-Lab-Technician",
        "due_months_ago": 6,
    },
    {
        "instrument": "Thermometer (Solution Prep)",
        "equipment_id": "EQP-CAL-006",
        "machine_id": "MACH-QC-006",
        "workstation": "Solution Prep Station",
        "stage": "Coating",
        "frequency": "Annual",
        "cal_type": "3-point calibration",
        "standard": "NIST traceable",
        "status": "Scheduled",
        "result": "Pending",
        "technician": "QC-Lab-Technician",
        "due_months_ago": -3,
    },
    {
        "instrument": "Inlet Air Temp Sensor (FBD)",
        "equipment_id": "EQP-CAL-007",
        "machine_id": "MACH-QC-007",
        "workstation": "FBD Loading Station",
        "stage": "Drying",
        "frequency": "Annual",
        "cal_type": "Calibrate at 3 points, verify against reference RTD",
        "standard": "NIST traceable",
        "status": "Completed",
        "result": "Within-Tolerance",
        "technician": "QC-Lab-Technician",
        "due_months_ago": 8,
    },
    {
        "instrument": "Exhaust Temp Sensor (FBD)",
        "equipment_id": "EQP-CAL-008",
        "machine_id": "MACH-QC-008",
        "workstation": "FBD Unloading Station",
        "stage": "Drying",
        "frequency": "Annual",
        "cal_type": "Calibrate at 3 points",
        "standard": "NIST traceable",
        "status": "Scheduled",
        "result": "Pending",
        "technician": "QC-Lab-Technician",
        "due_months_ago": -2,
    },
    {
        "instrument": "Inlet Temp Sensor (Auto Coater)",
        "equipment_id": "EQP-CAL-009",
        "machine_id": "MACH-QC-009",
        "workstation": "Coating Pan Station",
        "stage": "Coating",
        "frequency": "Annual",
        "cal_type": "Calibrate at 3 points",
        "standard": "NIST traceable",
        "status": "Completed",
        "result": "Within-Tolerance",
        "technician": "QC-Lab-Technician",
        "due_months_ago": 3,
    },
    {
        "instrument": "Exhaust Temp Sensor (Auto Coater)",
        "equipment_id": "EQP-CAL-010",
        "machine_id": "MACH-QC-010",
        "workstation": "Coating Pan Station",
        "stage": "Coating",
        "frequency": "Annual",
        "cal_type": "Calibrate at 3 points",
        "standard": "NIST traceable",
        "status": "Scheduled",
        "result": "Pending",
        "technician": "QC-Lab-Technician",
        "due_months_ago": -5,
    },
    {
        "instrument": "RH Sensor (AHU)",
        "equipment_id": "EQP-CAL-011",
        "machine_id": "MACH-QC-011",
        "workstation": "Inlet/Outlet Air Station",
        "stage": "Plant",
        "frequency": "Annual",
        "cal_type": "Calibrate at 2 points (33% RH, 75% RH)",
        "standard": "NIST traceable",
        "status": "Completed",
        "result": "Within-Tolerance",
        "technician": "QC-Lab-Technician",
        "due_months_ago": 7,
    },
    {
        "instrument": "RH Sensor (Coating Pan)",
        "equipment_id": "EQP-CAL-012",
        "machine_id": "MACH-QC-012",
        "workstation": "Coating Pan Station",
        "stage": "Coating",
        "frequency": "Annual",
        "cal_type": "Calibrate at 2 points (33% RH, 75% RH)",
        "standard": "NIST traceable",
        "status": "Completed",
        "result": "Within-Tolerance",
        "technician": "QC-Lab-Technician",
        "due_months_ago": 5,
    },
    {
        "instrument": "Compression Force Sensor",
        "equipment_id": "EQP-CAL-013",
        "machine_id": "MACH-QC-013",
        "workstation": "Tablet Press Station",
        "stage": "Compression",
        "frequency": "6-monthly",
        "cal_type": "Static calibration at 0-100% range using certified weights",
        "standard": "OEM / NIST",
        "status": "Completed",
        "result": "Within-Tolerance",
        "technician": "QC-Lab-Technician",
        "due_months_ago": 3,
    },
    {
        "instrument": "Hardness Tester",
        "equipment_id": "EQP-CAL-014",
        "machine_id": "MACH-QC-014",
        "workstation": "In-Process QC Station",
        "stage": "Compression",
        "frequency": "6-monthly",
        "cal_type": "Verify using certified reference tablets at 3 levels",
        "standard": "OEM standard",
        "status": "Scheduled",
        "result": "Pending",
        "technician": "QC-Lab-Technician",
        "due_months_ago": -1,
    },
    {
        "instrument": "Friability Tester",
        "equipment_id": "EQP-CAL-015",
        "machine_id": "MACH-QC-015",
        "workstation": "In-Process QC Station",
        "stage": "Compression",
        "frequency": "Annual",
        "cal_type": "Verify RPM against tachometer, verify drum dimensions",
        "standard": "USP",
        "status": "Completed",
        "result": "Within-Tolerance",
        "technician": "QC-Lab-Technician",
        "due_months_ago": 10,
    },
    {
        "instrument": "Disintegration Tester",
        "equipment_id": "EQP-CAL-016",
        "machine_id": "MACH-QC-016",
        "workstation": "In-Process QC Station",
        "stage": "Compression",
        "frequency": "Annual",
        "cal_type": "Verify stroke rate, temperature probe calibration",
        "standard": "USP",
        "status": "Scheduled",
        "result": "Pending",
        "technician": "QC-Lab-Technician",
        "due_months_ago": -4,
    },
    {
        "instrument": "Viscometer (Solution Prep)",
        "equipment_id": "EQP-CAL-017",
        "machine_id": "MACH-QC-017",
        "workstation": "Solution Prep Station",
        "stage": "Coating",
        "frequency": "Annual",
        "cal_type": "Calibrate using certified viscosity standard fluids",
        "standard": "ISO 17025",
        "status": "Completed",
        "result": "Within-Tolerance",
        "technician": "QC-Lab-Technician",
        "due_months_ago": 9,
    },
    {
        "instrument": "Differential Pressure Gauge (AHU)",
        "equipment_id": "EQP-CAL-018",
        "machine_id": "MACH-QC-018",
        "workstation": "Inlet/Outlet Air Station",
        "stage": "Plant",
        "frequency": "Annual",
        "cal_type": "Zero check, span check against reference manometer",
        "standard": "NIST traceable",
        "status": "Scheduled",
        "result": "Pending",
        "technician": "QC-Lab-Technician",
        "due_months_ago": -6,
    },
    {
        "instrument": "Differential Pressure Gauge (LAF)",
        "equipment_id": "EQP-CAL-019",
        "machine_id": "MACH-QC-019",
        "workstation": "Sampling / Weighing Booth",
        "stage": "Dispensing",
        "frequency": "Annual",
        "cal_type": "Zero and span check",
        "standard": "NIST traceable",
        "status": "Completed",
        "result": "Within-Tolerance",
        "technician": "QC-Lab-Technician",
        "due_months_ago": 2,
    },
    {
        "instrument": "Fill Weight Sensor (Bottle Filler)",
        "equipment_id": "EQP-CAL-020",
        "machine_id": "MACH-QC-020",
        "workstation": "Bottle Filling Station",
        "stage": "Packaging",
        "frequency": "6-monthly",
        "cal_type": "Calibrate at 3 points, verify reject threshold",
        "standard": "OEM / NIST",
        "status": "Scheduled",
        "result": "Pending",
        "technician": "QC-Lab-Technician",
        "due_months_ago": -2,
    },
    {
        "instrument": "Fill Weight Sensor (Capsule Filler)",
        "equipment_id": "EQP-CAL-021",
        "machine_id": "MACH-QC-021",
        "workstation": "Capsule Filler Station",
        "stage": "Capsule Filling",
        "frequency": "6-monthly",
        "cal_type": "Calibrate at 3 points",
        "standard": "OEM / NIST",
        "status": "Completed",
        "result": "Within-Tolerance",
        "technician": "QC-Lab-Technician",
        "due_months_ago": 1,
    },
    {
        "instrument": "Checkweigher (Cartoning)",
        "equipment_id": "EQP-CAL-022",
        "machine_id": "MACH-QC-022",
        "workstation": "Cartoning Station",
        "stage": "Packaging",
        "frequency": "6-monthly",
        "cal_type": "Static calibration, verify reject gate trigger accuracy",
        "standard": "OIML",
        "status": "Scheduled",
        "result": "Pending",
        "technician": "QC-Lab-Technician",
        "due_months_ago": -3,
    },
    {
        "instrument": "Checkweigher (Bottle Line)",
        "equipment_id": "EQP-CAL-023",
        "machine_id": "MACH-QC-023",
        "workstation": "Bottle Filling Station",
        "stage": "Packaging",
        "frequency": "6-monthly",
        "cal_type": "Static calibration",
        "standard": "OIML",
        "status": "Completed",
        "result": "Within-Tolerance",
        "technician": "QC-Lab-Technician",
        "due_months_ago": 4,
    },
    {
        "instrument": "Vision Inspection System (Blister)",
        "equipment_id": "EQP-CAL-024",
        "machine_id": "MACH-QC-024",
        "workstation": "Blister/Strip Station",
        "stage": "Packaging",
        "frequency": "6-monthly",
        "cal_type": "Verify detection rate using reference defect samples",
        "standard": "OEM SOP",
        "status": "Scheduled",
        "result": "Pending",
        "technician": "QC-Lab-Technician",
        "due_months_ago": -1,
    },
    {
        "instrument": "Vision Inspection System (Labelling)",
        "equipment_id": "EQP-CAL-025",
        "machine_id": "MACH-QC-025",
        "workstation": "Labelling Station",
        "stage": "Packaging",
        "frequency": "6-monthly",
        "cal_type": "Verify label placement detection, barcode read rate",
        "standard": "OEM SOP",
        "status": "Completed",
        "result": "Within-Tolerance",
        "technician": "QC-Lab-Technician",
        "due_months_ago": 5,
    },
    {
        "instrument": "Tachometer (portable)",
        "equipment_id": "EQP-CAL-026",
        "machine_id": "MACH-QC-026",
        "workstation": "All rotating equipment",
        "stage": "All",
        "frequency": "Annual",
        "cal_type": "Verify against NIST traceable reference tachometer",
        "standard": "NIST",
        "status": "Scheduled",
        "result": "Pending",
        "technician": "QC-Lab-Technician",
        "due_months_ago": -8,
    },
    {
        "instrument": "Torque Wrench Set",
        "equipment_id": "EQP-CAL-027",
        "machine_id": "MACH-QC-027",
        "workstation": "Tablet Press / Capsule Filler",
        "stage": "Compression / Capsule Filling",
        "frequency": "Annual",
        "cal_type": "Calibrate at 25%, 50%, 75%, 100% rated torque",
        "standard": "ISO 6789",
        "status": "Completed",
        "result": "Within-Tolerance",
        "technician": "QC-Lab-Technician",
        "due_months_ago": 11,
    },
    {
        "instrument": "Clamp Meter",
        "equipment_id": "EQP-CAL-028",
        "machine_id": "MACH-QC-028",
        "workstation": "All electrical stations",
        "stage": "All",
        "frequency": "Annual",
        "cal_type": "AC/DC current calibration at 3 points",
        "standard": "NIST traceable",
        "status": "Scheduled",
        "result": "Pending",
        "technician": "QC-Lab-Technician",
        "due_months_ago": -7,
    },
    {
        "instrument": "Multimeter",
        "equipment_id": "EQP-CAL-029",
        "machine_id": "MACH-QC-029",
        "workstation": "All electrical stations",
        "stage": "All",
        "frequency": "Annual",
        "cal_type": "Voltage, resistance, continuity calibration",
        "standard": "NIST traceable",
        "status": "Completed",
        "result": "Within-Tolerance",
        "technician": "QC-Lab-Technician",
        "due_months_ago": 9,
    },
    {
        "instrument": "Loop Calibrator",
        "equipment_id": "EQP-CAL-030",
        "machine_id": "MACH-QC-030",
        "workstation": "All instrument stations",
        "stage": "All",
        "frequency": "Annual",
        "cal_type": "4mA, 12mA, 20mA point calibration",
        "standard": "NIST traceable",
        "status": "Scheduled",
        "result": "Pending",
        "technician": "QC-Lab-Technician",
        "due_months_ago": -5,
    },
]


def _escape_tag(value: str) -> str:
    return value.replace(",", "\\,").replace(" ", "\\ ").replace("=", "\\=")


def _field_value(value: str) -> str:
    return f'"{value}"'


def write_calibration(cal: dict, offset_months: int):
    """Write a single calibration record to InfluxDB."""
    cal_time = NOW + timedelta(days=offset_months * 30)
    measurement = "calibration_log"
    tags = (
        f"equipment_id={_escape_tag(cal['equipment_id'])},"
        f"equipment_name={_escape_tag(cal['instrument'])},"
        f"machine_id={_escape_tag(cal['machine_id'])},"
        f"status={_escape_tag(cal['status'])},"
        f"technician={_escape_tag(cal['technician'])},"
        f"result={_escape_tag(cal['result'])}"
    )
    payload = {
        "id": f"CAL-{cal['equipment_id'][-6:]}",
        "type": "calibration",
        "equipment_id": cal["equipment_id"],
        "equipment_name": cal["instrument"],
        "machine_id": cal["machine_id"],
        "workstation": cal["workstation"],
        "stage": cal["stage"],
        "frequency": cal["frequency"],
        "cal_type": cal["cal_type"],
        "standard": cal["standard"],
        "status": cal["status"],
        "result": cal["result"],
        "technician": cal["technician"],
        "date": cal_time.strftime("%Y-%m-%d"),
    }
    fields = f"payload_type={_field_value('json')}"
    ts = int(cal_time.timestamp() * 1_000_000_000)
    line = f"{measurement},{tags} {fields} {ts}"
    return line


def write_lines(lines: list[str]):
    """Write multiple lines to InfluxDB."""
    query = urlencode({"db": settings.INFLUXDB_BUCKET})
    url = f"{settings.INFLUXDB_URL.rstrip('/')}/api/v3/write_lp?{query}"
    headers = {"Content-Type": "text/plain; charset=utf-8"}
    if settings.INFLUXDB_TOKEN:
        headers["Authorization"] = f"Bearer {settings.INFLUXDB_TOKEN}"

    body = "\n".join(lines)
    request = Request(url, data=body.encode("utf-8"), headers=headers, method="POST")
    with urlopen(request, timeout=10) as response:
        response.read()
    print(f"  Wrote {len(lines)} records to InfluxDB")


def main():
    print(f"Writing {len(CALIBRATIONS)} calibration records to InfluxDB...")
    lines = []
    for cal in CALIBRATIONS:
        line = write_calibration(cal, cal["due_months_ago"])
        lines.append(line)

    write_lines(lines)
    print(f"Done. {len(lines)} calibration records seeded.")


if __name__ == "__main__":
    main()
