#!/usr/bin/env python3
"""AgentOS Benchmark — full metrics for ALL classifier examples.

Metrics:
  1. Answer Accuracy
  2. Intent Accuracy
  3. Entity Resolution Accuracy
  4. Workflow Accuracy
  5. Latency (mean, median, P95, per-phase)
  6. Ambiguity Rate
  7. Question Complexity
  8. Failure Breakdown
  9. Accuracy by Complexity
 10. LLM metrics (tokens, model, invocations)
"""

import json, re, time, httpx, sys
from services.agentos.classifier.intent_examples import INTENT_EXAMPLES
from services.agentos.gateway.goal_router import route_intent_to_pipeline

ENDPOINT = "http://localhost:8000/api/v1/agentos/query"
BATCH = 20

# Complexity buckets
COMPLEXITY_MAP = {
    "count": "count",
    "count_line": "count",
    "count_maintenance": "count",
    "count_change_control": "count",
    "count_batch": "count",
    "count_work_order": "count",
    "stage": "lookup",
    "line": "lookup",
    "plant_topology": "lookup",
    "stage_workstation": "lookup",
    "line_stage": "lookup",
    "line_workstation": "lookup",
    "workstation": "lookup",
    "plant": "lookup",
    "asset_management": "lookup",
    "machine_detail": "lookup",
    "worker": "relationship",
    "worker_plant": "relationship",
    "sop_compliance": "relationship",
    "process_sop": "relationship",
    "workstation_sop": "relationship",
    "workstation_asset": "relationship",
    "production_kpi": "aggregation",
    "stage_kpi": "aggregation",
    "line_kpi": "aggregation",
    "batch_record": "aggregation",
    "batch_quality": "aggregation",
    "maintenance": "aggregation",
    "change_control": "aggregation",
    "quality_approval": "aggregation",
    "work_order_query": "aggregation",
    "work_order_detail": "aggregation",
    "work_order_event": "aggregation",
    "simulation": "multi_hop",
    "decision_intelligence": "multi_hop",
    "knowledge_base": "multi_hop",
    "document_search": "multi_hop",
    "web_search": "multi_hop",
    "drug_research": "multi_hop",
    "warehouse_shipping": "multi_hop",
}

KNOWN_ENTITIES = [
    "Tablet Manufacturing Plant",
    "Tablet Line A",
    "Capsule Line B",
    "Granulation",
    "Compression",
    "Blending",
    "Film Coating",
    "Dispensing",
    "Packaging",
    "Inspection",
    "Drying",
    "Sifting",
    "Milling",
    "Sampling Booth",
    "Weighing Booth",
    "Tablet Press Station",
    "Coating Pan Station",
    "Binder Preparation Station",
    "RMG",
    "V-Blender",
    "Bin Blender",
    "Fluid Bed Dryer",
    "Blend Preparation Workstation",
    "Granulation Processing Workstation",
    "BATCH-2026",
    "Coating Pan 01",
    "Rapid Mixer Granulator RMG-01",
]


def complexity_bucket(intent):
    return COMPLEXITY_MAP.get(intent, "lookup")


def is_ambiguous(q):
    ql = q.lower().strip()
    has_entity = bool(re.search(r"\b[A-Z][a-z]+\b", q))
    return (not has_entity) or len(ql.split()) < 4


def entity_ok(answer, question):
    ql = question.lower()
    for e in KNOWN_ENTITIES:
        if e.lower() in ql:
            return e.lower() in answer.lower()
    return True


def answer_ok(answer):
    if not answer or not answer.strip():
        return False
    low = answer.lower()[:80]
    if "error" in low or "no result from pipeline" in low:
        return False
    return True


def failure_type(answer, expected_intent, actual_intent, question):
    if not answer or not answer.strip():
        return "Runtime Exception"
    low = answer.lower()[:80]
    if "error" in low or "exception" in low:
        return "Runtime Exception"
    if "no result from pipeline" in low:
        return "Pipeline Selection"
    if "not found" in low[:50]:
        return "Entity Resolution"
    if actual_intent and actual_intent != expected_intent:
        return "Intent Misclassification"
    if not answer_ok(answer):
        return "Wrong Answer"
    return None


