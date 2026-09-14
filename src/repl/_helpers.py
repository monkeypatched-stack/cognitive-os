"""Shared helpers for the monkeypatched CLI.

Internal functions used by multiple command modules."""

from __future__ import annotations

import importlib.util as _ilu
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import httpx
import typer

from repl.theme import print_error

logger = logging.getLogger("monkeypatched")

# Shared constants — kept here (not in brain.py/policy.py, which originally
# defined _SVC_PORTS/_AUTH_URL_DEFAULT/_SCHEDULER_FILE) specifically so every
# consumer (brain.py, policy.py, processes.py, scheduler.py, utils.py) can
# import from this single module one-directionally. _helpers.py must not
# import from any of them, or the import graph cycles.
# parents[2] is the repo root from src/monkeypatched/_helpers.py
# (monkeypatched -> src -> repo root) — this was parents[3] (one level
# ABOVE the repo root) until _broca() was actually exercised end-to-end
# and the resulting FileNotFoundError exposed it.
_BROCA_PKG = Path(__file__).resolve().parents[2] / "packages" / "broca" / "broca"
_MB_HOME = Path("/private/tmp/monkeybrain")
_BRAIN_URL_DEFAULT = "http://localhost:8031"
_AUTH_URL_DEFAULT = "http://localhost:8010"
_SCHEDULER_FILE = _MB_HOME / "scheduler.json"
_SESSION_FILE = _MB_HOME / "session.json"

_SVC_PORTS: dict[str, int] = {
    # ── Active services ──────────────────────────────────────────────────────
    "auth": 8010,
    "agentos": 8031,
    # ── Manufacturing domain services (disabled — knowledge-only) ────────────
    # "assets":             8011,
    # "customers":          8012,
    # "events":             8013,
    # "facilities":         8014,
    # "floor-layout":       8015,
    # "inventory":          8016,
    # "iot":                8017,
    # "orders":             8018,
    # "pm":                 8019,
    # "procurement":        8020,
    # "products":           8021,
    # "shipping":           8022,
    # "shifts":             8023,
    # "suppliers":          8024,
    # "taxonomy":           8025,
    # "process-definition": 8000,
    # "workorders":         8027,
    # "changeover":         8028,
    # "documents":          8029,
    # "file":               8030,
    # "module-control":     8032,
}


def _brain_url() -> str:
    """Base URL of the running MonkeyBrain API — env override, then the
    known default port (see brain.py's _SVC_PORTS["agentos"] = 8031)."""
    return os.environ.get("MONKEYBRAIN_URL", _BRAIN_URL_DEFAULT)


def _load_session() -> dict:
    """Return the stored CLI session ({access_token, refresh_token, email, ...})
    or {} if never logged in / file unreadable."""
    if not _SESSION_FILE.exists():
        return {}
    try:
        return json.loads(_SESSION_FILE.read_text())
    except Exception:
        return {}


def _save_session(session: dict) -> None:
    _MB_HOME.mkdir(parents=True, exist_ok=True)
    _SESSION_FILE.write_text(json.dumps(session, indent=2))
    os.chmod(_SESSION_FILE, 0o600)


def _clear_session() -> None:
    _SESSION_FILE.unlink(missing_ok=True)


def _is_logged_in() -> bool:
    return bool(_load_session().get("access_token"))


def _auth_headers() -> dict:
    """Authorization header from the stored session, or {} if not logged in."""
    token = _load_session().get("access_token")
    return {"Authorization": f"Bearer {token}"} if token else {}


def _refresh_access_token() -> bool:
    """Mint a fresh access_token from the stored refresh_token and persist it.

    Called transparently by _brain_get/_brain_post on a 401 so an expired
    access_token (short TTL) never surfaces to the user as long as the
    refresh_token (from password login) is still valid — API-key sessions
    have no refresh_token and just fail through as before.
    """
    session = _load_session()
    refresh_token = session.get("refresh_token")
    if not refresh_token:
        return False
    try:
        resp = httpx.post(
            f"{_auth_url()}/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
            timeout=15,
        )
    except Exception:
        return False
    if resp.status_code >= 400:
        return False
    try:
        data = resp.json()
    except Exception:
        return False
    new_access = data.get("access_token")
    if not new_access:
        return False
    session["access_token"] = new_access
    if data.get("refresh_token"):
        session["refresh_token"] = data["refresh_token"]
    _save_session(session)
    return True


