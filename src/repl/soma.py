"""Soma commands."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import typer

from cortex.engineering_report import EngineeringReport
from repl._helpers import (
    _auth_headers,
    _brain_get,
    _brain_post,
    _brain_url,
    _broca,
    _observations,
    _payload,
    _run_cingulate_review,
)

logger = logging.getLogger("monkeypatched")


def soma_callback(ctx: typer.Context):
    """Soma constitutional compiler — delegate all sub-commands."""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


def soma_compile(
    source: str = typer.Argument(
        "",
        help="Chart file (values.yaml), chart folder, or charts directory. Auto-detected from repo root if omitted.",
    ),
    output_dir: str = typer.Option(
        "somatic/compiled",
        "--output-dir",
        "-o",
        help="Directory to write compiled prompt files",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON summary instead of human-readable output"),
    reload: bool = typer.Option(
        False,
        "--reload",
        "-r",
        help="Hot-reload the running MonkeyBrain instance after compile",
    ),
):
    """Compile somatic charts → prompt files.

    Source can be:
      - A single chart file:   monkeypatched make compile somatic/charts/cortex/values.yaml
      - A chart folder:        monkeypatched make compile somatic/charts/cortex
      - A charts directory:    monkeypatched make compile somatic/charts
      - Omitted (auto):        monkeypatched make compile

    Writes one <chart>.prompt.md per compiled prompt into --output-dir.
    """
    import sys
    from pathlib import Path

    repo_root = Path(__file__).parent.parent.parent
    sys.path.insert(0, str(repo_root / "src"))

    from sittingface.somatic_compiler import SomaticCompiler

    compiler = SomaticCompiler()

    if source:
        if Path(source).is_absolute():
            src_path = Path(source)
        else:
            cwd_path = Path.cwd() / source
            src_path = cwd_path if cwd_path.exists() else repo_root / source
        try:
            compiler.load_path(src_path)
        except FileNotFoundError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1) from e
    else:
        compiler.load_all()

    prompts = compiler.compile_prompts()
    summary = compiler.summary()

    # Always write prompt files
    out_path = Path(output_dir) if Path(output_dir).is_absolute() else repo_root / output_dir
    written = compiler.write_prompts(out_path)

    if json_output:
        import json

        data = {
            **summary,
            "output_dir": str(out_path),
            "files_written": [
                (str(p) if not p.is_relative_to(repo_root) else str(p.relative_to(repo_root))) for p in written
            ],
            "prompts": [
                {
                    "chart": p.chart_name,
                    "file": f"{p.chart_name.replace('/', '_').replace(' ', '_')}.prompt.md",
                    "cot_steps": len(p.cot_steps),
                    "constraints": p.constraints,
                    "preamble": (p.preamble[:120] + "…" if len(p.preamble) > 120 else p.preamble),
                }
                for p in prompts
            ],
        }
        typer.echo(json.dumps(data, indent=2))
    else:
        typer.echo("\nSomatic compilation complete")
        typer.echo(
            f"  Charts loaded  : {summary['total_charts']}  "
            f"(module={summary['by_type']['module']}  "
            f"capability={summary['by_type']['capability']}  "
            f"agent={summary['by_type']['agent']})"
        )
        typer.echo(f"  Prompts built  : {summary['prompts_compiled']}")
        typer.echo(f"  Files written  : {len(written)}")
        typer.echo(f"  Output dir     : {out_path}")
        if written:
            typer.echo("\nWritten files:")
            for path in written:
                typer.echo(f"  {path if not path.is_relative_to(repo_root) else path.relative_to(repo_root)}")

    if reload:
        import httpx

        brain_url = "http://localhost:8031"
        try:
            r = httpx.post(f"{brain_url}/somatic/recompile", timeout=10.0)
            if r.status_code == 200:
                result = r.json()
                typer.echo(
                    f"\nMonkeyBrain reloaded: {result.get('charts')} charts, {result.get('agents_loaded')} agents"
                )
            else:
                typer.echo(f"\nReload failed: {r.status_code}", err=True)
        except Exception as e:
            typer.echo(f"\nCould not reach MonkeyBrain at {brain_url}: {e}", err=True)

    # Commit to git, then create embeddings in Elasticsearch
    if written:
        typer.echo("\nCommitting to git...")
        try:
            import subprocess

            subprocess.run(
                ["git", "add", str(out_path)],
                cwd=str(repo_root),
                capture_output=True,
                timeout=10,
            )
            subprocess.run(
                ["git", "commit", "-m", f"soma compile: {len(written)} prompt files"],
                cwd=str(repo_root),
                capture_output=True,
                timeout=10,
            )
            typer.echo("  Committed to git")
        except Exception as e:
            typer.echo(f"  Git commit failed: {e}", err=True)

        typer.echo("Indexing to Elasticsearch...")
        try:
            import asyncio

            from src.monkey_brain.kernel.semantic_memory import SemanticMemory

            sm = SemanticMemory()
            sm.initialize()
            if sm._embeddings.available:
                for chart in compiler.charts:
                    text = f"Chart: {chart.name} | " + " | ".join(
                        inv.get("statement", "") for inv in chart.values.get("invariants", [])
                    )
                    asyncio.get_event_loop().run_until_complete(
                        sm.store(
                            f"chart:{chart.name}",
                            text,
                            {"type": "chart", "name": chart.name},
                        )
                    )
                typer.echo(f"  Indexed {len(compiler.charts)} charts to Elasticsearch")
            else:
                typer.echo("  Elasticsearch not available — skipping indexing")
        except Exception as e:
            typer.echo(f"  Elasticsearch indexing failed: {e}", err=True)


def soma_api(
    service_type: str = typer.Argument(
        ...,
        help="Service type to generate, e.g. todo, work-order, inventory, crm, or any custom name.",
    ),
    output_dir: str = typer.Option(
        "somatic/compiled",
        "--output-dir",
        "-o",
        help="Directory to write the compiled .prompt.md file.",
    ),
    save_chart: bool = typer.Option(
        True,
        "--save-chart/--no-save-chart",
        help="Persist the generated values.yaml to somatic/charts/<service>-api/.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON summary."),
    review: bool = typer.Option(
        False,
        "--review",
        "-R",
        help="Run Cingulate compliance review on the compiled prompt before code generation. Exits 1 if rejected.",
    ),
):
    """Generate a production-grade DDD API prompt for a specific service type.

    With --review the flow is:
      compile chart → check prompt compliance → gate (exit 1 if rejected) → ready for code gen

    Examples:
      monkeypatched make api todo
      monkeypatched make api todo --review
      monkeypatched make api work-order --output-dir somatic/prompts
    """
    import asyncio
    import json as _json
    from pathlib import Path

    repo_root = Path(__file__).parent.parent.parent
    svc_slug = service_type.lower().replace(" ", "-").replace("_", "-")
    out_path = Path(output_dir) if Path(output_dir).is_absolute() else repo_root / output_dir

    ApiChartAgent = _broca("agents.api_chart_agent.ApiChartAgent")
    agent = ApiChartAgent()
    result = asyncio.run(
        agent.handle(
            {
                "service_slug": svc_slug,
                "compiled_dir": str(out_path),
                "save_chart": save_chart,
            }
        )
    )
    p = _payload(result)
    compiled = p.get("compiled", False)
    prompt_path_str = p.get("prompt_path", "")

    # ── Cingulate pre-check gate ─────────────────────────────────────────────
    if review and compiled and prompt_path_str:
        if not json_output:
            typer.echo("\n── Cingulate compliance pre-check ──────────────────────")
        verdict = _run_cingulate_review(prompt_path_str, repo_root, json_output, return_verdict=True)
        if verdict == "REJECTED":
            typer.echo(
                "\nPrompt REJECTED — fix compliance issues before code generation.",
                err=True,
            )
            raise typer.Exit(1)
        if verdict == "NEEDS_REVISION":
            typer.echo(
                "\nPrompt flagged NEEDS_REVISION — review issues above before proceeding.",
                err=True,
            )
            raise typer.Exit(2)
        if not json_output:
            typer.echo("\nPrompt APPROVED — ready for code generation.")
            typer.echo("────────────────────────────────────────────────────────\n")

    if json_output:
        typer.echo(_json.dumps(p, indent=2, default=str))
    else:
        typer.echo(f"Service:   {svc_slug}")
        typer.echo(f"Aggregate: {p.get('aggregate', '')}")
        if p.get("chart_path"):
            _cp = Path(p["chart_path"])
            typer.echo(f"Chart:     {_cp.relative_to(repo_root) if _cp.is_absolute() else _cp}")
        if prompt_path_str:
            _pp = Path(prompt_path_str)
            typer.echo(f"Prompt:    {_pp.relative_to(repo_root) if _pp.is_absolute() else _pp}")
        if not compiled:
            for obs in _observations(result):
                typer.echo(f"  {obs}", err=True)


# ── Cingulate compliance review helpers ──────────────────────────────────────


def soma_review(
    prompt_file: str = typer.Argument(
        "",
        help="Compiled .prompt.md file to review. Defaults to most recent file in somatic/compiled/.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON report."),
):
    """Run the Cingulate compliance review on a compiled prompt file.

    Runs structural invariant checks (via ComplianceEngine) and an LLM semantic
    review (via CingulateAgent pattern, requires ANTHROPIC_API_KEY).
    Saves a JSON report to ~/.monkeybrain/reports/.

    Examples:
      monkeypatched make review somatic/compiled/todo-api.prompt.md
      monkeypatched make review          # reviews most recent compiled file
      monkeypatched make review --json
    """
    from pathlib import Path

    repo_root = Path(__file__).parent.parent.parent

    if prompt_file:
        p = Path(prompt_file) if Path(prompt_file).is_absolute() else Path.cwd() / prompt_file
        if not p.exists():
            p = repo_root / prompt_file
        if not p.exists():
            typer.echo(f"Error: file not found: {prompt_file}", err=True)
            raise typer.Exit(1)
    else:
        compiled_dir = repo_root / "somatic" / "compiled"
        files = sorted(
            compiled_dir.glob("*.prompt.md"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        if not files:
            typer.echo("No compiled prompt files found in somatic/compiled/.", err=True)
            raise typer.Exit(1)
        p = files[0]
        if not json_output:
            typer.echo(f"Reviewing most recent: {p.relative_to(repo_root)}")

    _run_cingulate_review(p, repo_root, json_output)


def soma_codegen(
    service_type: str = typer.Argument(
        ...,
        help="Service type to generate code for, e.g. todo, work-order, inventory, crm.",
    ),
    output_dir: str = typer.Option(
        "generated",
        "--output-dir",
        "-o",
        help="Directory to write generated service files.",
    ),
    review: bool = typer.Option(
        False,
        "--review",
        "-R",
        help="Run Cingulate compliance gate on the compiled prompt before generating code.",
    ),
    fmt: str = typer.Option(
        "files",
        "--fmt",
        help="Output format: files (default) or zip.",
    ),
    correct_with_claude: bool = typer.Option(
        False,
        "--correct-with-claude",
        "-C",
        help="Generate with Ollama, then run a Claude correction pass to fix quality issues.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON summary."),
):
    """Generate a DDD-compliant service from its compiled somatic prompt via CodeGenAgent.

    Flow:
      1. Locate compiled prompt for <service-type> in somatic/compiled/
      2. (optional --review) Run Cingulate compliance gate — exit 1 if rejected
      3. CodeGenAgent.generate_ddd_service() — LLM generates DDD-layered code
         following the prompt's preamble, CoT steps, and invariants
      4. Write files to <output-dir>/<service>/
      5. (optional --correct-with-claude) Claude correction pass over written files
      6. SeedAgent auto-runs — seeds MongoDB with Pydantic-validated synthetic data

    Examples:
      monkeypatched make codegen todo
      monkeypatched make codegen todo --review
      monkeypatched make codegen work-order --correct-with-claude
      monkeypatched make codegen work-order --output-dir generated/services
    """
    import asyncio
    import json as _json
    from pathlib import Path

    repo_root = Path(__file__).parent.parent.parent
    svc_slug = service_type.lower().replace(" ", "-").replace("_", "-")
    out_base = Path(output_dir) if Path(output_dir).is_absolute() else repo_root / output_dir

    # ── Optional Cingulate review gate (before codegen) ──────────────────────
    if review:
        compiled_dir = repo_root / "somatic" / "compiled"
        prompt_file = compiled_dir / f"{svc_slug}-api.prompt.md"
        if not prompt_file.exists():
            candidates = list(compiled_dir.glob(f"*{svc_slug}*.prompt.md"))
            prompt_file = candidates[0] if candidates else None
        if prompt_file and prompt_file.exists():
            if not json_output:
                typer.echo("\n── Cingulate compliance pre-check ──────────────────────")
            verdict = _run_cingulate_review(prompt_file, repo_root, json_output, return_verdict=True)
            if verdict == "REJECTED":
                typer.echo(
                    "\nPrompt REJECTED — fix compliance issues before code generation.",
                    err=True,
                )
                raise typer.Exit(1)
            if verdict == "NEEDS_REVISION":
                typer.echo(
                    "\nPrompt flagged NEEDS_REVISION — review issues before proceeding.",
                    err=True,
                )
                raise typer.Exit(2)
            if not json_output:
                typer.echo("Prompt APPROVED — proceeding to code generation.\n")

    ServiceGenAgent = _broca("agents.service_gen_agent.ServiceGenAgent")
    agent = ServiceGenAgent()
    result = asyncio.run(
        agent.handle(
            {
                "service_slug": svc_slug,
                "output_dir": str(out_base),
                "question_source": "codegen",  # guard: rejects if routed through simulator
            }
        )
    )
    p = _payload(result)
    generated = p.get("generated", False)
    written = p.get("files", [])

    if fmt == "zip" and generated:
        import io
        import zipfile

        svc_dir = out_base / svc_slug
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in written:
                fp = repo_root / f
                if fp.exists():
                    zf.writestr(str(Path(f).relative_to(out_base / svc_slug)), fp.read_text())
        zip_path = out_base / f"{svc_slug}.zip"
        zip_path.write_bytes(buf.getvalue())
        if json_output:
            typer.echo(_json.dumps({"service": svc_slug, "zip": str(zip_path), "files": len(written)}))
        else:
            typer.echo(f"ZIP: {zip_path.relative_to(repo_root)}  ({len(written)} files)")
    elif json_output:
        typer.echo(_json.dumps(p, indent=2, default=str))
    else:
        if generated:
            typer.echo(f"\nGenerated {len(written)} files → {p.get('service_dir', svc_slug)}/")
            for f in written:
                typer.echo(f"  {f}")
        else:
            typer.echo(f"Code generation failed for {svc_slug}", err=True)
            for obs in _observations(result):
                typer.echo(f"  {obs}", err=True)
            raise typer.Exit(1)

    # ── Claude correction pass ───────────────────────────────────────────────
    if correct_with_claude and generated and written:
        typer.echo("\n── Claude correction pass ──────────────────────────────────")
        svc_dir = out_base / svc_slug

        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            typer.echo("  [skip] ANTHROPIC_API_KEY not set — skipping correction", err=True)
        else:
            try:
                import anthropic as _anthropic

                from sittingface.codegen_agent import CodeGenAgent as _CGA

                # Collect written .py files (largest first — most likely to have issues)
                py_files = sorted(
                    [repo_root / f for f in written if f.endswith(".py") and (repo_root / f).exists()],
                    key=lambda p: p.stat().st_size,
                    reverse=True,
                )[:12]

                files_ctx = ""
                for pf in py_files:
                    rel = pf.relative_to(svc_dir)
                    files_ctx += f"\n=== FILE: {rel} ===\n{pf.read_text()[:3000]}\n=== END FILE ===\n"

                system = (
                    "You are a senior Python DDD engineer reviewing Ollama-generated code.\n"
                    "Fix: missing imports, undefined names, wrong layer dependencies (domain importing fastapi/motor), "
                    "missing __init__.py exports, malformed class definitions, syntax errors.\n"
                    "Keep business logic intact. Output ONLY the files you changed.\n"
                    "Format: === FILE: <relative-path> ===\\n<content>\\n=== END FILE ==="
                )
                user = (
                    f"Review and fix these generated files for service '{svc_slug}':\n{files_ctx}\n"
                    "Output only corrected files. Skip files that need no changes."
                )

                typer.echo(f"  Sending {len(py_files)} files to Claude for correction…")
                client = _anthropic.Anthropic(api_key=api_key)
                msg = client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=8192,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                )
                raw_fix = msg.content[0].text

                # Parse and write fixes
                agent = _CGA()
                fixed = agent._parse_file_blocks(raw_fix)
                patched = 0
                for rel_path, content in fixed.items():
                    # _parse_file_blocks already normalizes, but guard here too.
                    safe = Path(rel_path)
                    if safe.is_absolute():
                        safe = Path(*safe.parts[1:])
                    dest = svc_dir / safe
                    if dest.exists() or dest.parent.exists():
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_text(content + "\n")
                        typer.echo(f"  patched {safe}")
                        patched += 1

                typer.echo(f"\n  Correction pass complete — {patched}/{len(fixed)} files patched")
            except Exception as _ce:
                typer.echo(f"  [correction] failed: {_ce}", err=True)

    # ── Auto-seed step (runs after codegen + correction, before serve) ────────
    if generated:
        if not json_output:
            typer.echo("\n── Seeding MongoDB with Pydantic-validated data ────────────")
        try:
            SeedAgent = _broca("agents.seed_agent.SeedAgent")
            seed_agent = SeedAgent()
            seed_result = asyncio.run(
                seed_agent.handle(
                    {
                        "service_slug": svc_slug,
                        "service_dir": str(out_base / svc_slug),
                        "question_source": "codegen",
                    }
                )
            )
            sp = _payload(seed_result)
            if json_output:
                p["seed"] = sp
            else:
                if sp.get("seeded"):
                    typer.echo(f"  Seeded {sp.get('inserted', 0)} record(s) into {sp.get('collection', svc_slug)}")
                else:
                    typer.echo(
                        f"  [seed] skipped — {sp.get('error', 'no models found')}",
                        err=True,
                    )
        except Exception as _se:
            if not json_output:
                typer.echo(f"  [seed] failed: {_se}", err=True)


def soma_govern(
    artifact: str = typer.Argument(
        "",
        help="File or directory to review, or leave blank to pass --question text.",
    ),
    question: str = typer.Option(
        "",
        "--question",
        "-q",
        help="Free-text artifact or question to review (used when no file path given).",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON report."),
):
    """Run the CingulateAgent governance gate on any artifact.

    Accepts a file path, service directory, or free-text question.
    Reviews against the governance_review.yaml workload (constitutional
    compliance, security posture, DDD policy adherence). Emits the decision
    to Lemon and saves a governance report.

    Examples:
      monkeypatched make govern somatic/compiled/todo-api.prompt.md
      monkeypatched make govern generated/todo
      monkeypatched make govern --question "Does this service expose PII without audit logging?"
    """
    import asyncio
    import json as _json
    import sys
    import time
    from pathlib import Path

    repo_root = Path(__file__).parent.parent.parent
    sys.path.insert(0, str(repo_root / "src"))

    # ── Build artifact text ───────────────────────────────────────────────────
    artifact_label = artifact or "inline question"
    content: str = ""

    if artifact:
        target = Path(artifact) if Path(artifact).is_absolute() else Path.cwd() / artifact
        if not target.exists():
            target = repo_root / artifact
        if target.is_dir():
            # Summarise the directory structure + key file snippets
            py_files = sorted(target.rglob("*.py"))[:20]
            lines = [f"Directory: {target}\n\nFiles:"]
            for f in py_files:
                lines.append(f"  {f.relative_to(target)}")
            lines.append("\n--- Key file contents ---")
            for f in py_files[:6]:
                lines.append(f"\n=== {f.relative_to(target)} ===")
                lines.append(f.read_text()[:1500])
            content = "\n".join(lines)
        elif target.is_file():
            content = target.read_text()
        else:
            typer.echo(f"Path not found: {artifact}", err=True)
            raise typer.Exit(1)
    elif question:
        content = question
    else:
        typer.echo("Provide a file path or --question text.", err=True)
        raise typer.Exit(1)

    if not json_output:
        typer.echo(f"\n── Governance review: {artifact_label} ──────────────────────")
        typer.echo(f"Artifact length: {len(content)} chars\n")

    # ── Run CingulateAgent ────────────────────────────────────────────────────
    CingulateAgent = _broca("agents.cingulate.CingulateAgent")
    agent = CingulateAgent()
    result = asyncio.run(agent.handle({"question": content[:6000]}))

    p = _payload(result)
    compliant = p.get("compliant", True)
    # evidence is a list of dicts; each may be the full LLM response or inner issues list
    _ev = result.evidence if hasattr(result, "evidence") else []
    issues: list = []
    for item in _ev:
        if isinstance(item, dict) and "issues" in item:
            issues.extend(item["issues"] if isinstance(item["issues"], list) else [item])
        elif isinstance(item, dict):
            issues.append(item)
        elif isinstance(item, list):
            issues.extend(item)
    rec = p.get("recommendation", "") or (_observations(result) or [""])[0]
    verdict = "COMPLIANT" if compliant else "NON-COMPLIANT"

    if not json_output:
        typer.echo(f"Verdict:        {verdict}")
        typer.echo(f"Recommendation: {rec or '—'}")
        if issues:
            typer.echo(f"\nIssues ({len(issues)}):")
            for iss in issues[:5]:
                if isinstance(iss, dict):
                    sev = iss.get("severity", "")
                    desc = iss.get("description", str(iss))
                    rem = iss.get("remediation", "")
                    typer.echo(f"  [{sev}] {desc}")
                    if rem:
                        typer.echo(f"        → {rem}")
                else:
                    typer.echo(f"  {iss}")

    # ── Save report ───────────────────────────────────────────────────────────
    reports_dir = Path.home() / ".monkeybrain" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    label_slug = artifact_label.replace("/", "_").replace(" ", "_")[:40]
    report_path = reports_dir / f"govern_{label_slug}_{ts}.json"
    report: dict = {
        "artifact": artifact_label,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "verdict": verdict,
        "compliant": compliant,
        "recommendation": rec,
        "issues": issues,
        "payload": p,
        "observations": _observations(result),
    }
    report_path.write_text(_json.dumps(report, indent=2, default=str))

    # ── Emit to Lemon ─────────────────────────────────────────────────────────
    try:
        from introspection.lemon import Lemon

        lemon = Lemon()
        lemon.observe_governance(
            policy_path=artifact_label,
            decision=verdict,
            principal="CingulateAgent",
            obligations=[i.get("description", str(i)) if isinstance(i, dict) else str(i) for i in issues],
            trust_score=1.0 if compliant else 0.3,
            provenance_chain=["soma_govern", "cingulate_agent"],
            audit_ref=str(report_path),
            pipeline_id="soma_govern",
        )
        lemon.info(
            _json.dumps(report, default=str),
            component="govern",
            pipeline_id="soma_govern",
        )
    except Exception as e:
        logger.debug("Lemon emit failed: %s", e)

    if json_output:
        typer.echo(_json.dumps(report, indent=2, default=str))
    else:
        typer.echo(f"\nReport: {report_path}")

    if not compliant:
        raise typer.Exit(1)


def soma_fixit(
    service_type: str = typer.Argument(
        ...,
        help="Service or client to fix, e.g. todo.",
    ),
    output_dir: str = typer.Option(
        "generated",
        "--output-dir",
        "-o",
        help="Root directory containing generated services/clients.",
    ),
    report: str = typer.Option(
        "",
        "--report",
        "-r",
        help="Path to a specific governance/comply JSON report. Defaults to most recent.",
    ),
    artifact: str = typer.Option(
        "",
        "--artifact",
        "-a",
        help="Path to the source directory to fix (default: generated/<service>-client/).",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON summary."),
):
    """Fix compliance issues found by govern/comply using FixItAgent.

    Reads the most recent governance report for <service> (or --report path),
    extracts the issue list, and calls CodeGenAgent to patch the source files.

    Examples:
      monkeypatched make fixit todo
      monkeypatched make fixit todo --report ~/.monkeybrain/reports/govern_todo_123.json
      monkeypatched make fixit todo --artifact generated/todo-client
    """
    import asyncio
    import json as _json
    from pathlib import Path

    repo_root = Path(__file__).parent.parent.parent
    svc_slug = service_type.lower().replace(" ", "-").replace("_", "-")
    out_base = Path(output_dir) if Path(output_dir).is_absolute() else repo_root / output_dir

    if not json_output:
        typer.echo(f"\n── FixItAgent: {svc_slug} ──────────────────────────────────")

    FixItAgent = _broca("agents.fixit_agent.FixItAgent")
    agent = FixItAgent()
    result = asyncio.run(
        agent.handle(
            {
                "service_slug": svc_slug,
                "output_dir": str(out_base),
                "report_path": report,
                "artifact_path": artifact,
            }
        )
    )
    p = _payload(result)
    fixed = p.get("fixed", False)

    if json_output:
        typer.echo(_json.dumps(p, indent=2, default=str))
    elif fixed:
        typer.echo(f"Fixed {p.get('issues_fixed', 0)}/{p.get('issues_found', 0)} issues")
        for f in p.get("files", []):
            typer.echo(f"  patched {f}")
        typer.echo(f"\nNext: monkeypatched make govern {artifact or f'generated/{svc_slug}-client'}")
    else:
        typer.echo(p.get("error", "FixItAgent found nothing to fix"), err=True)
        for obs in _observations(result):
            typer.echo(f"  {obs}", err=True)
        if not p.get("issues_found", 1):
            pass  # no issues = success
        else:
            raise typer.Exit(1)


def soma_comply(
    standard: str = typer.Argument(
        ...,
        help="Regulatory standard: gdpr, soc2, fda, gxp, iec61508, iso10218, iso27001",
    ),
    signals: str = typer.Option(
        "{}",
        "--signals",
        "-s",
        help="data_signals as JSON string or path to a JSON file.",
    ),
    attributes: str = typer.Option(
        "{}",
        "--attributes",
        "-a",
        help="system_attributes as JSON string or path to a JSON file.",
    ),
    action: str = typer.Option("", "--action", help="Operation being proposed (e.g. 'store_user_data')."),
    resource: str = typer.Option("", "--resource", help="Target resource (e.g. 'users')."),
    json_output: bool = typer.Option(False, "--json", help="Print JSON report."),
):
    """Run a regulatory compliance agent against data_signals and system_attributes.

    Available standards: gdpr · soc2 · fda · gxp · iec61508 · iso10218 · iso27001

    Each agent evaluates the provided signals against its rule set and returns
    structured findings with severity, article citations, and remediation steps.
    CRITICAL/HIGH findings exit 1. Report saved and emitted to Lemon.

    Examples:
      monkeypatched make comply gdpr --signals '{"has_pii":true,"lawful_basis":"consent"}'
      monkeypatched make comply soc2 --signals signals.json --attributes attrs.json
      monkeypatched make comply gdpr --signals '{"has_pii":true}' --json
    """
    import asyncio
    import json as _json
    import sys
    import time
    from pathlib import Path

    repo_root = Path(__file__).parent.parent.parent
    sys.path.insert(0, str(repo_root / "src"))

    _AGENT_MAP = {
        "gdpr": ("broca.agents.ddd.compliance.gdpr", "GDPRAgent"),
        "soc2": ("broca.agents.ddd.compliance.soc2", "SOC2Agent"),
        "fda": ("broca.agents.ddd.compliance.fda", "FDAAgent"),
        "gxp": ("broca.agents.ddd.compliance.gxp", "GxPAgent"),
        "iec61508": ("broca.agents.ddd.compliance.iec61508", "IEC61508Agent"),
        "iso10218": ("broca.agents.ddd.compliance.iso10218", "ISO10218Agent"),
        "iso27001": ("broca.agents.ddd.compliance.iso27001", "ISO27001Agent"),
    }
    std_key = standard.lower()
    if std_key not in _AGENT_MAP:
        typer.echo(
            f"Unknown standard '{standard}'. Choose from: {', '.join(_AGENT_MAP)}",
            err=True,
        )
        raise typer.Exit(1)

    def _load_json(val: str) -> dict:
        if not val or val == "{}":
            return {}
        p = Path(val)
        if p.exists():
            return _json.loads(p.read_text())
        return _json.loads(val)

    try:
        data_signals = _load_json(signals)
        system_attributes = _load_json(attributes)
    except Exception as e:
        typer.echo(f"Failed to parse JSON: {e}", err=True)
        raise typer.Exit(1) from e

    context = {
        "data_signals": data_signals,
        "system_attributes": system_attributes,
        "action": action,
        "resource": resource,
        "principal": {},
    }

    # ── Instantiate the right agent via _broca() ─────────────────────────────
    mod_path, cls_name = _AGENT_MAP[std_key]
    # Convert "broca.agents.ddd.compliance.X" → "agents.ddd.compliance.X.ClassName"
    broca_dotted = mod_path.removeprefix("broca.") + "." + cls_name
    agent_cls = _broca(broca_dotted)
    agent = agent_cls()

    if not json_output:
        typer.echo(f"\n── Compliance check: {standard.upper()} ────────────────────────────")
        typer.echo(f"Signals:    {_json.dumps(data_signals)}")
        typer.echo(f"Attributes: {_json.dumps(system_attributes)}\n")

    result = asyncio.run(agent.handle(context))

    report_data = _payload(result)

    compliant = report_data.get("compliant", True)
    findings = report_data.get("findings", [])
    critical_count = report_data.get("critical_count", sum(1 for f in findings if f.get("severity") == "CRITICAL"))
    high_count = report_data.get("high_count", sum(1 for f in findings if f.get("severity") == "HIGH"))
    verdict = "COMPLIANT" if compliant else "NON-COMPLIANT"

    if not json_output:
        typer.echo(f"Standard:  {standard.upper()}")
        typer.echo(f"Verdict:   {verdict}")
        typer.echo(f"Findings:  {len(findings)}  (CRITICAL: {critical_count}, HIGH: {high_count})")
        if findings:
            typer.echo("")
            for f in findings:
                sev = f.get("severity", "?")
                art = f.get("article", "")
                desc = f.get("description", str(f))
                rem = f.get("remediation", "")
                typer.echo(f"  [{sev}] {art}  {desc}")
                if rem:
                    typer.echo(f"        → {rem}")

    # ── Save report ───────────────────────────────────────────────────────────
    reports_dir = Path.home() / ".monkeybrain" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    report_path = reports_dir / f"comply_{std_key}_{ts}.json"
    full_report = {
        "standard": standard.upper(),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "verdict": verdict,
        "compliant": compliant,
        "critical_count": critical_count,
        "high_count": high_count,
        "findings": findings,
        "inputs": {
            "data_signals": data_signals,
            "system_attributes": system_attributes,
        },
        "agent_result": result,
    }
    report_path.write_text(_json.dumps(full_report, indent=2, default=str))

    # ── Emit to Lemon ─────────────────────────────────────────────────────────
    try:
        from introspection.lemon import Lemon

        lemon = Lemon()
        lemon.observe_governance(
            policy_path=f"compliance/{std_key}",
            decision=verdict,
            principal=f"compliance_{std_key}",
            obligations=[f.get("description", str(f)) for f in findings if f.get("severity") in ("CRITICAL", "HIGH")],
            trust_score=(1.0 if compliant else max(0.0, 1.0 - (critical_count * 0.3 + high_count * 0.1))),
            provenance_chain=["soma_comply", f"compliance_{std_key}"],
            audit_ref=str(report_path),
            pipeline_id="soma_comply",
        )
        lemon.info(
            _json.dumps(full_report, default=str),
            component="comply",
            pipeline_id="soma_comply",
        )
    except Exception as e:
        logger.debug("Lemon emit failed: %s", e)

    if json_output:
        typer.echo(_json.dumps(full_report, indent=2, default=str))
    else:
        typer.echo(f"\nReport: {report_path}")

    if not compliant or critical_count > 0 or high_count > 0:
        raise typer.Exit(1)


def soma_ddd_check(
    service_type: str = typer.Argument(
        ...,
        help="Generated service to check, e.g. todo, work-order.",
    ),
    output_dir: str = typer.Option(
        "generated",
        "--output-dir",
        "-o",
        help="Root directory containing generated services.",
    ),
    max_loops: int = typer.Option(
        2,
        "--max-loops",
        "-L",
        help="Max codegen retry attempts when structural issues are found (0 = check only).",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON report."),
):
    """Post-codegen DDD compliance check with self-healing retry loop.

    Runs structural layer checks (domain purity, application isolation,
    infrastructure separation) then pipes a summary to CingulateAgent
    for semantic governance review.

    If structural issues are found (missing layers, forbidden imports), the
    failing layers' codegen cache is invalidated and ServiceGenAgent re-runs
    for only those layers — up to --max-loops times. Saves report and emits to Lemon.

    Examples:
      monkeypatched make ddd-check todo
      monkeypatched make ddd-check work-order --max-loops 3
      monkeypatched make ddd-check work-order --max-loops 0   # check only, no retry
    """
    import json as _json
    import sys
    import time
    from pathlib import Path

    repo_root = Path(__file__).parent.parent.parent
    sys.path.insert(0, str(repo_root / "src"))

    svc_slug = service_type.lower().replace(" ", "-").replace("_", "-")
    out_base = Path(output_dir) if Path(output_dir).is_absolute() else repo_root / output_dir
    svc_dir = out_base / svc_slug

    # Handle nested: generated/todo/todo/
    if not (svc_dir / "main.py").exists():
        nested = out_base / svc_slug / svc_slug
        if (nested / "main.py").exists():
            svc_dir = nested

    if not svc_dir.exists():
        typer.echo(
            f"Service directory not found: {svc_dir}\nRun: monkeypatched make codegen {svc_slug}",
            err=True,
        )
        raise typer.Exit(1)

    import asyncio
    import re as _re

    if not json_output:
        typer.echo(f"\n── DDD compliance check: {svc_slug} ────────────────────────")
        typer.echo(f"Service dir: {svc_dir.relative_to(repo_root)}\n")

    # ── Layer spec: (name, forbidden_imports, required_files) ────────────────
    _LAYER_SPEC = [
        ("domain", ["fastapi", "motor", "pymongo", "sqlalchemy", "httpx"], []),
        ("application", ["fastapi", "motor", "pymongo", "sqlalchemy"], []),
        ("infrastructure", [], []),
        ("api", [], []),
    ]

    def _run_structural_checks() -> tuple[list[str], list[str]]:
        _issues: list[str] = []
        _passed: list[str] = []

        for layer, forbidden_imports, required_files in _LAYER_SPEC:
            layer_dir = svc_dir / layer
            if not layer_dir.exists():
                _issues.append(f"Missing layer: {layer}/")
                continue
            _passed.append(f"{layer}/ layer exists")
            for py_file in layer_dir.rglob("*.py"):
                content = py_file.read_text()
                for forbidden in forbidden_imports:
                    if forbidden in content:
                        _issues.append(f"{py_file.relative_to(svc_dir)}: forbidden import '{forbidden}'")
            for req in required_files:
                if not (layer_dir / req).exists():
                    _issues.append(f"{layer}/{req} missing")
                else:
                    _passed.append(f"{layer}/{req} present")

        if (svc_dir / "main.py").exists():
            _passed.append("main.py present")
        else:
            _issues.append("main.py missing")

        if (svc_dir / "settings.py").exists():
            _passed.append("settings.py present")
        else:
            _issues.append("settings.py missing")

        return _passed, _issues

    def _layers_from_issues(issue_list: list[str]) -> set[str]:
        """Extract DDD layer names that need regeneration from issue strings."""
        layer_names = {name for name, _, _ in _LAYER_SPEC} | {"main"}
        broken: set[str] = set()
        for issue in issue_list:
            # "Missing layer: application/" → application
            m = _re.match(r"Missing layer:\s*(\w+)", issue)
            if m and m.group(1) in layer_names:
                broken.add(m.group(1))
                continue
            # "application/foo.py: forbidden import 'fastapi'" → application
            m = _re.match(r"(\w+)/", issue)
            if m and m.group(1) in layer_names:
                broken.add(m.group(1))
        return broken

    def _bust_cache(layers: set[str]) -> None:
        cache_dir = svc_dir / ".codegen_cache"
        for layer in layers:
            cp = cache_dir / f"{layer}.json"
            if cp.exists():
                cp.unlink()
                if not json_output:
                    typer.echo(f"  [retry] cache busted: {layer}")

    def _rerun_codegen(broken_layers: set[str]) -> None:
        if not json_output:
            typer.echo(f"  [retry] re-running codegen for layers: {sorted(broken_layers)}")
        try:
            ServiceGenAgent = _broca("agents.service_gen_agent.ServiceGenAgent")
            agent = ServiceGenAgent()
            asyncio.run(
                agent.handle(
                    {
                        "service_slug": svc_slug,
                        "output_dir": str(out_base),
                        "question_source": "codegen",
                    }
                )
            )
        except Exception as _rce:
            if not json_output:
                typer.echo(f"  [retry] codegen error: {_rce}", err=True)

    # ── 1. Structural check + self-healing retry loop ─────────────────────────
    loop_history: list[dict] = []
    passed, issues = _run_structural_checks()

    for attempt in range(max_loops):
        if not issues:
            break
        broken = _layers_from_issues(issues)
        if not broken:
            break  # issues are not layer-regeneratable (e.g. main.py only)
        if not json_output:
            typer.echo(f"\n── Retry {attempt + 1}/{max_loops} — fixing layers: {sorted(broken)} ──")
        loop_history.append(
            {
                "attempt": attempt + 1,
                "broken_layers": sorted(broken),
                "issues_before": list(issues),
            }
        )
        _bust_cache(broken)
        _rerun_codegen(broken)
        passed, issues = _run_structural_checks()
        loop_history[-1]["issues_after"] = list(issues)

    score = int(100 * len(passed) / max(len(passed) + len(issues), 1))
    verdict_structural = "APPROVED" if not issues else ("NEEDS_REVISION" if score >= 60 else "REJECTED")

    if not json_output:
        typer.echo(f"\nStructural checks: {len(passed)} passed, {len(issues)} issues  (score {score})")
        for i in issues:
            typer.echo(f"  ✗ {i}")
        for p in passed:
            typer.echo(f"  ✓ {p}")

    # ── 2. CingulateAgent semantic review ────────────────────────────────────
    summary_text = (
        f"DDD compliance check for service: {svc_slug}\n\n"
        f"Structural score: {score}/100\n"
        f"Passed checks: {passed}\n"
        f"Issues found: {issues}\n\n"
        f"Layers present: {[d.name for d in svc_dir.iterdir() if d.is_dir()]}\n"
        f"Files: {[f.name for f in svc_dir.rglob('*.py')][:20]}\n"
    )
    _cingulate_payload: dict = {}
    try:
        CingulateAgent = _broca("agents.cingulate.CingulateAgent")
        cingulate = CingulateAgent()
        _cingulate_raw = asyncio.run(cingulate.handle({"question": summary_text[:6000]}))
        _cingulate_payload = _payload(_cingulate_raw)
        if not json_output:
            typer.echo(f"\nCingulate verdict: {_cingulate_payload.get('verdict', 'N/A')}")
            for o in _observations(_cingulate_raw)[:3]:
                typer.echo(f"  {o}")
    except Exception as e:
        _cingulate_payload = {"error": str(e), "verdict": verdict_structural}
        if not json_output:
            typer.echo(f"CingulateAgent unavailable ({e}) — using structural verdict.")

    # ── 3. Save report + emit to Lemon ───────────────────────────────────────
    report = {
        "service": svc_slug,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "structural_score": score,
        "verdict": _cingulate_payload.get("verdict", verdict_structural),
        "passed": passed,
        "issues": issues,
        "retry_history": loop_history,
        "cingulate": _cingulate_payload,
    }
    reports_dir = Path.home() / ".monkeybrain" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    report_path = reports_dir / f"ddd_check_{svc_slug}_{ts}.json"
    report_path.write_text(_json.dumps(report, indent=2, default=str))

    try:
        from introspection.lemon import Lemon

        lemon = Lemon()
        lemon.observe_governance(
            policy_path=str(svc_dir),
            decision=report["verdict"],
            principal="CingulateAgent",
            obligations=issues,
            trust_score=round(score / 100.0, 3),
            provenance_chain=["soma_codegen", "ddd_structural", "cingulate_agent"],
            audit_ref=str(report_path),
            pipeline_id="soma_ddd_check",
        )
        lemon.info(
            _json.dumps(report, default=str),
            component="ddd_check",
            pipeline_id="soma_ddd_check",
        )
    except Exception as _e:
        pass

    if json_output:
        typer.echo(_json.dumps(report, indent=2, default=str))
    else:
        typer.echo(f"\nReport saved: {report_path}")
        typer.echo(f"Final verdict: {report['verdict']}")

    if report["verdict"] == "REJECTED":
        raise typer.Exit(1)


def soma_seed(
    service_type: str = typer.Argument(..., help="Service slug to seed, e.g. todo."),
    output_dir: str = typer.Option("generated", "--output-dir", "-o", help="Root of generated services."),
    mongo_uri: str = typer.Option("", "--mongo-uri", help="MongoDB URI (default: $MONGODB_URI or localhost)."),
    db_name: str = typer.Option("", "--db", help="Database name (default: service slug)."),
    seed_count: int = typer.Option(5, "--count", "-n", help="Records to generate per model."),
    json_output: bool = typer.Option(False, "--json", help="Print raw JSON result."),
):
    """Seed the database with Pydantic-validated synthetic data after codegen, before serve.

    Discovers all Pydantic BaseModel subclasses in the generated service, asks the LLM
    to generate realistic records, validates each record with the live model, and inserts
    passing records into MongoDB.

    Examples:
      monkeypatched make seed todo
      monkeypatched make seed work-order --count 10
      monkeypatched make seed todo --mongo-uri mongodb://localhost:27017 --db todo_dev
    """
    import asyncio as _asyncio
    import json as _json

    svc_slug = service_type.lower().replace(" ", "-").replace("_", "-")
    ctx: dict = {
        "service_slug": svc_slug,
        "output_dir": str(
            __import__("pathlib").Path(output_dir)
            if __import__("pathlib").Path(output_dir).is_absolute()
            else __import__("pathlib").Path(__file__).parent.parent.parent / output_dir
        ),
        "seed_count": seed_count,
        "question_source": "codegen",  # never route through simulator
    }
    if mongo_uri:
        ctx["mongo_uri"] = mongo_uri
    if db_name:
        ctx["db_name"] = db_name

    SeedAgent = _broca("agents.seed_agent.SeedAgent")
    agent = SeedAgent()
    result = _asyncio.run(agent.handle(ctx))
    p = _payload(result)

    if json_output:
        typer.echo(_json.dumps(p, indent=2, default=str))
        return

    if p.get("seeded"):
        typer.echo(f"\nSeeded  : {svc_slug} → {p.get('db', svc_slug)}")
        typer.echo(f"Total   : {p.get('total_inserted', 0)} records across {len(p.get('collections', {}))} collections")
        for col, n in (p.get("collections") or {}).items():
            typer.echo(f"  {col}: {n} records")
    else:
        typer.echo(
            f"Seed failed: {p.get('error') or p.get('reason', 'no records inserted')}",
            err=True,
        )
        raise typer.Exit(1)


def soma_serve(
    service_type: str = typer.Argument(
        ...,
        help="Generated service to start, e.g. todo.",
    ),
    output_dir: str = typer.Option(
        "generated",
        "--output-dir",
        "-o",
        help="Root directory containing generated services.",
    ),
    port: int = typer.Option(8090, "--port", "-p", help="Port to bind the service."),
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind."),
    background: bool = typer.Option(True, "--background/--foreground", help="Run detached (default: yes)."),
    restart: bool = typer.Option(False, "--restart", "-r", help="Kill the running instance then start fresh."),
):
    """Start a generated DDD service with uvicorn.

    Writes the PID to ~/.monkeybrain/pids/<service>.pid.
    Use --restart to kill an existing instance and relaunch.

    Examples:
      monkeypatched make serve todo
      monkeypatched make serve todo --restart
      monkeypatched make serve todo --port 8091 --foreground
    """
    import asyncio
    import os
    import signal
    from pathlib import Path

    repo_root = Path(__file__).parent.parent.parent
    svc_slug = service_type.lower().replace(" ", "-").replace("_", "-")
    out_base = Path(output_dir) if Path(output_dir).is_absolute() else repo_root / output_dir

    # ── Kill existing instance if --restart ───────────────────────────────────
    if restart:
        pid_file = Path.home() / ".monkeybrain" / "pids" / f"{svc_slug}.pid"
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, signal.SIGTERM)
                typer.echo(f"Stopped {svc_slug} (PID {pid})")
                pid_file.unlink(missing_ok=True)
            except ProcessLookupError:
                typer.echo(f"PID {pid} not found — process already stopped.")
                pid_file.unlink(missing_ok=True)
            except Exception as e:
                typer.echo(f"Could not stop {svc_slug}: {e}", err=True)
        else:
            typer.echo(f"No running instance found for {svc_slug} — starting fresh.")

    ServeAgent = _broca("agents.serve_agent.ServeAgent")
    agent = ServeAgent()
    result = asyncio.run(
        agent.handle(
            {
                "service_slug": svc_slug,
                "output_dir": str(out_base),
                "port": port,
                "host": host,
                "background": background,
            }
        )
    )
    p = _payload(result)
    running = p.get("running", False)

    if running:
        action = "Restarted" if restart else "Starting"
        typer.echo(f"{action} {svc_slug} on {host}:{port}")
        if p.get("pid"):
            typer.echo(f"PID: {p['pid']}  ({p.get('pid_file', '')})")
        typer.echo(f"http://{host}:{port}/docs")
    else:
        typer.echo(p.get("error", "ServeAgent failed"), err=True)
        raise typer.Exit(1)


def soma_client(
    service_type: str = typer.Argument(
        ...,
        help="Service to generate a client for, e.g. todo, work-order.",
    ),
    output_dir: str = typer.Option(
        "generated",
        "--output-dir",
        "-o",
        help="Root directory containing generated services (also where client is written).",
    ),
    base_url: str = typer.Option(
        "http://localhost:8090",
        "--base-url",
        "-u",
        help="Base URL the generated client will call.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON summary."),
):
    """Generate a typed Python API client for a generated DDD service.

    Reads the service's API layer (router + schemas), then uses CodeGenAgent
    with the api-client somatic chart to produce a fully-typed httpx client.
    Output goes to <output-dir>/<service>-client/.

    Client invariants (from api-client chart):
      · All method signatures are fully typed
      · No raw HTTP objects exposed to the caller
      · HTTP errors mapped to domain exceptions
      · Auth (JWT Bearer) injected at construction, not per call

    Examples:
      monkeypatched make client todo
      monkeypatched make client todo --base-url http://api.example.com/v1
    """
    import asyncio
    import json as _json
    from pathlib import Path

    repo_root = Path(__file__).parent.parent.parent
    svc_slug = service_type.lower().replace(" ", "-").replace("_", "-")
    out_base = Path(output_dir) if Path(output_dir).is_absolute() else repo_root / output_dir

    ClientGenAgent = _broca("agents.client_gen_agent.ClientGenAgent")
    agent = ClientGenAgent()
    result = asyncio.run(
        agent.handle(
            {
                "service_slug": svc_slug,
                "output_dir": str(out_base),
                "base_url": base_url,
            }
        )
    )
    p = _payload(result)
    generated = p.get("generated", False)
    written = p.get("files", [])

    if json_output:
        typer.echo(_json.dumps(p, indent=2, default=str))
    elif generated:
        typer.echo(f"Generated {len(written)} files → {p.get('client_dir', svc_slug + '-client')}/")
        for f in written:
            typer.echo(f"  {f}")
    else:
        typer.echo(p.get("error", "ClientGenAgent failed"), err=True)
        for obs in _observations(result):
            typer.echo(f"  {obs}", err=True)
        raise typer.Exit(1)


def soma_chart_from_client(
    service_type: str = typer.Argument(
        ...,
        help="Service whose client to charter, e.g. todo.",
    ),
    output_dir: str = typer.Option(
        "generated",
        "--output-dir",
        "-o",
        help="Root directory where <service>-client/ lives.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON result."),
):
    """Run ClientCharterAgent to convert a generated API client into a SOMA chart.

    Reads <output-dir>/<service>-client/ (client.py, models.py, exceptions.py),
    calls the LLM via the client_charter workload spec, and writes:
      somatic/charts/<service>-client/values.yaml

    Examples:
      monkeypatched make chart-from-client todo
      monkeypatched make chart-from-client todo --output-dir generated/services
    """
    import asyncio
    import json as _json
    import sys
    from pathlib import Path

    repo_root = Path(__file__).parent.parent.parent
    sys.path.insert(0, str(repo_root / "src"))

    svc_slug = service_type.lower().replace(" ", "-").replace("_", "-")
    out_base = Path(output_dir) if Path(output_dir).is_absolute() else repo_root / output_dir
    client_dir = out_base / f"{svc_slug}-client"

    if not client_dir.exists():
        typer.echo(
            f"Client directory not found: {client_dir}\nRun: monkeypatched make client {svc_slug}",
            err=True,
        )
        raise typer.Exit(1)

    if not json_output:
        typer.echo(f"\n── ClientCharterAgent: {svc_slug} ──────────────────────────")
        typer.echo(f"Client dir: {client_dir.relative_to(repo_root)}\n")

    ClientCharterAgent = _broca("agents.client_charter.ClientCharterAgent")
    agent = ClientCharterAgent()
    result = asyncio.run(
        agent.handle(
            {
                "client_dir": str(client_dir),
                "service_slug": svc_slug,
            }
        )
    )

    p = _payload(result)
    charted = p.get("charted", False)
    chart_path = p.get("chart_path", "")
    observations = _observations(result)

    if json_output:
        typer.echo(_json.dumps(p, indent=2, default=str))
    else:
        if charted:
            _cp = Path(chart_path) if chart_path else None
            rel = _cp.relative_to(repo_root) if (_cp and _cp.is_relative_to(repo_root)) else chart_path
            typer.echo(f"Chart written: {rel}")
            typer.echo(f"\nNext: monkeypatched make compile-agent {svc_slug}")
        else:
            for obs in observations:
                typer.echo(f"  {obs}", err=True)

    if not charted:
        raise typer.Exit(1)


def soma_compile_agent(
    service_type: str = typer.Argument(
        ...,
        help="Service whose client capability chart to compile, e.g. todo.",
    ),
    output_dir: str = typer.Option(
        "somatic/compiled",
        "--output-dir",
        "-o",
        help="Directory to write the compiled .prompt.md.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON summary."),
):
    """Compile the client capability chart into an executable .prompt.md.

    Reads somatic/charts/<service>-client/values.yaml (a capability chart produced
    by chart-from-client) and compiles it into a structured prompt that describes
    how an orchestrator should use the capability. Operations are rendered as
    numbered steps. Invariants become constraints in the front-matter.

    Examples:
      monkeypatched make compile-agent todo
      monkeypatched make compile-agent todo --output-dir somatic/compiled
    """
    import asyncio
    import json as _json
    from pathlib import Path

    repo_root = Path(__file__).parent.parent.parent
    svc_slug = service_type.lower().replace(" ", "-").replace("_", "-")
    out_base = Path(output_dir) if Path(output_dir).is_absolute() else repo_root / output_dir

    CompileAgentAgent = _broca("agents.compile_agent_agent.CompileAgentAgent")
    agent = CompileAgentAgent()
    result = asyncio.run(
        agent.handle(
            {
                "service_slug": svc_slug,
                "output_dir": str(out_base),
            }
        )
    )
    p = _payload(result)
    compiled = p.get("compiled", False)
    prompt_path_str = p.get("prompt_path", "")

    if json_output:
        typer.echo(_json.dumps(p, indent=2, default=str))
    elif compiled:
        _pp = Path(prompt_path_str)
        rel = _pp.relative_to(repo_root) if _pp.is_absolute() else _pp
        typer.echo(f"Prompt:     {rel}")
        typer.echo(f"Operations: {p.get('operations', 0)}")
        typer.echo(f"\nNext: monkeypatched make govern {rel}")
    else:
        typer.echo(p.get("error", "CompileAgentAgent failed"), err=True)
        for obs in _observations(result):
            typer.echo(f"  {obs}", err=True)
        raise typer.Exit(1)


def soma_create_agent(
    service_type: str = typer.Argument(
        ...,
        help="Service to create an agent for, e.g. todo.",
    ),
    base_url: str = typer.Option(
        "",
        "--base-url",
        "-u",
        help="Override the base URL from the chart.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON spec."),
):
    """Create a ClientCapabilityAgent from the generated capability chart.

    Reads somatic/charts/<service>-client/values.yaml, instantiates a
    ClientCapabilityAgent, and registers it into the Broca registry.
    The agent discovers its ICapability from the runtime registry
    (registered via SomaticCompiler.register_capabilities) and falls back
    to direct httpx calls using the chart's endpoint + operations.

    Examples:
      monkeypatched make create-agent todo
      monkeypatched make create-agent todo --base-url http://localhost:9000
    """
    import json as _json
    import sys
    from pathlib import Path

    repo_root = Path(__file__).parent.parent.parent
    sys.path.insert(0, str(repo_root / "src"))

    svc_slug = service_type.lower().replace(" ", "-").replace("_", "-")
    chart_path = repo_root / "somatic" / "charts" / f"{svc_slug}-client" / "values.yaml"

    if not chart_path.exists():
        typer.echo(
            f"Capability chart not found: {chart_path}\nRun: monkeypatched make chart-from-client {svc_slug}",
            err=True,
        )
        raise typer.Exit(1)

    if not json_output:
        typer.echo(f"\n── Creating agent from chart: {svc_slug}-client ────────────")
        typer.echo(f"Chart: {chart_path.relative_to(repo_root)}\n")

    ClientCapabilityAgent = _broca("agents.client_capability_agent.ClientCapabilityAgent")

    # Instantiate from chart — reads operations, endpoint, auth
    agent = ClientCapabilityAgent(
        service_slug=svc_slug,
        chart_path=chart_path,
    )

    # Override base_url if provided
    if base_url:
        agent._base_url = base_url.rstrip("/")

    # Register into the live Broca registry
    try:
        get_registry = _broca("registry.get_registry")
        registry = get_registry()
        registry.register(agent)
        if not json_output:
            typer.echo(f"Registered in Broca registry: {agent.agent_type}")
    except Exception as e:
        if not json_output:
            typer.echo(f"Registry registration skipped: {e}")

    spec = agent.spec()

    if not json_output:
        typer.echo(f"\nAgent type: {spec['agent_type']}")
        typer.echo(f"Base URL:   {spec['base_url']}")
        typer.echo(f"Auth:       {spec['auth_type']}")
        typer.echo(f"Operations ({len(spec['operations'])}):")
        for name, op in spec["operations"].items():
            auth_flag = " 🔒" if op.get("auth_required") else ""
            typer.echo(f"  {op.get('method', 'GET'):6} {op.get('path', ''):<30} → {name}{auth_flag}")
        op_example = "list_" + svc_slug.replace("-", "_") + "s"
        agent_type = spec["agent_type"]
        typer.echo(f"\nTo invoke: monkeypatched query 'run {agent_type} operation={op_example}'")

    if json_output:
        typer.echo(_json.dumps(spec, indent=2, default=str))


def soma_test_service(
    service_type: str = typer.Argument(
        ...,
        help="Generated service to test, e.g. todo.",
    ),
    output_dir: str = typer.Option(
        "generated",
        "--output-dir",
        "-o",
        help="Root directory containing generated services.",
    ),
    max_loops: int = typer.Option(
        3,
        "--max-loops",
        "-n",
        help="Maximum correction-loop iterations when tests fail (0 = no correction).",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON summary."),
):
    """Run static analysis and unit tests; auto-correct failures via CodeGenAgent.

    On each failure the correction loop asks CodeGenAgent to fix the errors,
    writes the patched files back, and re-runs tests — up to --max-loops times.
    Saves a timestamped error log to ~/.monkeybrain/logs/test_<service>_<ts>.log.

    Examples:
      monkeypatched make test-service todo
      monkeypatched make test-service todo --max-loops 5
      monkeypatched make test-service todo --max-loops 0   # no correction
    """
    import asyncio
    import json as _json
    from pathlib import Path

    repo_root = Path(__file__).parent.parent.parent
    svc_slug = service_type.lower().replace(" ", "-").replace("_", "-")
    out_base = Path(output_dir) if Path(output_dir).is_absolute() else repo_root / output_dir

    if not json_output:
        typer.echo(f"\n── Testing service: {svc_slug} ──────────────────────────────")
        typer.echo(f"Max correction loops: {max_loops}\n")

    TestServiceAgent = _broca("agents.test_service_agent.TestServiceAgent")
    agent = TestServiceAgent()
    result = asyncio.run(
        agent.handle(
            {
                "service_slug": svc_slug,
                "output_dir": str(out_base),
                "max_loops": max_loops,
            }
        )
    )
    p = _payload(result)
    all_passed = p.get("all_passed", False)

    if json_output:
        typer.echo(_json.dumps(p, indent=2, default=str))
    else:
        sa = p.get("static_analysis", {})
        ut = p.get("unit_tests", {})
        typer.echo("\n── Final results ────────────────────────────────────────────")
        typer.echo(f"Static analysis: {'clean' if sa.get('clean') else 'issues'}")
        if not sa.get("clean"):
            typer.echo(sa.get("output", "")[-400:])
        typer.echo(f"Unit tests:      {'passed' if ut.get('passed') else 'failed'}")
        if not ut.get("passed"):
            typer.echo(ut.get("output", "")[-600:])
        typer.echo(f"\nResult: {'PASSED' if all_passed else 'FAILED'}")
        if p.get("error_log"):
            typer.echo(f"Error log: {p['error_log']}")

    if not all_passed:
        raise typer.Exit(1)


def soma_plan(
    goal: str = typer.Argument(..., help="Goal description, e.g. 'build a todo service with MongoDB'."),
    service: str = typer.Option("", "--service", "-s", help="Service name if relevant (e.g. todo)."),
    output: str = typer.Option(
        "",
        "--output",
        "-o",
        help="Save plan YAML to this path (default: ~/.monkeybrain/plans/<slug>_<ts>.yaml).",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON representation of the plan."),
):
    """Generate a structured YAML execution plan from a free-text goal using PlannerAgent.

    The plan maps your goal to a sequence of monkeypatched make commands with dependency
    ordering. Save it with --output or pipe to execute-plan.

    Examples:
      monkeypatched make plan "build a todo service with MongoDB" --service todo
      monkeypatched make plan "validate compliance for todo" --service todo -o my-plan.yaml
    """
    import asyncio as _asyncio
    import datetime as _dt
    import json as _json

    PlannerAgent = _broca("agents.planner.PlannerAgent")
    agent = PlannerAgent()
    context = {"goal": goal, "service": service}
    result = _asyncio.run(agent.handle(context))
    payload = result.payload if hasattr(result, "payload") else result
    plan = payload.get("plan") if isinstance(payload, dict) else {}

    if not plan:
        typer.echo("PlannerAgent returned no plan.", err=True)
        raise typer.Exit(1)

    import yaml as _yaml

    plan_yaml = _yaml.dump(plan, default_flow_style=False, sort_keys=False, allow_unicode=True)

    # Determine output path
    plans_dir = Path.home() / ".monkeybrain" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = (service or goal[:20]).replace(" ", "_").replace("/", "-")
    default_path = plans_dir / f"{slug}_{ts}.yaml"
    out_path = Path(output) if output else default_path

    out_path.write_text(plan_yaml)
    typer.echo(f"Plan saved: {out_path}\n")
    typer.echo(plan_yaml)

    if json_output:
        typer.echo(_json.dumps(plan, indent=2, default=str))


def soma_execute_plan(
    plan_file: str = typer.Argument(..., help="Path to the plan YAML file generated by 'make plan'."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print steps without executing them."),
    continue_on_error: bool = typer.Option(
        True,
        "--continue-on-error/--stop-on-error",
        help="Continue past failures and report at end (default: on).",
    ),
    auto_fix: bool = typer.Option(
        True,
        "--auto-fix/--no-auto-fix",
        help="Run loss-driven repair on step failure (default: on).",
    ),
    from_step: int = typer.Option(
        1,
        "--from-step",
        "-s",
        help="Resume from this step number (skip earlier steps as already done).",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print JSON execution report."),
):
    """Execute a YAML plan with loss-driven repair convergence loop.

    Instead of Test → Fix, uses:
        Simulation Loss → Decompose → Solver → Repair → Recompute → Converge

    Examples:
      monkeypatched make execute-plan my-plan.yaml
      monkeypatched make execute-plan my-plan.yaml --dry-run
      monkeypatched make execute-plan my-plan.yaml --no-auto-fix
    """
    import asyncio as _asyncio
    import json as _json
    import time

    ExecutorAgent = _broca("agents.executor.ExecutorAgent")

    plan_path = Path(plan_file).expanduser()
    if not plan_path.exists():
        typer.echo(f"Error: plan file not found: {plan_path}", err=True)
        raise typer.Exit(1)

    agent = ExecutorAgent()
    context = {
        "plan_file": str(plan_path),
        "dry_run": dry_run,
        "continue_on_error": continue_on_error,
        "auto_fix": auto_fix,
        "from_step": from_step,
    }
    t0 = time.monotonic()
    result = _asyncio.run(agent.handle(context))
    elapsed = (time.monotonic() - t0) * 1000
    payload = result.payload if hasattr(result, "payload") else result

    steps = payload.get("steps", [])
    all_ok = payload.get("all_ok", False)
    mode = "DRY RUN" if dry_run else "EXECUTED"

    # Build engineering report
    if not json_output:
        typer.echo("")
        typer.echo(f"── {mode} {len(steps)} steps ──")
        for s in steps:
            status = s.get("status", "unknown")
            icon = {"ok": "✓", "healed": "⟳", "failed": "✗"}.get(status, "?")
            name = s.get("name", "?")
            detail = s.get("detail", "")
            typer.echo(f"  {icon}  {name} {detail}")

        typer.echo("")

    # Engineering telemetry report

    report = EngineeringReport()
    report.runtime.latency_ms = elapsed
    report.quality.files_generated = sum(1 for s in steps if s.get("name") == "codegen")
    report.quality.files_patched = sum(1 for s in steps if s.get("status") == "healed")
    report.quality.tests_passed = sum(1 for s in steps if s.get("name") == "test_service" and s.get("status") == "ok")
    report.quality.tests_healed = sum(
        1 for s in steps if s.get("name") == "test_service" and s.get("status") == "healed"
    )
    report.repair.iterations = sum(1 for s in steps if s.get("name") == "fixit")
    report.repair.success_rate = 1.0 if all_ok else 0.5

    if not json_output:
        typer.echo(report.format())
    else:
        typer.echo(
            _json.dumps(
                {**payload, "engineering_report": report.to_dict()},
                indent=2,
                default=str,
            )
        )


def soma_run(
    service: str = typer.Argument(..., help="Service name to build, e.g. work-order, todo."),
    goal: str = typer.Option(
        "",
        "--goal",
        "-g",
        help="Override goal text (default: 'build <service> service').",
    ),
    no_simulate: bool = typer.Option(
        False,
        "--no-simulate",
        help="Omit world-model simulation step (faster iteration).",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan only — print steps, do not execute."),
    from_step: int = typer.Option(1, "--from-step", "-s", help="Resume execution from this step number."),
    continue_on_error: bool = typer.Option(True, "--continue-on-error/--stop-on-error"),
    auto_fix: bool = typer.Option(True, "--auto-fix/--no-auto-fix"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Plan and execute the full pipeline for a service in one command.

    Generates a 14-step execution plan via PlannerAgent, saves it to
    ~/.monkeybrain/plans/, then immediately executes it via ExecutorAgent.

    Examples:
      monkeypatched make run work-order
      monkeypatched make run todo --no-simulate
      monkeypatched make run work-order --dry-run
      monkeypatched make run work-order --from-step 4
      monkeypatched make run work-order --goal "rebuild work-order with audit trail"
    """
    import asyncio as _asyncio
    import datetime as _dt
    import json as _json

    import yaml as _yaml

    svc = service.lower().replace(" ", "-").replace("_", "-")
    resolved_goal = goal.strip() or f"build {svc} service"
    simulate_enabled = not no_simulate

    # ── 1. Plan ───────────────────────────────────────────────────────────────
    if not json_output:
        typer.echo(f"\n── Planning: {resolved_goal} ──────────────────────────────")

    PlannerAgent = _broca("agents.planner.PlannerAgent")
    planner = PlannerAgent()
    plan_result = _asyncio.run(
        planner.handle(
            {
                "goal": resolved_goal,
                "service": svc,
                "simulate_enabled": simulate_enabled,
            }
        )
    )
    plan_payload = _payload(plan_result)
    plan = plan_payload.get("plan")

    if not plan:
        typer.echo("PlannerAgent returned no plan.", err=True)
        raise typer.Exit(1)

    plan_yaml = _yaml.dump(plan, default_flow_style=False, sort_keys=False, allow_unicode=True)
    plans_dir = Path.home() / ".monkeybrain" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    plan_path = plans_dir / f"{svc}_{ts}.yaml"
    plan_path.write_text(plan_yaml)

    steps = plan.get("steps", [])
    if not json_output:
        typer.echo(f"Plan saved: {plan_path}  ({len(steps)} steps)\n")
        for s in steps:
            marker = "  " if s["id"] < from_step else "▶ " if s["id"] == from_step else "  "
            skip = " [skip]" if s["id"] < from_step else ""
            typer.echo(f"  {marker}{s['id']:2d}. {s['name']:<22} {s['command']}{skip}")
        typer.echo("")

    if dry_run:
        if json_output:
            typer.echo(
                _json.dumps(
                    {"plan": plan, "plan_file": str(plan_path), "dry_run": True},
                    indent=2,
                    default=str,
                )
            )
        return

    # ── 2. Execute ────────────────────────────────────────────────────────────
    if not json_output:
        typer.echo(f"── Executing plan ({'from step ' + str(from_step) if from_step > 1 else 'all steps'}) ──")

    ExecutorAgent = _broca("agents.executor.ExecutorAgent")
    executor = ExecutorAgent()
    exec_result = _asyncio.run(
        executor.handle(
            {
                "plan_file": str(plan_path),
                "dry_run": False,
                "continue_on_error": continue_on_error,
                "auto_fix": auto_fix,
                "from_step": from_step,
            }
        )
    )
    exec_payload = _payload(exec_result)

    if json_output:
        typer.echo(
            _json.dumps(
                {
                    "plan": plan,
                    "plan_file": str(plan_path),
                    "execution": exec_payload,
                },
                indent=2,
                default=str,
            )
        )
        return

    steps_run = exec_payload.get("steps_run", [])
    passed = sum(1 for s in steps_run if s.get("status") == "passed")
    failed = sum(1 for s in steps_run if s.get("status") == "failed")
    typer.echo(f"\n── Result: {passed} passed, {failed} failed ──────────────────────────")
    for s in steps_run:
        icon = "✓" if s.get("status") == "passed" else "✗"
        typer.echo(f"  {icon} {s.get('id', '?'):2}. {s.get('name', '')}")
    if exec_payload.get("error"):
        typer.echo(f"\nError: {exec_payload['error']}", err=True)
    if failed:
        raise typer.Exit(1)