def print_kpi(results, batch_num, total_all):
    n = len(results)
    if n == 0:
        return
    aa = sum(1 for r in results if r["aa"]) / n
    ia = sum(1 for r in results if r["ia"]) / n
    wa = sum(1 for r in results if r["wa"]) / n
    era = sum(1 for r in results if r["er"]) / n
    lats = sorted(r["ms"] for r in results)
    avg_lat = sum(lats) / n
    med_lat = lats[n // 2]
    p95_lat = lats[int(n * 0.95)]
    amb = sum(1 for r in results if r["amb"])
    amb_res = sum(1 for r in results if r["amb"] and r["aa"] and r["wa"])
    total_pt = sum(r.get("pt", 0) for r in results)
    total_ct = sum(r.get("ct", 0) for r in results)
    llm_calls = sum(r.get("llm", 0) for r in results)
    avg_pt = total_pt // n if n else 0
    avg_ct = total_ct // n if n else 0
    llm_per_q = llm_calls / n if n else 0

    # Failure breakdown
    failures = {}
    for r in results:
        if not (r["aa"] and r["wa"]):
            ft = r.get("failure_type", "Unknown")
            failures[ft] = failures.get(ft, 0) + 1

    # Complexity buckets
    by_cb = {}
    for r in results:
        cb = r["bucket"]
        if cb not in by_cb:
            by_cb[cb] = {"n": 0, "p": 0}
        by_cb[cb]["n"] += 1
        if r["aa"] and r["wa"]:
            by_cb[cb]["p"] += 1

    print()
    print("=" * 72)
    print(f"  CUMULATIVE KPI — Batch {batch_num} ({n} questions, {total_all} total)")
    print("=" * 72)
    print(f"  | KPI                        | Value          |")
    print(f"  |----------------------------|----------------|")
    print(f"  | Overall Accuracy           | {aa:.1%}           |")
    print(f"  | Intent Accuracy            | {ia:.1%}           |")
    print(f"  | Pipeline Accuracy          | {wa:.1%}           |")
    print(f"  | Entity Resolution Accuracy | {era:.1%}           |")
    print(f"  | Mean Latency               | {avg_lat / 1000:.1f} s          |")
    print(f"  | Median Latency             | {med_lat / 1000:.1f} s          |")
    print(f"  | P95 Latency                | {p95_lat / 1000:.1f} s          |")
    print(f"  | LLM Calls / Query          | {llm_per_q:.1f}            |")
    print(f"  | Average Prompt Tokens      | {avg_pt}            |")
    print(f"  | Average Completion Tokens  | {avg_ct}            |")
    amb_pct = f"{amb_res / amb:.0%}" if amb else "N/A"
    print(f"  | Ambiguous Queries Resolved | {amb_res}/{amb} ({amb_pct})         |")
    print(f"  | Benchmark Coverage         | {total_all} questions |")
    print(f"  |----------------------------|----------------|")

    # Failure breakdown
    print(f"  |                            |                |")
    print(f"  | Failures                   |                |")
    total_fails = sum(failures.values())
    for ft in [
        "Intent Misclassification",
        "Entity Resolution",
        "Pipeline Selection",
        "Grounding",
        "Runtime Exception",
        "Wrong Answer",
    ]:
        cnt = failures.get(ft, 0)
        print(f"  |   {ft:26s} | {cnt:3d}            |")
    print(f"  |   {'Total Failures':26s} | {total_fails:3d}            |")

    # Complexity breakdown
    print(f"  |                            |                |")
    print(f"  | Accuracy by Complexity     |                |")
    for cb in ["count", "lookup", "relationship", "aggregation", "multi_hop"]:
        if cb in by_cb:
            d = by_cb[cb]
            cr = d["p"] / d["n"] if d["n"] else 0
            print(f"  |   {cb:26s} | {cr:.1%} ({d['n']:3d})    |")

    # LLM metrics
    total_crit = sum(r.get("crit_ms", 0) for r in results)
    avg_crit = total_crit // n if n else 0
    print(f"  |                            |                |")
    print(f"  | LLM Metrics                |                |")
    print(f"  |   LLM Calls / Query        | {llm_per_q:.1f}            |")
    print(f"  |   Avg Prompt Tokens        | {avg_pt}            |")
    print(f"  |   Avg Completion Tokens    | {avg_ct}            |")
    print(f"  |   Avg Critic Time          | {avg_crit}ms          |")
    print(f"  |   Total LLM Calls          | {llm_calls}            |")
    print(f"{'=' * 72}")
    sys.stdout.flush()


# ── Main ──────────────────────────────────────────────────────────────
all_results = []
batch_num = 0
total = 0
print(
    f"\n{'#':>4} {'OK':3} {'Intent':22} {'IntOK':5} {'EntOK':5} {'WfOK':5} {'Bucket':12} {'Amb':3} {'ms':>6}  Question"
)
print("-" * 120)

for intent, examples in INTENT_EXAMPLES.items():
    for question in examples:
        total += 1
        t0 = time.monotonic()
        try:
            r = httpx.post(
                ENDPOINT,
                json={
                    "question": question,
                    "user_id": "t",
                    "role": "admin",
                    "session_id": "s",
                },
                timeout=90.0,
            )
            d = r.json()
            answer = d.get("answer", "")
            ms = round((time.monotonic() - t0) * 1000)
            meta = d.get("metadata", {})
            actual = meta.get("intent")
            profile = meta.get("profile", [])
            pt = sum(p.get("prompt_tokens", 0) for p in profile)
            ct = sum(p.get("completion_tokens", 0) for p in profile)
            crit_ms = sum(p.get("ms", 0) for p in profile if "Critic" in p.get("phase", ""))
            llm = sum(
                1
                for p in profile
                if p.get("prompt_tokens", 0) > 0 or "LLM" in p.get("phase", "") or "Critic" in p.get("phase", "")
            )
            model = next((p.get("model", "") for p in profile if p.get("model")), "n/a")
        except Exception:
            ms = round((time.monotonic() - t0) * 1000)
            answer = ""
            actual = None
            pt = 0
            ct = 0
            llm = 0
            crit_ms = 0
            model = "n/a"

        aa = answer_ok(answer)
        ia = actual == intent if actual else False
        er = entity_ok(answer, question)
        wa = bool(answer.strip()) and "No result from pipeline" not in answer
        amb = is_ambiguous(question)
        bucket = complexity_bucket(intent)
        ft = failure_type(answer, intent, actual, question)
        ok = aa and wa

        all_results.append(
            {
                "intent": intent,
                "aa": aa,
                "ia": ia,
                "er": er,
                "wa": wa,
                "ms": ms,
                "amb": amb,
                "bucket": bucket,
                "actual": actual,
                "pt": pt,
                "ct": ct,
                "llm": llm,
                "ft": ft,
                "crit_ms": crit_ms,
                "model": model,
            }
        )

        tag = "PASS" if ok else "FAIL"
        ans_short = answer[:80].replace("\n", " ") if answer else "(empty)"
        crit_ms = sum(p.get("ms", 0) for p in profile if "Critic" in p.get("phase", ""))
        llm_count = sum(
            1
            for p in profile
            if "LLM" in p.get("phase", "")
            or "Critic" in p.get("phase", "")
            or "Answer" in p.get("phase", "")
            or "Synthesis" in p.get("phase", "")
        )
        print(
            f"{total:4} {tag:3} {intent:22} int={'Y' if ia else 'N'} ent={'Y' if er else 'N'} wf={wa!s:5} {bucket:12} amb={'?' if amb else ' '} {ms:6}ms crit={crit_ms:5}ms llm={llm_count} pt={pt:5d} ct={ct:4d} model={model[:15]}"
        )
        print(f"  Q: {question}")
        print(f"  A: {ans_short}")
        sys.stdout.flush()

        if len(all_results) % BATCH == 0:
            batch_num += 1
            print_kpi(all_results, batch_num, total)

batch_num += 1
print_kpi(all_results, batch_num, total)

# Save
with open("classifier_examples_report.json", "w") as f:
    json.dump({"total": total, "results": all_results}, f, indent=2)
print("\n  Saved: classifier_examples_report.json")
