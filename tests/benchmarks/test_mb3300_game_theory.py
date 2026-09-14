from src.monkey_brain.kernel.society.game_theory import (
    GameTheoryRuntime,
    Strategy,
    StrategyProfile,
)
from src.monkey_brain.kernel.society.integration import PlanetaryRuntime
from src.monkey_brain.kernel.society.runtime import SocietyRuntime


def profile(actor, *strategies, **preferences):
    return StrategyProfile(
        actor_id=actor, preferences=preferences, strategies=tuple(strategies)
    )


def test_mb3300_through_mb3310_strategic_runtime_contract():
    runtime = GameTheoryRuntime()
    fast = Strategy("fast", attributes={"speed": 10, "cost": 2, "risk": 2})
    cheap = Strategy("cheap", attributes={"speed": 3, "cost": 1, "risk": 1})
    customer = profile("customer", fast, cheap, speed=1, cost=-1)
    merchant = profile("merchant", fast, cheap, cost=-2, speed=0.5)

    assert runtime.requires_strategic_reasoning(
        ("customer", "merchant"), shared_resources=("delivery",)
    )
    assert runtime.choose(customer).strategy.name == "fast"
    agreement = runtime.negotiate("delivery exception", (customer, merchant))
    assert agreement.chosen_strategy is not None
    assert (
        runtime.world_state["negotiated_agreements"][agreement.agreement_id]["strategy"]
        == agreement.chosen_strategy.name
    )

    metrics = runtime.metrics_snapshot()
    assert metrics["negotiations_completed"] == 1
    assert metrics["utility_evaluations"] >= 4


def test_game_theory_is_wired_through_society_and_planetary_boundaries():
    society = SocietyRuntime()
    assert society.coordinate().strategic_runtime is society.game_theory

    planetary = PlanetaryRuntime()
    assert planetary.coordinate().strategic_runtime is planetary.game_theory
    created = planetary.create_society("Strategic Society")
    assert created.coordinate().strategic_runtime is planetary.game_theory