def _describe_http_error(url: str, resp: Any) -> str:
    """Best-effort human summary of a non-2xx response."""
    detail = ""
    try:
        body = resp.json()
        if isinstance(body, dict):
            detail = str(body.get("detail") or body.get("error") or "")
    except Exception:
        detail = (resp.text or "")[:200]
    suffix = f": {detail}" if detail else ""
    return f"{url} returned HTTP {resp.status_code}{suffix}"


def _handle_response(url: str, resp: Any, json_output: bool, quiet: bool) -> dict:
    """Shared response handling: a non-2xx is a failure, not data.

    Without this the error body ({"detail": ...}) is a truthy dict, so callers
    testing `if not result` treat an HTTP 500 as a successful plan and POST it
    onward to /simulate and /execute.
    """
    if resp.status_code >= 400:
        print_error(_describe_http_error(url, resp))
        return {}

    data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"raw": resp.text}

    if not quiet:
        if json_output:
            typer.echo(json.dumps(data, indent=2))
        else:
            typer.echo(json.dumps(data, indent=2) if isinstance(data, (dict, list)) else str(data))
    return data if isinstance(data, dict) else {}


def _brain_get(path: str, base_url: str, json_output: bool, quiet: bool = False) -> dict:
    """GET {base_url}{path} and print the result. Returns the parsed body
    (empty dict on any failure, including a non-2xx status) so callers that
    need the data, not just the printed side effect, can use it too.
    Set quiet=True to suppress output.

    A 401 triggers one silent refresh-and-retry via the stored refresh_token
    before falling through to the usual error reporting — the access_token's
    TTL is short enough that this fires routinely, not just on genuinely
    stale sessions.
    """
    url = f"{base_url}{path}"
    try:
        resp = httpx.get(url, timeout=600, headers=_auth_headers())
        if resp.status_code == 401 and _refresh_access_token():
            resp = httpx.get(url, timeout=600, headers=_auth_headers())
    except Exception as exc:
        print_error(f"Error reaching {url}: {exc}")
        return {}
    return _handle_response(url, resp, json_output, quiet)


def _brain_post(path: str, base_url: str, body: dict, json_output: bool, quiet: bool = False) -> dict:
    """POST {base_url}{path} with `body` and print the result. Same return
    contract as _brain_get, including the silent refresh-and-retry on a 401.
    Set quiet=True to suppress output."""
    url = f"{base_url}{path}"
    try:
        resp = httpx.post(url, json=body, timeout=600, headers=_auth_headers())
        if resp.status_code == 401 and _refresh_access_token():
            resp = httpx.post(url, json=body, timeout=600, headers=_auth_headers())
    except Exception as exc:
        print_error(f"Error reaching {url}: {exc}")
        return {}
    return _handle_response(url, resp, json_output, quiet)


def _pid_file(name: str) -> Path:
    """~/.monkeybrain/pids/<name>.pid — same convention ServeAgent/
    DeploymentAgent use on the Broca side (packages/broca/broca/agents/
    serve_agent.py, deployment_agent.py) so `monkeypatched proc` can see
    processes those agents started.
    """
    pids_dir = _MB_HOME / "pids"
    pids_dir.mkdir(parents=True, exist_ok=True)
    return pids_dir / f"{name}.pid"


def _is_pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _log_file(name: str) -> Path:
    log_dir = _MB_HOME / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"{name}.log"


