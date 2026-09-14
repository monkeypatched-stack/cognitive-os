"""sittingface_pipeline — routes ETASS questions through the dynamic agent pipeline.

Flow:
  LLMExplorer generates steps (calls Claude with Broca agent catalog)
  → Runtime executes each step
  → Steps discover agents from Broca registry
  → Agents discover capabilities from Cerebellum registry
  → Agents emit Bellman feedback after each step
"""

from __future__ import annotations

import difflib
import logging
import os
from pathlib import Path

logger = logging.getLogger("agentos.sittingface_pipeline")

_REPO = Path("/Users/prashunjaveri/Code/monkeypatched")
_GEN_DIR = Path("/Users/prashunjaveri/Code/generated/monkeypatched")
_SRC_DIR = _REPO / "src"

# Set SITTINGFACE_DISABLED=1 to skip the codegen pipeline entirely.
# This prevents sittingface from overwriting source files on every request.
_SITTINGFACE_DISABLED = os.environ.get("SITTINGFACE_DISABLED", "0") == "1"


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


async def sittingface_pipeline_question_answer(client, question, force=False):
    """Run the ETASS dynamic agent pipeline.

    1. LLMExplorer generates candidate agent-step plans (calls Claude with Broca catalog)
    2. BellmanPolicy selects the best candidate
    3. Runtime executes each step — steps discover agents, agents discover capabilities
    4. Agents emit feedback; Bellman updates Q-values for next iteration
    """

    # Disabled: sittingface codegen overwrites source files on every request
    if _SITTINGFACE_DISABLED:
        return (
            "SittingFace pipeline is disabled (SITTINGFACE_DISABLED=1). Set SITTINGFACE_DISABLED=0 to re-enable.",
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
                f"Code is stable. {_src_n} files match generated/ exactly — no pipeline run needed.",
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

        # Step 1: LLMExplorer generates candidate pipelines (agent-based steps)
        context = {
            "question": question,
            "goal": "Run the ETASS cognitive OS pipeline",
            "chart_name": "monkeypatched",
            "chart_version": "0.1.0",
        }
        candidates = explorer.generate_candidates(question, "sittingface_pipeline", context)

        # Step 2: BellmanPolicy selects the best candidate
        state = ExecutionState(question=question)
        pipelines = [explorer.candidate_to_workload(c) for c in candidates]
        selected = pipelines[0]
        if policy and pipelines:
            try:
                selected = policy.select(pipelines, state) or pipelines[0]
            except Exception:
                selected = pipelines[0]

        # Step 3: Build IntentIR and ExecutionContext for intent-based execution
        if cognitive_runtime is None:
            raise RuntimeError("CognitiveRuntime not initialised")

        from src.monkey_brain.kernel.plan.goals.intent_ir import build_intent_ir
        from src.monkey_brain.kernel.execute.context import ExecutionContext
        from src.monkey_brain.kernel.execute.models import ExecutionMode
        from uuid import uuid4

        run_id = f"sittingface-{uuid4().hex[:8]}"
        intent_ir = build_intent_ir(
            intent={"intent": "sittingface_pipeline", "confidence": 1.0},
            goal={
                "name": "Run the ETASS cognitive OS pipeline",
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
                        action=selected.pipeline_id,
                        reward=reward,
                        next_state={"answer": answer, "llm_answered": llm_answered},
                        done=True,
                    )
                )
            except Exception as e:
                logger.debug("[sittingface] policy update failed: %s", e)

        return (answer, semantic_hits, graph_paths, llm_answered)

    except Exception as e:
        logger.error("[sittingface] pipeline failed: %s", e)
        return (f"ETASS pipeline error: {e}", [], [], False)


def is_sittingface_pipeline_question(question):
    """Check if question is about the SittingFace / ETASS pipeline."""
    q = question.lower()
    return any(
        w in q
        for w in [
            "sitting face",
            "sittingface",
            "somatic pipeline",
            "run the pipeline",
            "execute pipeline",
            "soma pipeline",
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
        ]
    )
