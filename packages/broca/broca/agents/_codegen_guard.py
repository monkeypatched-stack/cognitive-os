"""Guardrails for executing LLM-generated agent code.

AutoAgentGenerator's whole job is: *agent not found → ask an LLM for its source → exec it*.
That is remote code execution by design, and it was completely unguarded:

  * `exec(agent_code, {"__builtins__": __builtins__})` — full builtins, in-process, with the
    server's privileges, and no validation of any kind.
  * `agent_name` / `agent_description` come from the request context, and are interpolated
    both into the LLM prompt (prompt injection → the model emits whatever the attacker asked
    for) and directly into a Python source template (a quote in the name escapes the string
    literal → template injection).
  * Even with no attacker, a model that hallucinates `shutil.rmtree(...)` gets it executed.

This module makes execution opt-in and validates the source first.
"""

from __future__ import annotations

import ast
import os
import re

# Modules generated agents may import. Deliberately tiny: an agent implements `handle()`,
# it does not need the filesystem, the network, or the process table.
ALLOWED_IMPORTS = frozenset(
    {
        "__future__",  # compiler directive only (e.g. annotations) — no runtime capability
        "typing",
        "dataclasses",
        "enum",
        "json",
        "math",
        "re",
        "datetime",
        "uuid",
        "collections",
        "itertools",
        "functools",
        "abc",
        "asyncio",
        "broca",
        "broca.agents",
        "broca.agents._base",
    }
)

# Names that are RCE primitives regardless of how they are reached.
FORBIDDEN_NAMES = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "open",
        "input",
        "breakpoint",
        "globals",
        "locals",
        "vars",
        "getattr",
        "setattr",
        "delattr",
        "memoryview",
        "exit",
        "quit",
    }
)

# Builtins the generated code is allowed to see. Everything else is simply absent.
SAFE_BUILTINS = {
    name: (__builtins__[name] if isinstance(__builtins__, dict) else getattr(__builtins__, name))
    for name in (
        "None",
        "True",
        "False",
        "bool",
        "int",
        "float",
        "str",
        "bytes",
        "list",
        "dict",
        "set",
        "tuple",
        "len",
        "range",
        "enumerate",
        "zip",
        "map",
        "filter",
        "sorted",
        "sum",
        "min",
        "max",
        "abs",
        "round",
        "any",
        "all",
        "isinstance",
        "issubclass",
        "print",
        "repr",
        "type",
        "object",
        "super",
        "property",
        "staticmethod",
        "classmethod",
        "Exception",
        "ValueError",
        "TypeError",
        "KeyError",
        "RuntimeError",
        "AttributeError",
        "IndexError",
        "StopIteration",
        "NotImplementedError",
        "__build_class__",
        "__name__",
    )
    if (name in __builtins__ if isinstance(__builtins__, dict) else hasattr(__builtins__, name))
}

_IDENT_RE = re.compile(r"[^A-Za-z0-9_]")


class UnsafeGeneratedCode(Exception):
    """The generated source failed validation and must not be executed."""


def exec_enabled() -> bool:
    """Executing model-written code is opt-in. Default OFF: it is RCE by design, so running
    it must be a deliberate operator decision, not a silent default."""
    return os.getenv("BROCA_AUTO_AGENT_EXEC", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def safe_identifier(name: str) -> str:
    """Make a name safe to interpolate into generated source (blocks template injection)."""
    ident = _IDENT_RE.sub("_", str(name or "agent"))[:64]
    if not ident or ident[0].isdigit():
        ident = f"a_{ident}"
    return ident


def validate_agent_code(code: str) -> None:
    """Reject generated source that reaches for anything dangerous. Raises UnsafeGeneratedCode.

    An allow-list, not a deny-list: unknown imports are refused rather than permitted.
    """
    if not code or not code.strip():
        raise UnsafeGeneratedCode("empty source")
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise UnsafeGeneratedCode(f"syntax error: {exc}") from exc

    for node in ast.walk(tree):
        # imports: allow-listed roots only
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in ALLOWED_IMPORTS and alias.name not in ALLOWED_IMPORTS:
                    raise UnsafeGeneratedCode(f"import not allowed: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod.split(".")[0] not in ALLOWED_IMPORTS and mod not in ALLOWED_IMPORTS:
                raise UnsafeGeneratedCode(f"import not allowed: from {mod}")
        # direct RCE primitives
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            raise UnsafeGeneratedCode(f"forbidden name: {node.id}")
        # dunder access is how sandboxes get escaped (obj.__class__.__bases__...)
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__") and node.attr.endswith("__"):
            raise UnsafeGeneratedCode(f"forbidden dunder attribute: {node.attr}")
