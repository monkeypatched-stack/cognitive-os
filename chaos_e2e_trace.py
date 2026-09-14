#!/usr/bin/env python3
"""
Chaos test with E2E traceability — shows Q&A, quality, and metrics at each step.

Usage:
    python chaos_e2e_trace.py --count 100 --concurrency 10
    python chaos_e2e_trace.py --count 10000 --concurrency 100
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

import httpx

logging.basicConfig(level=logging.WARNING)

MONGO_URI = "mongodb://localhost:27017"
MONGO_DB = "demo"


# ---------------------------------------------------------------------------
# Load grounded data from MongoDB
# ---------------------------------------------------------------------------


async def load_ground_truth(mongo_uri: str = MONGO_URI, mongo_db: str = MONGO_DB) -> dict[str, list]:
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
    except ImportError:
        return {}
    client = AsyncIOMotorClient(mongo_uri)
    db = client[mongo_db]

    async def fetch(coll, projection, limit=500):
        try:
            projection["_id"] = 0
            return await db[coll].find({}, projection).limit(limit).to_list(limit)
        except Exception:
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
        "sop_ids": [d["id"] for d in sops if d.get("id")],
        "sop_titles": [d["title"] for d in sops if d.get("title")],
        "instrument_names": [d["name"] for d in instruments if d.get("name")],
        "serial_numbers": [d["serial_number"] for d in instruments if d.get("serial_number")],
        "cc_ids": [d["change_control_id"] for d in change_ctrls if d.get("change_control_id")],
        "cc_titles": [d["title"] for d in change_ctrls if d.get("title")],
        "ber_ids": [d["batch_execution_record_id"] for d in bers if d.get("batch_execution_record_id")],
    }
    client.close()
    return data


def _pick(lst, fallback):
    source = lst if lst else fallback
    if not source:
        return "UNKNOWN"
    return random.choice(source)


# ---------------------------------------------------------------------------
# Question corpus
# ---------------------------------------------------------------------------


def build_corpus(total, ground):
    def _f(lst, default):
        return lst if lst else default

    wo = _f(ground.get("work_order_ids"), [f"WO-{i:05d}" for i in range(1, 100)])
    wt = _f(ground.get("work_order_titles"), ["Quarterly PM", "Monthly PM"])
    bn = _f(ground.get("batch_numbers"), [f"BATCH-{i:03d}" for i in range(1, 50)])
    pn = _f(ground.get("product_names"), ["Cetirizine 10mg Tablets"])
    ai = _f(ground.get("approval_ids"), [f"APR-{i:04d}" for i in range(1, 20)])
    at_ = _f(ground.get("approval_titles"), ["Batch Release"])
    dn = _f(ground.get("dept_names"), ["Operations", "Quality Assurance"])
    wn = _f(ground.get("worker_names"), ["Ravi Kumar", "Priya Sharma"])
    soi = _f(ground.get("sop_ids"), ["SOP-001"])
    sot = _f(ground.get("sop_titles"), ["Dispensing"])
    ins = _f(ground.get("instrument_names"), ["Analytical Balance"])
    sn = _f(ground.get("serial_numbers"), ["SN-INS-001"])
    cci = _f(ground.get("cc_ids"), ["CC-2026-0001"])
    cct = _f(ground.get("cc_titles"), ["Increase Impeller Speed"])
    bei = _f(ground.get("ber_ids"), ["BER-001"])

    def q():
        return random.choice(
            [
                f"What is the status of work order {_pick(wo, wo)}?",
                f"Show me details for work order {_pick(wo, wo)}",
                f"List all work orders in {_pick(dn, dn)} department",
                f"Find all open work orders for {_pick(wt, wt)}",
                f"What work orders are overdue?",
                f"Show me all calibration work orders",
                f"List high priority work orders",
                f"Create a work order for {_pick(ins, ins)} calibration",
                f"What is the status of batch {_pick(bn, bn)}?",
                f"Show batch record for {_pick(bn, bn)}",
                f"Find all batches for {_pick(pn, pn)}",
                f"What batches are on hold?",
                f"List all in-progress batches",
                f"Show batch execution record {_pick(bei, bei)}",
                f"What is the yield for batch {_pick(bn, bn)}?",
                f"List all released batches this month",
                f"What is the approval status for {_pick(ai, ai)}?",
                f"Show pending approvals in {_pick(dn, dn)}",
                f"Who approved {_pick(at_, at_)}?",
                f"List all open approvals",
                f"Show production KPIs for {_pick(dn, dn)}",
                f"What is the OEE for the tablet line?",
                f"Show yield trend for {_pick(pn, pn)}",
                f"What are the quality metrics for batch {_pick(bn, bn)}?",
                f"Show downtime report for this week",
                f"Show me SOP {_pick(soi, soi)}",
                f"What is the SOP for {_pick(sot, sot)}?",
                f"What is the calibration status of {_pick(ins, ins)}?",
                f"Show calibration due instruments",
                f"When was {_pick(ins, ins)} last calibrated?",
                f"What is the status of change control {_pick(cci, cci)}?",
                f"Show change controls open in {_pick(dn, dn)}",
                f"List all approved change controls",
                f"What work orders are assigned to {_pick(wn, wn)}?",
                f"Show warehouse inventory for {_pick(pn, pn)}",
                f"What is the shipping status for batch {_pick(bn, bn)}?",
                f"What is the research status for {_pick(pn, pn)}?",
                f"Show formulation data for {_pick(pn, pn)}",
            ]
        )

    chaos = [
        " ",
        "\t\n",
        "?" * 500,
        "'; DROP TABLE work_orders; --",
        "<script>alert(1)</script>",
        "{{7*7}}",
        "${7*7}",
        "什么是工作订单状态？",
        "🚀🔥 batch status 🔥🚀",
        "null",
        "None",
        "undefined",
        "0",
        "-1",
        '{"question": "status"}',
        "Book me a flight to Mumbai",
        "What is the weather in Pune?",
        "Hello",
        "?",
        "2 + 2",
    ]

    chaos_count = max(len(chaos), int(total * 0.05))
    domain_count = total - chaos_count
    domain_qs = [q() for _ in range(domain_count)]
    chaos_qs = (chaos * ((chaos_count // len(chaos)) + 1))[:chaos_count]
    combined = domain_qs + chaos_qs
    random.shuffle(combined)
    return combined


# ---------------------------------------------------------------------------
# Quality evaluation
# ---------------------------------------------------------------------------


def evaluate_answer(question: str, answer: str, status: str, http_code: int | None) -> dict:
    """Evaluate answer quality and return a quality dict."""
    q = {
        "http_ok": status == "ok" and http_code is not None and 200 <= http_code < 400,
        "has_answer": False,
        "answer_len": 0,
        "quality": "fail",
        "quality_notes": [],
    }
    if not answer:
        q["quality_notes"].append("empty answer")
        return q

    q["answer_len"] = len(answer)
    q["has_answer"] = len(answer.strip()) > 5

    notes = q["quality_notes"]

    if not q["http_ok"]:
        notes.append(f"HTTP {http_code}")
        return q

    # Check for error patterns (be careful not to flag "500mg" in product names)
    lower = answer.lower()
    error_patterns = [
        "error",
        "traceback",
        "exception",
        "internal server",
        "failed",
        "crash",
        "timeout",
        "connection refused",
    ]
    for pat in error_patterns:
        if pat in lower:
            notes.append(f"error pattern: {pat}")
    # Only flag standalone "500" errors, not "500mg"
    import re

    if re.search(r"\b500\b(?!\s*mg)", lower):
        notes.append("error pattern: 500")

    # Check for meaningful content
    if len(answer.strip()) < 10:
        notes.append("too short (<10 chars)")
    elif len(answer.strip()) > 5000:
        notes.append("very long (>5k chars)")

    # Check for empty/stub responses
    stubs = ["no intent", "could not", "not found", "empty", "none"]
    for s in stubs:
        if s in lower:
            notes.append(f"stub: {s}")

    # Determine quality
    if notes:
        q["quality"] = "warn"
    elif q["has_answer"] and q["answer_len"] > 20:
        q["quality"] = "pass"
    else:
        q["quality"] = "warn"

    return q


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class TraceResult:
    question: str
    answer: str = ""
    status: str = ""
    http_code: int | None = None
    latency_ms: float = 0.0
    error: str = ""
    quality: str = "fail"
    quality_notes: list = field(default_factory=list)
    answer_len: int = 0


# ---------------------------------------------------------------------------
# HTTP runner with traceability
# ---------------------------------------------------------------------------


async def _send_traced(client: httpx.AsyncClient, base_url: str, question: str) -> TraceResult:
    url = f"{base_url}/api/v1/agentos/prompt"
    t0 = time.monotonic()
    result = TraceResult(question=question)
    try:
        resp = await client.post(
            url,
            json={"question": question, "run_query": True, "run_simulate": True},
            headers={"Content-Type": "application/json"},
        )
        result.latency_ms = (time.monotonic() - t0) * 1000
        result.http_code = resp.status_code

        if resp.status_code >= 400:
            result.status = "http_error"
            result.error = resp.text[:300]
        else:
            result.status = "ok"
            # Parse response body
            try:
                body = resp.json()
                result.answer = body.get("answer", "") or body.get("response", "") or json.dumps(body)[:2000]
            except Exception:
                result.answer = resp.text[:2000]
    except httpx.TimeoutException as e:
        result.latency_ms = (time.monotonic() - t0) * 1000
        result.status = "timeout"
        result.error = str(e)[:200]
    except httpx.NetworkError as e:
        result.latency_ms = (time.monotonic() - t0) * 1000
        result.status = "network"
        result.error = str(e)[:200]
    except Exception as e:
        result.latency_ms = (time.monotonic() - t0) * 1000
        result.status = "exception"
        result.error = str(e)[:200]

    # Evaluate quality
    q = evaluate_answer(question, result.answer, result.status, result.http_code)
    result.quality = q["quality"]
    result.quality_notes = q["quality_notes"]
    result.answer_len = q["answer_len"]
    return result


async def run_chaos(base_url, count, concurrency, timeout, mongo_uri=MONGO_URI, mongo_db=MONGO_DB):
    print("=" * 80)
    print("  MONKEYBRAIN CHAOS TEST — E2E TRACEABILITY")
    print("=" * 80)
    print(f"  Target       : {base_url}/api/v1/agentos/prompt")
    print(f"  Questions    : {count}  (95% domain, 5% chaos)")
    print(f"  Concurrency  : {concurrency}")
    print(f"  Timeout      : {timeout}s")
    print()

    print("Loading grounded data from MongoDB...")
    ground = await load_ground_truth(mongo_uri, mongo_db)
    questions = build_corpus(count, ground)
    print(f"Corpus built: {len(questions)} questions")
    print()

    results: list[TraceResult] = []
    sem = asyncio.Semaphore(concurrency)
    done = 0
    tick = max(1, count // 20)

    # Running accumulators
    quality_counts = Counter()
    latencies = []
    answer_lengths = []
    total_start = time.monotonic()

    async def limited(q):
        nonlocal done
        async with sem:
            r = await _send_traced(client, base_url, q)
            results.append(r)
            done += 1

            quality_counts[r.quality] += 1
            latencies.append(r.latency_ms)
            if r.answer_len > 0:
                answer_lengths.append(r.answer_len)

            # Print Q&A at each progress tick
            if done % tick == 0 or done <= 5:
                elapsed = time.monotonic() - total_start
                ok = quality_counts.get("pass", 0)
                warn = quality_counts.get("warn", 0)
                fail = quality_counts.get("fail", 0)
                avg_lat = statistics.mean(latencies[-tick:]) if latencies else 0
                p95_lat = sorted(latencies)[int(len(latencies) * 0.95)] if len(latencies) > 1 else 0
                avg_anslen = statistics.mean(answer_lengths[-tick:]) if answer_lengths else 0

                print(f"--- Progress {done}/{count} ({done / count * 100:.0f}%) | {elapsed:.1f}s elapsed ---")
                print(
                    f"  Quality: PASS={ok} ({ok / done * 100:.1f}%)  WARN={warn} ({warn / done * 100:.1f}%)  FAIL={fail} ({fail / done * 100:.1f}%)"
                )
                print(f"  Latency: avg={avg_lat:.0f}ms  p95={p95_lat:.0f}ms")
                print(f"  Answer length: avg={avg_anslen:.0f} chars")
                print()

                # Show last few Q&A samples
                show_count = min(5, tick)
                for rr in results[-show_count:]:
                    q_short = rr.question[:60].replace("\n", " ")
                    a_short = rr.answer[:80].replace("\n", " ") if rr.answer else "(empty)"
                    notes = ", ".join(rr.quality_notes) if rr.quality_notes else "clean"
                    icon = {"pass": "✓", "warn": "⚠", "fail": "✗"}.get(rr.quality, "?")
                    print(f"  {icon} Q: {q_short}")
                    print(f"    A: {a_short}")
                    print(f"    [{rr.latency_ms:.0f}ms] quality={rr.quality} len={rr.answer_len} notes={notes}")
                print()

    async with httpx.AsyncClient(timeout=timeout) as client:
        await asyncio.gather(*[limited(q) for q in questions])

    total_elapsed = time.monotonic() - total_start

    # ── Final Report ──
    print()
    print("=" * 80)
    print("  FINAL REPORT")
    print("=" * 80)

    total = len(results)
    pass_n = quality_counts.get("pass", 0)
    warn_n = quality_counts.get("warn", 0)
    fail_n = quality_counts.get("fail", 0)

    print(f"  Total questions  : {total}")
    print(f"  Wall time        : {total_elapsed:.1f}s")
    print(f"  Throughput       : {total / total_elapsed:.1f} req/s")
    print()

    print("  Quality Breakdown")
    print(f"    PASS (good answer)  : {pass_n:>6}  ({pass_n / total * 100:.1f}%)")
    print(f"    WARN (partial/weak) : {warn_n:>6}  ({warn_n / total * 100:.1f}%)")
    print(f"    FAIL (error/empty)  : {fail_n:>6}  ({fail_n / total * 100:.1f}%)")
    print()

    # Latency stats
    if latencies:
        slats = sorted(latencies)
        print("  Latency (ms)")
        print(f"    p50  = {slats[len(slats) // 2]:.0f}")
        print(f"    p95  = {slats[int(len(slats) * 0.95)]:.0f}")
        print(f"    p99  = {slats[int(len(slats) * 0.99)]:.0f}")
        print(f"    min  = {slats[0]:.0f}")
        print(f"    max  = {slats[-1]:.0f}")
        print(f"    mean = {statistics.mean(slats):.0f}")
    print()

    # Answer length stats
    if answer_lengths:
        print("  Answer Length (chars)")
        print(f"    avg  = {statistics.mean(answer_lengths):.0f}")
        print(f"    min  = {min(answer_lengths)}")
        print(f"    max  = {max(answer_lengths)}")
    print()

    # Quality notes breakdown
    all_notes = Counter()
    for r in results:
        for n in r.quality_notes:
            all_notes[n] += 1
    if all_notes:
        print("  Quality Notes Breakdown")
        for note, cnt in all_notes.most_common(10):
            print(f"    {note:<40} {cnt:>5}")
        print()

    # Status breakdown
    status_counts = Counter(r.status for r in results)
    print("  HTTP Status Breakdown")
    for s, cnt in status_counts.most_common():
        print(f"    {s:<20} {cnt:>6}")
    print()

    # Worst latency samples
    worst = sorted(results, key=lambda r: r.latency_ms, reverse=True)[:5]
    print("  Top 5 Slowest Requests")
    for r in worst:
        q_short = r.question[:50].replace("\n", " ")
        print(f"    {r.latency_ms:7.0f}ms | {r.status:<10} | {q_short}")
    print()

    # Worst quality samples
    fails = [r for r in results if r.quality == "fail"][:5]
    if fails:
        print("  Top 5 Failed Requests (sample)")
        for r in fails:
            q_short = r.question[:50].replace("\n", " ")
            notes = ", ".join(r.quality_notes)
            print(f"    Q: {q_short}")
            print(f"    notes: {notes}")
        print()

    # Verdict
    rate = pass_n / total * 100 if total else 0
    if rate >= 99.0:
        verdict = "PASS (exceptional)"
    elif rate >= 95.0:
        verdict = "PASS"
    elif rate >= 80.0:
        verdict = "WARNING — review quality issues"
    else:
        verdict = "FAIL — significant quality issues"
    print(f"  VERDICT: {verdict}  (pass rate: {rate:.1f}%)")
    print("=" * 80)
    print()

    return results


def main():
    ap = argparse.ArgumentParser(description="Chaos test with E2E traceability")
    ap.add_argument("--url", default="http://localhost:8031")
    ap.add_argument("--count", type=int, default=10_000)
    ap.add_argument("--concurrency", type=int, default=100)
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--mongo", default=MONGO_URI)
    ap.add_argument("--db", default=MONGO_DB)
    args = ap.parse_args()

    asyncio.run(run_chaos(args.url, args.count, args.concurrency, args.timeout, args.mongo, args.db))


if __name__ == "__main__":
    main()
