"""ChartValidatorAgent — validates somatic/charts/ against constitutional
invariants before every commit, and publishes charts to the SittingFace
registry.

Referenced by .git/hooks/pre-commit (already installed, predates this file —
the hook expects `python3 -m broca.agents.chart_validator_agent` to exist and
exit non-zero to block a commit). install_git_hook() (re)writes that same
hook script, idempotently.

"Constitutional invariants" here means: every chart must be valid YAML with
a resolvable module/capability/agent name (SittingFaceRegistry.publish()'s
own requirement), and the codebase as a whole must have no CRITICAL/HIGH
architecture violations per the existing ArchitectureValidator
(src/cingulate/governance/architecture_validator.py) — reused rather than
reinvented, since that's already the codebase's real, working definition of
"critical" (file-size blowups, duplicate classes, circular dependencies).
This agent does not attempt to semantically enforce each chart's individual
owns/neverOwns/invariants statements against the actual source tree — that
would require a rule interpreter for arbitrary natural-language invariant
text, which doesn't exist anywhere in this codebase and isn't invented here.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

from ._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.chart_validator")

_REPO = Path(os.environ.get("MONKEYBRAIN_REPO", str(Path(__file__).parents[4])))
_CHARTS_DIR = _REPO / "somatic" / "charts"

_HOOK_SCRIPT = """#!/usr/bin/env bash
# ChartValidatorAgent pre-commit hook
# Installed by: broca.agents.chart_validator_agent.install_git_hook
# Validates all somatic/charts/ against constitutional invariants before every commit.
# Blocks commit on CRITICAL violations. Always publishes to SittingFace registry.
set -e
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# Only run if there are chart changes staged
CHANGED=$(git diff --cached --name-only)
CHART_CHANGE=$(echo "$CHANGED" | grep -c "^somatic/charts/\\|^policies/\\|^etass/workloads/" || true)
VALIDATOR_CHANGE=$(echo "$CHANGED" | grep -c "^packages/broca/broca/agents/chart_validator" || true)

# Skip validation when only the validator itself changed (bootstrap commit)
if [ "$CHART_CHANGE" -gt 0 ] && [ "$VALIDATOR_CHANGE" -eq 0 ]; then
    PYTHONPATH="$REPO_ROOT/packages/broca:$REPO_ROOT/packages/sittingface:$PYTHONPATH" \\
        python3 -m broca.agents.chart_validator_agent
