"""SittingFace CLI — HuggingFace-style chart registry commands."""

from __future__ import annotations

import json
import os
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    name="sittingface",
    help="SittingFace — HuggingFace-style registry for MonkeyBrain charts (somatic + helm)",
    no_args_is_help=True,
)
console = Console()

REGISTRY_URL = os.environ.get("SITTINGFACE_URL", "http://localhost:8500")


def _find_repo_root() -> Path:
    p = Path.cwd()
    for _ in range(10):
        if (p / "somatic").exists() or (p / "helm").exists():
            return p
        if p.parent == p:
            break
        p = p.parent
    return Path.cwd()


def _get_client():
    from sittingface.client import SittingFaceClient

    return SittingFaceClient(REGISTRY_URL)


def _get_local():
    from sittingface.registry import SittingFaceRegistry

    return SittingFaceRegistry()


# ── Remote (online) commands ──────────────────────────────────────────────────


@app.command()
def publish(
    chart: str = typer.Argument(..., help="Chart name or path to publish"),
    tags: str = typer.Option("", "-t", "--tags", help="Comma-separated tags"),
    local: bool = typer.Option(False, "-l", "--local", help="Publish to local registry only"),
) -> None:
    """Publish a chart to the registry (online or local)."""
    repo_root = _find_repo_root()
    chart_dir = Path(chart) if Path(chart).is_absolute() else repo_root / chart

    if not chart_dir.exists():
        for search in [
            "somatic/charts",
            "somatic/charts/cerebellum/capabilities",
            "somatic/charts/broca/agents",
            "helm",
        ]:
            candidate = repo_root / search / chart
            if candidate.exists():
                chart_dir = candidate
                break

    if not chart_dir.exists():
        console.print(f"[red]Chart not found: {chart}[/red]")
        raise typer.Exit(1)

    if not (chart_dir / "values.yaml").exists():
        console.print(f"[red]No values.yaml in {chart_dir}[/red]")
        raise typer.Exit(1)

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    if local:
        registry = _get_local()
        meta = registry.publish(chart_dir, tags=tag_list)
        console.print(f"[green]Published locally[/green] {meta.name} v{meta.version}")
    else:
        try:
            client = _get_client()
            result = client.publish_chart_dir(chart_dir, tags=tag_list)
            console.print(f"[green]Published[/green] {result['name']} v{result['version']} → {REGISTRY_URL}")
        except Exception as e:
            console.print(f"[red]Failed to publish: {e}[/red]")
            console.print("Is the registry server running? Start with: sittingface serve")
            raise typer.Exit(1) from e


@app.command()
def publish_all(
    local: bool = typer.Option(False, "-l", "--local", help="Publish to local registry only"),
) -> None:
    """Publish all charts (somatic + helm + capabilities + agents) to the registry."""
    repo_root = _find_repo_root()
    published = 0

    if local:
        registry = _get_local()
    else:
        client = _get_client()

    def _pub(chart_dir: Path, tags: list[str] | None = None):
        nonlocal published
        if not (chart_dir / "values.yaml").exists():
            return
        try:
            if local:
                meta = registry.publish(chart_dir, tags=tags)
                console.print(f"  [green]OK[/green] {meta.name} v{meta.version} ({meta.chart_type})")
            else:
                result = client.publish_chart_dir(chart_dir, tags=tags)
                console.print(f"  [green]OK[/green] {result['name']} v{result['version']} ({result['chart_type']})")
            published += 1
        except Exception as e:
            console.print(f"  [red]ERR[/red] {chart_dir.name}: {e}")

    # 1. Somatic constitutional charts
    somatic_dir = repo_root / "somatic" / "charts"
    if somatic_dir.exists():
        for d in sorted(somatic_dir.iterdir()):
            if d.is_dir() and (d / "values.yaml").exists():
                _pub(d)

    # 2. Cerebellum capabilities
    cap_dir = somatic_dir / "cerebellum" / "capabilities"
    if cap_dir.exists():
        for d in sorted(cap_dir.iterdir()):
            if d.is_dir() and (d / "values.yaml").exists():
                _pub(d, tags=["capability"])

    # 3. Broca agents
    agent_dir = somatic_dir / "broca" / "agents"
    if agent_dir.exists():
        for d in sorted(agent_dir.iterdir()):
            if d.is_dir() and (d / "values.yaml").exists():
                _pub(d, tags=["agent"])

    # 4. Helm charts
    helm_dir = repo_root / "helm"
    if helm_dir.exists():
        for d in sorted(helm_dir.iterdir()):
            if d.is_dir() and (d / "Chart.yaml").exists():
                _pub(d, tags=["helm"])

    target = "local" if local else REGISTRY_URL
    console.print(f"\n[green]Published {published} charts → {target}[/green]")


