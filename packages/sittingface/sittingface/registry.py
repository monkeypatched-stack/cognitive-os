"""SittingFace Registry — local chart store with metadata index."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

REGISTRY_DIR = Path.home() / ".sittingface" / "registry"
INDEX_FILE = REGISTRY_DIR / "index.json"


class ChartMeta:
    """Metadata for a single chart in the registry."""

    def __init__(
        self,
        name: str,
        version: str,
        description: str = "",
        tags: list[str] | None = None,
        chart_type: str = "module",
        source_path: str = "",
        checksum: str = "",
        created_at: str = "",
        updated_at: str = "",
        extra: dict[str, Any] | None = None,
    ):
        self.name = name
        self.version = version
        self.description = description
        self.tags = tags or []
        self.chart_type = chart_type
        self.source_path = source_path
        self.checksum = checksum
        self.created_at = created_at or datetime.now(timezone.utc).isoformat()
        self.updated_at = updated_at or self.created_at
        self.extra = extra or {}

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "tags": self.tags,
            "chart_type": self.chart_type,
            "source_path": self.source_path,
            "checksum": self.checksum,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ChartMeta":
        return cls(**{k: v for k, v in d.items() if k in cls.__init__.__code__.co_varnames})


class SittingFaceRegistry:
    """Local registry for somatic charts — HuggingFace-style."""

    def __init__(self, registry_dir: Path | None = None):
        self.registry_dir = registry_dir or REGISTRY_DIR
        self.registry_dir.mkdir(parents=True, exist_ok=True)
        self._index_file = self.registry_dir / "index.json"
        self.index = self._load_index()

    def _load_index(self) -> dict[str, dict]:
        if self._index_file.exists():
            return json.loads(self._index_file.read_text())
        return {}

    def _save_index(self) -> None:
        self._index_file.write_text(json.dumps(self.index, indent=2))

    def _checksum(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]

    # ── Public API ─────────────────────────────────────────────────────────────

    def publish(self, chart_dir: Path, tags: list[str] | None = None) -> ChartMeta:
        """Publish a chart directory to the registry."""
        values_file = chart_dir / "values.yaml"
        if not values_file.exists():
            raise FileNotFoundError(f"No values.yaml in {chart_dir}")

        values = yaml.safe_load(values_file.read_text()) or {}
        name = (
            values.get("module", {}).get("name")
            or values.get("capability", {}).get("name")
            or values.get("agent", {}).get("name")
            or chart_dir.name
        )
        version = (
            values.get("module", {}).get("version")
            or values.get("capability", {}).get("version")
            or values.get("agent", {}).get("version")
            or "1.0.0"
        )
        description = (
            values.get("module", {}).get("softwareRole")
            or values.get("capability", {}).get("description")
            or values.get("agent", {}).get("description")
            or ""
        )

        checksum = self._checksum(values_file)

        # Determine chart type
        if values.get("capability"):
            chart_type = "capability"
        elif values.get("agent"):
            chart_type = "agent"
        else:
            chart_type = "module"

        meta = ChartMeta(
            name=name,
            version=version,
            description=description,
            tags=tags or [],
            chart_type=chart_type,
            source_path=str(chart_dir),
            checksum=checksum,
        )

        # Store in registry
        chart_dest = self.registry_dir / name / version
        chart_dest.mkdir(parents=True, exist_ok=True)

        # Copy values and templates
        import shutil

        shutil.copy2(values_file, chart_dest / "values.yaml")
        templates_dir = chart_dir / "templates"
        if templates_dir.exists():
            dest_templates = chart_dest / "templates"
            dest_templates.mkdir(exist_ok=True)
            for f in templates_dir.glob("*.yaml"):
                shutil.copy2(f, dest_templates / f.name)

        # Copy any sub-chart values (capabilities/*, agents/*)
        for sub in chart_dir.iterdir():
            if sub.is_dir() and sub.name != "templates":
                sub_dest = chart_dest / sub.name
                sub_dest.mkdir(exist_ok=True)
                sub_values = sub / "values.yaml"
                if sub_values.exists():
                    shutil.copy2(sub_values, sub_dest / "values.yaml")

        self.index[name] = meta.to_dict()
        self._save_index()

        return meta

    def get(self, name: str, version: str = "latest") -> Path | None:
        """Get a chart from the registry."""
        if name not in self.index:
            return None

        if version == "latest":
            chart_dir = self.registry_dir / name
            versions = sorted([d.name for d in chart_dir.iterdir() if d.is_dir()])
            if not versions:
                return None
            version = versions[-1]

        chart_path = self.registry_dir / name / version
        return chart_path if chart_path.exists() else None

    def list_charts(self, chart_type: str | None = None) -> list[ChartMeta]:
        """List all charts in the registry."""
        charts = []
        for name, meta_dict in self.index.items():
            meta = ChartMeta.from_dict(meta_dict)
            if chart_type and meta.chart_type != chart_type:
                continue
            charts.append(meta)
        return charts

    def search(self, query: str) -> list[ChartMeta]:
        """Search charts by name, description, or tags."""
        results = []
        query_lower = query.lower()
        for name, meta_dict in self.index.items():
            meta = ChartMeta.from_dict(meta_dict)
            if (
                query_lower in meta.name.lower()
                or query_lower in meta.description.lower()
                or any(query_lower in tag.lower() for tag in meta.tags)
            ):
                results.append(meta)
        return results

    def remove(self, name: str) -> bool:
        """Remove a chart from the registry."""
        if name not in self.index:
            return False

        chart_dir = self.registry_dir / name
        if chart_dir.exists():
            import shutil

            shutil.rmtree(chart_dir)

        del self.index[name]
        self._save_index()
        return True

    def info(self, name: str) -> dict | None:
        """Get detailed info about a chart."""
        if name not in self.index:
            return None
        meta = ChartMeta.from_dict(self.index[name])
        chart_path = self.registry_dir / name
        versions = sorted([d.name for d in chart_path.iterdir() if d.is_dir()]) if chart_path.exists() else []
        return {
            **meta.to_dict(),
            "versions": versions,
            "latest": versions[-1] if versions else None,
        }
