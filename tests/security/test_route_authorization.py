"""Broken access control — every route must sit behind an authentication guard.

Found unauthenticated during the domains review:
  * the ENTIRE file service — upload/download/delete and presigned S3 URL generation.
    Its `require_auth_context` guard existed but was COMMENTED OUT on the routers.
  * assets/equipment.py   — 11 routes incl. POST/PATCH/DELETE on pharmaceutical equipment
  * pm/calibration_point.py — 5 routes incl. DELETE on GxP-regulated calibration points
  * file/cad_conversion.py — 5 unauthenticated upload+convert endpoints

This test fails if any router regresses to open.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
SERVICES = ROOT / "domains/manufacturing/knowledge/services"

ROUTE_RE = re.compile(r"@(?:router|root_router)\.(get|post|put|patch|delete)\(")
GUARD_RE = re.compile(
    r"Depends\(\s*(get_current_user|require_permission|require_auth_context"
    r"|active_module_control_session|require_activation_session)"
)
DOCSTRING_RE = re.compile(r'("""|\'\'\')(?:.|\n)*?\1')

# Intentionally public (service discovery / health), verified by hand.
PUBLIC_ALLOWLIST = {
    ("warehouse_execution/routers/execution.py", "/capabilities"),
    ("supply_allocation/routers/allocation.py", "/capabilities"),
    ("replenishment/routers/replenishment.py", "/capabilities"),
    ("module_control/routers/module_control.py", "/status"),
}


def _router_files():
    for p in SERVICES.rglob("*.py"):
        if "__pycache__" in str(p) or "/tests/" in str(p):
            continue
        # Docstrings carry usage EXAMPLES with @router decorators — not real routes.
        text = DOCSTRING_RE.sub("", p.read_text(errors="ignore"))
        if ROUTE_RE.search(text):
            yield p, text


def test_no_router_exposes_routes_without_an_auth_guard():
    offenders = []
    for path, text in _router_files():
        rel = str(path.relative_to(SERVICES))
        if GUARD_RE.search(text):
            continue
        allowed = {p for f, p in PUBLIC_ALLOWLIST if f == rel}
        paths = re.findall(r"@(?:router|root_router)\.\w+\(\s*[\"']([^\"']*)[\"']", text)
        if paths and all(p in allowed for p in paths):
            continue
        offenders.append(f"{rel} ({len(ROUTE_RE.findall(text))} routes, no auth guard)")
    assert not offenders, "UNAUTHENTICATED ROUTES:\n  " + "\n  ".join(sorted(offenders))


@pytest.mark.parametrize(
    "rel",
    [
        "file/src/routes/files.py",
        "file/src/routes/presigned.py",
        "file/src/routes/cad_conversion.py",
        "assets/routers/equipment.py",
        "pm/routers/calibration_point.py",
    ],
)
def test_previously_open_routers_are_now_guarded(rel):
    text = (SERVICES / rel).read_text()
    assert GUARD_RE.search(text), f"{rel} lost its auth guard"
    assert not re.search(r"^\s*#\s*dependencies=\[Depends", text, re.M), f"{rel} has its auth dependency COMMENTED OUT"


def test_cad_upload_filename_cannot_inject_headers():
    """file.filename is attacker-controlled and was interpolated raw into Content-Disposition."""
    sys.path.insert(0, str(SERVICES / "file"))
    from src.helpers.cad_conversion import safe_filename_stem as stem

    assert '"' not in stem('evil"; x=y')  # cannot escape the quoted value
    assert "\n" not in stem("a\nSet-Cookie: x=y")  # cannot inject a header
    assert "/" not in stem("../../etc/passwd")  # no path separators
    assert stem(None) == "drawing"