@app.command()
def pull(
    chart: str = typer.Argument(..., help="Chart name to pull"),
    version: str = typer.Option("latest", "-v", "--version"),
    output: str = typer.Option("./charts", "-o", "--output"),
    local: bool = typer.Option(False, "-l", "--local", help="Pull from local registry"),
) -> None:
    """Pull a chart from the registry."""
    if local:
        registry = _get_local()
        path = registry.get(chart, version)
        if path:
            console.print(f"[green]Found locally[/green] {chart} v{version} at {path}")
        else:
            console.print(f"[red]Not found in local registry: {chart}[/red]")
        return

    try:
        client = _get_client()
        dest = Path(output) / chart
        path = client.pull(chart, version, dest)
        if path:
            console.print(f"[green]Pulled[/green] {chart} v{version} → {path}")
        else:
            console.print(f"[red]Not found: {chart} v{version}[/red]")
    except Exception as e:
        console.print(f"[red]Failed to pull: {e}[/red]")
        raise typer.Exit(1) from e


@app.command("list")
def list_charts(
    type: str = typer.Option("", "-t", "--type", help="Filter: module, capability, agent, helm"),
    local: bool = typer.Option(False, "-l", "--local", help="List local registry"),
) -> None:
    """List all charts in the registry."""
    if local:
        registry = _get_local()
        charts = registry.list_charts(chart_type=type if type else None)
        _print_chart_table(charts, "Local Registry")
        return

    try:
        client = _get_client()
        charts = client.list_charts(chart_type=type if type else None)
        _print_chart_table(charts, f"Online Registry ({REGISTRY_URL})")
    except Exception as e:
        console.print(f"[red]Failed to connect: {e}[/red]")
        raise typer.Exit(1) from e


def _print_chart_table(charts: list, title: str):
    if not charts:
        console.print("[yellow]No charts found[/yellow]")
        return
    table = Table(title=f"{title} ({len(charts)} charts)")
    table.add_column("Name", style="cyan")
    table.add_column("Version", style="green")
    table.add_column("Type", style="magenta")
    table.add_column("Description")
    table.add_column("Tags")
    for c in charts:
        if isinstance(c, dict):
            table.add_row(
                c["name"],
                c["version"],
                c.get("chart_type", ""),
                c.get("description", "")[:50],
                ", ".join(c.get("tags", [])[:3]),
            )
        else:
            table.add_row(
                c.name,
                c.version,
                c.chart_type,
                c.description[:50],
                ", ".join(c.tags[:3]),
            )
    console.print(table)


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query"),
    local: bool = typer.Option(False, "-l", "--local", help="Search local registry"),
) -> None:
    """Search charts by name, description, or tags."""
    if local:
        registry = _get_local()
        results = registry.search(query)
    else:
        client = _get_client()
        results = client.list_charts(search=query)

    if not results:
        console.print(f"[yellow]No charts found for '{query}'[/yellow]")
        return

    console.print(f"[cyan]Found {len(results)} charts[/cyan]\n")
    for c in results:
        if isinstance(c, dict):
            console.print(f"  [bold]{c['name']}[/bold] v{c['version']} ({c.get('chart_type', '')})")
            console.print(f"    {c.get('description', '')[:80]}")
        else:
            console.print(f"  [bold]{c.name}[/bold] v{c.version} ({c.chart_type})")
            console.print(f"    {c.description[:80]}")
        console.print()


