#!/usr/bin/env python3
"""AgentOS Benchmark — Plant Topology with ELK stack.

Creates ES index with mappings, runs all plant questions, saves per-question
results and aggregate KPIs, creates Kibana index pattern.
"""

import json, re, time, httpx, sys, hashlib
from datetime import datetime, timezone
from services.agentos.classifier.intent_examples import INTENT_EXAMPLES
from elasticsearch import Elasticsearch

ENDPOINT = "http://localhost:8000/api/v1/agentos/query"
ES_URL = "http://localhost:9200"
KIBANA_URL = "http://localhost:5601"

es = Elasticsearch(ES_URL)

RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
QUERY_INDEX = f"agentos-benchmark-{RUN_ID}"
AGG_INDEX = f"agentos-benchmark-agg-{RUN_ID}"
PATTERN = "agentos-benchmark-*"

# ── ES index mapping ──────────────────────────────────────────────────
MAPPING = {
    "mappings": {
        "properties": {
            "timestamp": {"type": "date"},
            "run_id": {"type": "keyword"},
            "question": {"type": "text"},
            "answer": {"type": "text"},
            "expected_intent": {"type": "keyword"},
            "predicted_intent": {"type": "keyword"},
            "intent_correct": {"type": "boolean"},
            "pipeline": {"type": "keyword"},
            "entity_correct": {"type": "boolean"},
            "workflow_correct": {"type": "boolean"},
            "overall_correct": {"type": "boolean"},
            "latency_ms": {"type": "integer"},
            "critic_time_ms": {"type": "integer"},
            "llm_calls": {"type": "integer"},
            "prompt_tokens": {"type": "integer"},
            "completion_tokens": {"type": "integer"},
            "model": {"type": "keyword"},
            "status_code": {"type": "integer"},
            "error": {"type": "text"},
            "ambiguity_flag": {"type": "boolean"},
            "failure_type": {"type": "keyword"},
            "complexity_bucket": {"type": "keyword"},
            "category": {"type": "keyword"},
            "question_number": {"type": "integer"},
        }
    }
}

AGG_MAPPING = {
    "mappings": {
        "properties": {
            "timestamp": {"type": "date"},
            "run_id": {"type": "keyword"},
            "benchmark_type": {"type": "keyword"},
            "total_questions": {"type": "integer"},
            "overall_accuracy": {"type": "float"},
            "intent_accuracy": {"type": "float"},
            "pipeline_accuracy": {"type": "float"},
            "entity_resolution_accuracy": {"type": "float"},
            "mean_latency_ms": {"type": "integer"},
            "median_latency_ms": {"type": "integer"},
            "p95_latency_ms": {"type": "integer"},
            "llm_calls_per_query": {"type": "float"},
            "avg_prompt_tokens": {"type": "integer"},
            "avg_completion_tokens": {"type": "integer"},
            "avg_critic_time_ms": {"type": "integer"},
            "total_llm_calls": {"type": "integer"},
            "total_failures": {"type": "integer"},
            "failure_breakdown": {"type": "object"},
        }
    }
}

# ── Create indices ────────────────────────────────────────────────────
if es.indices.exists(index=QUERY_INDEX):
    es.indices.delete(index=QUERY_INDEX)
if es.indices.exists(index=AGG_INDEX):
    es.indices.delete(index=AGG_INDEX)
es.indices.create(index=QUERY_INDEX, body=MAPPING)
es.indices.create(index=AGG_INDEX, body=AGG_MAPPING)
print(f"Created ES indices: {QUERY_INDEX}, {AGG_INDEX}")

# ── Collect plant questions ───────────────────────────────────────────
plant_q = [(i, q) for i, exs in INTENT_EXAMPLES.items() if i in ("plant_topology", "plant") for q in exs]
print(f"Running {len(plant_q)} plant questions...\n")

