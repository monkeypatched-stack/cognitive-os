"""RCE via LLM-generated agent code (B-1).

AutoAgentGenerator's design is: agent not found -> ask an LLM for its Python source -> exec it.
It ran `exec(agent_code, {"__builtins__": __builtins__})` with NO validation, in-process, and
`agent_name`/`agent_description` come from the request context — so both prompt injection and
template injection led to arbitrary code execution.
"""

from __future__ import annotations

import pytest

from broca.agents._codegen_guard import (
    SAFE_BUILTINS,
    UnsafeGeneratedCode,
    exec_enabled,
    safe_identifier,
    validate_agent_code,
)

BENIGN = """
from typing import Any
from broca.agents._base import BaseETASSAgent

class AutoGen_ok(BaseETASSAgent):
    agent_type = "ok"
    async def handle(self, context: dict[str, Any]):
        return {"ok": True}
"""


def test_benign_generated_agent_is_accepted():
    validate_agent_code(BENIGN)  # must not raise


@pytest.mark.parametrize(
    "payload",
    [
        "import os\nos.system('id')",  # the obvious one
        "import subprocess\nsubprocess.run(['sh','-c','id'])",
        "import socket",  # exfiltration
        "import shutil\nshutil.rmtree('/')",  # hallucinated destruction
        "__import__('os').system('id')",
        "eval('1+1')",
        "exec('x=1')",
        "open('/etc/passwd').read()",
        "().__class__.__bases__[0].__subclasses__()",  # classic sandbox escape
    ],
)
def test_dangerous_generated_code_is_rejected(payload):
    with pytest.raises(UnsafeGeneratedCode):
        validate_agent_code(payload)


def test_safe_builtins_do_not_expose_rce_primitives():
    for name in ("eval", "exec", "compile", "__import__", "open", "globals", "getattr"):
        assert name not in SAFE_BUILTINS, f"{name} must not be reachable from generated code"
    assert "len" in SAFE_BUILTINS and "dict" in SAFE_BUILTINS  # normal code still works


def test_execution_is_opt_in_by_default(monkeypatch):
    monkeypatch.delenv("BROCA_AUTO_AGENT_EXEC", raising=False)
    assert exec_enabled() is False  # executing model-written code must be deliberate
    monkeypatch.setenv("BROCA_AUTO_AGENT_EXEC", "1")
    assert exec_enabled() is True


# ── template injection: name/description went straight into Python source ────────


def test_agent_name_cannot_break_out_of_the_source_template():
    evil = 'x"; import os; os.system("id") #'
    ident = safe_identifier(evil)
    assert '"' not in ident and ";" not in ident and " " not in ident
    assert ident.isidentifier()


def test_generated_template_with_hostile_name_is_still_valid_and_safe():
    from broca.agents.auto_agent_generator import AutoAgentGenerator

    gen = AutoAgentGenerator.__new__(AutoAgentGenerator)  # no __init__/LLM needed
    code = gen._heuristic_agent('evil"; import os; os.system("id") #', 'desc"; import os #')
    validate_agent_code(code)  # the hostile name must not inject an import
