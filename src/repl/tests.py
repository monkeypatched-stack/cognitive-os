"""Test commands."""

from __future__ import annotations

from pathlib import Path

import typer

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_test_suites = {
    "unit": "tests/unit",
    "e2e": "tests/e2e",
    "soma": "tests/e2e/test_soma_compile.py",
    "fault": "tests/fault_injection",
    "load": "tests/load",
    "stress": "tests/stress",
    "security": "tests/security",
    "chaos": "tests/chaos",
    "all": "tests",
}


def test_cmd(
    suite: str = typer.Argument("unit", help=f"Test suite to run: {', '.join(_test_suites)}"),
    verbose: bool = typer.Option(False, "-v", "--verbose"),
    keyword: str = typer.Option("", "-k", "--keyword", help="pytest -k filter"),
    fail_fast: bool = typer.Option(False, "-x", "--fail-fast"),
):
    """Run MonkeyBrain test suites (unit, fault, load, stress, security, chaos, all)."""
    import subprocess, sys
    from pathlib import Path

    path = _test_suites.get(suite)
    if not path:
        typer.echo(f"Unknown suite '{suite}'. Choose from: {', '.join(_test_suites)}", err=True)
        raise typer.Exit(1)

    import datetime

    repo_root = Path(__file__).parent.parent.parent
    report_dir = Path.home() / ".monkeybrain" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    report_txt = report_dir / f"{suite}_{ts}.txt"
    report_xml = report_dir / f"{suite}_{ts}.xml"

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        path,
        "-v",
        "--tb=short",
        f"--junit-xml={report_xml}",
    ]
    if verbose:
        cmd.append("--tb=long")
    if keyword:
        cmd += ["-k", keyword]
    if fail_fast:
        cmd.append("-x")

    result = subprocess.run(
        cmd,
        cwd=str(repo_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    # stream output to console and save to file
    print(result.stdout, end="")
    report_txt.write_text(result.stdout)

    if result.returncode != 0:
        typer.echo(f"\nFailed. Report saved to:\n  {report_txt}\n  {report_xml}")
    else:
        typer.echo(f"\nAll passed. Report saved to:\n  {report_txt}")

    raise typer.Exit(result.returncode)


def test_report_cmd(
    suite: str = typer.Argument("", help="Filter by suite name (e.g. e2e, unit). Empty = latest of any suite."),
    last: int = typer.Option(1, "--last", "-n", help="Show the Nth most recent report (default: 1 = latest)"),
):
    """Print the saved report from the last test run."""
    from pathlib import Path

    report_dir = Path.home() / ".monkeybrain" / "reports"
    if not report_dir.exists():
        typer.echo("No reports found. Run: monkeypatched test <suite>")
        raise typer.Exit(1)

    pattern = f"{suite}_*.txt" if suite else "*.txt"
    reports = sorted(report_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    if not reports:
        typer.echo(f"No reports found for suite '{suite}'." if suite else "No reports found.")
        raise typer.Exit(1)

    idx = min(last - 1, len(reports) - 1)
    chosen = reports[idx]
    typer.echo(f"=== {chosen.name} ===\n")
    typer.echo(chosen.read_text())


# ===========================================================================
# soma sub-commands  (delegate to soma.cli.main.app)
# ===========================================================================
