"""TestServiceAgent — runs static analysis and unit tests on a generated DDD service.

Wraps the ruff + pytest + CodeGenAgent correction loop.  Called by soma_test_service.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from ._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.test_service_agent")

_REPO = Path(os.environ.get("MONKEYBRAIN_REPO", str(Path(__file__).parents[4])))


def _ensure_sittingface():
    src = _REPO / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


class TestServiceAgent(BaseETASSAgent):
    agent_type = "test_service"
    description = "Runs ruff + pytest on a generated DDD service and auto-corrects failures via CodeGenAgent"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict) -> dict:
        service_slug = str(context.get("service_slug", "")).strip()
        output_dir = Path(str(context.get("output_dir", _REPO / "generated")))
        max_loops: int = int(context.get("max_loops", 3))

        if not service_slug:
            self._reward(False, 0.0)
            return self._result(payload={"all_passed": False}, observations=["no service_slug"])

        out_base = output_dir if output_dir.is_absolute() else _REPO / output_dir
        svc_dir = out_base / service_slug

        if not svc_dir.exists():
            self._reward(False, 0.0)
            return self._result(
                payload={
                    "all_passed": False,
                    "error": f"service dir not found: {svc_dir}",
                },
                observations=[f"run codegen first: monkeypatched make codegen {service_slug}"],
            )

        _ensure_sittingface()

        # ── Helpers ───────────────────────────────────────────────────────────

        _TYPING_NAMES = {
            "Optional",
            "List",
            "Dict",
            "Tuple",
            "Set",
            "FrozenSet",
            "Any",
            "Union",
            "Type",
            "Callable",
            "Sequence",
            "Mapping",
            "Iterator",
            "Generator",
            "Awaitable",
            "Coroutine",
            "ClassVar",
            "Final",
            "Literal",
            "TypeVar",
            "overload",
        }

        # Names that must survive ruff's unused-import removal because they are
        # used as runtime values (e.g. class_=AsyncSession), not just type hints.
        _RUNTIME_IMPORTS: list[tuple[str, str]] = [
            ("sqlalchemy.ext.asyncio", "AsyncSession"),
            ("sqlalchemy.ext.asyncio", "AsyncEngine"),
            ("sqlalchemy.ext.asyncio", "create_async_engine"),
            ("sqlalchemy.orm", "sessionmaker"),
            ("sqlalchemy.orm", "Session"),
            ("motor.motor_asyncio", "AsyncIOMotorClient"),
            ("motor.motor_asyncio", "AsyncIOMotorDatabase"),
        ]

        def _inject_imports(path: Path) -> None:
            """Add missing imports (typing + known runtime) to every .py file under path."""
            for pyfile in path.rglob("*.py"):
                try:
                    src = pyfile.read_text()
                except Exception:
                    continue
                lines = src.splitlines(keepends=True)
                changed = False

                # 1. typing imports
                needed_typing = {n for n in _TYPING_NAMES if re.search(rf"\b{n}\b", src)}
                already_typing: set[str] = set()
                for m in re.finditer(r"from\s+typing\s+import\s+([^\n]+)", src):
                    already_typing.update(n.strip() for n in m.group(1).split(","))
                missing_typing = needed_typing - already_typing
                if missing_typing:
                    import_line = f"from typing import {', '.join(sorted(missing_typing))}\n"
                    insert_at = 0
                    for i, line in enumerate(lines):
                        if line.startswith("from __future__") or line.startswith("from typing"):
                            insert_at = i + 1
                    lines.insert(insert_at, import_line)
                    changed = True

                # 2. runtime imports that ruff --unsafe-fixes may strip
                for module, name in _RUNTIME_IMPORTS:
                    if not re.search(rf"\b{name}\b", "".join(lines)):
                        continue
                    pattern = rf"from\s+{re.escape(module)}\s+import\s+[^\n]*\b{name}\b"
                    if re.search(pattern, "".join(lines)):
                        continue
                    # Name is used but not imported — add import after last future/same-module line
                    import_line = f"from {module} import {name}\n"
                    insert_at = 0
                    for i, line in enumerate(lines):
                        if line.startswith("from __future__") or line.startswith(f"from {module}"):
                            insert_at = i + 1
                    lines.insert(insert_at, import_line)
                    changed = True

                if changed:
                    try:
                        pyfile.write_text("".join(lines))
                    except Exception:
                        pass

        def _run_ruff_autofix(path: Path) -> None:
            try:
                subprocess.run(
                    [sys.executable, "-m", "ruff", "check", "--fix", "."],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    cwd=str(path),
                )
            except Exception:
                pass

        def _run_ruff(path: Path) -> tuple[bool, str]:
            try:
                r = subprocess.run(
                    [sys.executable, "-m", "ruff", "check", "."],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    cwd=str(path),
                )
                return r.returncode == 0, (r.stdout + r.stderr)[-2000:]
            except Exception as e:
                return True, f"ruff skipped: {e}"

        def _run_pytest(test_target: Path, cwd: Path) -> tuple[bool, str]:
            try:
                r = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "pytest",
                        str(test_target),
                        "--tb=short",
                        "-q",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    cwd=str(cwd),
                )
                return r.returncode == 0, (r.stdout + r.stderr)[-3000:]
            except Exception as e:
                return False, f"pytest error: {e}"

        def _collect_errors() -> list[str]:
            errs: list[str] = []
            ruff_ok, ruff_out = _run_ruff(svc_dir)
            if not ruff_ok:
                errs.append(f"ruff:\n{ruff_out}")
            test_target = next((d for d in [svc_dir / "tests", svc_dir / "test"] if d.exists()), None)
            if test_target:
                pt_ok, pt_out = _run_pytest(test_target, out_base)
                if not pt_ok:
                    errs.append(f"pytest:\n{pt_out}")
            return errs

        def _source_context(errors: list[str]) -> str:
            combined = "\n".join(errors)
            seen: set[str] = set()
            blocks: list[str] = []
            for m in re.finditer(r"([\w/.-]+\.py)", combined):
                rel = m.group(1)
                if rel in seen:
                    continue
                seen.add(rel)
                parts = Path(rel).parts
                candidates = [svc_dir / rel, svc_dir.parent / rel]
                for i in range(1, len(parts)):
                    candidates.append(svc_dir / Path(*parts[i:]))
                found = next((c for c in candidates if c.exists()), None)
                if found:
                    try:
                        blocks.append(f"=== FILE: {rel} ===\n{found.read_text()[:3000]}\n=== END FILE ===")
                    except Exception:
                        pass
            return "\n\n".join(blocks)

        def _domain_context() -> str:
            blocks: list[str] = []
            for pattern in (
                "domain/entities/*.py",
                "domain/aggregates/*.py",
                "domain/value_objects/*.py",
                "domain/repositories/*.py",
                "domain/*.py",
            ):
                for p in sorted(svc_dir.glob(pattern)):
                    try:
                        rel = p.relative_to(svc_dir)
                        blocks.append(f"=== FILE: {rel} ===\n{p.read_text()[:1500]}\n=== END FILE ===")
                    except Exception:
                        pass
            return "\n\n".join(blocks[:6])

        def _apply_corrections(raw: str) -> list[str]:
            header_re = re.compile(r"===\s*FILE:\s*(.+?)\s*===")
            splits = list(header_re.finditer(raw))
            written: list[str] = []
            for i, m in enumerate(splits):
                rel_raw = m.group(1).strip()
                # Normalize: strip leading slashes so path is relative to service root.
                rel_path = Path(rel_raw)
                if rel_path.is_absolute():
                    rel_path = Path(*rel_path.parts[1:])
                rel = str(rel_path)
                print(f"  [test_service] filename: {rel!r}")
                start = m.end()
                end = splits[i + 1].start() if i + 1 < len(splits) else len(raw)
                block = raw[start:end]
                block = re.sub(r"===\s*END FILE\s*===.*", "", block, flags=re.DOTALL)
                content = re.sub(r"^```\w*\n?", "", block.strip())
                content = re.sub(r"\n?```$", "", content).strip()
                if not content:
                    continue
                try:
                    compile(content, rel, "exec")
                except SyntaxError as e:
                    logger.warning("[test_service] syntax error in fix for %s: %s", rel, e)
                    continue
                dest = svc_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content + "\n")
                written.append(rel)
                logger.info("[test_service] patched %s", rel)
            return written

        async def _correction_loop(errors: list[str], loop_num: int) -> list[str]:
            try:
                from sittingface.codegen_agent import CodeGenAgent
            except ImportError as e:
                logger.warning("[test_service] CodeGenAgent import failed: %s", e)
                return []
            agent = CodeGenAgent()
            if not agent.llm:
                logger.warning("[test_service] no LLM — skipping correction")
                return []

            error_text = "\n\n".join(errors)
            src_ctx = _source_context(errors)
            dom_ctx = _domain_context()

            system = (
                "You are a code correction agent for a Python DDD microservice.\n"
                "Rules:\n"
                "  1. Fix ONLY the files listed in the errors — do not regenerate unrelated files.\n"
                "  2. For F821 'Undefined name' errors: add the missing import at the top of the file.\n"
                "     The domain context shows where each class is defined — use those paths for imports.\n"
                "  3. For F401 'imported but unused': remove the unused import.\n"
                "  4. Do NOT use string annotations (Optional['Todo']). Use real imports instead.\n"
                "  5. Output ONLY the corrected files — no prose, no explanation.\n\n"
                "Output format (exact):\n"
                "=== FILE: <path/relative/to/service/root> ===\n"
                "<corrected content>\n"
                "=== END FILE ==="
            )
            user = (
                f"Correction loop {loop_num} — service: {service_slug}\n\n"
                f"=== ERRORS ===\n{error_text}\n\n"
                f"=== FAILING FILES ===\n{src_ctx}\n\n"
                + (f"=== DOMAIN LAYER (use for import paths) ===\n{dom_ctx}\n\n" if dom_ctx else "")
                + "Fix every error above. Output only the corrected file(s)."
            )

            raw = await agent._call_llm(user, system)
            return _apply_corrections(raw)

        # ── Main test + correction loop ───────────────────────────────────────
        _inject_imports(svc_dir)  # fill missing typing imports before any checks
        _run_ruff_autofix(svc_dir)  # fix remaining auto-fixable issues
        correction_history: list[dict] = []
        errors = _collect_errors()

        loop = 0
        while errors and loop < max_loops:
            loop += 1
            logger.info("[test_service] correction loop %d/%d", loop, max_loops)
            fixed_files = await _correction_loop(errors, loop)
            correction_history.append({"loop": loop, "errors": errors, "fixed_files": fixed_files})
            if not fixed_files:
                logger.warning("[test_service] no files patched in loop %d — stopping", loop)
                break
            _inject_imports(svc_dir)
            _run_ruff_autofix(svc_dir)  # clean up auto-fixable issues after each patch
            errors = _collect_errors()

        # ── Final state ───────────────────────────────────────────────────────
        ruff_ok, ruff_out = _run_ruff(svc_dir)
        test_target = next((d for d in [svc_dir / "tests", svc_dir / "test"] if d.exists()), None)
        pytest_passed, pytest_out = (
            (True, "no tests/ directory") if not test_target else _run_pytest(test_target, out_base)
        )
        all_passed = ruff_ok and pytest_passed

        # ── Save log ──────────────────────────────────────────────────────────
        logs_dir = Path.home() / ".monkeybrain" / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        log_path = logs_dir / f"test_{service_slug}_{ts}.log"
        lines = [
            f"=== Test run: {service_slug} @ {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} ===\n",
            f"Service dir: {svc_dir}\n",
            f"Correction loops: {loop}/{max_loops}\n\n",
        ]
        if not all_passed:
            lines.append("=== FINAL ERRORS ===\n")
            if not ruff_ok:
                lines.append(f"ruff:\n{ruff_out}\n\n")
            if not pytest_passed:
                lines.append(f"pytest:\n{pytest_out}\n\n")
        log_path.write_text("".join(lines))

        self._reward(all_passed, 0.9 if all_passed else 0.4)
        obs = [
            f"static analysis: {'clean' if ruff_ok else 'issues'}",
            f"unit tests: {'passed' if pytest_passed else 'failed'}",
            f"correction loops run: {loop}/{max_loops}",
        ]
        return self._result(
            payload={
                "all_passed": all_passed,
                "service_slug": service_slug,
                "static_analysis": {"clean": ruff_ok, "output": ruff_out},
                "unit_tests": {"passed": pytest_passed, "output": pytest_out},
                "correction_loops_run": loop,
                "correction_history": correction_history,
                "error_log": str(log_path),
            },
            observations=obs,
        )