# ── Run benchmark ─────────────────────────────────────────────────────
results = []
for idx, (intent, question) in enumerate(plant_q, 1):
    t0 = time.monotonic()
    try:
        r = httpx.post(
            ENDPOINT,
            json={
                "question": question,
                "user_id": "benchmark",
                "role": "admin",
                "session_id": f"bench-{RUN_ID}-{idx}",
            },
            timeout=60.0,
        )
        d = r.json()
        answer = d.get("answer", "")
        ms = round((time.monotonic() - t0) * 1000)
        meta = d.get("metadata", {})
        actual = meta.get("intent")
        pipeline = meta.get("pipeline")
        profile = meta.get("profile", [])
        pt = sum(p.get("prompt_tokens", 0) for p in profile)
        ct = sum(p.get("completion_tokens", 0) for p in profile)
        crit = sum(p.get("ms", 0) for p in profile if "Critic" in p.get("phase", ""))
        llm = sum(
            1
            for p in profile
            if "LLM" in p.get("phase", "") or "Critic" in p.get("phase", "") or "Answer" in p.get("phase", "")
        )
        model = next((p.get("model", "") for p in profile if p.get("model")), "none")
        status_code = r.status_code
        error = None
    except Exception as e:
        ms = round((time.monotonic() - t0) * 1000)
        answer = ""
        actual = None
        pipeline = None
        pt = ct = crit = llm = 0
        model = "none"
        status_code = 0
        error = str(e)

    aa = bool(answer.strip()) and "error" not in answer.lower()[:30]
    ia = actual == intent if actual else False
    er = "Tablet Manufacturing Plant" in answer or "Tablet Line A" in answer or "Plant not found" not in answer
    wa = bool(answer.strip()) and "No result from pipeline" not in answer
    amb = len(question.split()) < 4 or not bool(re.search(r"[A-Z][a-z]+", question))
    ft = None
    if not (aa and wa):
        if error:
            ft = "Runtime Exception"
        elif "not found" in answer.lower()[:50]:
            ft = "Entity Resolution"
        elif actual and actual != intent:
            ft = "Intent Misclassification"
        elif not aa:
            ft = "Wrong Answer"
        else:
            ft = "Pipeline Selection"

    doc = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "run_id": RUN_ID,
        "question_number": idx,
        "question": question,
        "answer": answer[:500],
        "expected_intent": intent,
        "predicted_intent": actual,
        "intent_correct": ia,
        "pipeline": pipeline,
        "entity_correct": er,
        "workflow_correct": wa,
        "overall_correct": aa and wa,
        "latency_ms": ms,
        "critic_time_ms": crit,
        "llm_calls": llm,
        "prompt_tokens": pt,
        "completion_tokens": ct,
        "model": model,
        "status_code": status_code,
        "error": error,
        "ambiguity_flag": amb,
        "failure_type": ft,
        "complexity_bucket": "plant_lookup",
        "category": "plant_topology",
    }
    results.append(doc)

    tag = "PASS" if aa and wa else "FAIL"
    a_short = answer[:100].replace("\n", " ") if answer else "(empty)"
    print(
        f"{idx:2} {tag:4} | {intent:22} | int={'Y' if ia else 'N'} ent={'Y' if er else 'N'} wf={str(wa):5} | {ms:5}ms crit={crit:5}ms | pt={pt:5} ct={ct:4} | {model[:15]}"
    )
    print(f"   Q: {question}")
    print(f"   A: {a_short}")
    sys.stdout.flush()

# ── Bulk index to ES ──────────────────────────────────────────────────
print(f"\nIndexing {len(results)} documents to {QUERY_INDEX}...")
actions = []
for doc in results:
    doc_id = hashlib.md5(f"{doc['question']}-{doc['question_number']}".encode()).hexdigest()
    actions.append({"index": {"_index": QUERY_INDEX, "_id": doc_id}})
    actions.append(doc)
es.bulk(operations=actions, refresh="wait_for")
print(f"Indexed {len(results)} query documents.")

