"""ETASS CLI — submit the ETASS v1.0 prompt to MonkeyBrain, or run the full pipeline.

Usage:
    python -m etass                              # submit v1.0 prompt to localhost:8031
    python -m etass --pipeline                  # run full pipeline (Compiler→DAG→4 outputs)
    python -m etass --pipeline --workload self_healing --goal "fix flaky tests"
    python -m etass --url http://host:8031
    python -m etass --json                       # raw JSON output
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("etass")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="etass",
        description="ETASS — orchestration CLI",
    )
    p.add_argument(
        "--url",
        default="http://localhost:8031",
        help="MonkeyBrain base URL (default: http://localhost:8031)",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Request timeout seconds (default: 120)",
    )
    p.add_argument(
        "--no-simulate",
        action="store_true",
        help="Skip the simulation leg (v1.0 mode only)",
    )
    p.add_argument("--json", action="store_true", help="Print raw JSON response")
    # Pipeline mode
    p.add_argument(
        "--pipeline",
        action="store_true",
        help="Run full ETASS pipeline (Compiler→Planner→DAG→4 adapters)",
    )
    p.add_argument(
        "--workload",
        default="etass",
        help="Workload type for pipeline mode (default: etass)",
    )
    p.add_argument("--goal", default="", help="Goal string for pipeline mode")
    p.add_argument(
        "--domain",
        default="software_engineering",
        help="DDD domain for pipeline mode (default: software_engineering)",
    )
    p.add_argument(
        "--context",
        default="",
        dest="bounded_context",
        help="DDD bounded context for pipeline mode",
    )
    return p


async def run_pipeline(args: argparse.Namespace) -> int:
    from etass.specification import ETASSSpec
    from etass.pipeline.orchestrator import ETASSPipelineOrchestrator

    goal = args.goal or f"Execute {args.workload} workload via ETASS pipeline"
    spec = ETASSSpec(
        workload=args.workload,
        goal=goal,
        domain=args.domain,
        bounded_context=args.bounded_context,
    )

    orch = ETASSPipelineOrchestrator(monkeybrain_url=args.url)
    result = await orch.run(spec)

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, default=str))
        return 0 if result.ok() else 1

    _print_pipeline_result(result)
    return 0 if result.ok() else 1


def _print_pipeline_result(result) -> None:
    w = 72
    print()
    print("=" * w)
    print("  ETASS Pipeline Execution")
    print("=" * w)
    print(f"  Workload  : {result.spec_workload}")
    print(f"  Goal      : {result.spec_goal[:60]}")
    print(f"  DAG       : {result.dag_id}  ({result.node_count} nodes)")
    print(f"  Elapsed   : {result.elapsed_ms:.0f}ms")
    print()
    print("  Adapter Results:")
    for a in result.adapters:
        icon = "✓" if a.status == "ok" else "✗" if a.status == "error" else "–"
        print(
            f"    {icon} {a.adapter:<18} {a.status:<12} {a.elapsed_ms:.0f}ms"
            + (f"  [{a.error[:60]}]" if a.error else "")
        )
    if result.mermaid:
        print()
        print("  Mermaid Diagram:")
        for line in result.mermaid.splitlines():
            print(f"    {line}")
    print("=" * w)
    print()


async def run(args: argparse.Namespace) -> int:
    from etass.runner.submit import submit_to_monkeybrain, print_response
    from etass.runner.prompt import ETASS_PROMPT

    logger.info("ETASS v1.0 — submitting to MonkeyBrain at %s", args.url)
    logger.info("Prompt: %d chars across 15 phases", len(ETASS_PROMPT))

    try:
        result = await submit_to_monkeybrain(
            base_url=args.url,
            run_query=True,
            run_simulate=not args.no_simulate,
            timeout=args.timeout,
        )
    except Exception as e:
        logger.error("Failed to reach MonkeyBrain: %s", e)
        logger.error("Is the server running?  Start with:")
        logger.error("  .venv/bin/python -m uvicorn src.monkey_brain.api.main:app --port 8031")
        return 1

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print_response(result)

    resp = result.get("response", {})
    query_ok = isinstance(resp.get("query_result"), dict)
    return 0 if query_ok else 1


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.pipeline:
        sys.exit(asyncio.run(run_pipeline(args)))
    else:
        sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
