"""Static architecture guard (MAKE DOMAIN ISOLATION AIRTIGHT, Phase 8).

Prevents a future edit from re-introducing domain-specific parameter
inspection into the OS/runtime layer -- the exact class of coupling this
task removed from ActionExecutor._build_recovery_action (it used to read
action.parameters["selection"]).

Uses the `ast` module, not a substring/blacklist grep: it walks real
Python syntax and flags only genuine `<something>.parameters[...]` /
`<something>.parameters.get(...)` accesses with a forbidden literal key --
so it can never false-positive on this file's own explanatory comments or
docstrings (which legitimately mention "grocery"/"selection" as examples
of what the OS must NOT know), only on actual code that would make it
true again.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

OS_RUNTIME_FILES = (
    "src/monkey_brain/kernel/pipeline/action_executor.py",
    "src/monkey_brain/kernel/pipeline/execution.py",
    "src/monkey_brain/kernel/pipeline/execution_checkpoint_store.py",
    "src/monkey_brain/kernel/pipeline/approval_store.py",
)
"""The OS/runtime files this guard protects. Deliberately a short,
explicit list rather than "every file under kernel/pipeline/" -- some
files in that tree (e.g. belief_runtime.py's plan-compilation helpers)
already have narrower, pre-existing, out-of-scope concerns this task was
not asked to touch; scope creep here would make the guard noisy rather
than trustworthy. Add a new OS/runtime file here as it's introduced."""

FORBIDDEN_DOMAIN_KEYS = {
    # Commerce/grocery
    "selection",
    "selected",
    "product_id",
    "qty",
    "quantity",
    "cart",
    "order_id",
    "provider_id",
    "store_id",
    "sku",
    "price",
    "delivery",
    "payment",
    "wallet",
    # Generalize the coupling CLASS, not just the literal reported word --
    # any of these appearing as a literal dict key read off `.parameters`
    # in an OS file is the same mistake in a different domain's clothes.
    "machine_ref",
    "robot_id",
    "target",
    "patient_id",
    "invoice_id",
}


def _parameters_base_name(node: ast.AST) -> str | None:
    """Returns a lowercase identifier for what's being subscripted/called
    -- covers `action.parameters[...]` (Attribute) and a local alias like
    `parameters.get(...)` or `retry_parameters[...]` (Name) -- as long as
    the accessed name itself contains "parameter", regardless of exactly
    what object it's an attribute of."""
    if isinstance(node, ast.Attribute) and "parameter" in node.attr.lower():
        return node.attr.lower()
    if isinstance(node, ast.Name) and "parameter" in node.id.lower():
        return node.id.lower()
    return None


def _string_key(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _find_forbidden_parameter_accesses(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        # <params>["key"]
        if isinstance(node, ast.Subscript) and _parameters_base_name(node.value) is not None:
            key = _string_key(node.slice)
            if key is not None and key.lower() in FORBIDDEN_DOMAIN_KEYS:
                violations.append((node.lineno, key))
        # <params>.get("key", ...)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and _parameters_base_name(node.func.value) is not None
            and node.args
        ):
            key = _string_key(node.args[0])
            if key is not None and key.lower() in FORBIDDEN_DOMAIN_KEYS:
                violations.append((node.lineno, key))
    return violations


@pytest.mark.parametrize("relative_path", OS_RUNTIME_FILES)
def test_os_runtime_file_never_inspects_domain_shaped_parameters(relative_path: str):
    repo_root = Path(__file__).resolve().parents[2]
    source_path = repo_root / relative_path
    assert source_path.exists(), f"expected OS/runtime file not found: {source_path}"

    violations = _find_forbidden_parameter_accesses(source_path.read_text())
    assert not violations, (
        f"{relative_path} inspects a domain-specific parameter key -- the OS/runtime "
        f'layer must only react to generic, top-level result keys (e.g. "recoverable", '
        f'"requires_approval"), never a domain\'s own parameter schema: '
        f"{[(f'line {ln}', key) for ln, key in violations]}"
    )


def test_guard_itself_actually_detects_the_original_violation():
    """A guard that can't catch the bug it was written for is worthless.
    Proves _find_forbidden_parameter_accesses genuinely flags the exact
    shape of code this task removed from action_executor.py."""
    reintroduced = (
        "def _build_recovery_action(action):\n"
        "    original_selection = action.parameters.get('selection') or []\n"
        "    return action\n"
    )
    violations = _find_forbidden_parameter_accesses(reintroduced)
    assert violations == [(2, "selection")]


def test_guard_does_not_false_positive_on_the_generic_recoverable_contract():
    """The one dict key the OS legitimately reacts to (on outcome.result,
    not action.parameters) must never trip this guard."""
    generic_contract = (
        "def execute(outcome):\n    if outcome.result.get('recoverable'):\n        return retry(outcome)\n"
    )
    assert _find_forbidden_parameter_accesses(generic_contract) == []
