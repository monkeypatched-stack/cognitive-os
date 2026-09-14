"""stability.py — Four-condition system stability evaluation."""

from __future__ import annotations

import difflib
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger("agentos.stability")

_REPO_ROOT = Path(os.getenv("MONKEYPATCHED_ROOT", str(Path(__file__).resolve().parents[5])))
_SRC_DIR = _REPO_ROOT / "src"
_GEN_DIR = Path(os.getenv("MONKEYPATCHED_GEN_DIR", str(_REPO_ROOT.parent / "generated" / "monkeypatched")))
_OPS_EVIDENCE_DIR = _REPO_ROOT / ".operational_evidence"


def check_stability(error_lines: list[str] | None = None, evidence_since: float = 0.0) -> dict:
    """Evaluate all four stability conditions.

    Returns dict with per-condition booleans and overall 'stable' flag.

    Stable when ALL of:
      1. git diff == 0       (no uncommitted changes)
      2. no runtime errors
      3. codegen diff == 0   (src/ matches generated/)
      4. no new operational evidence since evidence_since
    """
    result: dict[str, Any] = {
        "git_clean": False,
        "no_errors": False,
        "codegen_diff": -1,  # -1 = no generated/ baseline
        "no_new_evidence": False,
        "stable": False,
    }

    # 1. Git diff
    try:
        proc = subprocess.run(
            ["git", "diff", "--stat", "HEAD"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
        )
        result["git_clean"] = proc.returncode == 0 and proc.stdout.strip() == ""
    except Exception as e:
        logger.debug("[stability] git check failed: %s", e)

    # 2. Runtime errors
    result["no_errors"] = not error_lines

    # 3. Codegen diff
    if _GEN_DIR.exists():
        src_f = {str(f.relative_to(_SRC_DIR)): f.read_text() for f in _SRC_DIR.rglob("*.py") if f.name != "__init__.py"}
        gen_f = {str(f.relative_to(_GEN_DIR)): f.read_text() for f in _GEN_DIR.rglob("*.py") if f.name != "__init__.py"}
        if gen_f:
            both = set(src_f) & set(gen_f)
            match = sum(
                1
                for k in both
                if difflib.SequenceMatcher(None, src_f[k].splitlines(), gen_f[k].splitlines()).ratio() > 0.9
            )
            result["codegen_diff"] = (len(both) - match) + len(set(src_f) - set(gen_f))

    # 4. No new operational evidence
    try:
        if _OPS_EVIDENCE_DIR.exists():
            new_files = [f for f in _OPS_EVIDENCE_DIR.iterdir() if f.stat().st_mtime > evidence_since]
            result["no_new_evidence"] = len(new_files) == 0
        else:
            result["no_new_evidence"] = True
    except Exception as e:
        logger.debug("Exception caught: %s", e)

    result["stable"] = (
        result["git_clean"] and result["no_errors"] and result["codegen_diff"] == 0 and result["no_new_evidence"]
    )
    return result
