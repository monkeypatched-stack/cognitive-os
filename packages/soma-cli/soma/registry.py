"""Soma registry — Loads and resolves soma charts from the filesystem."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from soma.models import (
    Chart,
    ChartMetadata,
    ConstitutionalResource,
    ResourceKind,
    ResourceMetadata,
    SomaRelease,
)

CHART_FILE = "Chart.yaml"
VALUES_FILE = "values.yaml"
TEMPLATES_DIR = "templates"


class SomaRegistry:
    """Discovers, loads, and resolves all soma charts under a root directory."""

    def __init__(self, charts_root: Path, global_values_path: Path | None = None):
        self.charts_root = charts_root
        self.global_values: dict[str, Any] = {}

        if global_values_path and global_values_path.exists():
            self.global_values = self._load_yaml(global_values_path) or {}

    def load_release(self, release_name: str = "agentos") -> SomaRelease:
        """Load all charts and return a fully resolved SomaRelease."""
        charts = []
        for chart_dir in sorted(self._discover_chart_dirs()):
            chart = self._load_chart(chart_dir)
            if chart:
                charts.append(chart)
        return SomaRelease(name=release_name, charts=charts, global_values=self.global_values)

    def load_chart(self, name: str) -> Chart | None:
        """Load a single chart by name."""
        chart_dir = self.charts_root / name
        if not chart_dir.exists():
            for d in self.charts_root.iterdir():
                if d.is_dir() and d.name == name:
                    return self._load_chart(d)
            return None
        return self._load_chart(chart_dir)

    def list_charts(self) -> list[str]:
        return [d.name for d in self._discover_chart_dirs()]

    def _discover_chart_dirs(self) -> list[Path]:
        if not self.charts_root.exists():
            return []
        return [d for d in self.charts_root.iterdir() if d.is_dir() and (d / CHART_FILE).exists()]

    def _load_chart(self, chart_dir: Path) -> Chart | None:
        chart_file = chart_dir / CHART_FILE
        if not chart_file.exists():
            return None

        raw_meta = self._load_yaml(chart_file) or {}
        metadata = ChartMetadata(
            name=raw_meta.get("name", chart_dir.name),
            version=raw_meta.get("version", "1.0.0"),
            description=raw_meta.get("description"),
            apiVersion=raw_meta.get("apiVersion", "v2"),
        )

        chart_values = self._load_yaml(chart_dir / VALUES_FILE) or {}
        merged = self._deep_merge(self._deep_merge({}, self.global_values), chart_values)
        resources = self._parse_templates(chart_dir / TEMPLATES_DIR, merged)

        return Chart(metadata=metadata, values=merged, resources=resources)

    def _parse_templates(
        self, templates_dir: Path, values: dict[str, Any]
    ) -> list[ConstitutionalResource]:
        resources: list[ConstitutionalResource] = []
        if not templates_dir.exists():
            return resources

        for template_file in sorted(templates_dir.glob("*.yaml")):
            docs = self._load_yaml_multi(template_file)
            for doc in docs:
                if not doc or "kind" not in doc:
                    continue
                resource = self._parse_resource(doc, values)
                if resource:
                    resources.append(resource)
        return resources

    def _parse_resource(
        self, doc: dict[str, Any], values: dict[str, Any]
    ) -> ConstitutionalResource | None:
        try:
            kind_str = doc.get("kind", "")
            try:
                kind = ResourceKind(kind_str)
            except ValueError:
                return None

            raw_meta = doc.get("metadata", {})
            metadata = ResourceMetadata(
                name=raw_meta.get("name", "unknown"),
                module=raw_meta.get("module") or values.get("module", {}).get("name"),
                layer=raw_meta.get("layer") or values.get("module", {}).get("layer"),
                alias=raw_meta.get("alias") or values.get("module", {}).get("alias"),
                biologicalRole=raw_meta.get("biologicalRole")
                or values.get("module", {}).get("biologicalRole"),
                softwareRole=raw_meta.get("softwareRole")
                or values.get("module", {}).get("softwareRole"),
                phase=raw_meta.get("phase"),
                source=raw_meta.get("source") or values.get("module", {}).get("source"),
                id=raw_meta.get("id"),
                severity=raw_meta.get("severity"),
            )

            return ConstitutionalResource(
                apiVersion=doc.get("apiVersion", "constitution.agentos.io/v1"),
                kind=kind,
                metadata=metadata,
                spec=doc.get("spec", {}),
            )
        except Exception:
            return None

    @staticmethod
    def _load_yaml(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        with open(path) as f:
            return yaml.safe_load(f)

    @staticmethod
    def _load_yaml_multi(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        with open(path) as f:
            return [doc for doc in yaml.safe_load_all(f) if doc]

    @staticmethod
    def _deep_merge(base: dict, override: dict) -> dict:
        result = dict(base)
        for k, v in override.items():
            if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                result[k] = SomaRegistry._deep_merge(result[k], v)
            else:
                result[k] = v
        return result
