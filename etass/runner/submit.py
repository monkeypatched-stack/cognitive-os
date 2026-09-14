"""Submit the ETASS prompt to MonkeyBrain and return the response.

MonkeyBrain classifies the intent, selects a pipeline, and orchestrates execution.
ETASS does not manage the pipeline — it submits the prompt and waits.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from typing import Any

import httpx

from .prompt import ETASS_PROMPT

logger = logging.getLogger("etass.runner")


async def submit_to_monkeybrain(
    base_url: str = "http://localhost:8031",
    run_query: bool = True,
    run_simulate: bool = True,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """POST the ETASS v1.0 prompt to MonkeyBrain /prompt and return the full response."""
    url = f"{base_url}/api/v1/agentos/prompt"

    logger.info("Submitting ETASS v1.0 prompt to MonkeyBrain: %s", url)
    logger.info("Prompt length: %d chars", len(ETASS_PROMPT))

    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            url,
            json={
                "question": ETASS_PROMPT,
                "run_query": run_query,
                "run_simulate": run_simulate,
            },
            headers={"Content-Type": "application/json"},
        )
        elapsed = (time.monotonic() - t0) * 1000
        resp.raise_for_status()
        body = resp.json()

    logger.info("MonkeyBrain responded in %.0fms  HTTP %s", elapsed, resp.status_code)
    return {
        "http_status": resp.status_code,
        "elapsed_ms": elapsed,
        "response": body,
    }


def print_response(result: dict[str, Any]) -> None:
    """Print a human-readable summary of the MonkeyBrain response."""
    resp = result.get("response", {})
    query = resp.get("query_result") or {}
    sim = resp.get("simulation_result") or {}
    summ = resp.get("execution_summary") or {}

    print()
    print("=" * 72)
    print("  ETASS v1.0 — MonkeyBrain Pipeline Execution")
    print("=" * 72)
    print(f"  HTTP status   : {result['http_status']}")
    print(f"  Elapsed       : {result['elapsed_ms']:.0f}ms")
    print(f"  MB exec time  : {summ.get('total_execution_time_ms', '?')}ms")
    print(f"  Endpoints hit : {summ.get('endpoints_called', [])}")
    print(f"  Errors        : {summ.get('error_count', 0)}")
    print()

    if isinstance(query, dict):
        answer = query.get("answer", "")
        print("  Query answer:")
        for line in answer.splitlines():
            print(f"    {line}")
        print(f"  LLM answered  : {query.get('llm_answered', False)}")
        print(f"  Semantic hits : {len(query.get('semantic_hits', []))}")
    print()

    if sim:
        predicted = sim.get("predicted_state", {}) if isinstance(sim, dict) else {}
        collections = predicted.get("collections", {}) if isinstance(predicted, dict) else {}
        if collections:
            print("  Simulation — predicted state (collection counts):")
            for coll, info in list(collections.items())[:8]:
                cnt = info.get("count", "?") if isinstance(info, dict) else info
                print(f"    {coll:<45} {cnt}")
    print("=" * 72)
    print()
