"""FixItAgent — reads governance issues and applies CodeGenAgent fixes to source files.

Accepts issues from a governance report (or inline list), locates the relevant
source files, and calls CodeGenAgent to produce and write fixes.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

from ._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.fixit_agent")

_REPO = Path(os.environ.get("MONKEYBRAIN_REPO", str(Path(__file__).parents[4])))


def _ensure_sittingface():
    src = _REPO / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


class FixItAgent(BaseETASSAgent):
    agent_type = "fixit"
    description = "Reads governance/compliance issues and applies CodeGenAgent fixes to source files"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict) -> dict:
        service_slug = str(context.get("service_slug", "")).strip()
        output_dir = Path(str(context.get("output_dir", _REPO / "generated")))
        report_path = str(context.get("report_path", ""))
        artifact_path = str(context.get("artifact_path", ""))
        issues: list[dict] = context.get("issues", [])

        if not service_slug:
            self._reward(False, 0.0)
            return self._result(payload={"fixed": False}, observations=["no service_slug"])

        out_base = output_dir if output_dir.is_absolute() else _REPO / output_dir

        # ── 1. Load issues ────────────────────────────────────────────────────
        if not issues:
            issues = self._load_issues(service_slug, report_path)
        if not issues:
            self._reward(True, 1.0)
            return self._result(
                payload={
                    "fixed": True,
                    "service_slug": service_slug,
                    "files": [],
                    "issues_found": 0,
                },
                observations=["no issues found — nothing to fix"],
            )

        # ── 2. Locate source files ────────────────────────────────────────────
        candidate_dirs = [
            out_base / f"{service_slug}-client",
            out_base / service_slug,
        ]
        if artifact_path:
            ap = Path(artifact_path)
            if ap.is_dir():
                candidate_dirs.insert(0, ap)
            elif ap.is_file():
                candidate_dirs.insert(0, ap.parent)

        source_files = self._collect_source(candidate_dirs)
        if not source_files:
            self._reward(False, 0.1)
            return self._result(
                payload={"fixed": False, "error": "no source files found"},
                observations=[f"searched: {[str(d) for d in candidate_dirs]}"],
            )

        # ── 3. Build fix prompt ───────────────────────────────────────────────
        issues_text = "\n".join(
            f"  [{iss.get('severity', 'ISSUE')}] {iss.get('description', '')}"
            + (f"\n    Remediation: {iss.get('remediation', '')}" if iss.get("remediation") else "")
            for iss in issues
        )
        files_text = "\n\n".join(
            f"=== FILE: {rel} ===\n{content[:3000]}\n=== END FILE ===" for rel, content in source_files.items()
        )

        system = (
            "You are a code remediation agent. Fix the compliance issues listed below.\n"
            "Rules:\n"
            "  1. Fix ONLY what the issues describe — do not refactor unrelated code.\n"
            "  2. For auth issues: inject credentials at __init__, not per-method.\n"
            "  3. For HTTP error issues: map status codes to typed domain exceptions.\n"
            "  4. Output ONLY the corrected files — no prose.\n\n"
            "Output format (exact):\n"
            "=== FILE: <same relative path as input> ===\n"
            "<corrected content>\n"
            "=== END FILE ==="
        )
        user = (
            f"Fix compliance issues in the '{service_slug}' source files.\n\n"
            f"=== ISSUES ===\n{issues_text}\n\n"
            f"=== SOURCE FILES ===\n{files_text}\n\n"
            "Output every corrected file using === FILE: path === / === END FILE === format."
        )

        # ── 4. Call CodeGenAgent ──────────────────────────────────────────────
        _ensure_sittingface()
        try:
            from sittingface.codegen_agent import CodeGenAgent
        except ImportError as e:
            self._reward(False, 0.0)
            return self._result(
                payload={"fixed": False, "error": repr(e)},
                observations=[f"CodeGenAgent import failed: {e}"],
            )

        cg = CodeGenAgent()
        if not cg.llm:
            self._reward(False, 0.0)
            return self._result(
                payload={"fixed": False, "error": "no LLM available"},
                observations=["set ANTHROPIC_API_KEY or start Ollama"],
            )

        raw = await cg._call_llm(user, system)

        # ── 5. Apply fixes ────────────────────────────────────────────────────
        pattern = re.compile(r"===\s*FILE:\s*(.+?)\s*===\n(.*?)===\s*END FILE\s*===", re.DOTALL)
        written: list[str] = []
        primary_dir = candidate_dirs[0]

        for m in pattern.finditer(raw):
            rel = m.group(1).strip()
            content = re.sub(r"^```\w*\n?", "", m.group(2).rstrip())
            content = re.sub(r"\n?```$", "", content).strip()
            try:
                compile(content, rel, "exec")
            except SyntaxError as e:
                logger.warning("[fixit] syntax error in fix for %s: %s", rel, e)
                continue
            dest = primary_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content + "\n")
            written.append(str(dest.relative_to(_REPO)))
            logger.info("[fixit] patched %s", dest.relative_to(_REPO))

        if not written:
            self._reward(False, 0.3)
            return self._result(
                payload={"fixed": False, "issues_found": len(issues)},
                observations=["LLM returned no valid file blocks"],
            )

        self._reward(True, 0.9)
        return self._result(
            payload={
                "fixed": True,
                "service_slug": service_slug,
                "files": written,
                "issues_found": len(issues),
                "issues_fixed": len(written),
            },
            observations=[f"fixed {len(written)} file(s) for {len(issues)} issue(s)"],
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _load_issues(self, service_slug: str, report_path: str) -> list[dict]:
        """Find the most recent governance/comply report and extract issues."""
        if report_path and Path(report_path).exists():
            reports = [Path(report_path)]
        else:
            reports_dir = Path.home() / ".monkeybrain" / "reports"
            pattern = str(reports_dir / f"*{service_slug}*.json")
            candidates = sorted(glob.glob(pattern), reverse=True)
            if not candidates:
                pattern = str(reports_dir / "govern_*.json")
                candidates = sorted(glob.glob(pattern), reverse=True)
            reports = [Path(c) for c in candidates[:3]]

        for rp in reports:
            try:
                data = json.loads(rp.read_text())
                issues = data.get("issues", [])
                if issues:
                    logger.info("[fixit] loaded %d issues from %s", len(issues), rp.name)
                    return issues
            except Exception as e:
                logger.debug("[fixit] could not parse %s: %s", rp, e)
        return []

    def _collect_source(self, dirs: list[Path]) -> dict[str, str]:
        """Collect .py source files from candidate directories (relative path → content)."""
        files: dict[str, str] = {}
        for d in dirs:
            if not d.exists():
                continue
            for p in sorted(d.rglob("*.py")):
                if "__pycache__" in str(p):
                    continue
                rel = str(p.relative_to(d))
                try:
                    files[rel] = p.read_text()
                except Exception:
                    pass
            if files:
                break
        return files