@app.command()
def info(
    name: str = typer.Argument(..., help="Chart name"),
    local: bool = typer.Option(False, "-l", "--local", help="Local registry"),
) -> None:
    """Show detailed info about a chart."""
    if local:
        registry = _get_local()
        details = registry.info(name)
        if not details:
            console.print(f"[red]Not found: {name}[/red]")
            raise typer.Exit(1)
        _print_info(details)
        return

    try:
        client = _get_client()
        details = client.get_chart(name)
        if not details:
            console.print(f"[red]Not found: {name}[/red]")
            raise typer.Exit(1)
        _print_info(details)
    except Exception as e:
        console.print(f"[red]Failed: {e}[/red]")
        raise typer.Exit(1) from e


def _print_info(d: dict):
    console.print(f"[bold]{d['name']}[/bold] v{d['version']}")
    console.print(f"  Type: {d.get('chart_type', '')}")
    console.print(f"  Description: {d.get('description', '')}")
    console.print(f"  Tags: {', '.join(d.get('tags', [])) or '(none)'}")
    if "versions" in d:
        console.print(f"  Versions: {', '.join(d['versions'])}")
    console.print(f"  Checksum: {d.get('checksum', '')}")
    console.print(f"  Created: {d.get('created_at', '')}")


@app.command()
def remove(
    name: str = typer.Argument(..., help="Chart name to remove"),
    local: bool = typer.Option(False, "-l", "--local", help="Remove from local registry"),
) -> None:
    """Remove a chart from the registry."""
    if local:
        registry = _get_local()
        if registry.remove(name):
            console.print(f"[green]Removed {name} from local registry[/green]")
        else:
            console.print(f"[red]Not found: {name}[/red]")
        return

    try:
        client = _get_client()
        client.delete(name)
        console.print(f"[green]Removed {name} from {REGISTRY_URL}[/green]")
    except Exception as e:
        console.print(f"[red]Failed: {e}[/red]")
        raise typer.Exit(1) from e


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host"),
    port: int = typer.Option(8500, "--port", "-p"),
) -> None:
    """Start the SittingFace registry server."""
    from sittingface.server.app import run_server

    console.print(f"[green]Starting SittingFace Registry on {host}:{port}[/green]")
    run_server(host, port)


@app.command()
def stats(
    local: bool = typer.Option(False, "-l", "--local", help="Local registry stats"),
) -> None:
    """Show registry statistics."""
    if local:
        registry = _get_local()
        charts = registry.list_charts()
        by_type = {}
        for c in charts:
            by_type[c.chart_type] = by_type.get(c.chart_type, 0) + 1
        console.print(f"  Total charts: {len(charts)}")
        for t, count in sorted(by_type.items()):
            console.print(f"  {t}: {count}")
        return

    try:
        client = _get_client()
        s = client.stats()
        console.print(f"  Total charts: {s['total_charts']}")
        for t, count in s.get("by_type", {}).items():
            console.print(f"  {t}: {count}")
        if s.get("tags"):
            console.print(f"  Tags: {', '.join(s['tags'])}")
    except Exception as e:
        console.print(f"[red]Failed: {e}[/red]")
        raise typer.Exit(1) from e


@app.command()
def version() -> None:
    """Show SittingFace version."""
    console.print(f"sittingface v{__import__('sittingface').__version__}")


# ── Git-equivalent commands ────────────────────────────────────────────────────


@app.command()
def init() -> None:
    """Initialize .soma directory for chart version control."""
    from sittingface.history import SomaticHistory

    h = SomaticHistory()
    h.init()
    console.print("[green]Initialized .soma/ directory[/green]")


