"""ArtifactPackagingAgent — builds a versioned, deployable artifact.

Distinct from SourceControlAgent/CompileAgentAgent/Soma* agents (packaging
git commits, prompt compilation, chart values): this agent packages the
*generated service* itself — builds its container image and bumps its
version/changelog — so ReleaseAgent/DeploymentAgent has something concrete
to ship rather than a source checkout.
"""

from __future__ import annotations
import asyncio
import logging
import os
import re
import subprocess
from datetime import date
from pathlib import Path
from typing import Any
from ._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.artifact_packaging")

_REPO = Path(os.environ.get("MONKEYBRAIN_REPO", str(Path(__file__).parents[4])))


def _bump_patch(version: str) -> str:
    parts = version.split(".")
    while len(parts) < 3:
        parts.append("0")
    try:
        parts[2] = str(int(parts[2]) + 1)
    except ValueError:
        parts[2] = "1"
    return ".".join(parts[:3])


class ArtifactPackagingAgent(BaseETASSAgent):
    agent_type = "artifact_packaging"
    description = "Builds a versioned container image and changelog entry for a generated service"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict):
        service_slug = str(context.get("service_slug", "")).strip()
        if not service_slug:
            self._reward(False, 0.0)
            return self._result(payload={"packaged": False}, observations=["no service_slug in context"])

        output_dir = Path(str(context.get("output_dir") or _REPO / "generated"))
        service_dir = output_dir / service_slug
        if not service_dir.exists():
            self._reward(False, 0.1)
            return self._result(
                payload={
                    "packaged": False,
                    "error": f"service dir not found: {service_dir}",
                },
                observations=[f"generated service directory missing: {service_dir}"],
            )

        version = self._bump_version(service_dir, context)
        changelog_entry = self._write_changelog(service_dir, service_slug, version, context)
        image_tag, build_ok, build_output = await self._docker_build(service_dir, service_slug, version)

        self._reward(build_ok, 0.5)
        artifacts = []
        try:
            from src.monkey_brain.kernel.execute.runtime.outcome import Artifact

            if build_ok:
                artifacts = [Artifact(kind="container_image", name=image_tag, uri=image_tag)]
        except ImportError:
            pass

        return self._result(
            payload={
                "packaged": build_ok,
                "version": version,
                "image_tag": image_tag if build_ok else "",
                "changelog_entry": changelog_entry,
            },
            artifacts=artifacts,
            observations=([build_output[-300:]] if not build_ok else [f"built {image_tag}"]),
        )

    def _bump_version(self, service_dir: Path, context: dict) -> str:
        if context.get("version"):
            return str(context["version"])
        pyproject = service_dir / "pyproject.toml"
        current = "0.1.0"
        if pyproject.exists():
            try:
                text = pyproject.read_text()
                m = re.search(r'version\s*=\s*"([^"]+)"', text)
                if m:
                    current = m.group(1)
                    new_version = _bump_patch(current)
                    pyproject.write_text(text.replace(f'version = "{current}"', f'version = "{new_version}"', 1))
                    return new_version
            except Exception as e:
                logger.warning("[artifact_packaging] failed to bump pyproject version: %s", e)
        return _bump_patch(current)

    def _write_changelog(self, service_dir: Path, service_slug: str, version: str, context: dict) -> str:
        entry = (
            f"## {version} — {date.today().isoformat()}\n\n- {context.get('changelog_note', 'Automated release.')}\n\n"
        )
        try:
            changelog = service_dir / "CHANGELOG.md"
            existing = changelog.read_text() if changelog.exists() else f"# {service_slug} Changelog\n\n"
            changelog.write_text(existing + entry)
        except Exception as e:
            logger.warning("[artifact_packaging] failed to write changelog: %s", e)
        return entry

    async def _docker_build(self, service_dir: Path, service_slug: str, version: str) -> tuple[str, bool, str]:
        image_tag = f"{service_slug}:{version}"
        dockerfile = service_dir / "Dockerfile"
        if not dockerfile.exists():
            return image_tag, False, "no Dockerfile found — skipping build"
        try:
            loop = asyncio.get_event_loop()
            r = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    ["docker", "build", "-t", image_tag, "."],
                    cwd=str(service_dir),
                    capture_output=True,
                    text=True,
                    timeout=300,
                ),
            )
            return image_tag, r.returncode == 0, (r.stdout + r.stderr)
        except FileNotFoundError:
            return image_tag, False, "docker not installed"
        except Exception as e:
            return image_tag, False, str(e)
