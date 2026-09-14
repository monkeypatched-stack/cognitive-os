"""Soma CLI — Constitutional Compiler & CLI for MonkeyBrain.

Git passthrough: every `git <subcommand>` is available as `soma <subcommand>`.
All git commands operate on the configured spec repo (MONKEYBRAIN_SPEC_REPO env
var, or spec_repo in .soma/config.json, defaulting to the current repository).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import yaml
from pathlib import Path
from typing import Optional

import typer
from jinja2 import Environment
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    name="soma",
    help="Soma — Constitutional Compiler & CLI for MonkeyBrain",
    no_args_is_help=True,
)
console = Console()


def _find_repo_root() -> Path:
    p = Path.cwd()
    for _ in range(10):
        if (p / "somatic").exists():
            return p
        if p.parent == p:
            break
        p = p.parent
    return Path.cwd()


CONSTITUTIONS_DIR = _find_repo_root() / "somatic"


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _helm_to_jinja(template: str) -> str:
    """Convert Helm template syntax to Jinja2."""
    t = template

    # Convert block tags FIRST (before inline expressions)
    # {{- range $name, $sub := .Values.x }}
    t = re.sub(
        r"\{\{\s*-?\s*range\s+\$\w+,\s*\$\w+\s*:=\s*\.Values\.(\S+?)\s*-?\}\}",
        r"{% for key, item in values.\1.items() %}",
        t,
    )
    # {{ $name }} → {{ key }}
    t = re.sub(r"\{\{\s*\$name\s*\}\}", "{{ key }}", t)
    # {{- range .Values.x }} or {{ - range .Values.x }}
    t = re.sub(
        r"\{\{\s*-?\s*range\s+\.Values\.(\S+?)\s*-?\}\}",
        r"{% for item in values.\1 %}",
        t,
    )
    t = re.sub(
        r"\{\{\s*-?\s*range\s+values\.(\S+?)\s*-?\}\}",
        r"{% for item in values.\1 %}",
        t,
    )
    # {{- range item.x }} (nested range inside a for loop)
    t = re.sub(r"\{\{\s*-?\s*range\s+item\.(\S+?)\s*-?\}\}", r"{% for sub in item.\1 %}", t)
    # {{- range .x }} (bare dot nested range — item.x)
    t = re.sub(r"\{\{\s*-?\s*range\s+\.(\S+?)\s*-?\}\}", r"{% for sub in item.\1 %}", t)
    # {{- if .foo }} or {{- if item.foo }}
    t = re.sub(r"\{\{\s*-?\s*if\s+\.(\S+?)\s*-?\}\}", r"{% if item.\1 %}", t)
    t = re.sub(r"\{\{\s*-?\s*if\s+item\.(\S+?)\s*-?\}\}", r"{% if item.\1 %}", t)
    # {{- end }} or {{ end }}
    t = re.sub(r"\{\{\s*-?\s*end\s*-?\}\}", "{% end %}", t)

    # Resolve {% end %} to {% endfor %} or {% endif %} by tracking nesting
    lines = t.split("\n")
    stack = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("{% for"):
            # Detect which variable: "for item in" vs "for sub in"
            if "for sub " in stripped:
                stack.append("sub")
            else:
                stack.append("item")
        elif stripped.startswith("{% if"):
            stack.append("if")
        elif stripped == "{% end %}":
            if stack:
                block_type = stack.pop()
                if block_type == "if":
                    lines[i] = line.replace("{% end %}", "{% endif %}")
                else:
                    lines[i] = line.replace("{% end %}", "{% endfor %}")
    t = "\n".join(lines)

    # Now convert inline {{ ... }} expressions
    def convert_expr(m):
        expr = m.group(1).strip()

        # Remove | default <value>
        expr = re.sub(r"\s*\|\s*default\s+\S+", "", expr)
        # Remove | quote (Helm quoting is handled by YAML output)
        expr = re.sub(r"\s*\|\s*quote", "", expr)
        # Convert Helm | lower | replace " " "-" → Jinja2 filters
        expr = re.sub(r"\|\s*lower", "|lower", expr)
        expr = re.sub(r'\|\s*replace\s+"([^"]+)"\s+"([^"]+)"', r"|replace('\1', '\2')", expr)
        # Convert | toJson → | tojson
        expr = re.sub(r"\|\s*toJson", "|tojson", expr)

        # printf "fmt" arg1 arg2
        pm = re.match(r'printf\s+"([^"]+)"\s*(.*)', expr)
        if pm:
            fmt = pm.group(1)
            args_raw = pm.group(2).strip()
            args = re.sub(r"\$\.Values\.(\S+)", r"values.\1", args_raw)
            args = re.sub(r"\.Values\.(\S+)", r"values.\1", args)
            args = re.sub(r"(?<![a-zA-Z0-9_])\.([a-zA-Z])", r"item.\1", args)
            args = re.sub(r"\s+", ", ", args)
            return '{{ "' + fmt + '"|format(' + args + ") }}"

        # $.Values.x.y → values.x.y
        expr = re.sub(r"\$\.Values\.(\S+)", r"values.\1", expr)
        # .Values.x.y → values.x.y
        expr = re.sub(r"\.Values\.(\S+)", r"values.\1", expr)
        # bare . → item
        if expr == ".":
            expr = "item"
        # .foo → item.foo (but only when not already item.xxx or values.xxx)
        if not expr.startswith("item.") and not expr.startswith("values."):
            expr = re.sub(r"(?<![a-zA-Z0-9_])\.([a-zA-Z])", r"item.\1", expr)

        return "{{ " + expr + " }}"

    t = re.sub(r"\{\{(.+?)\}\}", convert_expr, t)

    # Fix: inside "for sub in" loops, "item" should be "sub"
    lines = t.split("\n")
    in_sub = False
    depth = 0
    result = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("{% for sub "):
            in_sub = True
            depth = 1
        elif stripped.startswith("{% for "):
            if in_sub:
                depth += 1
        elif stripped.startswith("{% endfor"):
            if in_sub:
                depth -= 1
                if depth == 0:
                    in_sub = False
        elif in_sub and "{{ item }}" in line:
            line = line.replace("{{ item }}", "{{ sub }}")
        result.append(line)
    t = "\n".join(result)

    return t


def _render_template(template: str, values: dict) -> str:
    """Render a Helm-style template with Jinja2."""
    jinja_template = _helm_to_jinja(template)
    env = Environment()
    tmpl = env.from_string(jinja_template)
    rendered = tmpl.render(values=values, Release={"Namespace": "default"})
    # Post-process: quote YAML values containing colons or multiline content
    lines = rendered.split("\n")
    result = []
    in_multiline = False
    for line in lines:
        stripped = line.strip()
        # Detect multiline string values (indented lines after a key without quotes)
        if in_multiline:
            if (
                stripped
                and not stripped.startswith("#")
                and not stripped.startswith("-")
                and ":" in stripped
                and not stripped.startswith('"')
            ):
                in_multiline = False
            else:
                result.append(line)
                continue
        # Quote values with colons
        if (
            ":" in line
            and not stripped.startswith("#")
            and not stripped.startswith("-")
            and not stripped.startswith("{")
        ):
            key, _, val = line.partition(":")
            val = val.strip()
            if (
                val
                and not val.startswith('"')
                and not val.startswith("'")
                and not val.startswith("{")
                and not val.startswith("[")
                and ":" in val
            ):
                line = f'{key}: "{val}"'
        result.append(line)
    return "\n".join(result)


def _parse_rendered_yaml(rendered: str) -> list[dict]:
    """Parse multi-doc YAML from rendered template."""
    docs = []
    for doc in yaml.safe_load_all(rendered):
        if doc and isinstance(doc, dict) and "kind" in doc:
            docs.append(doc)
    return docs


# ── Commands ──────────────────────────────────────────────────────────────────


@app.command()
def lint(
    path: Optional[str] = typer.Argument(None, help="Path to constitution file or directory"),
) -> None:
    """Lint constitution YAML files."""
    target = Path(path) if path else CONSTITUTIONS_DIR
    if not target.exists():
        console.print(f"[red]Path not found: {target}[/red]")
        raise typer.Exit(1)

    files = list(target.rglob("*.yaml")) if target.is_dir() else [target]
    console.print(f"[cyan]Linting {len(files)} files...[/cyan]")

    for f in files:
        content = f.read_text()
        issues = []
        if "TODO" in content:
            issues.append("Contains TODO")
        if len(content.strip()) == 0:
            issues.append("Empty file")
        if issues:
            console.print(f"  [yellow]WARN[/yellow] {f.name}: {', '.join(issues)}")
        else:
            console.print(f"  [green]OK[/green] {f.name}")

    console.print(f"\n[green]Linted {len(files)} files[/green]")


@app.command()
def list_constitutions() -> None:
    """List all constitution charts."""
    charts_dir = CONSTITUTIONS_DIR / "charts"
    if not charts_dir.exists():
        console.print("[red]Charts directory not found[/red]")
        raise typer.Exit(1)

    table = Table(title="Constitution Charts")
    table.add_column("Chart", style="cyan")
    table.add_column("File", style="green")
    table.add_column("Size", justify="right")

    for chart_dir in sorted(charts_dir.iterdir()):
        if chart_dir.is_dir():
            for f in sorted(chart_dir.rglob("*.yaml")):
                size = f.stat().st_size
                rel = f.relative_to(charts_dir)
                table.add_row(chart_dir.name, str(rel), f"{size:,} bytes")

    console.print(table)


@app.command()
def validate(
    file: str = typer.Argument(..., help="Path to constitution YAML file"),
) -> None:
    """Validate a constitution YAML file."""
    path = Path(file)
    if not path.exists():
        console.print(f"[red]File not found: {path}[/red]")
        raise typer.Exit(1)

    content = path.read_text()
    console.print(f"[cyan]Validating {path.name}...[/cyan]")

    checks = {
        "Has title": content.startswith("#"),
        "Has content": len(content.strip()) > 50,
        "No TODO items": "TODO" not in content,
        "No placeholder text": "[TBD]" not in content,
    }

    all_pass = True
    for check, passed in checks.items():
        status = "[green]PASS[/green]" if passed else "[red]FAIL[/red]"
        console.print(f"  {status} {check}")
        if not passed:
            all_pass = False

    if all_pass:
        console.print(f"\n[green]{path.name} is valid[/green]")
    else:
        console.print(f"\n[red]{path.name} has issues[/red]")
        raise typer.Exit(1)


@app.command()
def build(
    chart: Optional[str] = typer.Argument(None, help="Specific chart to build (default: all)"),
    output: str = typer.Option(
        "src/sittingface_charts/somatic", "-o", "--output", help="Output directory"
    ),
) -> None:
    """Build YAML resources from somatic charts, then extract prompts as markdown."""
    charts_dir = CONSTITUTIONS_DIR / "charts"
    if not charts_dir.exists():
        console.print("[red]Charts directory not found[/red]")
        raise typer.Exit(1)

    out_dir = _find_repo_root() / output
    out_dir.mkdir(parents=True, exist_ok=True)

    # Collect chart dirs: top-level + cerebellum/capabilities/*
    chart_dirs = []
    if chart:
        chart_dirs = [charts_dir / chart]
        if not chart_dirs[0].exists():
            console.print(f"[red]Chart not found: {chart}[/red]")
            raise typer.Exit(1)
    else:
        chart_dirs = sorted([d for d in charts_dir.iterdir() if d.is_dir()])

        # Also scan cerebellum/capabilities/*
        cap_dir = charts_dir / "cerebellum" / "capabilities"
        if cap_dir.exists():
            for cap_chart in sorted(cap_dir.iterdir()):
                if cap_chart.is_dir() and (cap_chart / "values.yaml").exists():
                    chart_dirs.append(cap_chart)

        # Also scan broca/agents/*
        agent_dir = charts_dir / "broca" / "agents"
        if agent_dir.exists():
            for agent_chart in sorted(agent_dir.iterdir()):
                if agent_chart.is_dir() and (agent_chart / "values.yaml").exists():
                    chart_dirs.append(agent_chart)

    console.print(f"[cyan]Building {len(chart_dirs)} charts → {out_dir}[/cyan]")

    for chart_dir in chart_dirs:
        chart_name = chart_dir.name
        values_file = chart_dir / "values.yaml"
        if not values_file.exists():
            continue
        values = _load_yaml(values_file)
        template_file = chart_dir / "templates" / f"{chart_name}.yaml"
        if not template_file.exists():
            template_file = chart_dir.parent / "templates" / f"{chart_dir.parent.name}.yaml"
        if not template_file.exists():
            # For capability sub-charts under cerebellum/capabilities/
            if "capabilities" in str(chart_dir) and chart_dir.parent.name == "capabilities":
                template_file = charts_dir / "capabilities" / "templates" / "capabilities.yaml"
            elif "capabilities" in str(chart_dir):
                template_file = charts_dir / "cerebellum" / "templates" / "cerebellum.yaml"
            # For agent sub-charts under broca/agents/
            elif "agents" in str(chart_dir) and chart_dir.parent.name == "agents":
                template_file = charts_dir / "broca" / "agents" / "templates" / "agents.yaml"
            elif "agents" in str(chart_dir):
                template_file = charts_dir / "broca" / "templates" / "broca.yaml"
            else:
                console.print(f"  [yellow]SKIP[/yellow] {chart_name}: no template")
                continue

        try:
            template_text = template_file.read_text()
            rendered = _render_template(template_text, values)
            resources = _parse_rendered_yaml(rendered)
        except Exception as e:
            console.print(f"  [red]ERR[/red] {chart_name}: {e}")
            continue

        md_lines = [f"# {chart_name}\n"]

        for res in resources:
            kind = res.get("kind", "")
            meta = res.get("metadata", {})
            spec = res.get("spec", {})

            if kind == "ConstitutionalModule":
                md_lines.append(f"## Module: {meta.get('name', '?')}")
                md_lines.append(f"- **Layer:** {meta.get('layer', '?')}")
                md_lines.append(f"- **Alias:** {meta.get('alias', '?')}")
                md_lines.append(f"- **Role:** {meta.get('softwareRole', '?')}")
                owns = spec.get("owns", [])
                if owns:
                    md_lines.append(f"- **Owns:** {', '.join(owns)}")
                never = spec.get("neverOwns", [])
                if never:
                    md_lines.append(f"- **Never Owns:** {', '.join(never)}")
                md_lines.append("")

            elif kind == "ConstitutionalPrinciple":
                md_lines.append(f"## Principle: {meta.get('name', '?')}")
                md_lines.append(f"> {spec.get('statement', '?')}")
                md_lines.append("")

            elif kind == "ConstitutionalInvariant":
                md_lines.append(f"## Invariant: {meta.get('name', '?')}")
                md_lines.append(f"- **Rule:** {spec.get('rule', '?')}")
                md_lines.append(f"- **Severity:** {meta.get('severity', '?')}")
                if spec.get("rationale"):
                    md_lines.append(f"- **Rationale:** {spec['rationale']}")
                if spec.get("auditCheck"):
                    md_lines.append(f"- **Audit:** {spec['auditCheck']}")
                if spec.get("rejection"):
                    md_lines.append(f"- **Rejection:** {spec['rejection']}")
                md_lines.append("")

            elif kind == "ConstitutionalPrompt":
                md_lines.append("## Prompt")
                if spec.get("preamble"):
                    md_lines.append(f"**Preamble:** {spec['preamble']}")
                cot = spec.get("cot", {})
                steps = cot.get("steps", [])
                if steps:
                    md_lines.append("\n**Chain of Thought:**")
                    for step in steps:
                        gate = " ⚠️ AUDIT GATE" if step.get("auditGate") else ""
                        constraint = f" — _{step['constraint']}_" if step.get("constraint") else ""
                        md_lines.append(f"{step['step']}. {step['instruction']}{constraint}{gate}")
                review = spec.get("reviewGate", {})
                if review:
                    md_lines.append(f"\n**Review Gate:** {review.get('mode', '?')}")
                    outcomes = review.get("outcomes", {})
                    if outcomes.get("approved"):
                        md_lines.append(f"- **Approved:** {outcomes['approved']}")
                    if outcomes.get("rejected"):
                        md_lines.append(f"- **Rejected:** {', '.join(outcomes['rejected'])}")
                md_lines.append("")

            elif kind == "Capability":
                cap = spec
                md_lines.append(f"## Capability: {cap.get('name', meta.get('name', '?'))}")
                md_lines.append(f"- **ID:** {cap.get('id', '?')}")
                md_lines.append(f"- **Platform:** {cap.get('platform', '?')}")
                md_lines.append(f"- **Version:** {cap.get('version', '?')}")
                md_lines.append(f"- **Status:** {cap.get('status', '?')}")
                md_lines.append(f"- **Description:** {cap.get('description', '?')}")
                md_lines.append(f"- **Module:** {cap.get('module_name', '?')}")
                tags = cap.get("tags", [])
                if tags:
                    md_lines.append(f"- **Tags:** {', '.join(tags)}")
                md_lines.append("")

                defn = cap.get("definition", {})
                auth = defn.get("auth", {})
                if auth:
                    md_lines.append("### Auth")
                    md_lines.append(f"- **Type:** {auth.get('type', '?')}")
                    md_lines.append("")

                endpoint = defn.get("endpoint", {})
                if endpoint:
                    md_lines.append("### Endpoint")
                    md_lines.append(f"- **Base URL:** `{endpoint.get('base_url', '?')}`")
                    md_lines.append(f"- **Protocol:** {endpoint.get('protocol', '?')}")
                    md_lines.append("")

                ops = defn.get("operations", [])
                if ops:
                    md_lines.append("### Operations")
                    for op in ops:
                        md_lines.append(
                            f"- **{op.get('name', '?')}** ({op.get('method', '?')}): {op.get('description', '?')}"
                        )
                    md_lines.append("")

                tests = defn.get("tests", {})
                scenarios = tests.get("test_scenarios", [])
                if scenarios:
                    md_lines.append("### Test Scenarios")
                    for s in scenarios:
                        md_lines.append(f"- {s}")
                    md_lines.append("")

            elif kind == "Agent":
                ag = spec
                md_lines.append(f"## Agent: {ag.get('name', meta.get('name', '?'))}")
                md_lines.append(f"- **ID:** {ag.get('id', '?')}")
                md_lines.append(f"- **Kind:** {ag.get('kind', '?')}")
                md_lines.append(f"- **Version:** {ag.get('version', '?')}")
                md_lines.append(f"- **Description:** {ag.get('description', '?')}")
                md_lines.append("")

                identity = ag.get("identity", {})
                if identity:
                    md_lines.append("### Identity")
                    md_lines.append(f"- **Role:** {identity.get('role', '?')}")
                    md_lines.append("")

                model = ag.get("model", {})
                if model:
                    md_lines.append("### Model")
                    md_lines.append(f"- **Provider:** {model.get('provider', '?')}")
                    md_lines.append(f"- **Name:** {model.get('name', '?')}")
                    md_lines.append(f"- **Temperature:** {model.get('temperature', '?')}")
                    md_lines.append("")

                workflow = ag.get("workflow", {})
                if workflow:
                    md_lines.append("### Workflow")
                    md_lines.append(f"- **Mode:** {workflow.get('mode', '?')}")
                    steps = workflow.get("steps", [])
                    if steps:
                        for s in steps:
                            md_lines.append(
                                f"  - {s.get('id', '?')} ({s.get('action', '?')}) → {s.get('on_failure', '?')}"
                            )
                    md_lines.append("")

                tools = ag.get("tools", [])
                if tools:
                    md_lines.append("### Tools")
                    for t in tools:
                        md_lines.append(
                            f"- **{t.get('name', '?')}** ({t.get('type', '?')}): {t.get('description', '?')}"
                        )
                    md_lines.append("")

                routing = ag.get("routing", {})
                if routing and routing.get("agents"):
                    md_lines.append("### Routing")
                    md_lines.append(f"- **Strategy:** {routing.get('strategy', '?')}")
                    for ra in routing["agents"]:
                        md_lines.append(
                            f"- **{ra.get('name', '?')}**: {', '.join(ra.get('handles', []))}"
                        )
                    md_lines.append("")

                mem = ag.get("memory", {})
                if mem:
                    md_lines.append("### Memory")
                    md_lines.append(f"- **Backend:** {mem.get('backend', '?')}")
                    md_lines.append(f"- **Scope:** {mem.get('scope', '?')}")
                    md_lines.append("")

            elif kind == "AgentCodeGenJob":
                cg = spec
                md_lines.append("### Code Generation")
                md_lines.append(f"- **Target:** {cg.get('target_language', '?')}")
                md_lines.append(f"- **Base class:** {cg.get('base_class', '?')}")
                cgconf = cg.get("codegen_config", {})
                if cgconf:
                    md_lines.append(f"- **Runtime module:** {cgconf.get('runtime_module', '?')}")
                    md_lines.append(
                        f"- **Auto-register:** {cgconf.get('registry_auto_register', '?')}"
                    )
                md_lines.append("")

        md_content = "\n".join(md_lines)
        out_file = out_dir / f"{chart_name}.md"
        out_file.write_text(md_content)
        console.print(
            f"  [green]OK[/green] {chart_name} ({len(resources)} resources) → {out_file.relative_to(_find_repo_root())}"
        )

    console.print(f"\n[green]Built {len(chart_dirs)} charts → {out_dir}[/green]")


@app.command()
def prompts(
    chart: Optional[str] = typer.Argument(None, help="Specific chart (default: all)"),
) -> None:
    """Extract ConstitutionalPrompt resources from rendered charts."""
    charts_dir = CONSTITUTIONS_DIR / "charts"
    if not charts_dir.exists():
        console.print("[red]Charts directory not found[/red]")
        raise typer.Exit(1)

    if chart:
        chart_dirs = [charts_dir / chart]
        if not chart_dirs[0].exists():
            console.print(f"[red]Chart not found: {chart}[/red]")
            raise typer.Exit(1)
    else:
        chart_dirs = sorted([d for d in charts_dir.iterdir() if d.is_dir()])

    all_prompts = []
    for chart_dir in chart_dirs:
        chart_name = chart_dir.name
        values = _load_yaml(chart_dir / "values.yaml")
        template_file = chart_dir / "templates" / f"{chart_name}.yaml"
        if not template_file.exists():
            continue

        try:
            template_text = template_file.read_text()
            rendered = _render_template(template_text, values)
            resources = _parse_rendered_yaml(rendered)
        except Exception:
            continue

        for res in resources:
            if res.get("kind") == "ConstitutionalPrompt":
                all_prompts.append({"chart": chart_name, **res})

    if not all_prompts:
        console.print("[yellow]No ConstitutionalPrompt resources found[/yellow]")
        return

    console.print(f"[cyan]Found {len(all_prompts)} prompt(s)[/cyan]\n")

    for p in all_prompts:
        meta = p.get("metadata", {})
        spec = p.get("spec", {})
        console.print(f"[bold]{p['chart']} — {meta.get('name', '?')}[/bold]")
        if spec.get("preamble"):
            console.print(f"  {spec['preamble']}")
        cot = spec.get("cot", {})
        steps = cot.get("steps", [])
        if steps:
            for step in steps:
                gate = " ⚠️" if step.get("auditGate") else ""
                console.print(f"  {step['step']}. {step['instruction']}{gate}")
        console.print()


@app.command()
def capabilities(
    output: str = typer.Option(
        "src/sittingface_charts/cerebellum", "-o", "--output", help="Output directory"
    ),
) -> None:
    """Build capability markdown from cerebellum capability charts."""
    charts_dir = CONSTITUTIONS_DIR / "charts"
    cap_dir = charts_dir / "capabilities"
    if not cap_dir.exists():
        console.print("[red]capabilities chart not found[/red]")
        raise typer.Exit(1)

    out_dir = _find_repo_root() / output
    out_dir.mkdir(parents=True, exist_ok=True)

    values = _load_yaml(cap_dir / "values.yaml")
    template_file = cap_dir / "templates" / "capabilities.yaml"

    if not template_file.exists():
        console.print("[red]No capability template found[/red]")
        raise typer.Exit(1)

    try:
        template_text = template_file.read_text()
        rendered = _render_template(template_text, values)
        resources = _parse_rendered_yaml(rendered)
    except Exception as e:
        console.print(f"[red]Render error: {e}[/red]")
        raise typer.Exit(1)

    md_lines = []
    for res in resources:
        kind = res.get("kind", "")
        meta = res.get("metadata", {})
        spec = res.get("spec", {})

        if kind == "Capability":
            cap = spec
            md_lines.append(f"# Capability: {cap.get('name', meta.get('name', '?'))}\n")
            md_lines.append(f"- **ID:** {cap.get('id', '?')}")
            md_lines.append(f"- **Platform:** {cap.get('platform', '?')}")
            md_lines.append(f"- **Version:** {cap.get('version', '?')}")
            md_lines.append(f"- **Status:** {cap.get('status', '?')}")
            md_lines.append(f"- **Description:** {cap.get('description', '?')}")
            md_lines.append(f"- **Authored by:** {cap.get('authored_by', '?')}")
            md_lines.append(f"- **Module:** {cap.get('module_name', '?')}")
            tags = cap.get("tags", [])
            if tags:
                md_lines.append(f"- **Tags:** {', '.join(tags)}")
            md_lines.append("")

            # Definition
            defn = cap.get("definition", {})

            auth = defn.get("auth", {})
            if auth:
                md_lines.append("## Auth")
                md_lines.append(f"- **Type:** {auth.get('type', '?')}")
                if auth.get("header_name"):
                    md_lines.append(f"- **Header:** {auth['header_name']}")
                if auth.get("scopes"):
                    md_lines.append(f"- **Scopes:** {', '.join(auth['scopes'])}")
                md_lines.append("")

            endpoint = defn.get("endpoint", {})
            if endpoint:
                md_lines.append("## Endpoint")
                md_lines.append(f"- **Base URL:** `{endpoint.get('base_url', '?')}`")
                md_lines.append(f"- **Protocol:** {endpoint.get('protocol', '?')}")
                md_lines.append(f"- **Timeout:** {endpoint.get('timeout_seconds', '?')}s")
                md_lines.append("")

            ops = defn.get("operations", [])
            if ops:
                md_lines.append("## Operations")
                md_lines.append("| Name | Method | Description |")
                md_lines.append("|------|--------|-------------|")
                for op in ops:
                    md_lines.append(
                        f"| {op.get('name', '?')} | {op.get('method', '?')} | {op.get('description', '?')} |"
                    )
                md_lines.append("")

            rate = defn.get("rate_limit", {})
            if rate:
                md_lines.append("## Rate Limiting")
                md_lines.append(f"- **Requests/hour:** {rate.get('requests_per_hour', '?')}")
                md_lines.append(f"- **Strategy:** {rate.get('strategy', '?')}")
                md_lines.append(f"- **Max retries:** {rate.get('max_retries', '?')}")
                md_lines.append("")

            errors = defn.get("error_handling", {})
            if errors:
                md_lines.append("## Error Handling")
                md_lines.append(f"- **Transient:** {errors.get('transient_statuses', [])}")
                md_lines.append(f"- **Permanent:** {errors.get('permanent_statuses', [])}")
                md_lines.append(
                    f"- **Circuit breaker:** {errors.get('circuit_breaker_threshold', '?')} failures / {errors.get('circuit_breaker_window_seconds', '?')}s"
                )
                md_lines.append("")

            tests = defn.get("tests", {})
            if tests:
                md_lines.append("## Tests")
                scenarios = tests.get("test_scenarios", [])
                if scenarios:
                    for s in scenarios:
                        md_lines.append(f"- {s}")
                md_lines.append("")

        elif kind == "CapabilityCodeGenJob":
            cg = spec
            md_lines.append("## Code Generation")
            md_lines.append(f"- **Target:** {cg.get('target_language', '?')}")
            md_lines.append(f"- **Base class:** {cg.get('base_class', '?')}")
            cgconf = cg.get("codegen_config", {})
            if cgconf:
                md_lines.append(f"- **Runtime module:** {cgconf.get('runtime_module', '?')}")
                md_lines.append(f"- **Auto-register:** {cgconf.get('registry_auto_register', '?')}")
            md_lines.append("")

    md_content = "\n".join(md_lines)
    out_file = out_dir / "capabilities.md"
    out_file.write_text(md_content)
    console.print(
        f"[green]Built {len(resources)} resources → {out_file.relative_to(_find_repo_root())}[/green]"
    )


@app.command()
def reviews(
    output: str = typer.Option(
        "src/sittingface_charts/somatic", "-o", "--output", help="Output directory"
    ),
) -> None:
    """Generate architecture and governance review documents from charts."""
    repo_root = _find_repo_root()
    out_dir = repo_root / output
    charts_dir = repo_root / "somatic" / "charts"

    if not charts_dir.exists():
        console.print("[red]somatic/charts not found[/red]")
        raise typer.Exit(1)

    import sys

    sys.path.insert(0, str(repo_root / "src"))
    from sittingface.review_generator import (
        generate_architecture_review,
        generate_governance_review,
        generate_coding_standards_review,
    )

    console.print("[cyan]Generating review documents...[/cyan]")

    p = generate_architecture_review(charts_dir, out_dir / "reviews")
    console.print(f"  [green]OK[/green] {p.relative_to(repo_root)}")

    p = generate_governance_review(charts_dir, out_dir / "reviews")
    console.print(f"  [green]OK[/green] {p.relative_to(repo_root)}")

    p = generate_coding_standards_review(charts_dir, out_dir / "reviews")
    console.print(f"  [green]OK[/green] {p.relative_to(repo_root)}")

    console.print(f"\n[green]Review documents generated → {out_dir / 'reviews'}[/green]")


@app.command()
def run(
    output: str = typer.Option(
        "/Users/prashunjaveri/Code/generated/monkeypatched",
        "-o",
        "--output",
        help="Output directory for generated code",
    ),
) -> None:
    """Run compiled prompts through MiMo Code Agent and generate Python code."""
    import sys

    sys.path.insert(0, str(_find_repo_root() / "src"))

    from sittingface.somatic_compiler import SomaticCompiler
    from sittingface.codegen_agent import CodeGenAgent

    console.print("[cyan]Loading somatic charts...[/cyan]")
    compiler = SomaticCompiler()
    charts = compiler.load_all()
    prompts = compiler.compile_prompts()
    console.print(f"  {len(charts)} charts loaded, {len(prompts)} prompts compiled")

    console.print(f"\n[cyan]Running code generation → {output}[/cyan]")
    agent = CodeGenAgent(output_dir=Path(output))

    for prompt in prompts:
        prompt_dict = {
            "chart": prompt.chart_name,
            "preamble": prompt.preamble,
            "steps": prompt.cot_steps,
            "constraints": prompt.constraints,
            "review_gate": prompt.review_gate,
        }
        chart_name = prompt.chart_name
        steps = prompt.cot_steps
        console.print(f"\n  [bold]{chart_name}[/bold] ({len(steps)} steps)")
        report = agent.run_prompt(prompt_dict)
        for s in report.steps:
            status = "[green]OK[/green]" if s.success else "[red]FAIL[/red]"
            console.print(f"    {status} Step {s.step}: {s.instruction[:70]}...")
            if s.target_file:
                console.print(f"         → {s.target_file}")

    summary = agent.summary()
    console.print("\n[green]Code generation complete[/green]")
    console.print(f"  Prompts run: {summary['prompts_run']}")
    console.print(f"  Total steps: {summary['total_steps']}")
    console.print(f"  Files written: {summary['files_written']}")
    console.print(f"  Output: {summary['output_dir']}")


@app.command("source-diff")
def source_diff(
    src_dir: str = typer.Option("src", "-s", "--src", help="Source directory (existing code)"),
    gen_dir: str = typer.Option(
        "/Users/prashunjaveri/Code/generated/monkeypatched",
        "-g",
        "--generated",
        help="Generated directory",
    ),
    output: str = typer.Option("docs/diff-report.md", "-o", "--output", help="Output report path"),
) -> None:
    """Diff src/ against generated/ and produce a markdown report."""
    import difflib
    from pathlib import Path

    repo_root = _find_repo_root()
    src = repo_root / src_dir
    gen = Path(gen_dir)

    if not src.exists():
        console.print(f"[red]Source not found: {src}[/red]")
        raise typer.Exit(1)
    if not gen.exists():
        console.print(f"[red]Generated not found: {gen}[/red]")
        raise typer.Exit(1)

    lines = ["# Code Diff Report\n"]
    lines.append(f"**Source:** `{src}`\n")
    lines.append(f"**Generated:** `{gen}`\n")

    modules = sorted(
        set(
            [d.name for d in src.iterdir() if d.is_dir() and (d / "__init__.py").exists()]
            + [d.name for d in gen.iterdir() if d.is_dir()]
        )
    )

    total_src_lines = 0
    total_gen_lines = 0

    for mod in modules:
        src_mod = src / mod
        gen_mod = gen / mod

        src_files = {}
        if src_mod.exists():
            for f in src_mod.rglob("*.py"):
                if f.name != "__init__.py":
                    src_files[str(f.relative_to(src))] = f.read_text()

        gen_files = {}
        if gen_mod.exists():
            for f in gen_mod.rglob("*.py"):
                if f.name != "__init__.py":
                    gen_files[str(f.relative_to(gen))] = f.read_text()

        only_src = set(src_files.keys()) - set(gen_files.keys())
        only_gen = set(gen_files.keys()) - set(src_files.keys())
        both = set(src_files.keys()) & set(gen_files.keys())

        lines.append(f"\n## {mod}\n")
        lines.append("| Metric | Count |")
        lines.append("|--------|-------|")
        lines.append(f"| In src/ only | {len(only_src)} |")
        lines.append(f"| In generated/ only | {len(only_gen)} |")
        lines.append(f"| In both | {len(both)} |")
        lines.append("")

        matched = 0
        diffed = 0
        for f in sorted(both):
            src_lines = src_files[f].splitlines()
            gen_lines = gen_files[f].splitlines()
            total_src_lines += len(src_lines)
            total_gen_lines += len(gen_lines)
            ratio = difflib.SequenceMatcher(None, src_lines, gen_lines).ratio()
            if ratio > 0.9:
                matched += 1
            else:
                diffed += 1

        lines.append(f"**Match:** {matched}/{len(both)} | **Diff:** {diffed}/{len(both)}\n")

        if only_src:
            lines.append(f"### Only in src/ ({len(only_src)})")
            for f in sorted(only_src):
                lines.append(f"- `{f}`")
            lines.append("")

        if only_gen:
            lines.append(f"### Only in generated/ ({len(only_gen)})")
            for f in sorted(only_gen):
                lines.append(f"- `{f}`")
            lines.append("")

    lines.insert(
        2,
        f"## Summary\n| Metric | Value |\n|--------|-------|\n| src/ lines | {total_src_lines} |\n| generated/ lines | {total_gen_lines} |\n",
    )

    out_path = repo_root / output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines))
    console.print(f"[green]Diff report written → {out_path.relative_to(repo_root)}[/green]")
    console.print(f"  src/ lines: {total_src_lines}")
    console.print(f"  generated/ lines: {total_gen_lines}")


@app.command()
def update(
    chart: str = typer.Argument(..., help="Chart name to update from src/"),
) -> None:
    """Update a somatic chart from the actual src/ code."""
    import yaml

    repo_root = _find_repo_root()
    src_dir = repo_root / "src" / chart

    if not src_dir.exists():
        console.print(f"[red]src/{chart} not found[/red]")
        raise typer.Exit(1)

    # Collect actual files
    files = []
    for f in sorted(src_dir.rglob("*.py")):
        if f.name == "__init__.py":
            continue
        rel = f.relative_to(repo_root / "src" / chart)
        files.append(str(rel))

    # Load existing values
    values_file = repo_root / "somatic" / "charts" / chart / "values.yaml"
    if values_file.exists():
        values = yaml.safe_load(values_file.read_text()) or {}
    else:
        values = {}

    module = values.setdefault("module", {})
    old_owns = module.get("owns", [])

    # Convert file paths to owns names
    new_owns = []
    for f in files:
        name = f.replace("/", "_").replace(".py", "")
        new_owns.append(name)

    if set(old_owns) == set(new_owns):
        console.print(f"[green]{chart}: chart already matches src/ ({len(new_owns)} files)[/green]")
        return

    module["owns"] = new_owns
    module["treeRoot"] = f"src/{chart}/"

    # Write back
    values_file.parent.mkdir(parents=True, exist_ok=True)
    values_file.write_text(yaml.dump(values, default_flow_style=False, sort_keys=False))

    added = set(new_owns) - set(old_owns)
    removed = set(old_owns) - set(new_owns)

    console.print(f"[green]Updated {chart} chart[/green]")
    console.print(f"  Files: {len(old_owns)} → {len(new_owns)}")
    if added:
        console.print(f"  Added: {', '.join(sorted(added))}")
    if removed:
        console.print(f"  Removed: {', '.join(sorted(removed))}")


@app.command()
def update_all() -> None:
    """Update all somatic charts from actual src/ code."""
    import yaml

    repo_root = _find_repo_root()
    somatic_dir = repo_root / "somatic" / "charts"

    modules = [
        "broca",
        "cerebellum",
        "cingulate",
        "cortex",
        "deepdive",
        "homeostasis",
        "introspection",
        "plasticity",
        "sync",
    ]

    updated = 0
    for mod in modules:
        src_dir = repo_root / "src" / mod
        values_file = somatic_dir / mod / "values.yaml"

        if not src_dir.exists() or not values_file.exists():
            continue

        files = []
        for f in sorted(src_dir.rglob("*.py")):
            if f.name == "__init__.py":
                continue
            rel = f.relative_to(repo_root / "src" / mod)
            files.append(str(rel))

        values = yaml.safe_load(values_file.read_text()) or {}
        module = values.setdefault("module", {})
        old_owns = module.get("owns", [])

        new_owns = []
        for f in files:
            name = f.replace("/", "_").replace(".py", "")
            new_owns.append(name)

        if set(old_owns) != set(new_owns):
            module["owns"] = new_owns
            module["treeRoot"] = f"src/{mod}/"
            values_file.write_text(yaml.dump(values, default_flow_style=False, sort_keys=False))
            console.print(f"  [green]OK[/green] {mod}: {len(old_owns)} → {len(new_owns)} owns")
            updated += 1
        else:
            console.print(f"  [dim]-- {mod}: unchanged ({len(new_owns)})[/dim]")

    console.print(f"\n[green]Updated {updated} charts[/green]")


@app.command()
def model(
    name: str = typer.Argument(None, help="Model name (e.g. gemma3:latest, qwen2.5-coder:7b)"),
) -> None:
    """Set or show the LLM model for the coding agent."""
    import json

    config_file = _find_repo_root() / ".soma" / "config.json"
    config_file.parent.mkdir(parents=True, exist_ok=True)

    if not name:
        if config_file.exists():
            config = json.loads(config_file.read_text())
            console.print(f"Current model: [bold]{config.get('model', 'gemma3:latest')}[/bold]")
        else:
            console.print("Current model: gemma3:latest (default)")
        console.print("\nAvailable models:")
        console.print("  gemma3:latest")
        console.print("  qwen2.5-coder:7b")
        console.print("  minimax-m2.7:cloud")
        return

    config = {}
    if config_file.exists():
        config = json.loads(config_file.read_text())
    config["model"] = name
    config_file.write_text(json.dumps(config, indent=2))
    os.chmod(config_file, 0o600)
    console.print(f"[green]Model set to {name}[/green]")


@app.command()
def tests(
    chart: str = typer.Argument(None, help="Chart to generate tests for (default: all)"),
    test_type: str = typer.Option(
        "unit", "-t", "--type", help="Test type: unit, integration, static"
    ),
    output: str = typer.Option("tests", "-o", "--output", help="Output directory"),
) -> None:
    """Generate unit/integration tests from somatic charts."""
    repo_root = _find_repo_root()
    out_dir = repo_root / output
    out_dir.mkdir(parents=True, exist_ok=True)

    import sys

    sys.path.insert(0, str(repo_root / "src"))
    from sittingface.somatic_compiler import SomaticCompiler

    compiler = SomaticCompiler()
    charts = compiler.load_all()
    capabilities = [c for c in charts if c.chart_type == "capability"]

    if chart:
        capabilities = [c for c in capabilities if c.name == chart]
        if not capabilities:
            console.print(f"[red]No capability chart found: {chart}[/red]")
            raise typer.Exit(1)

    console.print(
        f"[cyan]Generating {test_type} tests for {len(capabilities)} capabilities...[/cyan]"
    )

    for cap in capabilities:
        cap_data = cap.values.get("capability", {})
        operations = cap.values.get("operations", [])
        cap_name = cap_data.get("name", cap.name)

        if test_type == "unit":
            code = _gen_unit_tests(cap_name, operations, cap_data)
            out_file = out_dir / f"test_{cap_name}_unit.py"
        elif test_type == "integration":
            code = _gen_integration_tests(cap_name, operations, cap_data)
            out_file = out_dir / f"test_{cap_name}_integration.py"
        else:
            code = _gen_static_analysis(cap_name, operations, cap_data)
            out_file = out_dir / f"analysis_{cap_name}.py"

        out_file.write_text(code)
        console.print(f"  [green]OK[/green] {cap_name} → {out_file.relative_to(repo_root)}")

    console.print(f"\n[green]Generated {len(capabilities)} test files → {out_dir}[/green]")


def _gen_unit_tests(cap_name: str, operations: list[dict], cap_data: dict) -> str:
    lines = [
        f'"""Unit tests for {cap_name} capability."""',
        "",
        "import pytest",
        "from unittest.mock import AsyncMock, MagicMock",
        "",
        "",
    ]
    for op in operations:
        op_name = op.get("name", "unknown")
        lines.append("@pytest.mark.asyncio")
        lines.append(f"async def test_{op_name}():")
        lines.append(f'    """Test {op_name} operation."""')
        lines.append("    assert True  # TODO: implement")
        lines.append("")
    lines.append("")
    return "\n".join(lines)


def _gen_integration_tests(cap_name: str, operations: list[dict], cap_data: dict) -> str:
    lines = [
        f'"""Integration tests for {cap_name} capability."""',
        "",
        "import pytest",
        "",
        "",
    ]
    for op in operations:
        op_name = op.get("name", "unknown")
        lines.append("@pytest.mark.asyncio")
        lines.append(f"async def test_{op_name}_integration():")
        lines.append(f'    """Integration test for {op_name} against live endpoint."""')
        lines.append("    assert True  # TODO: implement with real endpoint")
        lines.append("")
    return "\n".join(lines)


def _gen_static_analysis(cap_name: str, operations: list[dict], cap_data: dict) -> str:
    lines = [
        f'"""Static analysis report for {cap_name} capability."""',
        "",
        "import ast",
        "from pathlib import Path",
        "",
        "",
    ]
    for op in operations:
        op_name = op.get("name", "unknown")
        method = op.get("method", "GET")
        path = op.get("path", "/")
        req = op.get("required_params", [])
        opt = op.get("optional_params", [])
        lines.append(f"def check_{op_name}():")
        lines.append(f'    """Check {method} {path}"""')
        lines.append("    checks = [")
        lines.append(f"        {'True' if req else 'False'}  # has required params")
        lines.append(f"        {'True' if opt else 'False'}  # has optional params")
        lines.append(f"        {method in ('GET', 'POST', 'PUT', 'DELETE')}  # valid method")
        lines.append("    ]")
        lines.append("    return all(checks)")
        lines.append("")
    return "\n".join(lines)


@app.command()
def simulate(
    prompt: str = typer.Argument(..., help="Prompt to simulate"),
    context: str = typer.Option("", "-c", "--context", help="JSON context"),
) -> None:
    """Run a simulation through the cortex world model."""
    import json
    import asyncio

    repo_root = _find_repo_root()
    sys.path.insert(0, str(repo_root / "src"))

    console.print(f"[cyan]Simulating: {prompt}[/cyan]")

    try:
        from src.cortex.world_model_simulation import simulate as run_simulate

        ctx = json.loads(context) if context else {}
        result = asyncio.run(run_simulate(prompt, context=ctx))

        if isinstance(result, dict):
            for key, value in result.items():
                if isinstance(value, (dict, list)):
                    console.print(f"  {key}: {json.dumps(value, indent=2)[:200]}")
                else:
                    console.print(f"  {key}: {value}")
        else:
            console.print(f"  Result: {result}")
    except Exception as e:
        console.print(f"[red]Simulation error: {e}[/red]")
        console.print("  Falling back to prompt-only mode")
        console.print(f"  Prompt: {prompt}")


@app.command()
def code_lint(
    path: str = typer.Argument(None, help="Path to lint (default: src/)"),
) -> None:
    """Run static analysis on src/ code."""
    repo_root = _find_repo_root()
    target = repo_root / (path or "src")

    console.print(f"[cyan]Running static analysis on {target.relative_to(repo_root)}...[/cyan]")

    # Run ruff
    result = run(
        [
            str(_find_repo_root() / ".venv" / "bin" / "ruff"),
            "check",
            str(target),
            "--output-format=text",
        ],
        check=False,
        capture=True,
    )
    if result.stdout:
        console.print(result.stdout)
    if result.returncode == 0:
        console.print("[green]No issues found[/green]")
    else:
        console.print(f"[yellow]Found issues (exit code {result.returncode})[/yellow]")

    # Run mypy if available
    mypy_result = run(
        [
            str(_find_repo_root() / ".venv" / "bin" / "mypy"),
            str(target),
            "--ignore-missing-imports",
        ],
        check=False,
        capture=True,
    )
    if mypy_result.stdout:
        console.print(f"\n{mypy_result.stdout}")


@app.command()
def version() -> None:
    """Show Soma version."""
    console.print(f"soma-cli v{__import__('soma').__version__}")


# ===========================================================================
# Git passthrough — 1:1 mapping of every git subcommand
#
# The spec repo is resolved in this order:
#   1. MONKEYBRAIN_SPEC_REPO env var
#   2. spec_repo field in .soma/config.json
#   3. Repository root of the current working directory (fallback)
# ===========================================================================

_GIT_CTX = {"allow_extra_args": True, "ignore_unknown_options": True}


def _spec_repo() -> Path:
    env = os.environ.get("MONKEYBRAIN_SPEC_REPO", "")
    if env:
        return Path(env).expanduser().resolve()
    config_file = _find_repo_root() / ".soma" / "config.json"
    if config_file.exists():
        try:
            cfg = json.loads(config_file.read_text())
            if cfg.get("spec_repo"):
                return Path(cfg["spec_repo"]).expanduser().resolve()
        except Exception:
            pass
    return _find_repo_root()


def _git(*args: str, cwd: Path | None = None) -> int:
    repo = cwd or _spec_repo()
    cmd = ["git", *[str(a) for a in args]]
    console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
    result = subprocess.run(cmd, cwd=repo)
    return result.returncode


def _git_cmd(name: str, ctx: typer.Context, cwd: Path | None = None) -> None:
    code = _git(name, *ctx.args, cwd=cwd)
    raise typer.Exit(code)


# ── Repo setup ──────────────────────────────────────────────────────────────


@app.command(context_settings=_GIT_CTX)
def init(ctx: typer.Context) -> None:
    """git init — create or reinitialize a repository."""
    _git_cmd("init", ctx)


@app.command(context_settings=_GIT_CTX)
def clone(ctx: typer.Context) -> None:
    """git clone — clone a repository into a new directory."""
    code = _git("clone", *ctx.args, cwd=Path.cwd())
    raise typer.Exit(code)


# ── Working tree ─────────────────────────────────────────────────────────────


@app.command(context_settings=_GIT_CTX)
def status(ctx: typer.Context) -> None:
    """git status — show working tree status."""
    _git_cmd("status", ctx)


@app.command(context_settings=_GIT_CTX)
def add(ctx: typer.Context) -> None:
    """git add — stage file contents."""
    _git_cmd("add", ctx)


@app.command(context_settings=_GIT_CTX)
def mv(ctx: typer.Context) -> None:
    """git mv — move or rename a file."""
    _git_cmd("mv", ctx)


@app.command(context_settings=_GIT_CTX)
def rm(ctx: typer.Context) -> None:
    """git rm — remove files from the working tree and index."""
    _git_cmd("rm", ctx)


@app.command(context_settings=_GIT_CTX)
def restore(ctx: typer.Context) -> None:
    """git restore — restore working tree files."""
    _git_cmd("restore", ctx)


@app.command(context_settings=_GIT_CTX)
def clean(ctx: typer.Context) -> None:
    """git clean — remove untracked files from the working tree."""
    _git_cmd("clean", ctx)


# ── Commits ───────────────────────────────────────────────────────────────────


@app.command(context_settings=_GIT_CTX)
def commit(ctx: typer.Context) -> None:
    """git commit — record changes to the repository."""
    _git_cmd("commit", ctx)


@app.command(context_settings=_GIT_CTX)
def amend(ctx: typer.Context) -> None:
    """git commit --amend — amend the last commit."""
    code = _git("commit", "--amend", *ctx.args)
    raise typer.Exit(code)


# ── History ───────────────────────────────────────────────────────────────────


@app.command(context_settings=_GIT_CTX)
def log(ctx: typer.Context) -> None:
    """git log — show commit history."""
    _git_cmd("log", ctx)


@app.command(context_settings=_GIT_CTX)
def show(ctx: typer.Context) -> None:
    """git show — show various types of objects."""
    _git_cmd("show", ctx)


@app.command(context_settings=_GIT_CTX)
def diff(ctx: typer.Context) -> None:
    """git diff — show changes between commits, commit and working tree, etc."""
    _git_cmd("diff", ctx)


@app.command(context_settings=_GIT_CTX)
def blame(ctx: typer.Context) -> None:
    """git blame — show what revision and author last modified each line."""
    _git_cmd("blame", ctx)


@app.command(context_settings=_GIT_CTX)
def grep(ctx: typer.Context) -> None:
    """git grep — print lines matching a pattern."""
    _git_cmd("grep", ctx)


# ── Branching ─────────────────────────────────────────────────────────────────


@app.command(context_settings=_GIT_CTX)
def branch(ctx: typer.Context) -> None:
    """git branch — list, create, or delete branches."""
    _git_cmd("branch", ctx)


@app.command(context_settings=_GIT_CTX)
def checkout(ctx: typer.Context) -> None:
    """git checkout — switch branches or restore working tree files."""
    _git_cmd("checkout", ctx)


@app.command(context_settings=_GIT_CTX)
def switch(ctx: typer.Context) -> None:
    """git switch — switch branches."""
    _git_cmd("switch", ctx)


@app.command(context_settings=_GIT_CTX)
def merge(ctx: typer.Context) -> None:
    """git merge — join two or more development histories together."""
    _git_cmd("merge", ctx)


@app.command(context_settings=_GIT_CTX)
def rebase(ctx: typer.Context) -> None:
    """git rebase — reapply commits on top of another base tip."""
    _git_cmd("rebase", ctx)


@app.command(context_settings=_GIT_CTX)
def cherry_pick(ctx: typer.Context) -> None:
    """git cherry-pick — apply the changes introduced by existing commits."""
    _git_cmd("cherry-pick", ctx)


# ── Reset / undo ──────────────────────────────────────────────────────────────


@app.command(context_settings=_GIT_CTX)
def reset(ctx: typer.Context) -> None:
    """git reset — reset current HEAD to the specified state."""
    _git_cmd("reset", ctx)


@app.command(context_settings=_GIT_CTX)
def revert(ctx: typer.Context) -> None:
    """git revert — revert some existing commits."""
    _git_cmd("revert", ctx)


@app.command(context_settings=_GIT_CTX)
def stash(ctx: typer.Context) -> None:
    """git stash — stash the changes in a dirty working directory."""
    _git_cmd("stash", ctx)


# ── Tags ──────────────────────────────────────────────────────────────────────


@app.command(context_settings=_GIT_CTX)
def tag(ctx: typer.Context) -> None:
    """git tag — create, list, delete or verify a tag object."""
    _git_cmd("tag", ctx)


# ── Remotes / sync ────────────────────────────────────────────────────────────


@app.command(context_settings=_GIT_CTX)
def remote(ctx: typer.Context) -> None:
    """git remote — manage set of tracked repositories."""
    _git_cmd("remote", ctx)


@app.command(context_settings=_GIT_CTX)
def fetch(ctx: typer.Context) -> None:
    """git fetch — download objects and refs from another repository."""
    _git_cmd("fetch", ctx)


@app.command(context_settings=_GIT_CTX)
def pull(ctx: typer.Context) -> None:
    """git pull — fetch from and integrate with another repository or branch."""
    _git_cmd("pull", ctx)


@app.command(context_settings=_GIT_CTX)
def push(ctx: typer.Context) -> None:
    """git push — update remote refs along with associated objects."""
    _git_cmd("push", ctx)


# ── Inspection / utility ──────────────────────────────────────────────────────


@app.command(context_settings=_GIT_CTX)
def bisect(ctx: typer.Context) -> None:
    """git bisect — use binary search to find the commit that introduced a bug."""
    _git_cmd("bisect", ctx)


@app.command(context_settings=_GIT_CTX)
def shortlog(ctx: typer.Context) -> None:
    """git shortlog — summarize git log output."""
    _git_cmd("shortlog", ctx)


@app.command("rev-parse", context_settings=_GIT_CTX)
def rev_parse(ctx: typer.Context) -> None:
    """git rev-parse — parse revision (or other objects) names."""
    _git_cmd("rev-parse", ctx)


@app.command("ls-files", context_settings=_GIT_CTX)
def ls_files(ctx: typer.Context) -> None:
    """git ls-files — show information about files in the index and the working tree."""
    _git_cmd("ls-files", ctx)


@app.command(context_settings=_GIT_CTX)
def submodule(ctx: typer.Context) -> None:
    """git submodule — initialize, update or inspect submodules."""
    _git_cmd("submodule", ctx)


@app.command(context_settings=_GIT_CTX)
def worktree(ctx: typer.Context) -> None:
    """git worktree — manage multiple working trees."""
    _git_cmd("worktree", ctx)


# ── Spec repo configuration ───────────────────────────────────────────────────


@app.command("repo")
def repo_cmd(
    action: str = typer.Argument(
        "show", help="Action: show | set <path> | create <name> | github <name>"
    ),
    path: Optional[str] = typer.Argument(None, help="Repo path or name"),
    remote: Optional[str] = typer.Option(None, "--remote", "-r", help="Remote URL (for 'create')"),
    bare: bool = typer.Option(False, "--bare", help="Create a bare repo (for 'create')"),
    initial_branch: str = typer.Option("main", "--branch", "-b", help="Initial branch name"),
    # GitHub creation flags
    github_org: Optional[str] = typer.Option(
        None, "--org", help="GitHub org to create under (default: authenticated user)"
    ),
    github_private: bool = typer.Option(False, "--private", help="Make GitHub repo private"),
    github_description: str = typer.Option(
        "", "--description", "-d", help="GitHub repo description"
    ),
    github_push: bool = typer.Option(
        True,
        "--push/--no-push",
        help="Push initial commit after GitHub create (default: yes)",
    ),
) -> None:
    """Configure, display, or create the target spec repository.

    \b
    soma repo show                           — print current spec repo path
    soma repo set <path>                     — save path to .soma/config.json
    soma repo create <name>                  — git init locally at <name>
    soma repo create <name> --remote <url>   — git init + add remote + push
    soma repo github <name>                  — create on GitHub via API, git init locally, push
    soma repo github <name> --org myorg      — create under a GitHub org
    soma repo github <name> --private        — create private repo
    """
    if action == "show":
        repo = _spec_repo()
        console.print(f"Spec repo: [bold cyan]{repo}[/bold cyan]")
        exists = repo.exists() and (repo / ".git").exists()
        console.print(f"Git repo:  {'[green]yes[/green]' if exists else '[red]no[/red]'}")
        if exists:
            result = subprocess.run(
                ["git", "remote", "-v"], cwd=repo, capture_output=True, text=True
            )
            if result.stdout.strip():
                console.print(f"Remotes:\n{result.stdout.strip()}")

    elif action == "set":
        if not path:
            console.print("[red]Usage: soma repo set <path>[/red]")
            raise typer.Exit(1)
        config_file = _find_repo_root() / ".soma" / "config.json"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        cfg = {}
        if config_file.exists():
            try:
                cfg = json.loads(config_file.read_text())
            except Exception:
                pass
        cfg["spec_repo"] = str(Path(path).expanduser().resolve())
        config_file.write_text(json.dumps(cfg, indent=2))
        console.print(f"[green]Spec repo set to {cfg['spec_repo']}[/green]")

    elif action == "create":
        if not path:
            console.print("[red]Usage: soma repo create <name|path>[/red]")
            raise typer.Exit(1)

        repo_path = Path(path).expanduser()
        if not repo_path.is_absolute():
            repo_path = Path.cwd() / repo_path
        repo_path = repo_path.resolve()

        if repo_path.exists() and (repo_path / ".git").exists():
            console.print(f"[yellow]Repo already exists: {repo_path}[/yellow]")
        else:
            repo_path.mkdir(parents=True, exist_ok=True)

            # git init
            init_args = ["git", "init", "--initial-branch", initial_branch]
            if bare:
                init_args.append("--bare")
            r = subprocess.run(init_args, cwd=repo_path, capture_output=True, text=True)
            if r.returncode != 0:
                # older git doesn't support --initial-branch
                subprocess.run(["git", "init"], cwd=repo_path, check=True)
                subprocess.run(
                    ["git", "checkout", "-b", initial_branch],
                    cwd=repo_path,
                    capture_output=True,
                )
            console.print(f"[green]Initialized repo: {repo_path}[/green]")

            # Write a .gitignore and README
            (repo_path / ".gitignore").write_text("*.pyc\n__pycache__/\n.env\n.DS_Store\n")
            (repo_path / "README.md").write_text(
                f"# {repo_path.name}\n\nMonkeyBrain spec repository.\n"
            )

            # Initial commit
            subprocess.run(["git", "add", "."], cwd=repo_path, check=True)
            subprocess.run(
                ["git", "commit", "-m", "chore: initial spec repo"],
                cwd=repo_path,
                check=True,
            )
            console.print("  [green]Initial commit created[/green]")

        # Add remote and push if provided
        if remote:
            r = subprocess.run(
                ["git", "remote", "add", "origin", remote],
                cwd=repo_path,
                capture_output=True,
                text=True,
            )
            if r.returncode != 0 and "already exists" not in r.stderr:
                console.print(f"[red]Failed to add remote: {r.stderr}[/red]")
                raise typer.Exit(1)
            console.print(f"  Remote origin → {remote}")

            r = subprocess.run(
                ["git", "push", "--set-upstream", "origin", initial_branch],
                cwd=repo_path,
                capture_output=True,
                text=True,
            )
            if r.returncode == 0:
                console.print(f"  [green]Pushed to {remote}[/green]")
            else:
                console.print(
                    f"  [yellow]Push failed (remote may not exist yet): {r.stderr.strip()}[/yellow]"
                )

        # Save as configured spec repo
        config_file = _find_repo_root() / ".soma" / "config.json"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        cfg = {}
        if config_file.exists():
            try:
                cfg = json.loads(config_file.read_text())
            except Exception:
                pass
        cfg["spec_repo"] = str(repo_path)
        config_file.write_text(json.dumps(cfg, indent=2))
        console.print("  [green]Configured as active spec repo[/green]")

    elif action == "github":
        # Create on GitHub via API, then git init locally and push
        if not path:
            console.print("[red]Usage: soma repo github <name>[/red]")
            raise typer.Exit(1)

        token = os.environ.get("GITHUB_TOKEN", "")
        if not token:
            console.print("[red]GITHUB_TOKEN not set. Export it or add to .env[/red]")
            raise typer.Exit(1)

        import asyncio as _asyncio
        import sys as _sys

        _sys.path.insert(0, str(_find_repo_root() / "packages" / "cerebellum"))

        async def _create_on_github():
            from cerebellum.capabilities.source_control.source_control import (
                GitHubCapability,
            )

            cap = GitHubCapability(token=token)
            return await cap.execute(
                {
                    "operation": "create_repo",
                    "name": path,
                    "org": github_org or "",
                    "private": github_private,
                    "description": github_description or "MonkeyBrain spec repository",
                    "auto_init": False,
                    "default_branch": initial_branch,
                }
            )

        console.print(f"[cyan]Creating GitHub repo '{path}'...[/cyan]")
        gh_result = _asyncio.run(_create_on_github())

        if gh_result.get("status") != "created":
            console.print(f"[red]GitHub API error: {gh_result.get('error', gh_result)}[/red]")
            raise typer.Exit(1)

        clone_url = gh_result["clone_url"]
        html_url = gh_result["html_url"]
        console.print(f"  [green]Created[/green] {html_url}")

        # git init locally using the repo name as directory
        repo_path = (Path.cwd() / path).resolve()
        repo_path.mkdir(parents=True, exist_ok=True)

        r = subprocess.run(
            ["git", "init", "--initial-branch", initial_branch],
            cwd=repo_path,
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            subprocess.run(["git", "init"], cwd=repo_path, check=True)
            subprocess.run(
                ["git", "checkout", "-b", initial_branch],
                cwd=repo_path,
                capture_output=True,
            )

        (repo_path / ".gitignore").write_text("*.pyc\n__pycache__/\n.env\n.DS_Store\n")
        (repo_path / "README.md").write_text(
            f"# {path}\n\n{github_description or 'MonkeyBrain spec repository.'}\n"
        )
        subprocess.run(["git", "add", "."], cwd=repo_path, check=True)
        subprocess.run(["git", "commit", "-m", "chore: initial commit"], cwd=repo_path, check=True)
        console.print(f"  [green]Initialized[/green] {repo_path}")

        # wire remote
        subprocess.run(
            ["git", "remote", "add", "origin", clone_url],
            cwd=repo_path,
            capture_output=True,
        )
        console.print(f"  Remote origin → {clone_url}")

        if github_push:
            r = subprocess.run(
                ["git", "push", "--set-upstream", "origin", initial_branch],
                cwd=repo_path,
                capture_output=True,
                text=True,
            )
            if r.returncode == 0:
                console.print(f"  [green]Pushed → {html_url}[/green]")
            else:
                console.print(f"  [yellow]Push failed: {r.stderr.strip()[:200]}[/yellow]")

        # save as active spec repo
        config_file = _find_repo_root() / ".soma" / "config.json"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        cfg = {}
        if config_file.exists():
            try:
                cfg = json.loads(config_file.read_text())
            except Exception:
                pass
        cfg["spec_repo"] = str(repo_path)
        config_file.write_text(json.dumps(cfg, indent=2))
        console.print("  [green]Configured as active spec repo[/green]")

    else:
        console.print(
            f"[red]Unknown action: {action!r}. Use 'show', 'set', 'create', or 'github'.[/red]"
        )
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