@app.command()
def commit(
    message: str = typer.Option(..., "-m", "--message", help="Commit message"),
    author: str = typer.Option("soma-cli", "-a", "--author", help="Author name"),
) -> None:
    """Commit current chart state (like git commit)."""
    from sittingface.history import SomaticHistory

    h = SomaticHistory()
    h.init()
    c = h.commit(message=message, author=author)
    if c is None:
        console.print("[yellow]No changes to commit[/yellow]")
        return
    console.print(f"[green]Committed {c.commit_id}[/green] — {message}")
    console.print(f"  Charts: {len(c.charts)}")


@app.command()
def log(
    limit: int = typer.Option(20, "-n", "--limit", help="Number of commits"),
) -> None:
    """Show commit history (like git log)."""
    from sittingface.history import SomaticHistory

    h = SomaticHistory()
    commits = h.log(limit)
    if not commits:
        console.print("[yellow]No commits yet[/yellow]")
        return
    for c in commits:
        console.print(f"[bold]{c['commit_id']}[/bold] {c['message']}")
        console.print(f"  {c['author']} — {c['timestamp'][:19]}")


@app.command()
def status() -> None:
    """Show chart status (like git status)."""
    from sittingface.history import SomaticHistory

    h = SomaticHistory()
    s = h.status()
    console.print(f"Charts: {s['total_charts']}")
    console.print(f"Commits: {s['commits']}")
    console.print(f"Tags: {s['tags']}")
    if s["head"]:
        console.print(f"HEAD: {s['head']}")
    if s["changed"]:
        console.print("\nChanged:")
        for c in s["changed"]:
            console.print(f"  M {c}")
    if s["added"]:
        console.print("\nAdded:")
        for a in s["added"]:
            console.print(f"  A {a}")
    if s["removed"]:
        console.print("\nRemoved:")
        for r in s["removed"]:
            console.print(f"  D {r}")
    if not s["changed"] and not s["added"] and not s["removed"]:
        console.print("  [green]Nothing to commit[/green]")


@app.command()
def show(
    ref: str = typer.Argument(..., help="Commit ID or tag name"),
) -> None:
    """Show details of a commit (like git show)."""
    from sittingface.history import SomaticHistory

    h = SomaticHistory()
    h.init()
    c = h.show(ref)
    if c:
        console.print(f"[bold]{c['commit_id']}[/bold] — {c['message']}")
        console.print(f"Author: {c['author']}")
        console.print(f"Time: {c['timestamp'][:19]}")
        if c.get("parent"):
            console.print(f"Parent: {c['parent']}")
        console.print(f"Charts: {len(c['charts'])}")
    else:
        tags = [t for t in h.list_tags() if t["name"] == ref]
        if tags:
            tag = tags[0]
            console.print(f"[bold]Tag: {tag['name']}[/bold] → {tag['commit_id']}")
            if tag.get("message"):
                console.print(f"Message: {tag['message']}")
        else:
            console.print(f"[red]Not found: {ref}[/red]")


@app.command()
def tag(
    name: str = typer.Argument(..., help="Tag name"),
    message: str = typer.Option("", "-m", "--message", help="Tag message"),
) -> None:
    """Create a tag (like git tag)."""
    from sittingface.history import SomaticHistory

    h = SomaticHistory()
    h.init()
    t = h.tag(name=name, message=message)
    console.print(f"[green]Tagged {name} → {t.commit_id}[/green]")


@app.command()
def revert(
    ref: str = typer.Argument(..., help="Commit ID to revert to"),
) -> None:
    """Revert to a specific commit (like git revert)."""
    from sittingface.history import SomaticHistory

    h = SomaticHistory()
    h.init()
    result = h.revert(ref)
    if "error" in result:
        console.print(f"[red]{result['error']}[/red]")
        return
    console.print(f"[green]Reverted to {ref}[/green] — {result['charts']} charts")


