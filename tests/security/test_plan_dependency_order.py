"""A plan must run prerequisites before the things that need them.

Planning "build a simple todo agent" produced:

    TodoInputAgent -> TodoListAgent -> TaskCompletionAgent -> DatabaseConnector
    execution_order: [[node_1], [node_2], [node_3], [node_4]]

DatabaseConnector runs LAST — after the two agents that need it — and everything is serial.

The topological sort was never the problem: _topological_order() computes parallel layers
correctly from the edges it is given. It was given a straight line, because the only worked
example in the edge prompt was a compiler pipeline (Lexer -> Parser -> AST -> ... -> CodeGen),
which is strictly linear. The prompt asked for parallelism in prose and demonstrated a chain,
and a small model copies the shape it is shown.
"""

from __future__ import annotations

from pathlib import Path

import pytest

GEN = Path("packages/broca/broca/agents/graph_generator_agent.py")


@pytest.fixture(scope="module")
def prompt() -> str:
    """The edge prompt, with line wrapping normalised — the rules are wrapped in the source."""
    import re

    src = GEN.read_text()
    start = src.index("construct a dependency graph")
    return re.sub(r"\s+", " ", src[start : start + 2600])


def test_the_worked_example_is_not_a_straight_line(prompt):
    """The example is what gets imitated. If it is a chain, the output is a chain."""
    assert "StorageAgent" in prompt, "the example no longer shows a shared prerequisite"
    # the provider fans out to more than one consumer
    fan_out = prompt.count('"from": "StorageAgent"')
    assert fan_out >= 3, f"the example's provider only feeds {fan_out} consumer(s) — still a chain"


def test_the_example_shows_a_join_as_well_as_a_fan_out(prompt):
    """Parallel branches that reconverge — otherwise the model learns fan-out but not joins."""
    assert prompt.count('"to": "NotifyAgent"') >= 2


def test_a_provider_is_stated_to_come_first(prompt):
    assert "PREREQUISITE" in prompt
    assert "never the last node" in prompt


def test_chaining_independent_agents_is_explicitly_forbidden(prompt):
    assert "must NOT be chained" in prompt
    assert "A chain of every agent in a line is almost always wrong" in prompt


def test_the_linear_case_is_still_permitted_when_it_is_genuinely_correct(prompt):
    """A compiler really is a chain. The rule is 'do not DEFAULT to it', not 'never'."""
    assert "Lexer -> Parser -> CodeGen" in prompt
    assert "Do not default to it" in prompt


# ---------------------------------------------------------------- the sort itself


def test_topological_order_groups_independent_nodes_into_one_layer():
    """The sort was always right — this pins it, so a future 'fix' aimed at the prompt bug
    cannot be misapplied here."""
    import sys

    sys.path.insert(0, "packages/broca")
    from broca.agents.graph_generator_agent import GraphGeneratorAgent

    nodes = [
        {"id": "db"},
        {"id": "create"},
        {"id": "query"},
        {"id": "complete"},
        {"id": "notify"},
    ]
    edges = [
        {"from": "db", "to": "create"},
        {"from": "db", "to": "query"},
        {"from": "db", "to": "complete"},
        {"from": "create", "to": "notify"},
        {"from": "complete", "to": "notify"},
    ]
    order = GraphGeneratorAgent()._topological_order(nodes, edges)

    assert order[0] == ["db"], "the prerequisite must run alone, first"
    assert sorted(order[1]) == [
        "complete",
        "create",
        "query",
    ], "independent work must share a layer"
    assert order[2] == ["notify"], "the join must wait for both branches"
    assert len(order) == 3, "a 5-node graph resolved into 3 layers, not 5"
