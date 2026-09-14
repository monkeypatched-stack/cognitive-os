"""sittingface_workload — routes ETASS questions through the dynamic agent workload.

Flow:
  LLMExplorer generates steps (calls Claude with Broca agent catalog)
  → Runtime executes each step
  → Steps discover agents from Broca registry
  → Agents discover capabilities from Cerebellum registry
  → Agents emit Bellman feedback after each step

Build-service flow (triggered by "build a X service"):
  PlannerAgent generates 13-step YAML plan
  → plan saved to ~/.monkeybrain/plans/
  → ExecutorAgent runs all steps with auto-heal
"""

from __future__ import annotations

import difflib
import logging
import os
import re
import sys
from pathlib import Path

logger = logging.getLogger("agentos.sittingface_workload")

_REPO = Path("/Users/prashunjaveri/Code/monkeypatched")
_GEN_DIR = Path("/Users/prashunjaveri/Code/generated/monkeypatched")
_SRC_DIR = _REPO / "src"

# Set SITTINGFACE_DISABLED=1 to skip the codegen workload entirely.
# This prevents sittingface from overwriting source files on every request.
_SITTINGFACE_DISABLED = os.environ.get("SITTINGFACE_DISABLED", "0") == "1"

_BUILD_PATTERNS = re.compile(
    r"(?:build|create|generate|make|run(?: the)? (?:full )?workload for)\s+(?:a\s+|an\s+)?([a-z][a-z0-9 _-]+?)\s*(?:service|api|microservice)?$",
    re.IGNORECASE,
)


def _extract_service_slug(question: str) -> str | None:
    """Return a service slug from a build-intent question, or None."""
    m = _BUILD_PATTERNS.search(question.strip())
    if not m:
        return None
    raw = m.group(1).strip().lower()
    return raw.replace(" ", "-").replace("_", "-")


def _ensure_broca():
    for p in ("packages/broca", "packages/cerebellum", "packages/soma-cli"):
        full = str(_REPO / p)
        if full not in sys.path:
            sys.path.insert(0, full)


async def _run_etass_workload(question: str, service_slug: str) -> tuple[str, list, list, bool]:
    """Plan + execute the full 13-step ETASS workload for a service."""
    _ensure_broca()
    from broca.agents.planner import PlannerAgent
    from broca.agents.executor import ExecutorAgent
    import yaml

    def _payload(result) -> dict:
        if hasattr(result, "payload"):
            return result.payload or {}
        if isinstance(result, dict):
            return result.get("payload", result)
        return {}

    # 1. Generate plan
    planner = PlannerAgent()
    plan_result = await planner.handle({"goal": question, "service": service_slug})
    plan = _payload(plan_result).get("plan")
    if not plan or not plan.get("steps"):
        return (f"Planner produced no steps for '{service_slug}'.", [], [], False)

    # 2. Save plan to disk
    plans_dir = Path.home() / ".monkeybrain" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    import time

    ts = time.strftime("%Y%m%d_%H%M%S")
    plan_path = plans_dir / f"{service_slug}_{ts}.yaml"
    plan_path.write_text(yaml.dump(plan, default_flow_style=False, allow_unicode=True))
    logger.info(
        "[sittingface] plan saved → %s (%d steps)",
        plan_path,
        len(plan.get("steps", [])),
    )

    # 3. Execute plan
    executor = ExecutorAgent()
    exec_result = await executor.handle(
        {
            "plan_file": str(plan_path),
            "auto_fix": True,
            "continue_on_error": True,
        }
    )
    ep = _payload(exec_result)
    steps = ep.get("steps", [])
    steps_ok = sum(1 for s in steps if s.get("status") == "ok")
    steps_total = len(steps) or len(plan.get("steps", []))
    success = ep.get("all_ok", False)

    answer = (
        f"ETASS workload {'COMPLETE' if success else 'PARTIAL'}: "
        f"{steps_ok}/{steps_total} steps passed for '{service_slug}'. "
        f"Plan: {plan_path.name}"
    )
    return (answer, [], [], success)


def _compute_diff(src_dir: Path, gen_dir: Path) -> tuple[int, int, int]:
    src_files: dict[str, str] = {}
    gen_files: dict[str, str] = {}
    if src_dir.exists():
        for f in src_dir.rglob("*.py"):
            if f.name != "__init__.py":
                src_files[str(f.relative_to(src_dir))] = f.read_text()
    if gen_dir.exists():
        for f in gen_dir.rglob("*.py"):
            if f.name != "__init__.py":
                gen_files[str(f.relative_to(gen_dir))] = f.read_text()
    both = set(src_files) & set(gen_files)
    match = sum(
        1
        for f in both
        if difflib.SequenceMatcher(None, src_files[f].splitlines(), gen_files[f].splitlines()).ratio() > 0.9
    )
    diff_count = len(both) - match
    only_src = len(set(src_files) - set(gen_files))
    return len(src_files), len(gen_files), diff_count + only_src


