#!/usr/bin/env python3
"""Scan repository for suspicious inline `.execute(...)` awaits.

This script reports places where code calls `await X.execute(...)` which may
indicate inline pipeline/operator execution bypassing the centralized
PipelineRuntime/Engine lifecycle. It excludes known safe files such as the
engine/runtime/thread helpers and common library calls (redis pipeline).

Usage: python3 scripts/find_inline_pipeline_execs.py
"""

import re
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SAFE_PATHS = {
    "services/agentos/pipeline/pipeline_engine.py",
    "services/agentos/pipeline/pipeline_runtime.py",
    "services/agentos/pipeline/pipeline_threads.py",
    "services/agentos/pipeline/entity_pipeline.py",
    "services/agentos/pipeline/entity_adapter.py",
}

REDIS_PIPELINE_SIGNS = ["client.pipeline()", "pipe.execute("]

pattern = re.compile(r"await\s+([\w\.]+)\.execute\s*\(")

# Common variable names that indicate this is not an operator/pipeline invocation
# but a DB/driver/adapter internal call or agent/capability invocation that is
# expected. Lowercase comparison is used.
SKIP_VAR_NAMES = {
    "pipe",
    "client",
    "adapter",
    "agent",
    "db",
    "collection",
    "blk",
    "cursor",
    "conn",
    "callback",
    "checkpoint",
    "step",
    "_adapter",
    "_pipeline",
    "_cursor",
    "self",
    "runtime",
    "default_runtime",
    "orchestrator",
    "capability",
    "entry",
}


def is_safe_path(relpath: str) -> bool:
    if relpath.replace("\\", "/") in SAFE_PATHS:
        return True
    # Allow tests and examples to use execute for adapters and agents
    if relpath.startswith("sdk/") or relpath.startswith("tests/") or relpath.startswith("scripts/"):
        return True
    return False


def file_contains_redis_pipe(text: str) -> bool:
    return any(s in text for s in REDIS_PIPELINE_SIGNS)


def scan() -> list[dict]:
    results = []
    for p in ROOT.rglob("*.py"):
        try:
            rel = str(p.relative_to(ROOT))
        except Exception:
            rel = str(p)
        if is_safe_path(rel):
            continue
        try:
            text = p.read_text()
        except Exception:
            continue

        if file_contains_redis_pipe(text):
            # likely using redis pipeline, skip
            continue

        for m in pattern.finditer(text):
            var = m.group(1)
            lower = var.split(".")[-1].lower()

            # Skip common safe names (drivers, redis pipelines, internal coroutines,
            # agent/capability execution points, etc.) to reduce noise.
            if lower in SKIP_VAR_NAMES:
                continue

            results.append(
                {
                    "file": rel,
                    "line": text.count("\n", 0, m.start()) + 1,
                    "match": m.group(0).strip(),
                    "var": var,
                    "likely_safe": False,
                }
            )

    return results


def main():
    hits = scan()
    if not hits:
        print("No suspicious inline await .execute(...) found (after exclusions).")
        return

    print(f"Found {len(hits)} suspicious await .execute(...) occurrences:\n")
    for h in hits:
        print(f"{h['file']}:{h['line']}: {h['match']}  var={h['var']} likely_safe={h['likely_safe']}")


if __name__ == "__main__":
    main()
