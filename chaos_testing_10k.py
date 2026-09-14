#!/usr/bin/env python3
"""
Chaos test — 10,000 questions against POST /api/v1/agentos/prompt
Uses real grounded data pulled from the demo MongoDB database.

Usage:
    python chaos_testing_10k.py
    python chaos_testing_10k.py --url http://localhost:8031
    python chaos_testing_10k.py --url http://localhost:8031 --count 1000   # smoke
    python chaos_testing_10k.py --url http://localhost:8031 --concurrency 200
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("chaos")

MONGO_URI = "mongodb://localhost:27017"
MONGO_DB = "demo"


# ---------------------------------------------------------------------------
# Load grounded data from MongoDB
# ---------------------------------------------------------------------------


async def load_ground_truth(mongo_uri: str = MONGO_URI, mongo_db: str = MONGO_DB) -> dict[str, list]:
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
    except ImportError:
        logger.warning("motor not installed — falling back to synthetic placeholders")
        return {}

    client = AsyncIOMotorClient(mongo_uri)
    db = client[mongo_db]

    async def fetch(coll: str, projection: dict, limit: int = 500) -> list[dict]:
        try:
            projection["_id"] = 0
            return await db[coll].find({}, projection).limit(limit).to_list(limit)
        except Exception as e:
            logger.warning("Could not fetch %s: %s", coll, e)
            return []

    work_orders = await fetch("work_orders", {"work_order_id": 1, "title": 1, "status": 1})
    batches = await fetch("production_batches", {"batch_number": 1, "product_name": 1, "batch_id": 1})
    approvals = await fetch("approvals", {"approval_id": 1, "title": 1, "status": 1}, 200)
    departments = await fetch("departments", {"name": 1})
    workers = await fetch("workers", {"name": 1, "role": 1, "title": 1})
    sops = await fetch("sops", {"id": 1, "title": 1})
    instruments = await fetch("instruments", {"name": 1, "serial_number": 1})
    change_ctrls = await fetch("change_controls", {"change_control_id": 1, "title": 1, "status": 1})
    bers = await fetch(
        "batch_production_execution_records",
        {"batch_execution_record_id": 1, "status": 1},
    )

    data = {
        "work_order_ids": [d["work_order_id"] for d in work_orders if d.get("work_order_id")],
        "work_order_titles": [d["title"] for d in work_orders if d.get("title")],
        "batch_numbers": [d["batch_number"] for d in batches if d.get("batch_number")],
        "product_names": list({d["product_name"] for d in batches if d.get("product_name")}),
        "approval_ids": [d["approval_id"] for d in approvals if d.get("approval_id")],
        "approval_titles": [d["title"] for d in approvals if d.get("title")],
        "dept_names": [d["name"] for d in departments if d.get("name")],
        "worker_names": [d["name"] for d in workers if d.get("name")],
        "worker_titles": [d["title"] for d in workers if d.get("title")],
        "sop_ids": [d["id"] for d in sops if d.get("id")],
        "sop_titles": [d["title"] for d in sops if d.get("title")],
        "instrument_names": [d["name"] for d in instruments if d.get("name")],
        "serial_numbers": [d["serial_number"] for d in instruments if d.get("serial_number")],
        "cc_ids": [d["change_control_id"] for d in change_ctrls if d.get("change_control_id")],
        "cc_titles": [d["title"] for d in change_ctrls if d.get("title")],
        "ber_ids": [d["batch_execution_record_id"] for d in bers if d.get("batch_execution_record_id")],
    }

    counts = {k: len(v) for k, v in data.items()}
    logger.info("Grounded data loaded: %s", counts)
    client.close()
    return data


def _pick(lst: list, fallback: list, default: str = "N/A") -> str:
    source = lst if lst else (fallback if fallback else None)
    return random.choice(source) if source else default


# ---------------------------------------------------------------------------
# Question corpus builder
# ---------------------------------------------------------------------------


def build_corpus(total: int, ground: dict) -> list[str]:
    wo = ground.get("work_order_ids", [f"WO-{i:05d}" for i in range(1, 100)])
    wt = ground.get("work_order_titles", ["Quarterly PM", "Monthly PM", "Calibration"])
    bn = ground.get("batch_numbers", [f"BATCH-{i:03d}" for i in range(1, 50)])
    pn = ground.get("product_names", ["Cetirizine 10mg Tablets", "Atorvastatin 10mg Tablets"])
    ai = ground.get("approval_ids", [f"APR-{i:04d}" for i in range(1, 20)])
    at = ground.get("approval_titles", ["Batch Release", "Process Change"])
    dn = ground.get("dept_names", ["Operations", "Quality Assurance", "R&D"])
    wn = ground.get("worker_names", ["Ravi Kumar", "Priya Sharma"])
    soi = ground.get("sop_ids", ["SOP-001", "SOP-002"])
    sot = ground.get("sop_titles", ["Dispensing", "Blending"])
    ins = ground.get("instrument_names", ["Analytical Balance", "Moisture Analyzer"])
    sn = ground.get("serial_numbers", ["SN-INS-001"])
    cci = ground.get("cc_ids", ["CC-2026-0001"])
    cct = ground.get("cc_titles", ["Increase Impeller Speed"])
    bei = ground.get("ber_ids", ["BER-001"])

    def q() -> str:
        return random.choice(
            [
                # --- Work Orders ---
                f"What is the status of work order {_pick(wo, wo)}?",
                f"Show me details for work order {_pick(wo, wo)}",
                f"Who is assigned to work order {_pick(wo, wo)}?",
                f"List all work orders in {_pick(dn, dn)} department",
                f"Find all open work orders for {_pick(wt, wt)}",
                f"What work orders are overdue?",
                f"Show me all calibration work orders",
                f"List high priority work orders",
                f"What maintenance work orders are in progress?",
                f"Create a work order for {_pick(ins, ins)} calibration",
                # --- Batches ---
                f"What is the status of batch {_pick(bn, bn)}?",
                f"Show batch record for {_pick(bn, bn)}",
                f"Find all batches for {_pick(pn, pn)}",
                f"What batches are on hold?",
                f"List all in-progress batches",
                f"Show batch execution record {_pick(bei, bei)}",
                f"What is the yield for batch {_pick(bn, bn)}?",
                f"List all released batches this month",
                f"Find batches with deviations",
                f"What is the batch status for {_pick(pn, pn)}?",
                # --- Approvals ---
                f"What is the approval status for {_pick(ai, ai)}?",
                f"Show pending approvals in {_pick(dn, dn)}",
                f"Who approved {_pick(at, at)}?",
                f"List all open approvals",
                f"Find approvals pending for more than 7 days",
                f"Show approval history for batch {_pick(bn, bn)}",
                f"What approvals are due this week?",
                # --- Production KPIs ---
                f"Show production KPIs for {_pick(dn, dn)}",
                f"What is the OEE for the tablet line?",
                f"Show yield trend for {_pick(pn, pn)}",
                f"What are the quality metrics for batch {_pick(bn, bn)}?",
                f"Show downtime report for this week",
                f"What is the batch rejection rate?",
                f"List all deviations this month",
                # --- SOPs ---
                f"Show me SOP {_pick(soi, soi)}",
                f"What is the SOP for {_pick(sot, sot)}?",
                f"List all SOPs in {_pick(dn, dn)} department",
                f"Is SOP {_pick(soi, soi)} current?",
                f"Who approved SOP {_pick(soi, soi)}?",
                # --- Instruments ---
                f"What is the calibration status of {_pick(ins, ins)}?",
                f"Show calibration due instruments",
                f"Is instrument {_pick(sn, sn)} calibrated?",
                f"List overdue calibrations",
                f"When was {_pick(ins, ins)} last calibrated?",
                # --- Change Controls ---
                f"What is the status of change control {_pick(cci, cci)}?",
                f"Show change controls open in {_pick(dn, dn)}",
                f"List all approved change controls",
                f"Who raised change control {_pick(cci, cci)}?",
                f"Show impact assessment for {_pick(cct, cct)}",
                # --- Workers / People ---
                f"What work orders are assigned to {_pick(wn, wn)}?",
                f"Show me tasks for {_pick(wn, wn)}",
                f"Who is the manager of {_pick(dn, dn)}?",
                f"List all operators in {_pick(dn, dn)}",
                # --- Warehouse / Shipping ---
                f"Show warehouse inventory for {_pick(pn, pn)}",
                f"What is the shipping status for batch {_pick(bn, bn)}?",
                f"List pending dispatch orders",
                f"Show inbound shipments this week",
                # --- Drug Research ---
                f"What is the research status for {_pick(pn, pn)}?",
                f"Show formulation data for {_pick(pn, pn)}",
                f"List active drug research projects",
            ]
        )

    chaos = [
        # input validation edge cases
        " ",
        "\t\n",
        "a" * 4000,
        "?" * 500,
        "status " * 150,
        # injection
        "'; DROP TABLE work_orders; --",
        "<script>alert(1)</script>",
        "{{7*7}}",
        "${7*7}",
        "../../etc/passwd",
        "\x00\x01\x02\x03",
        # unicode
        "什么是工作订单状态？",
        "ما هو حالة أمر العمل؟",
        "Статус партии BATCH-001?",
        "🚀🔥 batch status BATCH-001 🔥🚀",
        "‮batch status",  # RTL override
        # null-like
        "null",
        "None",
        "undefined",
        "NaN",
        "Infinity",
        "0",
        "-1",
        # structured data as question
        '{"question": "status"}',
        "[1,2,3]",
        # unsupported intents — must return graceful fallback, not 500
        "Book me a flight to Mumbai",
        "What is the weather in Pune?",
        "Translate to French",
        "Who is the Prime Minister of India?",
        "Write a poem",
        "Hello",
        "?",
        "2 + 2",
        # real IDs with noise appended
        f"{random.choice(wo) if wo else 'WO-001'} INVALID_SUFFIX!!!",
        f"batch {random.choice(bn) if bn else 'BATCH-001'} " + "x" * 200,
    ]

    chaos_count = max(len(chaos), int(total * 0.05))
    domain_count = total - chaos_count
    domain_qs = [q() for _ in range(domain_count)]
    chaos_qs = (chaos * ((chaos_count // len(chaos)) + 1))[:chaos_count]
    combined = domain_qs + chaos_qs
    random.shuffle(combined)
    return combined


# ---------------------------------------------------------------------------
# Result + Stats
# ---------------------------------------------------------------------------


@dataclass
class Result:
    question: str
    status: str  # ok | http_error | timeout | network | exception
    http_code: int | None = None
    latency_ms: float = 0.0
    error: str = ""


@dataclass
class Stats:
    results: list[Result] = field(default_factory=list)
    start: float = field(default_factory=time.monotonic)
    end: float = 0.0

    def pct(self, p: float) -> float:
        lats = sorted(r.latency_ms for r in self.results)
        if not lats:
            return 0.0
        return lats[min(int(len(lats) * p / 100), len(lats) - 1)]

    def print_report(self) -> None:
        total = len(self.results)
        ok = sum(1 for r in self.results if r.status == "ok")
        wall = self.end - self.start

        status_counts: Counter = Counter(r.status for r in self.results)
        http_codes: Counter = Counter(r.http_code for r in self.results if r.status != "ok")
        error_samples: dict[str, list[str]] = defaultdict(list)
        for r in self.results:
            if r.status != "ok" and len(error_samples[r.status]) < 3:
                q_snip = r.question[:80].replace("\n", " ")
                error_samples[r.status].append(f"[HTTP {r.http_code}] {r.error[:100]} | q={q_snip!r}")

        lats = sorted(r.latency_ms for r in self.results)

        print()
        print("=" * 72)
        print("  CHAOS TEST — MonkeyBrain /prompt  (grounded data)")
        print("=" * 72)
        print(f"  Questions      : {total:,}")
        print(f"  Passed (2xx)   : {ok:,}  ({ok / total * 100:.2f}%)")
        print(f"  Failed         : {total - ok:,}")
        print(f"  Wall time      : {wall:.1f}s")
        print(f"  Throughput     : {total / wall:.1f} req/s")
        print()
        if lats:
            print("  Latency (ms)")
            print(f"    p50  = {self.pct(50):.0f}")
            print(f"    p95  = {self.pct(95):.0f}")
            print(f"    p99  = {self.pct(99):.0f}")
            print(f"    min  = {lats[0]:.0f}")
            print(f"    max  = {lats[-1]:.0f}")
            print(f"    mean = {statistics.mean(lats):.0f}")
        print()
        print("  Outcome breakdown")
        for status, count in status_counts.most_common():
            print(f"    {status:<18} {count:>6}")
        if http_codes:
            print()
            print("  HTTP codes on failures")
            for code, count in http_codes.most_common():
                print(f"    {str(code):<18} {count:>6}")
        for status, samples in error_samples.items():
            print()
            print(f"  Sample [{status}]")
            for s in samples:
                print(f"    {s}")

        print()
        rate = ok / total * 100
        if rate >= 99.9:
            verdict = "PASS  (exceptional)"
        elif rate >= 99.0:
            verdict = "PASS"
        elif rate >= 95.0:
            verdict = "WARNING — review failures"
        else:
            verdict = "FAIL  — significant error rate"
        print(f"  VERDICT: {verdict}")
        print("=" * 72)
        print()


# ---------------------------------------------------------------------------
# HTTP runner
# ---------------------------------------------------------------------------


async def _send(client: httpx.AsyncClient, base_url: str, question: str) -> Result:
    url = f"{base_url}/api/v1/agentos/prompt"
    t0 = time.monotonic()
    try:
        resp = await client.post(
            url,
            json={"question": question, "run_query": True, "run_simulate": True},
            headers={"Content-Type": "application/json"},
        )
        ms = (time.monotonic() - t0) * 1000
        if resp.status_code >= 400:
            return Result(question, "http_error", resp.status_code, ms, resp.text[:300])
        return Result(question, "ok", resp.status_code, ms)
    except httpx.TimeoutException as e:
        return Result(question, "timeout", None, (time.monotonic() - t0) * 1000, str(e)[:200])
    except httpx.NetworkError as e:
        return Result(question, "network", None, (time.monotonic() - t0) * 1000, str(e)[:200])
    except Exception as e:
        return Result(question, "exception", None, (time.monotonic() - t0) * 1000, str(e)[:200])


async def run_chaos(
    base_url: str,
    count: int,
    concurrency: int,
    timeout: float,
    mongo_uri: str = MONGO_URI,
    mongo_db: str = MONGO_DB,
) -> Stats:
    logger.info("Loading grounded data from MongoDB %s/%s …", mongo_uri, mongo_db)
    ground = await load_ground_truth(mongo_uri, mongo_db)
    questions = build_corpus(count, ground)

    stats = Stats()
    sem = asyncio.Semaphore(concurrency)
    done = 0
    tick = max(1, count // 20)

    async def limited(q: str) -> None:
        nonlocal done
        async with sem:
            r = await _send(client, base_url, q)
            stats.results.append(r)
            done += 1
            if done % tick == 0:
                ok_n = sum(1 for x in stats.results if x.status == "ok")
                logger.info(
                    "Progress %d/%d (%.0f%%)  pass=%.1f%%",
                    done,
                    count,
                    done / count * 100,
                    ok_n / done * 100,
                )

    logger.info("Target      : %s/api/v1/agentos/prompt", base_url)
    logger.info("Questions   : %d  (95%% grounded domain, 5%% chaos)", count)
    logger.info("Concurrency : %d", concurrency)
    logger.info("Timeout     : %.1fs per request", timeout)

    async with httpx.AsyncClient(timeout=timeout) as client:
        await asyncio.gather(*[limited(q) for q in questions])

    stats.end = time.monotonic()
    return stats


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description="MonkeyBrain /prompt chaos test")
    ap.add_argument("--url", default="http://localhost:8031")
    ap.add_argument("--count", type=int, default=10_000)
    ap.add_argument("--concurrency", type=int, default=100)
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--mongo", default=MONGO_URI, help="MongoDB URI")
    ap.add_argument("--db", default=MONGO_DB, help="MongoDB database name")
    args = ap.parse_args()

    stats = asyncio.run(run_chaos(args.url, args.count, args.concurrency, args.timeout, args.mongo, args.db))
    stats.print_report()
    ok = sum(1 for r in stats.results if r.status == "ok")
    sys.exit(0 if ok / args.count >= 0.99 else 1)


if __name__ == "__main__":
    main()