@app.command()
def stash(
    message: str = typer.Option("WIP", "-m", "--message", help="Stash message"),
) -> None:
    """Stash current chart changes (like git stash)."""
    from sittingface.history import SomaticHistory

    h = SomaticHistory()
    h.init()
    c = h.commit(message=f"STASH: {message}", author="soma-stash")
    if c:
        console.print(f"[green]Stashed as {c.commit_id}[/green]")
    else:
        console.print("[yellow]Nothing to stash[/yellow]")


@app.command()
def blame(
    chart: str = typer.Argument(..., help="Chart name to blame"),
) -> None:
    """Show who last modified each line of a chart (like git blame)."""
    from sittingface.history import SomaticHistory

    h = SomaticHistory()
    commits = h.log(limit=100)

    chart_changes = {}
    for c in commits:
        for name, _checksum in c.get("charts", {}).items():
            if name not in chart_changes:
                chart_changes[name] = {
                    "author": c["author"],
                    "date": c["timestamp"][:10],
                    "commit": c["commit_id"],
                }

    if chart in chart_changes:
        info = chart_changes[chart]
        console.print(f"{info['commit']} {info['date']} {info['author']}: {chart}")
    elif chart in chart_changes or any(chart in k for k in chart_changes):
        for name, info in sorted(chart_changes.items()):
            if chart in name:
                console.print(f"{info['commit']} {info['date']} {info['author']}: {name}")
    else:
        console.print(f"[yellow]No history for {chart}[/yellow]")


@app.command()
def remote(
    url: str = typer.Argument(None, help="Registry URL"),
) -> None:
    """Show or set remote registry URL (like git remote)."""
    import os

    if url:
        os.environ["SITTINGFACE_URL"] = url
        console.print(f"[green]Remote set to {url}[/green]")
    else:
        current = os.environ.get("SITTINGFACE_URL", REGISTRY_URL)
        console.print(f"origin  {current}")


@app.command()
def fetch() -> None:
    """Fetch charts from remote registry (like git fetch)."""
    try:
        client = _get_client()
        charts = client.list_charts()
        console.print(f"[green]Fetched {len(charts)} charts from {REGISTRY_URL}[/green]")
    except Exception as e:
        console.print(f"[red]Failed to fetch: {e}[/red]")


@app.command()
def push() -> None:
    """Push local charts to remote registry (like git push)."""
    try:
        client = _get_client()
        repo_root = _find_repo_root()
        somatic_dir = repo_root / "somatic" / "charts"
        published = 0

        for chart_dir in sorted(somatic_dir.iterdir()):
            if chart_dir.is_dir() and (chart_dir / "values.yaml").exists():
                try:
                    client.publish_chart_dir(chart_dir)
                    published += 1
                except Exception:
                    pass

        console.print(f"[green]Pushed {published} charts to {REGISTRY_URL}[/green]")
    except Exception as e:
        console.print(f"[red]Failed to push: {e}[/red]")


@app.command()
def pull_chart(
    name: str = typer.Argument(..., help="Chart name to pull"),
    version: str = typer.Option("latest", "-v", "--version"),
    output: str = typer.Option("./charts", "-o", "--output"),
) -> None:
    """Pull a chart from remote registry (like git pull)."""
    try:
        client = _get_client()
        dest = Path(output) / name
        path = client.pull(name, version, dest)
        if path:
            console.print(f"[green]Pulled {name} v{version} → {path}[/green]")
        else:
            console.print(f"[red]Not found: {name}[/red]")
    except Exception as e:
        console.print(f"[red]Failed to pull: {e}[/red]")


