"""Somatic History — git-like version control for somatic charts.

Tracks chart changes as commits, supports branches, tags, and log.
State stored in .soma/ directory.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

SOMA_DIR = Path(".soma")


class SomaticCommit:
    """A single commit of chart state."""

    def __init__(
        self,
        message: str,
        charts: dict[str, str],
        author: str = "",
        parent: str | None = None,
    ):
        self.commit_id = self._generate_id(message, charts)
        self.message = message
        self.author = author or "soma-cli"
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.charts = charts  # chart_name → checksum of values.yaml
        self.parent = parent

    def _generate_id(self, message: str, charts: dict) -> str:
        raw = f"{message}:{json.dumps(charts, sort_keys=True)}"
        return hashlib.sha256(raw.encode()).hexdigest()[:12]

    def to_dict(self) -> dict:
        return {
            "commit_id": self.commit_id,
            "message": self.message,
            "author": self.author,
            "timestamp": self.timestamp,
            "charts": self.charts,
            "parent": self.parent,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SomaticCommit":
        c = cls.__new__(cls)
        c.commit_id = d["commit_id"]
        c.message = d["message"]
        c.author = d.get("author", "soma-cli")
        c.timestamp = d.get("timestamp", "")
        c.charts = d.get("charts", {})
        c.parent = d.get("parent")
        return c


class SomaticTag:
    """A named tag pointing to a commit."""

    def __init__(self, name: str, commit_id: str, message: str = ""):
        self.name = name
        self.commit_id = commit_id
        self.message = message
        self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "commit_id": self.commit_id,
            "message": self.message,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SomaticTag":
        t = cls.__new__(cls)
        t.name = d["name"]
        t.commit_id = d["commit_id"]
        t.message = d.get("message", "")
        t.timestamp = d.get("timestamp", "")
        return t


class SomaticHistory:
    """Git-like version control for somatic charts."""

    def __init__(self, repo_root: Path | None = None):
        self.repo_root = repo_root or Path.cwd()
        self.soma_dir = self.repo_root / ".soma"
        self.commits_file = self.soma_dir / "commits.json"
        self.tags_file = self.soma_dir / "tags.json"
        self.head_file = self.soma_dir / "HEAD"
        self.chart_state_file = self.soma_dir / "chart_state.json"

    def init(self) -> None:
        """Initialize .soma directory."""
        self.soma_dir.mkdir(parents=True, exist_ok=True)
        if not self.commits_file.exists():
            self.commits_file.write_text("[]")
        if not self.tags_file.exists():
            self.tags_file.write_text("[]")
        if not self.chart_state_file.exists():
            self.chart_state_file.write_text("{}")
        if not self.head_file.exists():
            self.head_file.write_text("")

    def _load_commits(self) -> list[dict]:
        if self.commits_file.exists():
            return json.loads(self.commits_file.read_text())
        return []

    def _save_commits(self, commits: list[dict]) -> None:
        self.commits_file.write_text(json.dumps(commits, indent=2))

    def _load_tags(self) -> list[dict]:
        if self.tags_file.exists():
            return json.loads(self.tags_file.read_text())
        return []

    def _save_tags(self, tags: list[dict]) -> None:
        self.tags_file.write_text(json.dumps(tags, indent=2))

    def _load_chart_state(self) -> dict[str, str]:
        if self.chart_state_file.exists():
            return json.loads(self.chart_state_file.read_text())
        return {}

    def _save_chart_state(self, state: dict[str, str]) -> None:
        self.chart_state_file.write_text(json.dumps(state, indent=2))

    def _checksum(self, path: Path) -> str:
        import hashlib

        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]

    def _get_current_charts(self) -> dict[str, str]:
        """Get checksums of all current charts."""

        charts = {}
        somatic_dir = self.repo_root / "somatic" / "charts"
        if not somatic_dir.exists():
            return charts
        for chart_dir in somatic_dir.iterdir():
            if chart_dir.is_dir():
                values_file = chart_dir / "values.yaml"
                if values_file.exists():
                    charts[chart_dir.name] = self._checksum(values_file)
                for sub in chart_dir.iterdir():
                    if sub.is_dir() and (sub / "values.yaml").exists():
                        charts[f"{chart_dir.name}/{sub.name}"] = self._checksum(sub / "values.yaml")
        return charts

    def commit(self, message: str, author: str = "") -> SomaticCommit:
        """Commit current chart state."""
        self.init()
        charts = self._get_current_charts()
        old_state = self._load_chart_state()

        changed = {k: v for k, v in charts.items() if old_state.get(k) != v}
        added = {k: v for k, v in charts.items() if k not in old_state}
        removed = {k: v for k, v in old_state.items() if k not in charts}

        if not changed and not added and not removed:
            return None

        commits = self._load_commits()
        parent = commits[-1]["commit_id"] if commits else None

        commit = SomaticCommit(
            message=message,
            charts=charts,
            author=author,
            parent=parent,
        )

        commits.append(commit.to_dict())
        self._save_commits(commits)
        self._save_chart_state(charts)
        self.head_file.write_text(commit.commit_id)

        return commit

    def log(self, limit: int = 20) -> list[dict]:
        """Show commit history."""
        commits = self._load_commits()
        return list(reversed(commits[-limit:]))

    def show(self, commit_id: str) -> dict | None:
        """Show a specific commit."""
        commits = self._load_commits()
        for c in commits:
            if c["commit_id"] == commit_id:
                return c
        return None

    def tag(self, name: str, commit_id: str | None = None, message: str = "") -> SomaticTag:
        """Create a tag."""
        self.init()
        if commit_id is None:
            commit_id = self.head_file.read_text().strip() if self.head_file.exists() else ""

        tag = SomaticTag(name=name, commit_id=commit_id, message=message)
        tags = self._load_tags()
        tags.append(tag.to_dict())
        self._save_tags(tags)
        return tag

    def list_tags(self) -> list[dict]:
        """List all tags."""
        return self._load_tags()

    def status(self) -> dict:
        """Show current status."""
        self.init()
        charts = self._get_current_charts()
        old_state = self._load_chart_state()

        changed = [k for k in charts if old_state.get(k) != charts[k]]
        added = [k for k in charts if k not in old_state]
        removed = [k for k in old_state if k not in charts]

        commits = self._load_commits()
        tags = self._load_tags()
        head = self.head_file.read_text().strip() if self.head_file.exists() else ""

        return {
            "total_charts": len(charts),
            "changed": changed,
            "added": added,
            "removed": removed,
            "commits": len(commits),
            "tags": len(tags),
            "head": head,
        }

    def diff(self, commit_id: str | None = None) -> dict:
        """Diff current state against a commit or previous state."""
        charts = self._get_current_charts()

        if commit_id:
            commit = self.show(commit_id)
            if not commit:
                return {"error": f"Commit not found: {commit_id}"}
            prev = commit["charts"]
        else:
            commits = self._load_commits()
            prev = commits[-1]["charts"] if commits else {}

        changed = {
            k: {"old": prev.get(k), "new": charts.get(k)}
            for k in set(list(charts.keys()) + list(prev.keys()))
            if charts.get(k) != prev.get(k)
        }

        return {
            "changed": len(changed),
            "details": changed,
        }

    def revert(self, commit_id: str) -> dict:
        """Revert to a specific commit's chart state."""
        commit = self.show(commit_id)
        if not commit:
            return {"error": f"Commit not found: {commit_id}"}

        self.init()
        self._save_chart_state(commit["charts"])
        self.head_file.write_text(commit_id)

        return {
            "status": "reverted",
            "commit": commit_id,
            "charts": len(commit["charts"]),
        }
