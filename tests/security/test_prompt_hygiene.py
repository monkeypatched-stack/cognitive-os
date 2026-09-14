"""Prompt hygiene — the planner's own instructions must not pollute the user's intent (P-1).

/plan wrapped the question in a wall of guidance (rules + worked examples for Compiler, Blog
Writer and Todo) and passed that ENTIRE string downstream as the "intent". Observed live via
the LLM bridge, the domain-extraction call received:

    Extract a single domain name from this intent: "<question + rules + Compiler/Blog/Todo
    examples>". Output ONLY the domain name. Example: "build a todo app" -> "Todo"

i.e. it was asked to name the domain of a prompt stuffed with unrelated example domains. A
weaker model answers "Compiler" or "Todo". The guidance now lives in the graph generator's
own prompt; the intent carried around is the raw question.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PLAN = ROOT / "src/monkey_brain/api/routes/plan.py"
GRAPH_GEN = ROOT / "packages/broca/broca/agents/graph_generator_agent.py"

POLLUTANTS = [
    "expert software architecture planner",
    "Blog Writer:",
    "Todo Application:",
    "LexerAgent",  # from the Compiler example
]


def test_plan_route_passes_the_raw_question_as_the_intent():
    src = PLAN.read_text()
    assert "prompt = payload.question" in src, "the intent must be the user's raw question"


def _code_only(src: str) -> str:
    """Strip comments — the explanation of this fix legitimately names the old pollutants."""
    return "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))


def test_plan_route_no_longer_embeds_guidance_in_the_intent():
    code = _code_only(PLAN.read_text())
    for bad in POLLUTANTS:
        assert bad not in code, f"planner boilerplate {bad!r} is still being fed into the intent"


def test_the_guidance_survives_in_the_agent_that_needs_it():
    """Removing the pollution must not lose the planning rules — they move, not vanish."""
    src = GRAPH_GEN.read_text()
    for rule in (
        "exactly one responsibility",
        "Do NOT create agents named Discovery",
        "well-known components",
        "parallel execution",
    ):
        assert rule in src, f"planning rule {rule!r} was lost, not relocated"


def test_domain_extraction_prompt_receives_only_the_question():
    """The domain-extraction call interpolates the intent. With a clean intent, no example
    domains can leak into it — so the plan route must not build a prompt template at all.
    """
    assert "Extract a single domain name from this intent" in GRAPH_GEN.read_text()
    code = _code_only(PLAN.read_text())
    assert 'prompt = f"""' not in code, "plan route is building a prompt template again"