@app.command()
def branch(
    name: str = typer.Argument(None, help="Branch name to create/switch"),
) -> None:
    """List, create, or switch chart branches (like git branch)."""
    from sittingface.history import SomaticHistory

    h = SomaticHistory()
    h.init()
    branches_file = h.soma_dir / "branches.json"
    if not branches_file.exists():
        branches_file.write_text('{"main": ""}')

    branches = json.loads(branches_file.read_text())

    if not name:
        current = h.head_file.read_text().strip() if h.head_file.exists() else ""
        for b, ref in branches.items():
            marker = " *" if ref == current else ""
            console.print(f"  {b}{marker}")
        return

    if name not in branches:
        branches[name] = h.head_file.read_text().strip() if h.head_file.exists() else ""
        branches_file.write_text(json.dumps(branches))
        console.print(f"[green]Created branch {name}[/green]")
    else:
        branches_file.write_text(json.dumps(branches))
        h.head_file.write_text(branches[name])
        console.print(f"[green]Switched to branch {name}[/green]")


@app.command()
def checkout(
    ref: str = typer.Argument(..., help="Commit, tag, or branch to checkout"),
) -> None:
    """Checkout a specific commit/tag/branch (like git checkout)."""
    from sittingface.history import SomaticHistory

    h = SomaticHistory()
    h.init()

    commits = h._load_commits()
    tags = h.list_tags()
    branches_file = h.soma_dir / "branches.json"
    branches = json.loads(branches_file.read_text()) if branches_file.exists() else {}

    if any(c["commit_id"] == ref for c in commits):
        commit_id = ref
    elif ref in [t["name"] for t in tags]:
        tag = next(t for t in tags if t["name"] == ref)
        commit_id = tag["commit_id"]
    elif ref in branches:
        commit_id = branches[ref]
        branches_file.write_text(json.dumps({**branches, ref: commit_id}))
        h.head_file.write_text(commit_id)
        console.print(f"[green]Switched to branch {ref}[/green]")
        return
    else:
        console.print(f"[red]Not found: {ref}[/red]")
        return

    result = h.revert(commit_id)
    if "error" in result:
        console.print(f"[red]{result['error']}[/red]")
    else:
        console.print(f"[green]Checked out {ref}[/green]")


@app.command()
def merge(
    branch: str = typer.Argument(..., help="Branch to merge"),
) -> None:
    """Merge a branch into current (like git merge)."""
    from sittingface.history import SomaticHistory

    h = SomaticHistory()
    h.init()
    branches_file = h.soma_dir / "branches.json"
    branches = json.loads(branches_file.read_text()) if branches_file.exists() else {}

    if branch not in branches:
        console.print(f"[red]Branch not found: {branch}[/red]")
        return

    branch_commit = branches.get(branch, "")
    current_head = h.head_file.read_text().strip() if h.head_file.exists() else ""
    if branch_commit == current_head:
        console.print("[yellow]Already up to date[/yellow]")
        return

    # Merge by committing current state from branch head
    branch_data = h.show(branch_commit)
    if branch_data:
        h._save_chart_state(branch_data.get("charts", {}))
        h.head_file.write_text(branch_commit)
        console.print(f"[green]Merged {branch} ({branch_commit}) into current branch[/green]")
    else:
        console.print(f"[red]Branch commit not found: {branch_commit}[/red]")


@app.command()
def reset(
    ref: str = typer.Option("HEAD~1", "--to", help="Reset to ref"),
) -> None:
    """Reset to a specific state (like git reset)."""
    from sittingface.history import SomaticHistory

    h = SomaticHistory()
    h.init()

    if ref == "HEAD~1":
        commits = h._load_commits()
        if len(commits) > 1:
            commit_id = commits[-2]["commit_id"]
            h.revert(commit_id)
            console.print(f"[green]Reset to {commit_id}[/green]")
        else:
            console.print("[yellow]No previous commit[/yellow]")
    else:
        h.revert(ref)
        console.print(f"[green]Reset to {ref}[/green]")


# ── Vault (password-protected code) ───────────────────────────────────────────


