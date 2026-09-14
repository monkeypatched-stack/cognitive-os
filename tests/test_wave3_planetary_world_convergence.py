"""Wave 3 ownership-convergence tests."""

from src.monkey_brain.kernel.compile.world_model_runtime import WorldModelRuntime
from src.monkey_brain.kernel.society.domain import Society
from src.monkey_brain.kernel.society.integration import PlanetaryRuntime
from src.monkey_brain.kernel.society.runtime import SocietyRuntime
from src.monkey_brain.kernel.society.world import WorldEntity
from src.monkey_brain.kernel.society.learning import SharedExperience


def test_planetary_runtime_has_one_world_model_and_shared_world_owner():
    planetary = PlanetaryRuntime(Society(name="wave3"))

    assert isinstance(planetary.world_model, WorldModelRuntime)
    assert planetary.world is planetary.world_model.semantic_world
    assert planetary._society_runtime._world is planetary.world
    assert planetary._society_runtime.context_stream is planetary.context_stream


def test_planetary_routes_world_mutation_through_world_model():
    planetary = PlanetaryRuntime(Society(name="wave3"))
    entity = WorldEntity(name="canonical-world")

    planetary.add_world_entity(entity)

    assert planetary.world.get_entity(entity.entity_id) is not None
    assert planetary.world_model.semantic_world is planetary.world
    assert planetary.context_stream.event_count >= 1


def test_hosted_societies_share_planetary_context_and_world():
    planetary = PlanetaryRuntime(Society(name="wave3"))
    secondary = SocietyRuntime(Society(name="secondary"))

    planetary.add_society(secondary)

    assert secondary._world is planetary.world
    assert secondary.context_stream is planetary.context_stream


def test_learning_world_impact_is_routed_through_planetary_world_model():
    planetary = PlanetaryRuntime(Society(name="wave3"))
    entity = WorldEntity(name="before")
    planetary.add_world_entity(entity)
    experience = SharedExperience(
        actor_id="actor-1",
        outcome="success",
        world_impact={
            "entity_updates": [
                {
                    "entity_id": entity.entity_id,
                    "attributes": {"state": {"status": "learned"}},
                }
            ]
        },
    )

    planetary.share_experience(experience)

    assert planetary.world.get_entity(entity.entity_id).state["status"] == "learned"
