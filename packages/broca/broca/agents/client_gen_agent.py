"""ClientGenAgent — generates a typed httpx API client from a DDD service's API layer.

Reads router.py + schemas.py from the generated service, loads the api-client
somatic chart invariants, and calls CodeGenAgent to produce the client package.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

from ._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.client_gen_agent")

_REPO = Path(os.environ.get("MONKEYBRAIN_REPO", str(Path(__file__).parents[4])))


def _ensure_sittingface():
    src = _REPO / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


class ClientGenAgent(BaseETASSAgent):
    agent_type = "client_gen"
    description = "Generates a typed httpx API client from a generated DDD service's API layer"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict) -> dict:
        service_slug = str(context.get("service_slug", "")).strip()
        output_dir = Path(str(context.get("output_dir", _REPO / "generated")))
        base_url = str(context.get("base_url", "http://localhost:8090"))

        if not service_slug:
            self._reward(False, 0.0)
            return self._result(payload={"generated": False}, observations=["no service_slug"])

        out_base = output_dir if output_dir.is_absolute() else _REPO / output_dir
        svc_dir = out_base / service_slug

        if not svc_dir.exists():
            self._reward(False, 0.0)
            return self._result(
                payload={
                    "generated": False,
                    "error": f"service dir not found: {svc_dir}",
                },
                observations=["run codegen first"],
            )

        # ── Find API files ────────────────────────────────────────────────────
        def _read_first(*paths: Path) -> tuple[str, str]:
            for p in paths:
                if p.exists():
                    return p.read_text(), str(p.relative_to(svc_dir))
            return "", ""

        router_content, router_path = _read_first(
            svc_dir / "api" / "router.py",
            svc_dir / "api" / "routers" / f"{service_slug}.py",
            svc_dir / service_slug / "api" / "router.py",
            svc_dir / "routes.py",
        )
        schemas_content, schemas_path = _read_first(
            svc_dir / "api" / "schemas.py",
            svc_dir / service_slug / "api" / "schemas.py",
            svc_dir / "schemas.py",
        )

        if not router_content and not schemas_content:
            self._reward(False, 0.1)
            return self._result(
                payload={
                    "generated": False,
                    "error": "no api/router.py or api/schemas.py found",
                },
                observations=["API files not found — run codegen first"],
            )

        # ── Load api-client chart invariants ──────────────────────────────────
        chart_path = _REPO / "somatic" / "charts" / "api-client" / "values.yaml"
        chart_preamble, chart_cot, chart_invariants_str = "", "", ""
        if chart_path.exists():
            try:
                import yaml

                chart = yaml.safe_load(chart_path.read_text()) or {}
                chart_preamble = chart.get("preamble", {}).get("statement", "")
                steps = chart.get("cot", {}).get("steps", [])
                chart_cot = "\n".join(
                    f"Step {s.get('step', '?')}: {s.get('title', '')} — {s.get('description', '')}" for s in steps
                )
                invs = chart.get("invariants", [])
                chart_invariants_str = "\n".join(f"  [{inv.get('id', '')}] {inv.get('statement', '')}" for inv in invs)
            except Exception as e:
                logger.debug("[client_gen] chart load failed: %s", e)

        # ── Build codegen prompt ──────────────────────────────────────────────
        system = (
            "You are a Python API client generator. Generate a fully-typed httpx client "
            "for the provided FastAPI service. Output only valid Python — no markdown.\n\n"
            + (f"Client invariants:\n{chart_invariants_str}\n\n" if chart_invariants_str else "")
            + "Output files using === FILE: <path> === / === END FILE === format."
        )
        user = (
            f"Generate a typed Python API client for service '{service_slug}'.\n"
            f"Base URL: {base_url}\n\n"
            + (f"=== Router ===\n{router_content[:3000]}\n\n" if router_content else "")
            + (f"=== Schemas ===\n{schemas_content[:2000]}\n\n" if schemas_content else "")
            + (f"Architecture:\n{chart_preamble[:800]}\n\n" if chart_preamble else "")
            + (f"Steps:\n{chart_cot[:800]}\n\n" if chart_cot else "")
            + "Required files:\n"
            f"  {service_slug}_client/client.py      — typed httpx client class\n"
            f"  {service_slug}_client/models.py      — Pydantic response models\n"
            f"  {service_slug}_client/exceptions.py  — domain exceptions mapping HTTP errors\n"
            f"  {service_slug}_client/__init__.py\n"
            f"  {service_slug}_client/README.md\n"
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

        cg = CodeGenAgent()
        raw = cg._run_llm_sync(user, system)
        files = cg._parse_file_blocks(raw)

        if not files:
            self._reward(False, 0.1)
            return self._result(
                payload={"generated": False, "error": "LLM returned no client files"},
                observations=["LLM returned no files — step failed, auto-heal will retry"],
            )

        client_dir = out_base / f"{service_slug}-client"
        client_dir.mkdir(parents=True, exist_ok=True)

        written: list[str] = []
        for rel_path, content in files.items():
            # Strip any leading package prefix the LLM may add
            parts = Path(rel_path).parts
            if parts and parts[0] in (
                f"{service_slug}_client",
                f"{service_slug}-client",
            ):
                dest = client_dir / Path(*parts[1:])
            else:
                dest = client_dir / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)
            written.append(str(dest.relative_to(_REPO)))

        self._reward(True, 0.9)
        return self._result(
            payload={
                "generated": True,
                "service_slug": service_slug,
                "client_dir": str(client_dir.relative_to(_REPO)),
                "files": written,
                "base_url": base_url,
            },
            observations=[f"client generated → {client_dir.relative_to(_REPO)}/"],
        )
