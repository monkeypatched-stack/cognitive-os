"""Backfill `tenant_id` on documents that predate multi-tenancy.

Tenant scoping filters every query by `{tenant_id: <tenant>}`. Existing documents carry no
tenant_id, so enabling enforcement (AGENTOS_TENANCY_ENFORCE=1) BEFORE this backfill would make
every query return empty. Run this once, verify, then enable enforcement.

    # dry run (default) — reports what would change, writes nothing
    python3 scripts/backfill_tenant_id.py

    # apply, stamping the default tenant
    python3 scripts/backfill_tenant_id.py --apply --tenant default

Idempotent: only documents MISSING tenant_id are touched.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

TENANT_FIELD = "tenant_id"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    ap.add_argument(
        "--tenant",
        default=os.getenv("DEFAULT_TENANT", "default"),
        help="tenant to stamp on untenanted documents",
    )
    ap.add_argument("--mongo-uri", default=os.getenv("MONGO_URI", "mongodb://localhost:27017"))
    ap.add_argument("--db", default=os.getenv("MONGO_DB", "monkeybrain"))
    args = ap.parse_args()

    try:
        from motor.motor_asyncio import AsyncIOMotorClient
    except ImportError:
        print("motor is required: pip install motor", file=sys.stderr)
        return 2

    client = AsyncIOMotorClient(args.mongo_uri)
    db = client[args.db]
    names = await db.list_collection_names()

    total_missing = 0
    total_updated = 0
    print(f"{'APPLY' if args.apply else 'DRY RUN'} — db={args.db} tenant={args.tenant!r}\n")
    for name in sorted(names):
        col = db[name]
        missing = await col.count_documents({TENANT_FIELD: {"$exists": False}})
        if not missing:
            continue
        total_missing += missing
        if args.apply:
            res = await col.update_many(
                {TENANT_FIELD: {"$exists": False}},
                {"$set": {TENANT_FIELD: args.tenant}},
            )
            total_updated += res.modified_count
            print(f"  {name:<40} stamped {res.modified_count:>8} / {missing}")
        else:
            print(f"  {name:<40} would stamp {missing:>8}")

    print(f"\ncollections scanned: {len(names)}")
    print(f"documents missing {TENANT_FIELD}: {total_missing}")
    if args.apply:
        print(f"documents updated: {total_updated}")
        print("\nBackfill complete. You can now set AGENTOS_TENANCY_ENFORCE=1.")
    else:
        print(
            "\nDry run only — re-run with --apply to write. "
            "Do NOT enable AGENTOS_TENANCY_ENFORCE until this is applied."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
