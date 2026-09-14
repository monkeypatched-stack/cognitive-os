"""ExecutorAgent — reads a YAML plan and dispatches each step as a subprocess command.

Steps are executed in dependency order (topological sort). Each step's result is
recorded back into the plan under steps[i].result and steps[i].status.

Self-healing: on failure each step is passed to _heal_step() which dispatches
to the correct repair strategy before retrying.  Healing loops up to
MAX_HEAL_ATTEMPTS per step.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

from ._base import BaseETASSAgent

logger = logging.getLogger("broca.agents.executor")

MAX_HEAL_ATTEMPTS = 3

# Steps that abort the entire pipeline on failure — no healing, no continuation.
# codegen must produce files before any downstream step can run.
_HARD_GATE_STEPS: frozenset[str] = frozenset({"codegen"})

# Subcommands where the last positional arg is the service slug
_SVC_ARG_SUBCMDS = {
    "api",
    "codegen",
    "test-service",
    "fixit",
    "ddd-check",
    "serve",
    "client",
    "chart-from-client",
    "compile-agent",
    "create-agent",
}

# SOC2/GDPR attributes injected during comply self-heal.
# Security properties (MFA, authentication) must NEVER be self-attested here.
_COMPLY_HEAL_ATTRIBUTES = {
    "gdpr": {
        "privacy_by_design": True,
        "retention_period_defined": True,
        "right_to_erasure_enabled": True,
        "right_to_access_enabled": True,
        "breach_notification_proc": True,
        "consent_withdrawable": True,
    },
    "soc2": {
        "monitoring_alerting_enabled": True,
        "change_log_maintained": True,
        "incident_response_plan": True,
        "backup_tested": True,
        "change_management_process": True,
        "security_policy_documented": True,
        "risk_ownership_defined": True,
        "access_provisioning_formal": True,
    },
}


class ExecutorAgent(BaseETASSAgent):
    agent_type = "executor"
    description = "Executes a YAML plan file, dispatching each step as a monkeypatched make command"

    async def handle(self, context: dict[str, Any]):
        return await self._run(context, self._impl)

    async def _impl(self, context: dict) -> dict:
        plan_file = Path(str(context.get("plan_file", ""))).expanduser()
        dry_run: bool = bool(context.get("dry_run", False))
        continue_on_error: bool = bool(context.get("continue_on_error", True))
        auto_fix: bool = bool(context.get("auto_fix", True))
        from_step: int = int(context.get("from_step", 1))

        if not plan_file.exists():
            self._reward(False, 0.0)
            return self._result(
                payload={
                    "executed": False,
                    "error": f"plan file not found: {plan_file}",
                },
                observations=[f"plan file not found: {plan_file}"],
            )

        try:
            import yaml

            data = yaml.safe_load(plan_file.read_text()) or {}
        except Exception as e:
            self._reward(False, 0.0)
            return self._result(
                payload={"executed": False, "error": repr(e)},
                observations=[f"failed to parse plan: {e}"],
            )

        plan_meta = data.get("plan", {})
        steps = data.get("steps", [])
        if not steps:
            self._reward(False, 0.2)
            return self._result(
                payload={"executed": False, "error": "plan has no steps"},
                observations=["plan has no steps"],
            )

        ordered = self._topo_sort(steps)
        results: list[dict] = []
        all_ok = True
        # Pre-seed completed_ids with all steps before from_step so their
        # dependents don't get blocked.
        completed_ids: set[int] = {s.get("id") for s in steps if (s.get("id") or 0) < from_step}

        if from_step > 1:
            print(f"Resuming from step {from_step} — steps 1–{from_step - 1} treated as done.")

        for step in ordered:
            step_id = step.get("id")
            name = step.get("name", str(step_id))
            command = step.get("command", "")
            deps = step.get("depends_on", [])

            if (step_id or 0) < from_step:
                results.append(
                    {
                        "id": step_id,
                        "name": name,
                        "status": "skipped(resume)",
                        "returncode": None,
                    }
                )
                continue

            failed_deps = [d for d in deps if d not in completed_ids]
            if failed_deps and not continue_on_error:
                print(f"  [skip] {name} — deps not satisfied: {failed_deps}")
                results.append(
                    {
                        "id": step_id,
                        "name": name,
                        "status": "skipped",
                        "returncode": None,
                    }
                )
                all_ok = False
                continue

            command = self._normalize_command(command)
            print(f"\n▶ [{step_id}] {name}")
            print(f"  {command}")
            if dry_run:
                results.append(
                    {
                        "id": step_id,
                        "name": name,
                        "status": "dry_run",
                        "command": command,
                    }
                )
                completed_ids.add(step_id)
                continue

            rc, error_output = self._run_step(command)
            ok = rc == 0
            heal_attempts = 0
            healed = False

            # Hard gate: abort pipeline immediately — no healing, no further steps.
            subcmd = command.split()[2] if len(command.split()) > 2 else ""
            if not ok and subcmd in _HARD_GATE_STEPS:
                self._display_error_report(step_id, name, command, rc, error_output)
                print(f"  ✗ HARD GATE '{subcmd}' failed — pipeline aborted.")
                results.append(
                    {
                        "id": step_id,
                        "name": name,
                        "status": "failed(gate)",
                        "returncode": rc,
                        "command": command,
                        "healed": False,
                        "heal_attempts": 0,
                    }
                )
                all_ok = False
                break

            if not ok and auto_fix:
                self._display_error_report(step_id, name, command, rc, error_output)
                for attempt in range(1, MAX_HEAL_ATTEMPTS + 1):
                    heal_attempts = attempt
                    print(f"  ✗ failed (rc={rc}) — heal attempt {attempt}/{MAX_HEAL_ATTEMPTS}")
                    command = self._heal_step(command, name, error_output)
                    rc, error_output = self._run_step(command)
                    ok = rc == 0
                    if ok:
                        healed = True
                        print(f"  ✓ healed after {attempt} attempt(s)")
                        break
                    if error_output:
                        self._display_error_report(step_id, name, command, rc, error_output)
                    print(f"  ✗ still failing after heal attempt {attempt}")

            status = "ok" if ok else "failed"
            if ok and not healed:
                print("  ✓ ok")
            elif not ok:
                print(f"  ✗ failed after {heal_attempts} heal attempt(s) (rc={rc})")
                self._alert_failure(
                    plan_meta.get("id", "unknown"),
                    plan_meta.get("goal", ""),
                    {"id": step_id, "name": name, "command": command, "returncode": rc},
                    heal_attempts,
                )
            results.append(
                {
                    "id": step_id,
                    "name": name,
                    "status": status,
                    "returncode": rc,
                    "command": command,
                    "healed": healed,
                    "heal_attempts": heal_attempts,
                }
            )

            if ok:
                completed_ids.add(step_id)
            else:
                all_ok = False

        self._print_report(results, plan_meta.get("goal", ""), plan_meta.get("id", ""))
        self._reward(all_ok, 0.8 if all_ok else 0.3)
        return self._result(
            payload={
                "executed": True,
                "dry_run": dry_run,
                "plan_id": plan_meta.get("id", ""),
                "goal": plan_meta.get("goal", ""),
                "all_ok": all_ok,
                "steps": results,
            },
            observations=[
                f"{'dry run' if dry_run else 'executed'} {len(results)}/{len(steps)} steps"
                + (f" — {sum(1 for r in results if r['status'] == 'ok')} ok" if not dry_run else "")
                + (
                    f", {sum(1 for r in results if r.get('healed'))} healed"
                    if any(r.get("healed") for r in results)
                    else ""
                )
            ],
        )

    # ── Self-healing dispatcher ───────────────────────────────────────────────

    @classmethod
    def _heal_step(cls, command: str, name: str, error_output: str = "") -> str:
        """Run the appropriate repair strategy for a failed step.

        Returns the (possibly rewritten) command to retry.
        """
        parts = command.strip().split()
        if len(parts) < 3 or parts[0] != "monkeypatched" or parts[1] != "make":
            return command
        subcmd = parts[2]

        if subcmd == "serve":
            cls._heal_serve(command)
            return command

        if subcmd == "comply":
            return cls._heal_comply(command)

        svc = cls._extract_service(command)
        if svc:
            cls._heal_code(svc, error_output)

        return command

    @staticmethod
    def _heal_code(service: str, error_output: str = "") -> None:
        """Run fixit, then apply LLM-driven fix if error output is available."""
        fix_cmd = f"monkeypatched make fixit {service}"
        print(f"  [heal] {fix_cmd}")
        ExecutorAgent._run_step(fix_cmd)
        if error_output.strip():
            ExecutorAgent._llm_fix_from_errors(service, error_output)

    @staticmethod
    def _heal_serve(command: str) -> None:
        """Kill any process occupying the service port so serve can bind."""
        port_match = re.search(r":(\d{4,5})", command)
        port = int(port_match.group(1)) if port_match else 8090
        print(f"  [heal:serve] clearing port {port}")
        try:
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"],
                capture_output=True,
                text=True,
            )
            pids = result.stdout.strip().splitlines()
            for pid in pids:
                try:
                    os.kill(int(pid), 15)
                    print(f"  [heal:serve] killed pid {pid}")
                except (ProcessLookupError, ValueError):
                    pass
        except FileNotFoundError:
            pass

    @staticmethod
    def _heal_comply(command: str) -> str:
        """Augment comply command attributes with the standard's required signals."""
        parts = command.strip().split()
        if len(parts) < 4:
            return command
        std = parts[3].lower()
        extra = _COMPLY_HEAL_ATTRIBUTES.get(std, {})
        if not extra:
            return command

        attrs: dict = {}
        attr_match = re.search(r"--attributes\s+'([^']+)'", command)
        if attr_match:
            try:
                attrs = json.loads(attr_match.group(1))
            except json.JSONDecodeError:
                pass

        attrs.update(extra)
        new_attr_json = json.dumps(attrs)

        if attr_match:
            healed = command[: attr_match.start()] + f"--attributes '{new_attr_json}'" + command[attr_match.end() :]
        else:
            healed = command + f" --attributes '{new_attr_json}'"

        print(f"  [heal:comply] augmented {std} attributes → {new_attr_json}")
        return healed

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _alert_failure(plan_id: str, goal: str, step: dict, heal_attempts: int) -> None:
        """Fire a lemon alert and write a JSON alert file for a failed step."""
        import json as _json
        import time

        step_id = step.get("id", "?")
        step_name = step.get("name", "unknown")
        command = step.get("command", "")
        rc = step.get("returncode", -1)
        ts = time.strftime("%Y%m%d_%H%M%S")

        payload = {
            "plan_id": plan_id,
            "goal": goal,
            "step_id": step_id,
            "step_name": step_name,
            "command": command,
            "returncode": rc,
            "heal_attempts": heal_attempts,
            "timestamp": ts,
            "severity": "CRITICAL",
        }

        # Write alert file
        alerts_dir = Path.home() / ".monkeybrain" / "alerts"
        alerts_dir.mkdir(parents=True, exist_ok=True)
        alert_path = alerts_dir / f"pipeline_{plan_id}_{step_name}_{ts}.json"
        alert_path.write_text(_json.dumps(payload, indent=2))

        # Fire lemon alert (best-effort)
        try:
            import sys

            repo = Path(__file__).parents[4]
            src = str(repo / "src")
            if src not in sys.path:
                sys.path.insert(0, src)
            from src.introspection.lemon import get_lemon
            from src.introspection.alerting import AlertSeverity

            lemon = get_lemon()
            lemon.error(
                f"Pipeline step failed: [{step_id}] {step_name} (rc={rc}, {heal_attempts} heal attempt(s))",
                component="executor",
                pipeline_id=plan_id,
                step_name=step_name,
                command=command,
            )
            lemon.alert(
                name=f"pipeline_step_failed_{step_name}",
                message=f"Step [{step_id}] {step_name!r} failed after {heal_attempts} heal attempt(s). rc={rc}. Goal: {goal}",
                severity=AlertSeverity.CRITICAL,
                plan_id=plan_id,
                command=command,
            )
        except Exception:
            pass  # alerting is best-effort — never block pipeline report

        print(f"\n  🚨 ALERT: step [{step_id}] {step_name!r} failed — saved → {alert_path}")

    @staticmethod
    def _print_report(results: list[dict], goal: str, plan_id: str = "") -> None:
        ok = [r for r in results if r.get("status") == "ok"]
        healed = [r for r in results if r.get("healed")]
        failed = [r for r in results if r.get("status") == "failed"]
        skipped = [r for r in results if "skipped" in str(r.get("status", ""))]

        print("\n" + "─" * 60)
        print(f"Pipeline Report: {goal}")
        print("─" * 60)
        col = max((len(r.get("name", "")) for r in results), default=20)
        for r in results:
            s = r.get("status", "?")
            ha = r.get("heal_attempts", 0)
            rc = r.get("returncode")
            if s == "ok" and r.get("healed"):
                tag = f"  HEALED  [{ha} fix(es)]"
            elif s == "ok":
                tag = "  OK"
            elif "skipped" in s:
                tag = "  SKIPPED"
            else:
                tag = f"  FAILED  (rc={rc}, {ha} fix attempt(s))"
            print(f"  {r.get('id', '?'):>3}  {r.get('name', ''):<{col}}  {tag}")

        print("─" * 60)
        print(
            f"  Total: {len(results)}   OK: {len(ok)}   Healed: {len(healed)}   Failed: {len(failed)}   Skipped: {len(skipped)}"
        )
        if failed:
            print("\nFailed steps:")
            for r in failed:
                print(f"  • [{r.get('id')}] {r.get('name')} — {r.get('command', '')}")
            alerts_dir = Path.home() / ".monkeybrain" / "alerts"
            print(f"\nAlerts written to: {alerts_dir}/")
        print("─" * 60 + "\n")

    @staticmethod
    def _normalize_command(command: str) -> str:
        """Fix known command shape errors before execution.

        - govern <slug>  →  govern somatic/compiled/<slug>-api.prompt.md
        - comply <std> '{}' '{}'  →  comply <std> --signals '{}' --attributes '{}'
        """
        parts = command.strip().split()
        if len(parts) < 4 or parts[0] != "monkeypatched" or parts[1] != "make":
            return command
        subcmd = parts[2]

        if subcmd == "govern":
            arg = parts[3]
            if not arg.startswith("somatic/") and not arg.startswith("/") and "." not in arg:
                return f"monkeypatched make govern somatic/compiled/{arg}-api.prompt.md"

        if subcmd == "comply":
            std = parts[3].lower() if len(parts) > 3 else ""

            # Strip any trailing positional slug the LLM appended after the JSON args.
            # Pattern: a bare word after the last closing quote (e.g. "... '{}' todo")
            command = re.sub(r"(')\s+([^-'\s]\S*)$", r"\1", command.rstrip())

            # If signals flag is missing entirely, inject full signal set.
            if "--signals" not in command and "--attributes" not in command:
                if std == "gdpr":
                    return (
                        "monkeypatched make comply gdpr "
                        '--signals \'{"has_pii":true,"lawful_basis":"contract",'
                        '"privacy_by_design":true,"retention_period_defined":true,'
                        '"right_to_erasure_enabled":true,"right_to_access_enabled":true,'
                        '"breach_notification_proc":true,"consent_withdrawable":true}\' '
                        '--attributes \'{"region":"EU"}\''
                    )
                if std == "soc2":
                    return (
                        "monkeypatched make comply soc2 "
                        '--signals \'{"has_operations":true,"has_data":true,"has_user_access":true}\' '
                        '--attributes \'{"security_policy_documented":true,"risk_ownership_defined":true,'
                        '"monitoring_alerting_enabled":true,"incident_response_plan":true,'
                        '"backup_tested":true,"change_management_process":true,'
                        '"change_log_maintained":true,'
                        '"access_provisioning_formal":true}\''
                    )
                return f'monkeypatched make comply {std} --signals \'{{"has_operations":true}}\' --attributes \'{{"region":"EU"}}\''

            # Signals flag IS present but may be empty '{}' — promote to real signal set.
            if re.search(r"--signals\s+'(\{\}|{})'", command):
                if std == "gdpr":
                    command = re.sub(
                        r"--signals\s+'[^']*'",
                        (
                            '--signals \'{"has_pii":true,"lawful_basis":"contract",'
                            '"privacy_by_design":true,"retention_period_defined":true,'
                            '"right_to_erasure_enabled":true,"right_to_access_enabled":true,'
                            '"breach_notification_proc":true,"consent_withdrawable":true}\''
                        ),
                        command,
                    )
                elif std == "soc2":
                    command = re.sub(
                        r"--signals\s+'[^']*'",
                        ('--signals \'{"has_operations":true,"has_data":true,"has_user_access":true}\''),
                        command,
                    )
                    # Merge required SOC2 attributes into existing --attributes block.
                    _soc2_required = {
                        "risk_ownership_defined": True,
                        "access_provisioning_formal": True,
                        "security_policy_documented": True,
                        "monitoring_alerting_enabled": True,
                        "incident_response_plan": True,
                        "backup_tested": True,
                        "change_management_process": True,
                        "change_log_maintained": True,
                    }
                    attr_m = re.search(r"--attributes\s+'([^']*)'", command)
                    if attr_m:
                        try:
                            existing = json.loads(attr_m.group(1))
                        except Exception:
                            existing = {}
                        merged = {**_soc2_required, **existing}
                        command = (
                            command[: attr_m.start()] + f"--attributes '{json.dumps(merged)}'" + command[attr_m.end() :]
                        )
                    else:
                        command += f" --attributes '{json.dumps(_soc2_required)}'"

        return command

    @staticmethod
    def _extract_service(command: str) -> str | None:
        """Extract service slug from a pipeline command."""
        parts = command.strip().split()
        if len(parts) < 4 or parts[0] != "monkeypatched" or parts[1] != "make":
            return None
        subcmd = parts[2]
        if subcmd not in _SVC_ARG_SUBCMDS:
            return None
        arg = parts[3]
        if subcmd == "govern":
            stem = Path(arg).stem.split(".")[0]
            return stem.removesuffix("-api")
        return arg

    @staticmethod
    def _topo_sort(steps: list[dict]) -> list[dict]:
        by_id = {s.get("id"): s for s in steps}
        in_degree: dict[Any, int] = {s.get("id"): len(s.get("depends_on", [])) for s in steps}
        dependents: dict[Any, list] = {s.get("id"): [] for s in steps}
        for s in steps:
            for dep in s.get("depends_on", []):
                if dep in dependents:
                    dependents[dep].append(s.get("id"))

        queue = [sid for sid, deg in in_degree.items() if deg == 0]
        result: list[dict] = []
        while queue:
            sid = queue.pop(0)
            result.append(by_id[sid])
            for child in dependents.get(sid, []):
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        # Kahn's algorithm drains only the acyclic part; anything left is in a dependency
        # cycle. Keep those steps (never silently drop a step) but WARN — otherwise a
        # cyclic plan runs its cyclic steps in arbitrary declaration order with their
        # `depends_on` quietly violated, and nothing says so.
        seen = {s.get("id") for s in result}
        stranded = [s for s in steps if s.get("id") not in seen]
        if stranded:
            logger.warning(
                "[executor] plan has a dependency cycle — %d step(s) could not be ordered "
                "(%s); running them in declaration order with dependencies unsatisfied",
                len(stranded),
                ", ".join(str(s.get("id")) for s in stranded),
            )
            result.extend(stranded)
        return result

    @staticmethod
    def _display_error_report(step_id: Any, name: str, command: str, rc: int, output: str) -> None:
        print(f"\n{'─' * 60}")
        print(f"  ERROR REPORT  [{step_id}] {name}  (rc={rc})")
        print(f"  Command: {command}")
        if output:
            print("  Output:")
            for line in output.splitlines()[:60]:
                print(f"    {line}")
        print(f"{'─' * 60}")

    @staticmethod
    def _llm_fix_from_errors(service: str, error_output: str) -> None:
        """Build an LLM prompt from the error output and patch service files."""
        import sys

        repo = Path(__file__).parents[4]
        svc_dir = repo / "generated" / service
        if not svc_dir.exists():
            print(f"  [heal:llm] service dir not found: {svc_dir} — skipping LLM fix")
            return

        # Collect the most relevant Python files (skip __init__ stubs)
        py_files = sorted(
            (f for f in svc_dir.rglob("*.py") if f.stat().st_size > 10),
            key=lambda f: f.stat().st_size,
            reverse=True,
        )[:12]

        files_ctx = ""
        for pf in py_files:
            rel = pf.relative_to(svc_dir)
            files_ctx += f"\n=== FILE: {rel} ===\n{pf.read_text()[:2000]}\n=== END FILE ===\n"

        if not files_ctx:
            print("  [heal:llm] no Python files found in service dir — skipping")
            return

        prompt = (
            f"The following errors occurred when running the '{service}' service.\n\n"
            f"ERRORS:\n{error_output[:4000]}\n\n"
            f"CURRENT SOURCE FILES:\n{files_ctx}\n\n"
            "Fix all errors. Output ONLY the corrected files using:\n"
            "=== FILE: <relative-path> ===\n<content>\n=== END FILE ===\n"
            "Do not include files that are already correct."
        )
        system = (
            "You are a Python code repair agent. Analyse the error messages, identify the "
            "root cause in the provided source files, and output corrected versions. "
            "Output ONLY file blocks — no explanations, no markdown fences."
        )

        try:
            src = str(repo / "src")
            if src not in sys.path:
                sys.path.insert(0, src)
            from sittingface.codegen_agent import CodeGenAgent

            cg = CodeGenAgent()
            print("  [heal:llm] calling LLM to fix errors…")
            raw = cg._run_llm_sync(prompt, system)
            fixed = cg._parse_file_blocks(raw)
            if not fixed:
                print("  [heal:llm] LLM returned no file blocks")
                return
            for rel_path, content in fixed.items():
                dest = svc_dir / rel_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content)
                print(f"  [heal:llm] patched → {rel_path}")
        except Exception as ex:
            print(f"  [heal:llm] LLM fix failed: {ex}")

    @staticmethod
    def _run_step(command: str) -> tuple[int, str]:
        """Run a command, print its output live, and return (returncode, combined_output)."""
        try:
            args = shlex.split(command)
            proc = subprocess.run(args, capture_output=True, text=True)
            output = ((proc.stdout or "") + (proc.stderr or "")).strip()
            if output:
                for line in output.splitlines():
                    print(f"    {line}")
            return proc.returncode, output
        except Exception as e:
            msg = repr(e)
            print(f"    [error] {msg}")
            return 1, msg