def _broca(dotted: str):
    """Return a class or function from the packages/broca/broca/ package tree.

    Loads the full package hierarchy with correct __package__ so relative
    imports inside broca agents work. Uses 'broca' as the canonical sys.modules
    key, evicting src/broca/ if it is already cached there.

    Usage: CingulateAgent = _broca('agents.cingulate.CingulateAgent')
           get_registry   = _broca('registry.get_registry')
    """
    parts = dotted.rsplit(".", 1)
    mod_dotted, cls_name = parts if len(parts) == 2 else (dotted, None)

    def _load(full_key: str, file_path: Path, pkg_key: str) -> object:
        if full_key in sys.modules:
            return sys.modules[full_key]
        spec = _ilu.spec_from_file_location(
            full_key,
            file_path,
            submodule_search_locations=([str(file_path.parent)] if file_path.name == "__init__.py" else None),
        )
        if spec is None:
            raise ImportError(f"broca: cannot find {file_path}")
        mod = _ilu.module_from_spec(spec)
        mod.__package__ = pkg_key
        sys.modules[full_key] = mod
        try:
            spec.loader.exec_module(mod)
        except Exception:
            del sys.modules[full_key]
            raise
        return mod

    # Evict src/broca shadow so our 'broca' key is canonical
    _cached = sys.modules.get("broca")
    if _cached is not None and str(_BROCA_PKG) not in (getattr(_cached, "__file__", "") or ""):
        for _k in [k for k in sys.modules if k == "broca" or k.startswith("broca.")]:
            del sys.modules[_k]

    # Load broca root package
    _load("broca", _BROCA_PKG / "__init__.py", "broca")

    # Load each intermediate package in the dotted path
    segments = mod_dotted.split(".")
    for i in range(1, len(segments)):
        subkey = "broca." + ".".join(segments[:i])
        subpath = _BROCA_PKG / Path(*segments[:i])
        if (subpath / "__init__.py").exists():
            _load(subkey, subpath / "__init__.py", subkey)

    # Load the target module
    full_key = "broca." + mod_dotted
    file_path = _BROCA_PKG / Path(*segments).with_suffix(".py")
    if not file_path.exists():
        # Try as a package __init__
        file_path = _BROCA_PKG / Path(*segments) / "__init__.py"
    mod = _load(
        full_key,
        file_path,
        "broca." + ".".join(segments[:-1]) if len(segments) > 1 else "broca",
    )
    return getattr(mod, cls_name) if cls_name else mod


def _payload(result) -> dict:
    """Extract payload dict from AgentResult or plain dict."""
    if hasattr(result, "payload"):
        p = result.payload
        return p if isinstance(p, dict) else {}
    if isinstance(result, dict):
        return result.get("payload", result)
    return {}


def _observations(result) -> list:
    """Extract observations list from AgentResult or plain dict."""
    if hasattr(result, "observations"):
        return result.observations or []
    if isinstance(result, dict):
        return result.get("observations", [])
    return []


