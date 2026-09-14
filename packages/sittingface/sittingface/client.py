"""SittingFace Online Registry — Python client for push/pull/search."""

from __future__ import annotations

from pathlib import Path

import httpx
import yaml


class SittingFaceClient:
    """Client for the SittingFace online registry."""

    def __init__(self, registry_url: str = "http://localhost:8500"):
        self.registry_url = registry_url.rstrip("/")
        self._http = httpx.Client(timeout=30.0)

    def health(self) -> dict:
        r = self._http.get(f"{self.registry_url}/health")
        r.raise_for_status()
        return r.json()

    def list_charts(
        self,
        chart_type: str | None = None,
        tag: str | None = None,
        search: str | None = None,
    ) -> list[dict]:
        params = {}
        if chart_type:
            params["type"] = chart_type
        if tag:
            params["tag"] = tag
        if search:
            params["search"] = search
        r = self._http.get(f"{self.registry_url}/api/v1/charts", params=params)
        r.raise_for_status()
        return r.json()

    def get_chart(self, name: str) -> dict:
        r = self._http.get(f"{self.registry_url}/api/v1/charts/{name}")
        if r.status_code == 404:
            return {}
        r.raise_for_status()
        return r.json()

    def get_chart_version(self, name: str, version: str) -> dict:
        r = self._http.get(f"{self.registry_url}/api/v1/charts/{name}/{version}")
        if r.status_code == 404:
            return {}
        r.raise_for_status()
        return r.json()

    def publish(
        self,
        name: str,
        version: str,
        values_yaml: str,
        description: str = "",
        tags: list[str] | None = None,
        chart_type: str = "module",
        templates: dict[str, str] | None = None,
        sub_charts: dict[str, dict[str, str]] | None = None,
    ) -> dict:
        payload = {
            "name": name,
            "version": version,
            "values_yaml": values_yaml,
            "description": description,
            "tags": tags or [],
            "chart_type": chart_type,
            "templates": templates or {},
            "sub_charts": sub_charts or {},
        }
        r = self._http.post(f"{self.registry_url}/api/v1/charts/publish", json=payload)
        r.raise_for_status()
        return r.json()

    def publish_chart_dir(self, chart_dir: Path, tags: list[str] | None = None) -> dict:
        """Publish a local chart directory to the online registry."""
        values_file = chart_dir / "values.yaml"
        if not values_file.exists():
            raise FileNotFoundError(f"No values.yaml in {chart_dir}")

        values_yaml = values_file.read_text()
        values = yaml.safe_load(values_yaml) or {}

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

        if values.get("capability"):
            chart_type = "capability"
        elif values.get("agent"):
            chart_type = "agent"
        elif values.get("helm"):
            chart_type = "helm"
        else:
            chart_type = "module"

        templates = {}
        templates_dir = chart_dir / "templates"
        if templates_dir.exists():
            for f in templates_dir.glob("*.yaml"):
                templates[f.name] = f.read_text()

        sub_charts = {}
        for sub in chart_dir.iterdir():
            if sub.is_dir() and sub.name != "templates":
                sub_values = sub / "values.yaml"
                if sub_values.exists():
                    sub_templates = {}
                    sub_templates_dir = sub / "templates"
                    if sub_templates_dir.exists():
                        for f in sub_templates_dir.glob("*.yaml"):
                            sub_templates[f.name] = f.read_text()
                    sub_charts[sub.name] = {
                        "values_yaml": sub_values.read_text(),
                        "templates": sub_templates,
                    }

        return self.publish(
            name,
            version,
            values_yaml,
            description,
            tags,
            chart_type,
            templates,
            sub_charts,
        )

    def pull(self, name: str, version: str = "latest", dest: Path | None = None) -> Path | None:
        """Pull a chart from the registry to a local directory."""
        if version == "latest":
            info = self.get_chart(name)
            if not info or not info.get("versions"):
                return None
            version = info["versions"][-1]

        # Use /download which wraps the response in {"meta": ..., "data": ...}
        r = self._http.get(f"{self.registry_url}/api/v1/charts/{name}/{version}/download")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        envelope = r.json()

        chart_data = envelope.get("data", {})

        if dest is None:
            dest = Path(".sittingface_cache") / name / version
        dest.mkdir(parents=True, exist_ok=True)

        (dest / "values.yaml").write_text(chart_data.get("values_yaml", ""))

        templates = chart_data.get("templates", {})
        if templates:
            templates_dir = dest / "templates"
            templates_dir.mkdir(exist_ok=True)
            for fname, content in templates.items():
                (templates_dir / fname).write_text(content)

        sub_charts = chart_data.get("sub_charts", {})
        for sub_name, sub_data in sub_charts.items():
            sub_dir = dest / sub_name
            sub_dir.mkdir(exist_ok=True)
            (sub_dir / "values.yaml").write_text(sub_data.get("values_yaml", ""))
            sub_templates = sub_data.get("templates", {})
            if sub_templates:
                sub_templates_dir = sub_dir / "templates"
                sub_templates_dir.mkdir(exist_ok=True)
                for fname, content in sub_templates.items():
                    (sub_templates_dir / fname).write_text(content)

        return dest

    def delete(self, name: str) -> dict:
        r = self._http.delete(f"{self.registry_url}/api/v1/charts/{name}")
        r.raise_for_status()
        return r.json()

    def stats(self) -> dict:
        r = self._http.get(f"{self.registry_url}/api/v1/stats")
        r.raise_for_status()
        return r.json()

    def close(self):
        self._http.close()