@app.command()
def vault_lock(
    directory: str = typer.Option(
        os.environ.get("SITTINGFACE_GENERATED_DIR", ""),
        "-d",
        "--dir",
        help="Directory to lock",
    ),
    password: str = typer.Option(None, "-p", "--password", help="Password (prompts if not set)"),
) -> None:
    """Encrypt generated code with password protection."""
    import getpass

    from sittingface.vault import CodeVault

    if not password:
        password = getpass.getpass("Enter password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            console.print("[red]Passwords don't match[/red]")
            raise typer.Exit(1)

    if not directory:
        console.print("[red]No directory specified. Use -d or set SITTINGFACE_GENERATED_DIR[/red]")
        raise typer.Exit(1)

    source_dir = Path(directory)
    if not source_dir.exists():
        console.print(f"[red]Directory not found: {directory}[/red]")
        raise typer.Exit(1)

    vault = CodeVault(password=password)
    enc_dir = vault.encrypt_directory(source_dir)

    # Remove original unencrypted files
    import shutil

    backup = source_dir.parent / (source_dir.name + ".backup")
    shutil.move(str(source_dir), str(backup))

    console.print("[green]Code locked with password protection[/green]")
    console.print(f"  Source: {source_dir}")
    console.print(f"  Encrypted: {enc_dir}")
    console.print(f"  Backup: {backup}")
    console.print("  Use 'sittingface vault-unlock' to decrypt")


@app.command()
def vault_unlock(
    directory: str = typer.Option(None, "-d", "--dir", help="Vault directory (default: auto-detect)"),
    password: str = typer.Option(None, "-p", "--password", help="Password"),
    output: str = typer.Option(None, "-o", "--output", help="Output directory"),
) -> None:
    """Decrypt generated code."""
    import getpass

    from sittingface.vault import CodeVault

    if not password:
        password = getpass.getpass("Enter password: ")

    vault_dir = Path(directory) if directory else None
    if vault_dir is None:
        # Auto-detect vault directory — search cwd and generated dir
        search_root = Path(os.environ.get("SITTINGFACE_GENERATED_DIR", str(Path.cwd()))).parent
        for d in search_root.iterdir():
            if d.is_dir() and d.name.endswith(".vault"):
                vault_dir = d
                break

    if not vault_dir or not vault_dir.exists():
        console.print("[red]No vault directory found[/red]")
        raise typer.Exit(1)

    vault = CodeVault(password=password)
    try:
        result = vault.decrypt_directory(vault_dir, password)
        console.print(f"[green]Code unlocked → {result}[/green]")
    except Exception as e:
        console.print("[red]Decryption failed: wrong password?[/red]")
        raise typer.Exit(1) from e


@app.command()
def vault_check(
    password: str = typer.Option(None, "-p", "--password", help="Password to verify"),
) -> None:
    """Verify a password against the vault."""
    import getpass

    from sittingface.vault import CodeVault

    if not password:
        password = getpass.getpass("Enter password: ")

    vault = CodeVault(password=password)
    if vault.check_password(password):
        console.print("[green]Password valid[/green]")
    else:
        console.print("[red]Invalid password[/red]")


@app.command()
def vault_status() -> None:
    """Check vault status — show which directories are encrypted."""
    gen_dir = os.environ.get("SITTINGFACE_GENERATED_DIR", "")
    vault_base = Path(gen_dir).parent if gen_dir else Path.cwd()
    locked = []

    if vault_base.exists():
        for d in vault_base.iterdir():
            if d.is_dir() and d.name.endswith(".vault"):
                locked.append(d.name)
            elif d.is_dir() and (d / ".soma" / "vault").exists():
                locked.append(d.name + ".vault")

    if locked:
        console.print("[red]🔒 Locked vaults:[/red]")
        for vault_name in locked:
            console.print(f"  {vault_name}")
    else:
        console.print("[green]🔓 No locked vaults[/green]")


if __name__ == "__main__":
    app()
