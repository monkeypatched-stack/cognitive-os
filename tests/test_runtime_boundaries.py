"""Runtime hierarchy boundary tests for Planet -> Society -> Actor."""

import ast
from pathlib import Path

from src.monkey_brain.kernel.compile.actor_runtime import ActorRuntime
from src.monkey_brain.kernel.society import (
    Planet,
    SocietyRuntime,
    SocietyRuntimeContract,
)
from src.monkey_brain.kernel.society.domain import ActorIdentity, ActorProfile, Society
from src.monkey_brain.kernel.society.integration import PlanetaryRuntime


def test_public_runtime_contracts_are_three_tiered():
    planet = PlanetaryRuntime(Society(name="planet-default"))
    society = planet.get_society_runtime(planet.society.society_id)
    assert isinstance(planet, Planet)
    assert isinstance(society, SocietyRuntimeContract)

    state = society.register_actor(ActorProfile(identity=ActorIdentity(name="actor")))
    actor = society.get_actor_runtime(state.actor_id)
    assert isinstance(actor, ActorRuntime)
    assert isinstance(actor, ActorRuntime)
    assert actor is state.actor_runtime


def test_society_does_not_construct_cognitive_os_directly():
    source = Path("src/monkey_brain/kernel/society/runtime.py").read_text()
    tree = ast.parse(source)
    cognitive_os_constructs = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "CognitiveOS"
    ]
    assert cognitive_os_constructs == []


def test_actor_runtime_hides_cognitive_implementation_from_public_contract():
    assert "CognitiveOS" not in {name for name in dir(ActorRuntime)}
