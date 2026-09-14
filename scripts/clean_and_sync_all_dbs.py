#!/usr/bin/env python3
"""
Clean and sync all databases following the sync architecture:

1. MongoDB (master)    → source of truth for all entity data
2. MongoDB (semantic)  → embeddings and mem0 semantic copies
3. Neo4j               → entity relationships (graph mirror)
4. InfluxDB            → event logs and time-series metrics
5. Redis               → session and cache data
6. Elasticsearch       → search indexes and audit trail

Sync direction: MongoDB (master) → all downstream stores

Usage:
    .venv/bin/python scripts/clean_and_sync_all_dbs.py [--dry-run] [--skip-sync] [--phase N]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timezone
from typing import Any

sys.path.insert(0, "/Users/prashunjaveri/Code/monkeypatched")

from motor.motor_asyncio import AsyncIOMotorClient
from services.common.config import settings
from services.common.neo4j_mirror import (
    _get_driver,
    mirror_document,
    safe_mirror,
    close_neo4j_mirror_driver,
    reset_neo4j_mirror,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("clean_and_sync")

DRY_RUN = False
SKIP_SYNC = False
ONLY_PHASE = None


def _raw_db(db):
    return getattr(db, "_db", db)


async def get_mongo_clients():
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    master_db = client[settings.DB_NAME]
    agentos_db = client["agentos"]
    return client, master_db, agentos_db


def _has_critical_fields(doc: dict, coll_name: str) -> bool:
    """Check if a document has its required critical fields populated."""
    if not doc.get("_id"):
        return False

    CRITICAL_FIELDS = {
        "users": ("user_id", "name", "email"),
        "workers": ("worker_id", "name"),
        "industrial_workers": ("worker_id", "user_id", "name"),
        "roles": ("role_id", "name"),
        "permissions": ("permission_id", "name", "resource"),
        "industrial_plants": ("plant_id", "name"),
        "industrial_lines": ("line_id", "name"),
        "industrial_stages": ("stage_id", "name"),
        "industrial_workstations": ("workstation_id", "name"),
        "workstations": ("workstation_id", "name"),
        "pharmaceutical_machines": ("machine_id", "name"),
        "pharmaceutical_equipment": ("equipment_id", "name"),
        "process_definitions": ("process_definition_id", "name"),
        "production_batches": ("batch_id", "name"),
        "work_orders": ("work_order_id", "name"),
        "sops": ("sop_id", "name", "title"),
        "industrial_sops": ("sop_id", "name", "title"),
        "approvals": ("approval_id", "name"),
        "devices": ("device_id", "name"),
        "tools": ("tool_id", "name"),
        "plant_locations": ("location_id", "name"),
        "raw_materials": ("material_id", "name"),
        "machine_parts": ("part_id", "name"),
        "classes": ("class_id", "name"),
        "equipment_classes": ("class_id", "name"),
        "families": ("family_id", "name"),
        "equipment_families": ("family_id", "name"),
        "subclasses": ("subclass_id", "name"),
        "equipment_subclasses": ("subclass_id", "name"),
        "rfid_tags": ("tag_id", "name"),
        "plcs": ("plc_id", "name"),
        "shifts": ("shift_id", "name"),
        "gxp_change_controls": ("change_control_id", "name"),
        "kanban_boards": ("board_id", "name"),
        "notifications": ("notification_id", "title"),
        "mbmr_templates": ("template_id", "name"),
        "packing_instructions": ("instruction_id", "name"),
        "calibration_records": ("calibration_id", "equipment_id"),
        "cleaning_records": ("cleaning_id", "equipment_id"),
        "batch_production_execution_records": ("batch_execution_record_id", "batch_id"),
        "batch_step_executions": ("step_execution_id", "batch_execution_record_id"),
        "dispense_weighing_records": (
            "dispense_weighing_id",
            "batch_execution_record_id",
        ),
        "area_room_usage_ledger": ("usage_id", "batch_execution_record_id"),
        "equipment_usage_ledger": ("usage_id", "batch_execution_record_id"),
        "yield_reconciliation_records": (
            "reconciliation_id",
            "batch_execution_record_id",
        ),
        "cpp_cqa_registry": ("registry_id", "batch_execution_record_id"),
        "part11_audit_trail": ("audit_id", "collection"),
        "part11_record_versions": ("version_id", "collection"),
        "part11_electronic_signatures": ("signature_id", "user_id"),
        "part11_login_events": ("event_id", "user_id"),
        "part11_export_jobs": ("job_id", "user_id"),
        "calendar_bookings": ("booking_id", "title"),
        "downtime_logs": ("log_id", "equipment_id"),
        "maintenance_logs": ("log_id", "equipment_id"),
        "agentos_api_keys": ("key_id", "key_hash"),
        "agentos_api_key_email_otps": ("otp_id", "email"),
    }

    fields = CRITICAL_FIELDS.get(coll_name)
    if fields is None:
        return True

    return any(doc.get(f) for f in fields)


# ─── Phase 1: MongoDB (master) ────────────────────────────────────────────


async def clean_mongo_master() -> dict[str, Any]:
    logger.info("=" * 60)
    logger.info("PHASE 1: Clean MongoDB (master) - remove incomplete docs")
    logger.info("=" * 60)

    client, master_db, _ = await get_mongo_clients()
    collections = await master_db.list_collection_names()
    results = {}

    for coll_name in sorted(collections):
        before = await master_db[coll_name].count_documents({})
        if before == 0:
            continue

        docs_to_delete = []
        async for doc in master_db[coll_name].find({}):
            if not _has_critical_fields(doc, coll_name):
                docs_to_delete.append(doc["_id"])

        if docs_to_delete:
            if not DRY_RUN:
                await master_db[coll_name].delete_many({"_id": {"$in": docs_to_delete}})
            results[coll_name] = len(docs_to_delete)
            logger.info(
                f"  {'[DRY] ' if DRY_RUN else ''}{coll_name}: removed {len(docs_to_delete)}/{before} incomplete"
            )

    total = sum(results.values())
    logger.info(f"  MongoDB master: cleaned {total} docs across {len(results)} collections")
    client.close()
    return results


# ─── Phase 2: MongoDB (semantic) ──────────────────────────────────────────


async def clean_mongo_semantic() -> dict[str, Any]:
    logger.info("=" * 60)
    logger.info("PHASE 2: Clean MongoDB (semantic) - embeddings & mem0 states")
    logger.info("=" * 60)

    client, master_db, agentos_db = await get_mongo_clients()
    results = {}

    for coll_name in [
        "mem0_semantic_states",
        "document_embeddings",
        "canonical_state_snapshots",
    ]:
        db_to_use = agentos_db if coll_name in ("mem0_semantic_states",) else master_db
        before = await db_to_use[coll_name].count_documents({})
        if before == 0:
            continue

        docs_to_delete = []
        async for doc in db_to_use[coll_name].find({}):
            state_id = doc.get("state_id") or doc.get("embedding_id") or doc.get("snapshot_id")
            if not state_id:
                docs_to_delete.append(doc["_id"])

        if docs_to_delete:
            if not DRY_RUN:
                await db_to_use[coll_name].delete_many({"_id": {"$in": docs_to_delete}})
            results[coll_name] = len(docs_to_delete)
            logger.info(f"  {'[DRY] ' if DRY_RUN else ''}{coll_name}: removed {len(docs_to_delete)}/{before}")

    total = sum(results.values())
    logger.info(f"  MongoDB semantic: cleaned {total} docs across {len(results)} collections")
    client.close()
    return results


# ─── Phase 3: Neo4j (relationships) ───────────────────────────────────────


async def clean_neo4j() -> dict[str, Any]:
    logger.info("=" * 60)
    logger.info("PHASE 3: Clean Neo4j - wipe all nodes and relationships")
    logger.info("=" * 60)

    driver = _get_driver()
    async with driver.session() as session:
        r = await (await session.run("MATCH (n) RETURN count(n) as c")).single()
        nodes_before = r["c"]
        r = await (await session.run("MATCH ()-[r]->() RETURN count(r) as c")).single()
        rels_before = r["c"]

    if not DRY_RUN:
        await reset_neo4j_mirror()

    async with driver.session() as session:
        r = await (await session.run("MATCH (n) RETURN count(n) as c")).single()
        nodes_after = r["c"]
        r = await (await session.run("MATCH ()-[r]->() RETURN count(r) as c")).single()
        rels_after = r["c"]

    await close_neo4j_mirror_driver()

    logger.info(
        f"  {'[DRY] ' if DRY_RUN else ''}Neo4j: {nodes_before}→{nodes_after} nodes, {rels_before}→{rels_after} rels"
    )
    return {
        "nodes_before": nodes_before,
        "nodes_after": nodes_after,
        "rels_before": rels_before,
        "rels_after": rels_after,
    }


# ─── Phase 4: InfluxDB (event logs) ──────────────────────────────────────


async def clean_influxdb() -> dict[str, Any]:
    logger.info("=" * 60)
    logger.info("PHASE 4: Clean InfluxDB - wipe event logs")
    logger.info("=" * 60)

    try:
        from influxdb_client import InfluxDBClient

        client = InfluxDBClient(
            url=settings.INFLUXDB_URL,
            token=settings.INFLUXDB_TOKEN,
            org=settings.INFLUXDB_ORG,
        )
        delete_api = client.delete_api()

        if not DRY_RUN:
            delete_api.delete(
                start="1970-01-01T00:00:00Z",
                stop=datetime.now(timezone.utc).isoformat(),
                predicate="",
                bucket=settings.INFLUXDB_BUCKET,
                org=settings.INFLUXDB_ORG,
            )

        client.close()
        logger.info(f"  {'[DRY] ' if DRY_RUN else ''}InfluxDB bucket '{settings.INFLUXDB_BUCKET}' cleared")
        return {"bucket": settings.INFLUXDB_BUCKET, "status": "cleared"}
    except Exception as e:
        logger.warning(f"  InfluxDB clean failed: {e}")
        return {"error": str(e)}


# ─── Phase 5: Redis (session data) ────────────────────────────────────────


async def clean_redis() -> dict[str, Any]:
    logger.info("=" * 60)
    logger.info("PHASE 5: Clean Redis - flush session and cache data")
    logger.info("=" * 60)

    import redis.asyncio as aioredis

    r = aioredis.from_url(settings.REDIS_URL)
    info_before = await r.info("keyspace")
    keys_before = info_before.get("db0", {}).get("keys", 0)

    if not DRY_RUN:
        await r.flushdb()

    info_after = await r.info("keyspace")
    keys_after = info_after.get("db0", {}).get("keys", 0)

    await r.aclose()

    logger.info(f"  {'[DRY] ' if DRY_RUN else ''}Redis: {keys_before}→{keys_after} keys")
    return {"before": keys_before, "after": keys_after}


# ─── Phase 6: Elasticsearch (search & audit) ─────────────────────────────


async def clean_elasticsearch() -> dict[str, Any]:
    logger.info("=" * 60)
    logger.info("PHASE 6: Clean Elasticsearch - wipe search and audit indexes")
    logger.info("=" * 60)

    import httpx

    es_url = settings.AUDIT_ELASTICSEARCH_URL.rstrip("/")
    results = {}

    indices_to_clean = [
        settings.AUDIT_ELASTICSEARCH_INDEX,
        settings.PRODUCT_RESEARCH_ELASTICSEARCH_INDEX,
        "workflow-events",
    ]

    async with httpx.AsyncClient(base_url=es_url, timeout=30.0) as client:
        for index in indices_to_clean:
            try:
                resp = await client.head(f"/{index}")
                if resp.status_code == 200:
                    count_resp = await client.get(f"/{index}/_count")
                    count = count_resp.json().get("count", 0) if count_resp.status_code == 200 else 0

                    if not DRY_RUN:
                        await client.post(
                            f"/{index}/_delete_by_query",
                            json={"query": {"match_all": {}}},
                        )

                    results[index] = {"before": count, "after": 0}
                    logger.info(f"  {'[DRY] ' if DRY_RUN else ''}Cleared '{index}': {count} docs")
                else:
                    logger.info(f"  Index '{index}' not found (status {resp.status_code})")
            except Exception as e:
                logger.warning(f"  Failed to clean '{index}': {e}")

    return results


# ─── Phase 7: Sync Neo4j from MongoDB (master → relationships) ────────────


async def sync_neo4j_from_master() -> dict[str, Any]:
    logger.info("=" * 60)
    logger.info("PHASE 7: Sync Neo4j from MongoDB (master → relationships)")
    logger.info("=" * 60)

    client, master_db, _ = await get_mongo_clients()
    collections = await master_db.list_collection_names()

    skip_collections = {
        "event_store",
        "event_reduced_state",
        "event_reducer_audits",
        "part11_audit_trail",
        "part11_audit_outbox",
        "part11_record_versions",
        "part11_electronic_signatures",
        "part11_login_events",
        "part11_export_jobs",
        "part11_audit_search_sync_state",
        "agentos_api_keys",
        "agentos_api_key_email_otps",
        "agent_registry",
        "canonical_state_snapshots",
        "integration_transformation_audits",
        "external_ingestion_audits",
        "integration_connection_manifest_audit",
        "module_control_audit",
    }

    total_mirrored = 0
    errors = 0

    for coll_name in sorted(collections):
        if coll_name in skip_collections:
            continue

        count = await master_db[coll_name].count_documents({})
        if count == 0:
            continue

        logger.info(f"  Mirroring {coll_name} ({count} docs)...")
        cursor = master_db[coll_name].find({})
        mirrored = 0
        async for doc in cursor:
            try:
                await safe_mirror(mirror_document(coll_name, doc, "clean_sync"))
                mirrored += 1
            except Exception as e:
                errors += 1
                if errors <= 5:
                    logger.warning(f"    Error: {coll_name}: {e}")

        total_mirrored += mirrored
        logger.info(f"    ✓ {mirrored} docs")

    await close_neo4j_mirror_driver()

    driver = _get_driver()
    async with driver.session() as session:
        r = await (await session.run("MATCH (n) RETURN count(n) as c")).single()
        nodes = r["c"]
        r = await (await session.run("MATCH ()-[r]->() RETURN count(r) as c")).single()
        rels = r["c"]

    await close_neo4j_mirror_driver()

    logger.info(f"  Neo4j synced: {total_mirrored} docs → {nodes} nodes, {rels} rels ({errors} errors)")
    client.close()
    return {"mirrored": total_mirrored, "nodes": nodes, "rels": rels, "errors": errors}


# ─── Phase 8: Sync Elasticsearch from MongoDB (master → search/audit) ─────


async def sync_elasticsearch_from_master() -> dict[str, Any]:
    logger.info("=" * 60)
    logger.info("PHASE 8: Sync Elasticsearch from MongoDB (master → search/audit)")
    logger.info("=" * 60)

    if not settings.AUDIT_ELASTICSEARCH_ENABLED:
        logger.info("  Elasticsearch sync is disabled")
        return {"enabled": False}

    client, master_db, _ = await get_mongo_clients()

    raw = _raw_db(master_db)
    sync_state = raw["part11_audit_search_sync_state"]

    if not DRY_RUN:
        await sync_state.delete_many({"_id": "elasticsearch"})
        logger.info("  Reset ES sync cursor in MongoDB")

    from services.auth.helpers.audit_elasticsearch_sync import (
        sync_part11_audit_to_elasticsearch,
    )

    try:
        result = await sync_part11_audit_to_elasticsearch(master_db, batch_size=1000)
        logger.info(f"  ES sync: {result.get('indexed', 0)} docs indexed to '{result.get('index')}'")
        return result
    except Exception as e:
        logger.error(f"  ES sync failed: {e}")
        return {"error": str(e)}
    finally:
        client.close()


# ─── Phase 9: Reset Mem0 state store ──────────────────────────────────────


async def clean_mem0() -> dict[str, Any]:
    logger.info("=" * 60)
    logger.info("PHASE 9: Clean Mem0 state store (in-memory)")
    logger.info("=" * 60)

    from services.agentos.persistence.mem0_state_store import get_mem0_state_store

    store = get_mem0_state_store()
    h = len(store._state_history)
    s = len(store._state_snapshots)

    if not DRY_RUN:
        await store.clear_history()

    logger.info(f"  {'[DRY] ' if DRY_RUN else ''}Mem0 cleared: {h} history, {s} snapshots")
    return {"history": h, "snapshots": s}


# ─── Main ──────────────────────────────────────────────────────────────────


async def main():
    global DRY_RUN, SKIP_SYNC, ONLY_PHASE

    parser = argparse.ArgumentParser(description="Clean and sync all databases per architecture")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    parser.add_argument("--skip-sync", action="store_true", help="Skip sync phases (7-8)")
    parser.add_argument("--phase", type=int, help="Run only this phase number (1-9)")
    args = parser.parse_args()

    DRY_RUN = args.dry_run
    SKIP_SYNC = args.skip_sync
    ONLY_PHASE = args.phase

    start = time.time()
    logger.info(f"Clean & sync {'(DRY RUN)' if DRY_RUN else ''} — phase {ONLY_PHASE or 'all'}")
    logger.info(f"  MongoDB:   {settings.MONGODB_URL} / {settings.DB_NAME}")
    logger.info(f"  Redis:     {settings.REDIS_URL}")
    logger.info(f"  Neo4j:     {settings.NEO4J_URI}")
    logger.info(f"  ES:        {settings.AUDIT_ELASTICSEARCH_URL}")
    logger.info(f"  InfluxDB:  {settings.INFLUXDB_URL}")

    def should_run(phase: int) -> bool:
        if ONLY_PHASE is not None:
            return ONLY_PHASE == phase
        return True

    summary = {}

    # Clean phase
    if should_run(1):
        summary["mongo_master"] = await clean_mongo_master()
    if should_run(2):
        summary["mongo_semantic"] = await clean_mongo_semantic()
    if should_run(3):
        summary["neo4j_clean"] = await clean_neo4j()
    if should_run(4):
        summary["influxdb"] = await clean_influxdb()
    if should_run(5):
        summary["redis"] = await clean_redis()
    if should_run(6):
        summary["elasticsearch_clean"] = await clean_elasticsearch()
    if should_run(9):
        summary["mem0"] = await clean_mem0()

    # Sync phase (master → downstream)
    if not SKIP_SYNC:
        if should_run(7):
            summary["neo4j_sync"] = await sync_neo4j_from_master()
        if should_run(8):
            summary["elasticsearch_sync"] = await sync_elasticsearch_from_master()

    elapsed = time.time() - start
    logger.info("=" * 60)
    logger.info(f"DONE in {elapsed:.1f}s")
    logger.info("=" * 60)

    for key, val in summary.items():
        logger.info(f"  {key}: {val}")


if __name__ == "__main__":
    asyncio.run(main())