async def sittingface_workload_question_answer(client, question, force=False):
    """Run the ETASS dynamic agent workload.

    If the question is a build-service intent ("build a work-order service"),
    delegates to PlannerAgent + ExecutorAgent for the full 13-step workload.
    Otherwise falls back to LLMExplorer-based dynamic workload.
    """
    service_slug = _extract_service_slug(question)
    if service_slug:
        logger.info("[sittingface] build-service intent detected → slug=%r", service_slug)
        return await _run_etass_workload(question, service_slug)

    # Disabled: sittingface codegen overwrites source files on every request
    if _SITTINGFACE_DISABLED:
        return (
            "SittingFace workload is disabled (SITTINGFACE_DISABLED=1). Set SITTINGFACE_DISABLED=0 to re-enable.",
            [],
            [],
            False,
        )

    # Early-exit: already stable
    if _GEN_DIR.exists():
        _src_n, _gen_n, _pre_diff = _compute_diff(_SRC_DIR, _GEN_DIR)
        if _pre_diff == 0 and _gen_n > 0:
            logger.info("[sittingface] stable (%d files match) — skipping", _src_n)
            return (
                f"Code is stable. {_src_n} files match generated/ exactly — no workload run needed.",
                [],
                [],
                False,
            )

    try:
        from src.monkey_brain.api.main import app

        cognitive_runtime = getattr(getattr(app, "state", None), "cognitive_runtime", None)
        policy = getattr(getattr(app, "state", None), "policy", None)
        lemon = getattr(getattr(app, "state", None), "lemon", None)

        from src.monkey_brain.kernel.execute.provider.llm_explorer import LLMExplorer
        from src.monkey_brain.kernel.execute.runtime.state import ExecutionState

        explorer = LLMExplorer()
        if lemon:
            explorer.set_lemon(lemon)

        # Step 1: LLMExplorer generates candidate workloads (agent-based steps)
        context = {
            "question": question,
            "goal": "Run the ETASS cognitive OS workload",
            "chart_name": "monkeypatched",
            "chart_version": "0.1.0",
        }
        candidates = explorer.generate_candidates(question, "sittingface_workload", context)

        # Step 2: BellmanPolicy selects the best candidate
        state = ExecutionState(question=question)
        workloads = [explorer.candidate_to_workload(c) for c in candidates]
        selected = workloads[0]
        if policy and workloads:
            try:
                selected = policy.select(workloads, state) or workloads[0]
            except Exception:
                selected = workloads[0]

        # Step 3: Build IntentIR and ExecutionContext for intent-based execution
        if cognitive_runtime is None:
            raise RuntimeError("CognitiveRuntime not initialised")

        from src.monkey_brain.kernel.plan.goals.intent_ir import build_intent_ir
        from src.monkey_brain.kernel.execute.context import ExecutionContext
        from src.monkey_brain.kernel.execute.models import ExecutionMode
        from uuid import uuid4

        run_id = f"sittingface-{uuid4().hex[:8]}"
        intent_ir = build_intent_ir(
            intent={"intent": "sittingface_workload", "confidence": 1.0},
            goal={
                "name": "Run the ETASS cognitive OS workload",
                "goal_type": "execute",
            },
            run_id=run_id,
            question=question,
            group="sittingface",
        )
        exec_context = ExecutionContext.create(
            run_id=run_id,
            execution_mode=ExecutionMode.EXECUTE,
            intent_ir=intent_ir,
            user_id="sittingface",
        )

        # Step 4: Convert workload steps to plan_steps format
        plan_steps = []
        for step in selected.steps:
            plan_steps.append(
                {
                    "step_id": step.step_id,
                    "capability_name": step.capability_name,
                    "agent_name": step.agent_name,
                    "dependencies": step.dependencies,
                    "metadata": step.metadata,
                }
            )

        # Step 5: Execute via intent path (GoalExecutor.execute_context)
        answer, semantic_hits, graph_paths, llm_answered = await cognitive_runtime.execute_cognitive_workload(
            exec_context,
            client,
            plan_steps=plan_steps,
        )

        # Step 6: Bellman policy update
        if policy:
            try:
                from src.monkey_brain.kernel.fix.policy.transition import Transition

                reward = 1.0 if llm_answered else 0.2
                policy.update(
                    Transition(
                        state=state.to_dict(),
                        action=selected.workload_id,
                        reward=reward,
                        next_state={"answer": answer, "llm_answered": llm_answered},
                        done=True,
                    )
                )
            except Exception as e:
                logger.debug("[sittingface] policy update failed: %s", e)

        return (answer, semantic_hits, graph_paths, llm_answered)

    except Exception as e:
        logger.error("[sittingface] workload failed: %s", e)
        return (f"ETASS workload error: {e}", [], [], False)


def is_sittingface_workload_question(question):
    """Check if question is about the SittingFace / ETASS workload."""
    q = question.lower()
    if any(
        w in q
        for w in [
            "sitting face",
            "sittingface",
            "somatic workload",
            "run the workload",
            "execute workload",
            "soma workload",
            "full loop",
            "generate code from charts",
            "etass",
            "load specification",
            "soma charts",
            "compile specification",
            "cingulate",
            "coding runtime",
            "chart evolution",
            "specification-driven",
            "engineering lifecycle",
            "governance review",
            "load the soma",
            "run the full workload",
            "full workload",
        ]
    ):
        return True
    # Also match build-service intents
    return _extract_service_slug(question) is not None
