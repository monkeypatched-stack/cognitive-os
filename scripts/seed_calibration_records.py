#!/usr/bin/env python3
"""Seed `calibration_records` in the demo database from the assets that actually exist.

The PM service defines a CalibrationRecord model (services/pm/models/calibrations.py) and
exposes routers for it, but the collection was never created — so the calibration agents had
nothing to read, and the old pipeline filled the hole by inventing pumps, dates and intervals.

This creates the collection from the REAL assets: every instrument and every piece of
pharmaceutical_equipment in the demo database gets a record whose equipment_id is that asset's
own id. Nothing here invents an asset; it only gives the assets that exist a calibration
history.

Deterministic: intervals and dates are derived from a hash of the asset id, so re-running
produces identical records and the same assets come out overdue each time.

    python3 scripts/seed_calibration_records.py            # seed
    python3 scripts/seed_calibration_records.py --drop     # wipe and re-seed
    python3 scripts/seed_calibration_records.py --dry-run  # show what it would write
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from datetime import UTC, date, datetime, timedelta

from pymongo import MongoClient

DB = os.getenv("MANUFACTURING_DB", "demo")
URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
COLLECTION = "calibration_records"

# Calibration intervals in real use are driven by the instrument's criticality. Assigned
# deterministically per asset rather than randomly, so the demo is reproducible.
INTERVALS = (90, 180, 365)
TYPES = ("Routine", "Verification", "Validation")
STANDARDS = ("ISO 17025", "NIST", "In-House", "USP")


def _bucket(asset_id: str, modulus: int) -> int:
    return int(hashlib.sha256(asset_id.encode()).hexdigest(), 16) % modulus


def _record(asset_id: str, name: str, line_id: str, today: date) -> dict:
    interval = INTERVALS[_bucket(asset_id + "i", len(INTERVALS))]

    # Where in its cycle this asset sits. A few land past due — that is the point of the
    # demo — but which ones is a function of the asset id, not of chance.
    offset = _bucket(asset_id + "o", interval + 45) - 30  # -30 .. interval+14
    performed = today - timedelta(days=offset if offset > 0 else 1)
    next_due = performed + timedelta(days=interval)

    overdue = next_due < today
    return {
        "record_id": f"CAL-{asset_id}",
        "equipment_id": asset_id,
        "instrument_tag": name,
        "line_id": line_id,
        "calibration_type": TYPES[_bucket(asset_id + "t", len(TYPES))],
        "status": "Overdue" if overdue else "Completed",
        "standard_used": STANDARDS[_bucket(asset_id + "s", len(STANDARDS))],
        "certificate_no": f"CERT-{_bucket(asset_id + 'c', 900000) + 100000}",
        "performed_date": performed.isoformat(),
        "next_due_date": next_due.isoformat(),
        "calibration_interval_days": interval,
        "out_of_tolerance": False,
        "created_at": datetime.now(UTC).isoformat(),
        "seeded_by": "scripts/seed_calibration_records.py",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--drop", action="store_true", help="drop the collection first")
    ap.add_argument("--dry-run", action="store_true", help="print, don't write")
    args = ap.parse_args()

    db = MongoClient(URL, serverSelectionTimeoutMS=5000)[DB]
    today = date.today()

    assets: list[tuple[str, str, str]] = []
    for row in db.instruments.find({}, {"_id": 0, "serial_number": 1, "name": 1, "line_id": 1}):
        if row.get("serial_number"):
            assets.append((row["serial_number"], row.get("name", ""), row.get("line_id", "")))
    for row in db.pharmaceutical_equipment.find({}, {"_id": 0, "equipment_id": 1, "name": 1, "line_id": 1}):
        if row.get("equipment_id"):
            assets.append((row["equipment_id"], row.get("name", ""), row.get("line_id", "")))

    if not assets:
        print(f"No assets found in {DB}.instruments / {DB}.pharmaceutical_equipment — nothing to seed.")
        return 1

    records = [_record(aid, name, line, today) for aid, name, line in assets]
    overdue = [r for r in records if r["status"] == "Overdue"]

    print(
        f"{DB}.{COLLECTION}: {len(records)} records from {len(assets)} real assets "
        f"({len(overdue)} overdue as of {today})"
    )
    for r in overdue[:5]:
        print(
            f"   OVERDUE  {r['equipment_id']:20s} {r['instrument_tag'][:28]:30s} "
            f"due {r['next_due_date']}  every {r['calibration_interval_days']}d"
        )

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    if args.drop:
        db[COLLECTION].drop()
        print(f"dropped {DB}.{COLLECTION}")

    db[COLLECTION].delete_many({"record_id": {"$in": [r["record_id"] for r in records]}})
    db[COLLECTION].insert_many(records)
    db[COLLECTION].create_index("equipment_id")
    db[COLLECTION].create_index("next_due_date")

    print(f"\nwrote {db[COLLECTION].count_documents({})} records; indexed equipment_id, next_due_date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
