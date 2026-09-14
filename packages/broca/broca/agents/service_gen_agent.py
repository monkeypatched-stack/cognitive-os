"""ServiceGenAgent — generates a DDD-layered service from a compiled somatic prompt.

Wraps CodeGenAgent.generate_ddd_service(). Called by soma_codegen.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

from ._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.service_gen_agent")

_REPO = Path(os.environ.get("MONKEYBRAIN_REPO", str(Path(__file__).parents[4])))


def _ensure_sittingface():
    src = _REPO / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


class ServiceGenAgent(BaseETASSAgent):
    agent_type = "service_gen"
    description = "Generates a DDD-layered microservice from a compiled somatic prompt via CodeGenAgent"

    # Codegen must never run inside a simulation context — it writes real files.
    _SIMULATION_BLOCKED = True

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict) -> dict:
        if context.get("question_source") == "simulation":
            self._reward(False, 0.0)
            return self._result(
                payload={
                    "generated": False,
                    "error": "codegen blocked in simulation context",
                },
                observations=["ServiceGenAgent must not run during simulation — dry_run path only"],
            )
        service_slug = str(context.get("service_slug", "")).strip()
        prompt_path = Path(str(context.get("prompt_path", "")))
        output_dir = Path(str(context.get("output_dir", _REPO / "generated")))

        if not service_slug:
            self._reward(False, 0.0)
            return self._result(payload={"generated": False}, observations=["no service_slug"])

        if not str(context.get("prompt_path", "")).strip() or not prompt_path.is_file():
            # Try default location
            default = _REPO / "somatic" / "compiled" / f"{service_slug}-api.prompt.md"
            if default.exists():
                prompt_path = default
            else:
                candidates = list((_REPO / "somatic" / "compiled").glob(f"*{service_slug}*.prompt.md"))
                if candidates:
                    prompt_path = candidates[0]
                else:
                    self._reward(False, 0.0)
                    return self._result(
                        payload={
                            "generated": False,
                            "error": f"prompt not found: {prompt_path}",
                        },
                        observations=[f"no compiled prompt for {service_slug}"],
                    )

        _ensure_sittingface()
        try:
            from sittingface.codegen_agent import CodeGenAgent
        except ImportError as e:
            self._reward(False, 0.0)
            return self._result(
                payload={"generated": False, "error": repr(e)},
                observations=[f"CodeGenAgent import failed: {e}"],
            )

        svc_out = output_dir / service_slug
        svc_out.mkdir(parents=True, exist_ok=True)

        agent = CodeGenAgent(output_dir=svc_out)
        llm_name = type(agent.llm).__name__ if agent.llm else "none"
        logger.info("[service_gen] using %s for %s", llm_name, service_slug)

        prompt_content = prompt_path.read_text()
        files: dict[str, str] = await agent.generate_ddd_service(prompt_content, service_slug, svc_out)

        if not files:
            self._reward(False, 0.2)
            return self._result(
                payload={"generated": False, "llm": llm_name},
                observations=["CodeGenAgent returned no files — check LLM output"],
            )

        # Layer name aliases the LLM emits instead of the canonical directory name
        _LAYER_ALIASES: dict[str, str] = {
            "app": "application",
            "apps": "application",
            "svc": "application",
            "service": "application",
        }
        # Application-layer subdirs that appear without their parent prefix
        _APP_SUBDIRS = {"commands", "queries", "handlers", "dto", "use_cases"}
        # Infrastructure-layer subdirs
        _INFRA_SUBDIRS = {"persistence", "database", "repositories"}
        # Top-level DDD layer names (canonical — leave as-is)
        _DDD_LAYERS = {"domain", "application", "infrastructure", "api"}

        def _normalize_layer(parts: tuple[str, ...]) -> tuple[str, ...]:
            if not parts:
                return parts
            first = parts[0]
            # Canonical layers — no change
            if first in _DDD_LAYERS:
                return parts
            # Direct alias (app → application)
            canonical = _LAYER_ALIASES.get(first)
            if canonical:
                return (canonical,) + parts[1:]
            # Strip src/ wrapper: src/domain/... → domain/...
            if first == "src" and len(parts) > 1:
                rest = parts[1:]
                if rest[0] in _DDD_LAYERS:
                    return rest
                canon2 = _LAYER_ALIASES.get(rest[0])
                if canon2:
                    return (canon2,) + rest[1:]
            # Bare application subdirs: commands/... → application/commands/...
            if first in _APP_SUBDIRS:
                return ("application",) + parts
            # Bare infra subdirs: persistence/... → infrastructure/persistence/...
            if first in _INFRA_SUBDIRS:
                return ("infrastructure",) + parts
            return parts

        written: list[str] = []
        for rel_path, content in files.items():
            # Strip leading slash — LLM often emits absolute-looking paths
            safe = Path(rel_path)
            if safe.is_absolute():
                safe = Path(*safe.parts[1:])
            parts = safe.parts
            # Strip leading service slug to avoid double-nesting
            if parts and parts[0] == service_slug:
                parts = parts[1:]
            # Normalize layer aliases (app/ → application/, etc.)
            parts = _normalize_layer(parts)
            safe = Path(*parts) if parts else safe
            print(f"  [service_gen] filename: {str(safe)!r}")
            dest = svc_out / safe
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)
            written.append(str(dest.relative_to(_REPO)))

        self._reward(True, 0.9)
        return self._result(
            payload={
                "generated": True,
                "service_slug": service_slug,
                "service_dir": str(svc_out.relative_to(_REPO)),
                "files": written,
                "llm": llm_name,
            },
            observations=[f"generated {len(written)} files → {svc_out.relative_to(_REPO)}/"],
        )
