"""healing.py — Self-healing phase runners and post-workload orchestrator."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any

from src.monkey_brain.kernel.execute.runtime.executor import get_executor
from src.monkey_brain.kernel.execute.models import ExecutionMode
from src.monkey_brain.kernel.models import (
    HealingResult,
    StabilityResult,
    WorkloadOutcome,
)
from src.monkey_brain.api.helpers.stability_helpers import check_stability

logger = logging.getLogger("agentos.healing")

# ---------------------------------------------------------------------------
# Config — env var overrides allow per-deployment tuning without code changes
# ---------------------------------------------------------------------------

_MAX_HEALING_ATTEMPTS: int = int(os.getenv("HEALING_MAX_ATTEMPTS", "3"))
_HEALING_COOLDOWN_S: int = int(os.getenv("HEALING_COOLDOWN_S", "300"))
_last_workload_time: float = 0.0

_REPO_ROOT = Path(os.getenv("MONKEYPATCHED_ROOT", str(Path(__file__).resolve().parents[5])))
_HEALING_CHART = _REPO_ROOT / "somatic/charts/cerebellum/capabilities/self_healing/values.yaml"


def reset_cooldown() -> None:
    """Reset the workload cooldown timer (call after manual SPEC REVIEW intervention)."""
    global _last_workload_time
    _last_workload_time = 0.0


# ---------------------------------------------------------------------------
# Healing helpers
# ---------------------------------------------------------------------------


def _load_healing_prompt() -> str:
    try:
        import yaml

        values = yaml.safe_load(_HEALING_CHART.read_text()) or {}
        return values.get("healing_prompt", "").strip()
    except Exception as e:
        logger.warning("[healing] Cannot load chart: %s", e)
        return ""


def _compose_healing_prompt(original: str, healing_prompt: str, error_lines: list[str]) -> str:
    errors_block = "\n".join(f"  - {line}" for line in error_lines)
    return (f"{original}\n\n---\n\n{healing_prompt}\n\nRuntime Errors Observed:\n{errors_block}")[:10_000]


# ---------------------------------------------------------------------------
# Phase runners
# ---------------------------------------------------------------------------


async def _run_healing_phase(
    question: str,
    error_lines: list[str],
    mongo_client: Any,
    max_attempts: int = _MAX_HEALING_ATTEMPTS,
) -> HealingResult:
    """Iterative healing loop — applies fixes only, does NOT evaluate stability.

    Success = the self_healing handler wrote at least one FILE: block to src/
              (executor returns llm_answered=True when files are written).
    Failure = no pass wrote files within max_attempts, or an exception occurred.

    Stability is NOT checked here. _run_stability_phase runs after this function
    regardless of outcome and is the sole arbiter of COMPLETE vs SPEC REVIEW.
    """
    if not error_lines:
        logger.info("[healing] No errors captured — skipping healing phase")
        return HealingResult(outcome="skipped", reason="no_errors")

    healing_prompt = _load_healing_prompt()
    if not healing_prompt:
        logger.warning("[healing] No healing_prompt in chart — skipping")
        return HealingResult(outcome="failure", reason="no_chart")

    composed = _compose_healing_prompt(question, healing_prompt, error_lines)
    passes = 0

    while passes < max_attempts:
        logger.info("[healing] Pass %d/%d", passes + 1, max_attempts)
        try:
            result = await get_executor().execute(composed, mongo_client, question_source=ExecutionMode.QUERY)
            passes += 1
            _, _, _, files_written = result
            if files_written:
                logger.info("[healing] SUCCESS — fixes applied on pass %d", passes)
                return HealingResult(outcome="success", passes=passes)
            logger.info("[healing] Pass %d: handler wrote no files — retrying", passes)
        except Exception as e:
            logger.warning("[healing] Pass %d error: %s", passes + 1, e)
            break

        await asyncio.sleep(2)

    logger.warning("[healing] FAILURE — %d pass(es), no fixes written", passes)
    return HealingResult(outcome="failure", passes=passes, reason="no_fixes_applied")


async def _run_stability_phase(
    error_lines: list[str],
    evidence_since: float,
) -> StabilityResult:
    """Single stability evaluation across all four conditions."""
    conditions = check_stability(error_lines=error_lines, evidence_since=evidence_since)
    outcome = "complete" if conditions["stable"] else "spec_review"
    logger.info(
        "[stability] %s — git_clean=%s no_errors=%s codegen_diff=%s no_new_evidence=%s",
        outcome.upper(),
        conditions["git_clean"],
        conditions["no_errors"],
        conditions["codegen_diff"],
        conditions["no_new_evidence"],
    )
    return StabilityResult(outcome=outcome, conditions=conditions)


# ---------------------------------------------------------------------------
# Post-workload orchestrator
# ---------------------------------------------------------------------------


async def run_post_workload(
    question: str,
    error_lines: list[str],
    mongo_client: Any,
    run_type: str,
    max_healing: int = _MAX_HEALING_ATTEMPTS,
) -> WorkloadOutcome:
    """
    FULL:
        Self-Healing (iterative)
               │
         ┌─────┴─────┐
         │           │
      Success     Failure
         │           │
         └─────┬─────┘
               ▼
         Stabilization
               │
         ┌─────┴─────┐
         │           │
      Success     Failure
         │           │
         ▼           ▼
      COMPLETE   SPEC REVIEW
    """
    global _last_workload_time

    outcome = WorkloadOutcome(run_type=run_type)
    t_start = time.time()

    # ── Healing phase ──────────────────────────────────────────────────────
    if run_type in ("full", "healing"):
        if time.time() - _last_workload_time < _HEALING_COOLDOWN_S and run_type == "full":
            logger.warning("[workload] Cooldown active — skipping healing")
            outcome.healing = HealingResult(outcome="skipped", reason="cooldown")
        else:
            outcome.healing = await _run_healing_phase(question, error_lines, mongo_client, max_attempts=max_healing)

    # ── Stabilization phase — always runs after healing ────────────────────
    if run_type in ("full", "stability"):
        remaining_errors = [] if outcome.healing and outcome.healing.outcome == "success" else error_lines
        outcome.stability = await _run_stability_phase(
            error_lines=remaining_errors,
            evidence_since=t_start,
        )

    # ── Final outcome log ──────────────────────────────────────────────────
    if outcome.stability:
        if outcome.stability.outcome == "complete":
            logger.info("[workload] COMPLETE — system stable")
        else:
            healing_status = outcome.healing.outcome if outcome.healing else "skipped"
            logger.error(
                "[workload] SPEC REVIEW — healing=%s, stability conditions=%s",
                healing_status,
                outcome.stability.conditions,
            )
            _last_workload_time = time.time()

    return outcome