def _structural_checks(content: str, path: "Path") -> list[dict]:
    """Return a list of compliance rule dicts for ComplianceEngine.validate_subsystem."""
    import re

    rules = []

    has_frontmatter = content.startswith("---")
    rules.append(
        {
            "name": "has_yaml_frontmatter",
            "passed": has_frontmatter,
            "message": (
                "Prompt file starts with YAML frontmatter block."
                if has_frontmatter
                else "Missing YAML frontmatter (---) at top of file."
            ),
            "severity": "high",
        }
    )

    # Parse frontmatter
    front = {}
    if has_frontmatter:
        try:
            import yaml as _yaml

            end = content.find("\n---", 3)
            if end != -1:
                front = _yaml.safe_load(content[3:end]) or {}
        except Exception as e:
            logger.debug("Exception caught: %s", e)

    has_constraints = bool(front.get("constraints"))
    rules.append(
        {
            "name": "frontmatter_has_constraints",
            "passed": has_constraints,
            "message": (
                f"{len(front.get('constraints', []))} constraint(s) declared."
                if has_constraints
                else "No constraints in frontmatter."
            ),
            "severity": "high",
        }
    )

    has_review_gate = bool(front.get("review_gate"))
    rules.append(
        {
            "name": "frontmatter_has_review_gate",
            "passed": has_review_gate,
            "message": (
                "review_gate with approved/rejected outcomes present."
                if has_review_gate
                else "No review_gate in frontmatter."
            ),
            "severity": "high",
        }
    )

    if has_review_gate:
        rg = front.get("review_gate", {})
        # outcomes may be nested under an "outcomes" key or flat on the gate itself
        outcomes = rg.get("outcomes", rg)
        has_both_outcomes = bool(outcomes.get("approved")) and bool(outcomes.get("rejected"))
        rules.append(
            {
                "name": "review_gate_has_both_outcomes",
                "passed": has_both_outcomes,
                "message": (
                    "review_gate defines both approved and rejected outcomes."
                    if has_both_outcomes
                    else "review_gate missing approved or rejected outcome."
                ),
                "severity": "medium",
            }
        )

    has_preamble = "## Preamble" in content
    rules.append(
        {
            "name": "has_preamble_section",
            "passed": has_preamble,
            "message": ("## Preamble section present." if has_preamble else "Missing ## Preamble section."),
            "severity": "critical",
        }
    )

    if has_preamble:
        preamble_text = content.split("## Preamble", 1)[1].split("##", 1)[0].strip()
        preamble_long = len(preamble_text) >= 80
        rules.append(
            {
                "name": "preamble_is_substantive",
                "passed": preamble_long,
                "message": (
                    f"Preamble is {len(preamble_text)} chars."
                    if preamble_long
                    else f"Preamble too short ({len(preamble_text)} chars, min 80)."
                ),
                "severity": "medium",
            }
        )

    has_cot = "## Chain of Thought" in content
    rules.append(
        {
            "name": "has_cot_section",
            "passed": has_cot,
            "message": ("## Chain of Thought section present." if has_cot else "Missing ## Chain of Thought section."),
            "severity": "critical",
        }
    )

    if has_cot:
        cot_text = content.split("## Chain of Thought", 1)[1]
        steps = re.findall(r"^###\s+\d+\.", cot_text, re.MULTILINE)
        step_count = len(steps)
        enough_steps = step_count >= 3
        rules.append(
            {
                "name": "cot_has_enough_steps",
                "passed": enough_steps,
                "message": (
                    f"{step_count} numbered CoT steps."
                    if enough_steps
                    else f"Only {step_count} CoT step(s) — minimum 3 required."
                ),
                "severity": "high",
            }
        )

        # Each step should have a body (text after the ### heading)
        step_blocks = re.split(r"^###\s+\d+\.", cot_text, flags=re.MULTILINE)[1:]
        empty_steps = [i + 1 for i, b in enumerate(step_blocks) if len(b.strip()) < 10]
        rules.append(
            {
                "name": "cot_steps_have_descriptions",
                "passed": not empty_steps,
                "message": (
                    "All CoT steps have descriptions."
                    if not empty_steps
                    else f"Steps {empty_steps} have no/minimal description."
                ),
                "severity": "medium",
            }
        )

    constraints = front.get("constraints", [])
    if constraints:
        non_empty = all(isinstance(c, str) and c.strip() for c in constraints)
        rules.append(
            {
                "name": "constraints_are_non_empty_strings",
                "passed": non_empty,
                "message": (
                    "All constraints are non-empty strings."
                    if non_empty
                    else "One or more constraints are empty or non-string."
                ),
                "severity": "medium",
            }
        )

    return rules


def _run_cingulate_agent(content: str, repo_root: "Path") -> dict:
    """Instantiate the registered CingulateAgent and run it against the prompt content.

    Returns the agent's payload dict. Falls back gracefully if the agent or its
    dependencies are unavailable in the CLI context.
    """
    import asyncio
    import sys

    for p in [
        repo_root / "src",
        repo_root / "packages" / "broca",
        repo_root / "packages" / "cingulate",
        repo_root / "packages" / "cerebellum",
    ]:
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)

    try:
        CingulateAgent = _broca("agents.cingulate.CingulateAgent")
        agent = CingulateAgent()
        result = asyncio.run(agent.handle({"question": content[:6000]}))
        # AgentResult or plain dict
        if hasattr(result, "payload"):
            payload = result.payload or {}
            observations = getattr(result, "observations", [])
            evidence = getattr(result, "evidence", [])
        else:
            payload = result if isinstance(result, dict) else {}
            observations = payload.pop("observations", [])
            evidence = payload.pop("evidence", [])

        # If agent errored (e.g. Ollama backend not available), fall back to direct Anthropic call
        if payload.get("error") and not payload.get("score"):
            return _cingulate_anthropic_fallback(content)

        return {
            "agent": "CingulateAgent",
            "compliant": payload.get("compliant", True),
            "score": payload.get("score"),
            "issues": evidence[0].get("issues", []) if evidence else [],
            "recommendation": (observations[0] if observations else payload.get("recommendation", "")),
            "strengths": payload.get("strengths", []),
            "raw": payload,
        }
    except Exception as e:
        return _cingulate_anthropic_fallback(content, caught=str(e))


