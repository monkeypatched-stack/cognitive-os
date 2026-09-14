"""self_healing_pipeline — autonomous runtime repair via the ETASS Self-Healing Directive.

The prompt reaching this handler is the composition built by prompt.py:
    original_question + healing_prompt (from chart) + Runtime Errors Observed

Claude reads that prompt, plans the repair steps, and outputs FILE: blocks.
This handler parses those blocks and writes the fixes to src/.

No repair logic is hardcoded here — everything is driven by the chart's
healing_prompt and Claude's reasoning.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

logger = logging.getLogger("agentos.self_healing")

_REPO = Path("/Users/prashunjaveri/Code/monkeypatched")
_CHART = _REPO / "somatic/charts/cerebellum/capabilities/self_healing/values.yaml"


# Ensure ANTHROPIC_API_KEY is loaded from .env if not already in environment
def _load_env() -> None:
    env_file = _REPO / ".env"
    if not env_file.exists():
        return
    import os

    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


_load_env()

# Sentinel injected into the composed prompt so the predicate can detect it
HEALING_SENTINEL = "# Self-Healing Directive"


async def _llm_generate(system: str, prompt: str) -> str | None:
    """Try Claude first, fall back to Ollama."""
    import os, httpx

    # --- Claude ---
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        try:
            import anthropic

            msg = anthropic.Anthropic(api_key=api_key).messages.create(
                model="claude-sonnet-4-6",
                max_tokens=16384,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            logger.info("[self_healing] Using Claude")
            return msg.content[0].text
        except Exception as e:
            logger.warning("[self_healing] Claude failed, trying Ollama: %s", e)

    # --- Ollama ---
    ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_model = os.environ.get("OLLAMA_MODEL", "gemma3:latest")
    try:
        async with httpx.AsyncClient(timeout=300) as hc:
            r = await hc.post(
                f"{ollama_url}/api/chat",
                json={
                    "model": ollama_model,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                },
            )
            r.raise_for_status()
            logger.info("[self_healing] Using Ollama (%s)", ollama_model)
            return r.json()["message"]["content"]
    except Exception as e:
        logger.error("[self_healing] Ollama failed: %s", e)

    return None


def _load_chart() -> dict:
    try:
        import yaml

        return yaml.safe_load(_CHART.read_text()) or {}
    except Exception as e:
        logger.warning("[self_healing] Cannot load chart: %s", e)
        return {}


async def self_healing_pipeline_question_answer(client, question: str, force: bool = False):
    """Receive the composed healing prompt, call LLM, parse FILE: blocks, apply fixes."""
    chart = _load_chart()
    cap = chart.get("capability", {})
    logger.info("[self_healing] Starting — %s v%s", cap.get("name"), cap.get("version"))

    # Initialize evidence collector
    from src.operational_evidence import EvidenceCollector, WorkloadInfo, GoalInfo

    evidence_collector = EvidenceCollector(
        workload=WorkloadInfo(
            name="self_healing",
            version=cap.get("version", "1.0.0"),
            module="monkey_brain",
            capability="self_healing",
        ),
        goal=GoalInfo(
            description="Repair runtime failures",
            success_criteria="All runtime errors resolved",
        ),
    )

    # Track execution metrics
    evidence_collector.increment_requests()

    src_context = _build_source_context(question)
    system = (
        "You are the ETASS Self-Healing Runtime. "
        "You receive a composed prompt that includes runtime errors and a healing directive. "
        "Output ONLY FILE: blocks — no explanations, no markdown outside the code fences."
    )
    full_prompt = question
    if src_context:
        full_prompt += f"\n\n# Current Source Files\n\n{src_context}"

    # Track LLM call
    evidence_collector.increment_llm_calls()

    response_text = await _llm_generate(system, full_prompt)
    if response_text is None:
        evidence_collector.add_error("llm_unavailable", "No LLM available for self-healing")
        evidence_collector.complete_execution("FAILED", 0.0)
        return ("Self-healing: no LLM available.", [], [], False)

    files_written = _apply_file_blocks(response_text)

    # Update evidence with results
    if files_written:
        answer = f"Self-healing applied {len(files_written)} fix(es): " + ", ".join(files_written)
        evidence_collector.record_generation_result(success=True)
        evidence_collector.add_code_changes(lines_changed=len(files_written))
        evidence_collector.set_status("SUCCESS", 0.95)
    else:
        answer = "Self-healing: no FILE: blocks found in LLM response — no changes applied."
        evidence_collector.record_generation_result(success=False)
        evidence_collector.set_status("NO_CHANGES", 0.8)

    # Complete evidence collection
    evidence_collector.complete_execution()

    # Store evidence for aggregation
    _store_evidence(evidence_collector.get_evidence())

    logger.info("[self_healing] Done. %s", answer)
    return (answer, [], [], bool(files_written))


def _build_source_context(question: str) -> str:
    """Read current content of any src/ files mentioned in the errors section."""
    lines = []
    # Extract paths like src/foo/bar.py from the errors section
    for match in re.finditer(r"src/[\w/]+\.py", question):
        path = _REPO / match.group(0)
        if path.exists() and str(path) not in [l[: len(str(path))] for l in lines]:
            try:
                code = path.read_text()
                lines.append(f"FILE: {match.group(0)}\n```python\n{code[:4000]}\n```")
            except Exception:
                logger.debug("_build_source_context: suppressed exception", exc_info=True)
    return "\n\n".join(lines)


def _apply_file_blocks(response: str) -> list[str]:
    """Parse FILE: blocks from Claude's response and write them to src/ with validation and backup."""
    written: list[str] = []
    backup_dir = _REPO / ".self_healing_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    # Match: FILE: <path>\n```python\n<code>\n```
    pattern = re.compile(
        r"FILE:\s*(?P<path>src/[\w/.-]+\.py)\s*\n```python\s*\n(?P<code>.*?)```",
        re.DOTALL,
    )

    for m in pattern.finditer(response):
        rel_path = m.group("path").strip()
        code = m.group("code").rstrip()

        # Constitution HEAL-INV-004: src/ only
        if not rel_path.startswith("src/"):
            logger.warning("[self_healing] Skipping %s — not under src/", rel_path)
            continue

        # Constitution HEAL-INV-003: never touch somatic/ or hand-written critical files
        target = _REPO / rel_path
        if "somatic" in rel_path or "charts" in rel_path:
            logger.warning("[self_healing] Skipping %s — chart/spec file", rel_path)
            continue
        _PROTECTED = {"src/cortex/world_model_simulation.py"}
        if rel_path in _PROTECTED:
            logger.warning("[self_healing] Skipping %s — protected hand-written file", rel_path)
            continue

        try:
            # Create backup before modifying
            if target.exists():
                backup_path = backup_dir / f"{rel_path.replace('/', '_')}_{int(time.time())}.backup"
                target.copy(backup_path)
                logger.info("[self_healing] 💾 Created backup: %s", backup_path)

            # Validate Python syntax before writing
            try:
                compile(code, rel_path, "exec")
            except SyntaxError as e:
                logger.error(
                    "[self_healing] ❌ Syntax error in generated code for %s: %s",
                    rel_path,
                    e,
                )
                continue

            # Write the fixed code
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(code + "\n")
            logger.info("[self_healing] ✅ Fixed %s", rel_path)
            written.append(rel_path)
        except Exception as e:
            logger.warning("[self_healing] Failed to write %s: %s", rel_path, e)

    return written


def _store_evidence(evidence_package: EvidencePackage) -> None:
    """Store evidence package for later aggregation and analysis."""
    try:
        import json

        evidence_dir = _REPO / ".operational_evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)

        evidence_file = evidence_dir / f"{evidence_package.execution_id}.json"
        evidence_file.write_text(json.dumps(evidence_package.to_dict(), indent=2))

        logger.info("[self_healing] 📊 Stored evidence: %s", evidence_file)

    except Exception as e:
        logger.warning("[self_healing] Failed to store evidence: %s", e)


def is_self_healing_question(question: str) -> bool:
    """Detect the healing sentinel injected by prompt.py."""
    return HEALING_SENTINEL in question