def soma_discover(
    intent: str = typer.Argument(..., help="Natural language intent to discover a specification from."),
    provider: str = typer.Option("auto", "--provider", "-p", help="LLM provider: auto, ollama, claude"),
    json_output: bool = typer.Option(False, "--json", help="Print JSON output"),
    spec: bool = typer.Option(False, "--spec", help="Run specification discovery only"),
    plan: bool = typer.Option(False, "--plan", help="Generate and display the execution graph"),
    simulate: bool = typer.Option(False, "--simulate", help="Simulate intent via simulation runtime"),
    observe: bool = typer.Option(False, "--observe", help="Annotate graph with runtime metrics"),
    log: bool = typer.Option(False, "--log", "--logs", help="Show recent server logs after the operation"),
    execute: bool = typer.Option(False, "--execute", help="Plan and execute end-to-end"),
    act: bool = typer.Option(False, "--act", help="Execute directly via capability classification"),
    diff: bool = typer.Option(False, "--diff", help="Compare simulation graph vs execution graph"),
    learn: bool = typer.Option(False, "--learn", help="Update Q-table from observations"),
    publish: bool = typer.Option(False, "--publish", help="Publish the canonical graph"),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Run the full pipeline: discover → plan → execute → learn → publish → engineer",
    ),
    max_epochs: int = typer.Option(10, "--max-epochs", help="Max learning epochs before convergence"),
    convergence: float = typer.Option(0.1, "--convergence", help="Loss threshold for convergence"),
    batch_size: int = typer.Option(10, "--batch-size", help="Number of iterations per batch for learning"),
    epochs: int = typer.Option(5, "--epochs", help="Number of cycles per batch for learning"),
):
    """Autonomous Specification Discovery — the full Cognitive OS pipeline.

    Intent → Discover → Plan → Execute → Learn → Converged? → Publish → Engineer

    Each flag runs one phase; flags compose and run in pipeline order.
    --auto runs all phases in sequence.

    Examples:
      monkeypatched discover "Build me a todo app" --spec
      monkeypatched discover "Build me a todo app" --plan
      monkeypatched discover "Build me a todo app" --plan --observe --log
      monkeypatched discover "Build me a todo app" --execute
      monkeypatched discover "Add a task" --act
      monkeypatched discover "Build me a todo app" --auto
    """
    import asyncio as _asyncio
    import json as _json

    if provider != "auto":
        os.environ["SPEC_DISCOVERY_PROVIDER"] = provider

    # ── Load persisted state if available ─────────────────────────────────
    state_path = Path.home() / ".monkeybrain" / "discover_state"
    state_file = state_path / f"{_hash_intent(intent)}.json"

    def _load_state() -> dict:
        if state_file.exists():
            return _json.loads(state_file.read_text())
        return {}

    def _save_state(state: dict) -> None:
        state_path.mkdir(parents=True, exist_ok=True)
        state_file.write_text(_json.dumps(state, indent=2, default=str))

    state = _load_state()
    converged = False  # set for real in the LEARN block below; defaulted here
    # so ENGINEER's convergence check can't NameError if a future reorder
    # ever makes it reachable without LEARN having run first.

    # ── LOGS (--log) — rendered at the end of whatever phases ran ─────────
    # Observability panel fetched during *this* invocation. Not read from
    # `state` — that persists across runs and would show stale logs.
    _fresh_obs: dict = {}

    def _show_logs() -> None:
        """Fetch and display recent server logs from the observability panel."""
        data = _fresh_obs or _brain_get("/api/v1/agentos/observability", _brain_url(), json_output=False, quiet=True)
        if not data:
            typer.echo("  Log fetch failed — is the runtime running?")
            return
        typer.echo("")
        typer.echo("═══ LOGS ═══")
        logs = data.get("logs", [])
        if not logs:
            typer.echo("  No logs available")
            return
        for entry in logs[-20:]:
            level = entry.get("severity", "info").upper()
            component = entry.get("component", "")
            typer.echo(f"  [{level:5}] {component:24} {entry.get('message', '')}")

    # ── ACT (--act) — execute directly via capability classification ──────
    if act:
        from repl import persona

        typer.echo(f"Classifying capability for: {intent}")

        base_url = _brain_url()
        result = _brain_post(
            "/api/v1/agentos/execute-direct",
            base_url,
            {"question": intent},
            json_output=False,
            quiet=True,
        )
        if not result:
            # A transport failure is recoverable — not a capability blocker.
            typer.echo(persona.note("The runtime did not respond. Execution was not attempted."))
            raise typer.Exit(1)

        if json_output:
            typer.echo(_json.dumps(result, indent=2))
            return

        # Extract from CapabilityResult format
        answer = result.get("response", {}).get("answer", "") or result.get("answer", "")
        metadata = result.get("metadata", {})
        capability = metadata.get("capability", "unknown")
        executable = metadata.get("executable", True)

        typer.echo("")
        typer.echo(persona.state("Capability", capability))
        typer.echo(persona.state("Executable", "yes" if executable else "no"))
        typer.echo(persona.state("Latency", f"{result.get('elapsed_ms', 0):.0f} ms"))
        typer.echo("")

        if executable:
            typer.echo(persona.state("Result", answer[:300]))
        else:
            # No capability backs this request. The answer stands, advisory only.
            if answer:
                typer.echo(persona.advisory(answer[:600], grounded=bool(metadata.get("grounded"))))
                typer.echo("")
            if metadata.get("unimplemented", True):
                # Broca and every configured provider were conclusively
                # checked — nothing exists. A genuine execution blocker.
                typer.echo(persona.refusal(metadata.get("blocker", "")))
            else:
                # Resolution was inconclusive (a provider was unreachable) —
                # this may resolve on retry, so it is not the HAL refusal.
                typer.echo(persona.note(metadata.get("blocker", "capability availability could not be confirmed")))

    # ── DISCOVER (--spec) ────────────────────────────────────────────────
    async def _discover():
        SpecificationDiscoveryAgent = _broca("agents.specification_discovery_agent.SpecificationDiscoveryAgent")
        agent = SpecificationDiscoveryAgent()
        return await agent.handle({"question": intent})

    if spec or auto:
        result = _asyncio.run(_discover())
        payload = result.payload
        if not payload.get("discovered"):
            typer.echo("Discovery failed.", err=True)
            raise typer.Exit(1)
        state["discovery"] = payload["discovery"]
        state["specification"] = payload["specification"]
        _save_state(state)
        if not auto:
            typer.echo("═══ SPEC ═══")
            typer.echo(f"  Goal: {payload['discovery'].get('goal', intent)}")
            typer.echo(f"  Agents: {', '.join(payload['discovery'].get('agents', []))}")

    # ── PLAN (--plan) ────────────────────────────────────────────────────
    def _plan():
        """Call POST /api/v1/agentos/plan via the runtime API."""
        base_url = _brain_url()
        body = {"question": intent, "target": "execute"}
        result = _brain_post("/api/v1/agentos/plan", base_url, body, json_output=False, quiet=True)
        if not result:
            typer.echo("  Plan request failed — is the runtime running?")
            return None
        state["plan_response"] = result
        _save_state(state)
        return result

    async def _resolve_agent(agent_name: str, provider_registry, auto_agent, spec_data, discovery) -> str:
        """Resolve an agent via cascade: local → OpenClaw → n8n → auto-gen.

        Found agents from OpenClaw/n8n are cached in the local registry
        so subsequent lookups skip the network call.

        Returns: "local" | "openclaw" | "n8n" | "auto_generated" | "not_found"
        """
        agent_lower = agent_name.lower().replace("-", " ").replace("_", " ")

        # 1. Check local Broca registry
        try:
            from broca.registry import get_registry

            registry = get_registry()
            if registry.discover(agent_name) is not None:
                logger.info("[plan] %s found in local registry", agent_name)
                return "local"
            # Fuzzy match
            for registered in getattr(registry, "_agents", {}).values():
                reg_type = getattr(registered, "agent_type", "").lower().replace("-", " ").replace("_", " ")
                if agent_lower in reg_type or reg_type in agent_lower:
                    logger.info(
                        "[plan] %s fuzzy-matched to local %s",
                        agent_name,
                        getattr(registered, "agent_type", ""),
                    )
                    return "local"
        except Exception:
            pass

        # 2. Check OpenClaw
        if provider_registry:
            try:
                openclaw = provider_registry.get_provider("openclaw")
                if openclaw:
                    oc_agents = await openclaw.discover_agents(query=agent_name)
                    for oc in oc_agents:
                        oc_name = oc.get("name", "").lower().replace("-", " ").replace("_", " ")
                        if agent_lower in oc_name or oc_name in agent_lower:
                            logger.info(
                                "[plan] %s found in OpenClaw: %s",
                                agent_name,
                                oc.get("name"),
                            )
                            _cache_provider_agent(agent_name, oc, "openclaw")
                            return "openclaw"
            except Exception:
                pass

        # 3. Check n8n
        if provider_registry:
            try:
                n8n = provider_registry.get_provider("n8n")
                if n8n:
                    n8n_agents = await n8n.discover_agents(query=agent_name)
                    for n in n8n_agents:
                        n_name = n.get("name", "").lower().replace("-", " ").replace("_", " ")
                        if agent_lower in n_name or n_name in agent_lower:
                            logger.info("[plan] %s found in n8n: %s", agent_name, n.get("name"))
                            _cache_provider_agent(agent_name, n, "n8n")
                            return "n8n"
            except Exception:
                pass

        # 4. Auto-generate (last resort)
        try:
            logger.info("[plan] %s not found anywhere — auto-generating", agent_name)
            r = await auto_agent.handle(
                {
                    "agent_name": agent_name,
                    "agent_description": agent_name,
                    "specification": spec_data,
                    "discovery": discovery,
                }
            )
            if r.payload.get("generated"):
                return "auto_generated"
        except Exception:
            pass

        return "not_found"

    def _cache_provider_agent(agent_name: str, provider_agent: dict, provider: str) -> None:
        """Cache a provider-discovered agent in the local Broca registry."""
        try:
            from broca.agents.provider_proxy import ProviderProxyAgent
            from broca.registry import get_registry

            registry = get_registry()
            agent_info = {
                "name": agent_name,
                "description": provider_agent.get("description", f"{provider} agent: {agent_name}"),
                "provider": provider,
            }
            proxy = ProviderProxyAgent(agent_info, provider_name=provider)
            registry.register(proxy)
            logger.info("[plan] Cached %s from %s in local registry", agent_name, provider)
        except Exception as e:
            logger.debug("[plan] Failed to cache %s: %s", agent_name, e)

    if plan or auto:
        gm = _plan()
        if not auto and gm:
            from repl.ascii_graph import render_ascii_graph

            resolved = state.get("resolved", {})
            graph = gm.get("graph", {}) if isinstance(gm, dict) else {}
            nodes = graph.get("nodes", [])
            edges = graph.get("edges", [])
            execution_order = graph.get("execution_order", [])
            print(render_ascii_graph(nodes, edges, execution_order, resolved=resolved, title="PLAN"))
            features = state.get("features", [])
            if features:
                typer.echo(f"\n  Features: {len(features)}")
                for f in features:
                    typer.echo(f"    - {f}")

    # ── SIMULATE (--simulate) ────────────────────────────────────────────
    def _simulate_fn():
        """Call POST /api/v1/agentos/simulate via the runtime API."""
        plan_response = state.get("plan_response")
        if not plan_response:
            typer.echo("  No plan response — run --plan first.")
            return None
        base_url = _brain_url()
        result = _brain_post(
            "/api/v1/agentos/simulate",
            base_url,
            plan_response,
            json_output=False,
            quiet=True,
        )
        if not result:
            typer.echo("  Simulate request failed — is the runtime running?")
            return None
        state["simulate_response"] = result
        _save_state(state)
        return result

    if simulate or auto:
        result = _simulate_fn()
        if not auto and result:
            from repl.ascii_graph import render_ascii_simulate_graph

            graph = result.get("graph", result.get("execution_graph", {}))
            nodes = graph.get("nodes", []) if isinstance(graph, dict) else []
            edges = graph.get("edges", []) if isinstance(graph, dict) else []
            execution_order = graph.get("execution_order", []) if isinstance(graph, dict) else []
            state_data = graph.get("state", {}) if isinstance(graph, dict) else {}
            print(
                render_ascii_simulate_graph(
                    nodes,
                    edges,
                    execution_order,
                    state_data,
                    grounding_score=result.get("grounding_score", 0.0),
                    needs_correction=result.get("needs_correction", False),
                )
            )

    # ── ACT (--act) ──────────────────────────────────────────────────────
    def _act_fn():
        """Call POST /api/v1/agentos/execute via the runtime API."""
        plan_response = state.get("plan_response")
        if not plan_response:
            typer.echo("  No plan response — run --plan first.")
            return None
        base_url = _brain_url()
        headers = _auth_headers()

        # Try streaming execute first
        from repl.execution_monitor import stream_execute

        try:
            result = stream_execute(base_url, plan_response, headers)
        except Exception as e:
            typer.echo(f"  Execute stream failed: {e}")
            return None

        if result.get("_stream_started"):
            # The server already ran the workload; re-POSTing would run it
            # a second time. Report the failure instead of retrying.
            typer.echo(f"  Execute failed mid-stream: {result.get('_stream_error', 'unknown error')}")
            typer.echo("  Not retrying — the workload already ran server-side.")
            return None

        if result.get("failed"):
            typer.echo(f"  Execute failed: {result.get('error', 'unknown error')}")
            return None

        if result and "answer" in result:
            state["execute_response"] = result
            _save_state(state)
            return result

        # Stream never started — safe to fall back to a single blocking POST.
        result = _brain_post(
            "/api/v1/agentos/execute",
            base_url,
            plan_response,
            json_output=False,
            quiet=True,
        )
        if not result:
            typer.echo("  Execute request failed — is the runtime running?")
            return None
        state["execute_response"] = result
        _save_state(state)
        return result

    def _render_execute_output(result: dict):
        """Render the execution result with the monitor display."""
        from repl.execution_monitor import render_execution_result

        output = render_execution_result(result)
        if output:
            typer.echo(output)
        else:
            typer.echo("═══ EXECUTE ═══")
            answer = result.get("answer", "")
            typer.echo(f"  {answer[:200]}")

    if execute or auto:
        result = _act_fn()
        if not auto and result:
            _render_execute_output(result)

    # ── DIFF (--diff) ────────────────────────────────────────────────────
    def _diff_fn():
        from src.monkey_brain.kernel.graph_manager import GraphManager

        gm = GraphManager()
        gm.graph = state.get("graph", {})
        sim_graph = state.get("simulation_graph", {})
        topo_loss = gm._topological_loss(sim_graph)
        return {
            "topological_loss": topo_loss,
            "simulation_graph": sim_graph,
            "execution_graph": gm.graph,
        }

    if diff or auto:
        diff_result = _diff_fn()
        state["diff"] = diff_result
        _save_state(state)
        if not auto:
            typer.echo("")
            typer.echo("═══ DIFF ═══")
            typer.echo(f"  Topological loss: {diff_result['topological_loss']:.3f}")

    # ── LEARN (--learn) ──────────────────────────────────────────────────
    def _learn_fn():
        """Call POST /api/v1/agentos/learn via the runtime API — returns perplexity.

        One call already runs batch_size * epochs iterations server-side
        (api/routes/predict.py's /learn route). max_epochs/convergence are
        an OUTER loop of repeated calls to that — the docstring's own
        "Learn → Converged?" pipeline stage — since the server has no
        convergence concept of its own to forward these into."""
        base_url = _brain_url()
        body = {"batch_size": batch_size, "epochs": epochs}
        result = _brain_post("/api/v1/agentos/learn", base_url, body, json_output=False, quiet=True)
        if not result:
            typer.echo("  Learn request failed — is the runtime running?")
            return None
        state["learn_response"] = result
        _save_state(state)
        return result

    if learn or auto:
        result = None
        converged = False
        outer_epochs_run = 0
        while outer_epochs_run < max_epochs:
            outer_epochs_run += 1
            result = _learn_fn()
            if not result:
                break
            if result.get("final_perplexity", float("inf")) <= convergence:
                converged = True
                break
        if not auto and result:
            typer.echo("")
            typer.echo("═══ LEARN ═══")
            typer.echo(f"  Status: {result.get('status', '?')}")
            typer.echo(f"  Iterations: {result.get('iterations_completed', 0)}/{result.get('total_iterations', 0)}")
            typer.echo(f"  Final perplexity: {result.get('final_perplexity', 0):.4f}")
            typer.echo(f"  Final Q-value: {result.get('final_q_value', 0):.4f}")
            typer.echo(f"  Elapsed: {result.get('elapsed_ms', 0):.0f}ms")
            for entry in result.get("results", []):
                typer.echo(
                    f"    batch={entry['batch']} epoch={entry['epoch']} "
                    f"perplexity={entry['perplexity']:.4f} q={entry['q_value']:.4f}"
                )
            if converged:
                typer.echo(f"  Converged after {outer_epochs_run}/{max_epochs} epoch(s) (perplexity <= {convergence})")
            else:
                typer.echo(f"  Did not converge within {max_epochs} epoch(s) (perplexity > {convergence})")

    # ── OBSERVE (--observe) ──────────────────────────────────────────────
    # Runs after every work phase (simulate / execute / diff / learn) so the
    # metrics and traces it reports include the work this invocation just did,
    # rather than whatever the runtime had accumulated beforehand.
    def _observe_fn():
        """Call GET /api/v1/agentos/observability via the runtime API."""
        base_url = _brain_url()
        result = _brain_get("/api/v1/agentos/observability", base_url, json_output=False, quiet=True)
        if not result:
            typer.echo("  Observability request failed — is the runtime running?")
            return {}
        _fresh_obs.update(result)
        state["observability"] = result
        _save_state(state)
        return result

    if observe or auto:
        result = _observe_fn()
        if not auto and result:
            typer.echo("")
            typer.echo("═══ OBSERVE ═══")
            metrics = result.get("metrics", {})
            counters = metrics.get("counters", {})
            histograms = metrics.get("histograms", {})
            gauges = metrics.get("gauges", {})
            typer.echo(f"  Counters: {len(counters)}")
            typer.echo(f"  Histograms: {len(histograms)}")
            typer.echo(f"  Gauges: {len(gauges)}")
            traces = result.get("tracing", {})
            typer.echo(f"  Traces: {traces.get('total_traces', 0)}")

    # ── PUBLISH (--publish) ──────────────────────────────────────────────
    if publish or auto:
        typer.echo("═══ PUBLISH ═══")
        typer.echo(f"  Canonical graph: {len(state.get('graph', {}).get('nodes', []))} nodes")
        typer.echo(f"  Q-table: {len(state.get('q_table', {}))} entries")
        typer.echo(f"  Loss: {state.get('loss', {}).get('total_loss', 'N/A')}")

    # ── ENGINEER (after --auto converges) ────────────────────────────────
    if auto:
        if not converged:
            typer.echo(
                f"  ⚠ Learn phase did not converge within {max_epochs} epoch(s) "
                f"(perplexity > {convergence}) — proceeding to engineer anyway"
            )
        typer.echo("")
        typer.echo("═══ ENGINEER ═══")
        typer.echo("  Starting SDLC pipeline...")
        from repl.sdlc import run_sdlc_pipeline

        try:
            run_sdlc_pipeline(intent)
        except Exception as e:
            typer.echo(f"  SDLC pipeline failed: {e}", err=True)
            raise typer.Exit(1) from e
        typer.echo("  ✓ Pipeline complete")

    # ── LOGS (--log) — always last, after every phase has rendered ────────
    if log:
        _show_logs()

    # ── No flag: show available phases ───────────────────────────────────
    if not any([spec, plan, simulate, observe, execute, act, diff, learn, publish, log, auto]):
        typer.echo("")
        typer.echo("  --spec      discover specification from intent")
        typer.echo("  --plan      generate execution graph, auto-expand agents")
        typer.echo("  --simulate  simulate intent via the simulation runtime")
        typer.echo("  --execute   plan and execute end-to-end")
        typer.echo("  --act       execute policy-selected path through graph")
        typer.echo("  --observe   annotate graph with runtime metrics")
        typer.echo("  --log       show recent server logs")
        typer.echo("  --diff      compare simulation vs execution graph")
        typer.echo("  --learn     update Q-table from observations")
        typer.echo("  --publish   publish the canonical graph")
        typer.echo("  --auto      run full pipeline: spec → plan → act → learn → diff → publish → engineer")
        typer.echo("")
        typer.echo('  Flags compose: discover "..." --plan --observe --log')

    typer.echo("")


def _hash_intent(intent: str) -> str:
    import hashlib

    # Fingerprint only (short id for a log/cache key), not a security or
    # integrity check -- usedforsecurity=False documents that and silences
    # bandit's weak-hash warning (B324) without switching to a slower hash.
    return hashlib.md5(intent.encode(), usedforsecurity=False).hexdigest()[:12]