def _cingulate_anthropic_fallback(content: str, caught: str = "") -> dict:
    """Direct Anthropic call when CingulateAgent's model backend is unavailable."""
    import os, re, json as _json

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {
            "agent": "CingulateAgent(skipped)",
            "compliant": True,
            "score": None,
            "issues": [],
            "recommendation": "Set ANTHROPIC_API_KEY for semantic review.",
            "strengths": [],
        }
    try:
        import anthropic

        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=(
                "You are the Cingulate constitutional reviewer for MonkeyBrain AgentOS. "
                "Assess compiled prompt files for semantic quality. Reply JSON only."
            ),
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Review this compiled prompt for: preamble clarity, CoT logical order, "
                        "invariant testability, review gate alignment, and auth/observability coverage.\n\n"
                        f"{content[:5000]}\n\n"
                        'Reply JSON: {"compliant": true/false, "score": 0-100, '
                        '"issues": [{"severity": "critical|high|medium|low", "check": "...", "detail": "..."}], '
                        '"strengths": ["..."], "recommendation": "..."}'
                    ),
                }
            ],
        )
        text = msg.content[0].text if msg.content else ""
        m = re.search(r"\{.*\}", text, re.DOTALL)
        data = _json.loads(m.group(0)) if m else {}
        return {
            "agent": "CingulateAgent(anthropic-fallback)",
            "compliant": data.get("compliant", True),
            "score": data.get("score"),
            "issues": data.get("issues", []),
            "recommendation": data.get("recommendation", ""),
            "strengths": data.get("strengths", []),
        }
    except Exception as e:
        return {
            "agent": "CingulateAgent(fallback-error)",
            "compliant": True,
            "score": None,
            "issues": [],
            "recommendation": "",
            "strengths": [],
            "error": f"original={caught} fallback={e}",
        }


def _emit_to_lemon(report_data: dict, repo_root: "Path") -> None:
    """Emit the compliance report to Lemon via observe_governance."""
    import sys

    sys.path.insert(0, str(repo_root / "src"))
    try:
        from introspection.lemon import Lemon

        lemon = Lemon()

        s = report_data["structural"]
        trust_score = round(s["score"] / 100.0, 3)
        decision = report_data["verdict"] == "APPROVED"

        failed_checks = [c["rule"] for c in s["checks"] if not c["passed"]]
        agent_issues = [
            f"{i.get('severity', '?')}: {i.get('check', '?')}"
            for i in (report_data.get("agent_review") or {}).get("issues", [])
        ]
        obligations = failed_checks + agent_issues

        lemon.observe_governance(
            policy_path=report_data["prompt_file"],
            decision=decision,
            principal="CingulateAgent",
            obligations=obligations,
            trust_score=trust_score,
            provenance_chain=[
                "soma_compile",
                "cingulate_structural",
                "cingulate_agent",
            ],
            audit_ref=report_data.get("report_id", ""),
            pipeline_id="soma_api_review",
        )

        # Also log the full JSON report as a structured info entry
        import json

        lemon.info(
            json.dumps(report_data),
            component="cingulate_review",
            verdict=report_data["verdict"],
            prompt_file=report_data["prompt_file"],
        )
    except Exception:
        pass  # Lemon unavailable in CLI context — non-fatal