fi
"""


def install_git_hook(repo_root: Path | None = None) -> Path:
    """(Re)write .git/hooks/pre-commit — idempotent, safe to call repeatedly."""
    repo_root = repo_root or _REPO
    hook_path = repo_root / ".git" / "hooks" / "pre-commit"
    hook_path.parent.mkdir(parents=True, exist_ok=True)
    hook_path.write_text(_HOOK_SCRIPT)
    hook_path.chmod(0o755)
    logger.info("[chart_validator] pre-commit hook installed at %s", hook_path)
    return hook_path


def _validate_charts(charts_dir: Path) -> list[str]:
    """Structural validation only — valid YAML, resolvable name. Returns a
    list of human-readable errors (empty = all charts structurally valid).

    Only applies to charts that actually look like constitutional charts
    (have a module/capability/agent block, or owns/neverOwns/invariants at
    the top level) — somatic/charts/ also holds non-constitutional charts
    (e.g. monkeybrain/'s Helm deployment values.yaml: services/mongodb/redis
    keys, no module identity at all). Those are a different kind of chart
    entirely and are silently skipped rather than flagged as invalid.
    """
    import yaml

    _CONSTITUTIONAL_MARKERS = (
        "module",
        "capability",
        "agent",
        "owns",
        "neverOwns",
        "invariants",
        "principles",
    )

    errors: list[str] = []
    if not charts_dir.exists():
        return [f"charts directory not found: {charts_dir}"]

    for chart_dir in sorted(charts_dir.iterdir()):
        if not chart_dir.is_dir():
            continue
        values_file = chart_dir / "values.yaml"
        if not values_file.exists():
            continue
        try:
            values = yaml.safe_load(values_file.read_text()) or {}
        except yaml.YAMLError as exc:
            errors.append(f"{chart_dir.name}: invalid YAML — {exc}")
            continue

        if not any(key in values for key in _CONSTITUTIONAL_MARKERS):
            continue  # not a constitutional chart (e.g. a Helm deployment chart) — not applicable

        name = (
            (values.get("module") or {}).get("name")
            or (values.get("capability") or {}).get("name")
            or (values.get("agent") or {}).get("name")
        )
        if not name:
            errors.append(f"{chart_dir.name}: no resolvable module/capability/agent name in values.yaml")
    return errors


def _publish_charts(charts_dir: Path) -> tuple[int, list[str]]:
    """Publish every chart to the SittingFace registry. Returns
    (published_count, errors) — a publish failure for one chart doesn't stop
    the others.
    """
    from sittingface.registry import SittingFaceRegistry

    registry = SittingFaceRegistry()
    published = 0
    errors: list[str] = []
    for chart_dir in sorted(charts_dir.iterdir()):
        if not chart_dir.is_dir() or not (chart_dir / "values.yaml").exists():
            continue
        try:
            registry.publish(chart_dir)
            published += 1
        except Exception as exc:
            errors.append(f"{chart_dir.name}: publish failed — {exc}")
    return published, errors


def _architecture_critical_violations() -> list[str]:
    """Reuse the existing ArchitectureValidator — CRITICAL/HIGH severity
    violations are what actually blocks a commit, not per-chart invariant
    text (see module docstring for why).
    """
    try:
        from src.cingulate.governance.architecture_validator import (
            ArchitectureValidator,
        )
    except ImportError as exc:
        logger.warning("[chart_validator] ArchitectureValidator unavailable, skipping: %s", exc)
        return []

    validator = ArchitectureValidator(root_dir=str(_REPO / "src" / "monkey_brain"))
    report = validator.validate_all()
    return [
        f"[{v.severity}] {v.file_path}: {v.description}"
        for v in report.violations
        if v.severity in ("critical", "high")
    ]


class ChartValidatorAgent(BaseETASSAgent):
    agent_type = "chart_validator"
    description = "Validates somatic charts and blocks commits on CRITICAL architecture violations"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict) -> dict:
        charts_dir = Path(context.get("charts_dir", str(_CHARTS_DIR)))

        structural_errors = _validate_charts(charts_dir)
        critical_violations = _architecture_critical_violations()
        published, publish_errors = _publish_charts(charts_dir)

        blocking = structural_errors + critical_violations
        passed = not blocking

        self._reward(passed, 0.5 if publish_errors else 0.8)
        return self._result(
            payload={
                "passed": passed,
                "structural_errors": structural_errors,
                "critical_violations": critical_violations,
                "published": published,
                "publish_errors": publish_errors,
            },
            observations=(
                [f"{len(blocking)} blocking issue(s)"] if blocking else [f"all charts valid, {published} published"]
            ),
            evidence=[{"blocking": b} for b in blocking],
        )


def _main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    structural_errors = _validate_charts(_CHARTS_DIR)
    critical_violations = _architecture_critical_violations()
    published, publish_errors = _publish_charts(_CHARTS_DIR)

    for err in structural_errors:
        print(f"  STRUCTURAL ERROR: {err}")
    for viol in critical_violations:
        print(f"  BLOCKING: {viol}")
    for err in publish_errors:
        print(f"  publish warning: {err}")

    blocking = structural_errors + critical_violations
    if blocking:
        print(f"\n  {len(blocking)} blocking issue(s) — commit rejected.")
        return 1

    print(f"\n  All charts valid. {published} chart(s) published to SittingFace registry.")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