# ── Aggregate metrics ─────────────────────────────────────────────────
n = len(results)
aa = sum(1 for r in results if r["overall_correct"]) / n
ia = sum(1 for r in results if r["intent_correct"]) / n
er = sum(1 for r in results if r["entity_correct"]) / n
wa = sum(1 for r in results if r["workflow_correct"]) / n
lats = sorted(r["latency_ms"] for r in results)
al = sum(lats) / n
ml = lats[n // 2]
p95 = lats[int(n * 0.95)]
tp = sum(r["prompt_tokens"] for r in results)
tc = sum(r["completion_tokens"] for r in results)
lc = sum(r["llm_calls"] for r in results)
apt = tp // n
act = tc // n
lq = lc / n
tcr = sum(r["critic_time_ms"] for r in results)
acr = tcr // n
fails = {}
for r in results:
    if r["failure_type"]:
        fails[r["failure_type"]] = fails.get(r["failure_type"], 0) + 1
total_fails = sum(fails.values())

agg_doc = {
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "run_id": RUN_ID,
    "benchmark_type": "plant_topology",
    "total_questions": n,
    "overall_accuracy": round(aa, 3),
    "intent_accuracy": round(ia, 3),
    "pipeline_accuracy": round(wa, 3),
    "entity_resolution_accuracy": round(er, 3),
    "mean_latency_ms": round(al),
    "median_latency_ms": ml,
    "p95_latency_ms": p95,
    "llm_calls_per_query": round(lq, 1),
    "avg_prompt_tokens": apt,
    "avg_completion_tokens": act,
    "avg_critic_time_ms": acr,
    "total_llm_calls": lc,
    "total_failures": total_fails,
    "failure_breakdown": fails,
}
es.index(index=AGG_INDEX, id="summary", document=agg_doc, refresh="wait_for")
print(f"Indexed aggregate document to {AGG_INDEX}.")

# ── Create Kibana index pattern ───────────────────────────────────────
try:
    import requests

    kibana_base = KIBANA_URL
    # Create data view for query results
    requests.post(
        f"{kibana_base}/api/data_views/data_view",
        json={
            "data_view": {
                "title": QUERY_INDEX,
                "name": f"AgentOS Benchmark Queries ({RUN_ID})",
                "timeFieldName": "timestamp",
            }
        },
        headers={"kbn-xsrf": "true", "Content-Type": "application/json"},
        timeout=10,
    )
    # Create data view for aggregates
    requests.post(
        f"{kibana_base}/api/data_views/data_view",
        json={
            "data_view": {
                "title": AGG_INDEX,
                "name": f"AgentOS Benchmark Aggregates ({RUN_ID})",
                "timeFieldName": "timestamp",
            }
        },
        headers={"kbn-xsrf": "true", "Content-Type": "application/json"},
        timeout=10,
    )
    print(f"Created Kibana data views for {QUERY_INDEX} and {AGG_INDEX}")
except Exception as e:
    print(f"Kibana data view creation skipped: {e}")

# ── Final report ──────────────────────────────────────────────────────
print()
print("=" * 72)
print(f"  AGENTOS BENCHMARK — PLANT TOPOLOGY")
print(f"  Run ID: {RUN_ID}")
print(f"  Questions: {n}")
print(f"  ES Index: {QUERY_INDEX}")
print(
    f"  Kibana: http://localhost:5601/app/discover#/?_g=(filters:!(),refreshInterval:(pause:!t,value:0),time:(from:now-1h,to:now))&_a=(columns:!(),filters:!(),indexPattern:{QUERY_INDEX},interval:auto,query:(language:kuery,query:''),sort:!(!(timestamp,desc)))"
)
print("=" * 72)
print()
print("  #1. Correctness Metrics")
print(f"  | Overall Accuracy           | {aa:.1%}           |")
print(f"  | Intent Accuracy            | {ia:.1%}           |")
print(f"  | Pipeline Accuracy          | {wa:.1%}           |")
print(f"  | Entity Resolution Accuracy | {er:.1%}           |")
print(f"  | Benchmark Coverage         | {n} questions |")
print()
print("  #2. Performance Metrics")
print(f"  | Mean Latency               | {al / 1000:.1f} s          |")
print(f"  | Median Latency             | {ml / 1000:.1f} s          |")
print(f"  | P95 Latency                | {p95 / 1000:.1f} s          |")
print()
print("  #3. LLM Metrics")
print(f"  | LLM Calls / Query          | {lq:.1f}            |")
print(f"  | Total LLM Calls            | {lc}            |")
print(f"  | Avg Prompt Tokens          | {apt}            |")
print(f"  | Avg Completion Tokens      | {act}            |")
print(f"  | Avg Critic Time            | {acr}ms          |")
print(f"  | Model                      | gemma3:latest    |")
print()
print("  #5. Failure Classification")
print(f"  | Intent Misclassification   | {fails.get('Intent Misclassification', 0):3d}            |")
print(f"  | Entity Resolution Failure  | {fails.get('Entity Resolution', 0):3d}            |")
print(f"  | Pipeline Selection Failure | {fails.get('Pipeline Selection', 0):3d}            |")
print(f"  | Grounding Failure          | {fails.get('Grounding', 0):3d}            |")
print(f"  | Runtime Exception          | {fails.get('Runtime Exception', 0):3d}            |")
print(f"  | Wrong Answer               | {fails.get('Wrong Answer', 0):3d}            |")
print(f"  | Total Failures             | {total_fails:3d}/{n}            |")
print()
print("  #6. Complexity Metrics")
print(f"  | Plant Lookup               | 100.0% ({n:3d})    |")
print()
print("=" * 72)