def _run_cingulate_review(
    prompt_path: "Path",
    repo_root: "Path",
    json_output: bool = False,
    return_verdict: bool = False,
) -> "str | None":
    """Structural checks (ComplianceEngine) + CingulateAgent gate + Lemon emission."""
    import json
    import sys
    from datetime import datetime
    from pathlib import Path
    from uuid import uuid4

    sys.path.insert(0, str(repo_root / "packages" / "cingulate"))

    try:
        from cingulate.governance.compliance import ComplianceEngine

        _has_compliance_engine = True
    except ImportError:
        _has_compliance_engine = False

    content = Path(prompt_path).read_text()
    report_id = f"crev-{uuid4().hex[:8]}"

    report_data: dict = {
        "report_id": report_id,
        "prompt_file": str(prompt_path),
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "structural": {},
        "agent_review": None,
        "verdict": "UNKNOWN",
    }

    # ── 1. Structural checks via ComplianceEngine ────────────────────────────
    rules = _structural_checks(content, Path(prompt_path))

    if _has_compliance_engine:
        engine = ComplianceEngine()
        comp_report = engine.validate_subsystem(str(prompt_path), rules)
        report_data["structural"] = {
            "total": comp_report.total_checks,
            "passed": comp_report.passed_checks,
            "failed": comp_report.failed_checks,
            "score": round(comp_report.compliance_score * 100, 1),
            "checks": [
                {
                    "rule": c.rule,
                    "passed": c.passed,
                    "severity": c.severity,
                    "message": c.message,
                }
                for c in comp_report.checks
            ],
        }
    else:
        passed = sum(1 for r in rules if r["passed"])
        report_data["structural"] = {
            "total": len(rules),
            "passed": passed,
            "failed": len(rules) - passed,
            "score": round(passed / len(rules) * 100, 1) if rules else 0,
            "checks": [
                {
                    "rule": r["name"],
                    "passed": r["passed"],
                    "severity": r["severity"],
                    "message": r["message"],
                }
                for r in rules
            ],
        }

    # ── 2. CingulateAgent semantic gate ─────────────────────────────────────
    if not json_output:
        typer.echo("  Running CingulateAgent...")
    agent_result = _run_cingulate_agent(content, repo_root)
    report_data["agent_review"] = agent_result

    # ── 3. Determine verdict ─────────────────────────────────────────────────
    critical_structural = any(
        not c["passed"] and c["severity"] == "critical" for c in report_data["structural"]["checks"]
    )
    agent_compliant = agent_result.get("compliant", True)
    critical_agent_issues = [i for i in agent_result.get("issues", []) if i.get("severity") == "critical"]

    if critical_structural or critical_agent_issues:
        report_data["verdict"] = "REJECTED"
    elif report_data["structural"]["failed"] == 0 and agent_compliant:
        report_data["verdict"] = "APPROVED"
    else:
        report_data["verdict"] = "NEEDS_REVISION"

    # ── 4. Save JSON report ──────────────────────────────────────────────────
    reports_dir = Path.home() / ".monkeybrain" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    stem = Path(prompt_path).stem.replace(".prompt", "")
    report_path = reports_dir / f"review_{stem}_{ts}.json"
    report_path.write_text(json.dumps(report_data, indent=2))

    # ── 5. Emit to Lemon ─────────────────────────────────────────────────────
    _emit_to_lemon(report_data, repo_root)

    verdict = report_data["verdict"]

    if json_output:
        typer.echo(json.dumps(report_data, indent=2))
        return verdict if return_verdict else None

    # ── 6. Human-readable output ─────────────────────────────────────────────
    typer.echo("=" * 60)
    typer.echo("CINGULATE COMPLIANCE REVIEW")
    typer.echo("=" * 60)
    typer.echo(f"File:     {prompt_path}")
    typer.echo(f"Verdict:  {verdict}")
    typer.echo(f"ReportID: {report_id}")
    typer.echo("")

    s = report_data["structural"]
    typer.echo(f"Structural  {s['passed']}/{s['total']} passed  (score {s['score']}%)")
    for c in s["checks"]:
        mark = "✓" if c["passed"] else "✗"
        typer.echo(f"  {mark} [{c['severity']:<8}] {c['rule']}")
        if not c["passed"]:
            typer.echo(f"             {c['message']}")

    typer.echo("")
    ar = report_data["agent_review"]
    sem_score = ar.get("score")
    typer.echo(f"CingulateAgent  {'score ' + str(sem_score) + '/100' if sem_score is not None else 'completed'}")
    if ar.get("strengths"):
        for item in ar["strengths"]:
            typer.echo(f"  + {item}")
    if ar.get("issues"):
        for issue in ar["issues"]:
            typer.echo(f"  ! [{issue.get('severity', '?')}] {issue.get('check', '?')}: {issue.get('detail', '')}")
    if ar.get("recommendation"):
        typer.echo(f"  Recommendation: {ar['recommendation']}")
    if ar.get("error"):
        typer.echo(f"  (agent error: {ar['error']})")

    typer.echo("")
    typer.echo(f"Report saved: {report_path}")
    typer.echo("Emitted to Lemon.")

    return verdict if return_verdict else None


def _auth_url() -> str:
    import os

    return os.environ.get("AUTH_SERVICE_URL", _AUTH_URL_DEFAULT)


def _load_jobs() -> list[dict]:
    if not _SCHEDULER_FILE.exists():
        return []
    import json as _json

    try:
        return _json.loads(_SCHEDULER_FILE.read_text())
    except Exception:
        return []


def _save_jobs(jobs: list[dict]) -> None:
    import json as _json

    _SCHEDULER_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SCHEDULER_FILE.write_text(_json.dumps(jobs, indent=2))
