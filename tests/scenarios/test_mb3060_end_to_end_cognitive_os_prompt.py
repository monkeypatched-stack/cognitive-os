"""MB-3060 End-to-End CognitiveOS /prompt — full pipeline scenario.

Customer: "I need a wireless gaming mouse under $100 with next-day
delivery."

Verify: Intent resolution, Planning correctness, Society coordination,
Actor cognition, Presence updates, Temporary memberships, Context
propagation, Lemon metrics, Planetary cycle completion, Correct user
response.

Investigation found the real /prompt pipeline (PlanetaryRuntime.
execute_actor_request() -> CognitiveActor._cognitive_tick()) only
genuinely did Intent Understanding/Planning + generic context events —
every downstream step (Search/Cart/Checkout/Payment/Fraud/Fulfillment/
Shipping) executed SIMULATED, because (a) no real capability_bus was
ever wired into a live actor's cognitive engine, and (b) even with one,
the LLM invented free-text action names ("search"/"select"/"purchase")
that never matched any real capability, and (c) Lemon metrics only
publish on cycle(), a separate operation from execute_actor_request().

Per explicit design choice ("wire the real capability bus into the live
pipeline"), this closes the two most load-bearing, correctly-scoped
pieces of that gap:

  1. kernel/pipeline/planning/context_engine.py::ContextConstructionEngine
     now actually populates PlanningContext.available_capabilities with
     every capability name ActionExecutor can genuinely invoke (has a
     real .handle() method) — a field that already existed but was
     always hardcoded to () before this.
  2. kernel/pipeline/llm_planner.py::LLMPlanner now renders that list in
     its prompt ("Available actions... must be one of these, verbatim")
     and instructs the model accordingly — a plain FACT about what's
     executable, not a hardcoded business decision, matching this
     planner's own "no business decision may be hardcoded" doctrine.
     Also hardened _normalize_required_permission() against a weaker
     model echoing the JSON schema's own placeholder text back
     ("resource:delivery or empty if none needed") as if it were a real
     permission value.

This file wires a real, business-capable CognitiveActor (via
domains/vertical_router.py::build_runtime_engine() — already-existing,
previously-uninvoked machinery, per its own docstring — plus a
ContextConstructionEngine so the planning stage actually uses it) and
runs the EXACT customer prompt against a real seeded catalog, using a
real local LLM (Ollama). Verified live, repeatedly: the model now picks
ONLY real capability names, and the first real step (ProductSelection)
genuinely queries the real KG and finds the real, matching product —
proof the wiring is real, not simulated.

Documented boundary NOT fixed here (a separate, larger, structural
gap): PlanStep/Action have no field for the LLM to pass STRUCTURED
parameters (e.g. "here is the specific product_id I'm choosing") — the
system prompt asks for it, but the plan-step JSON schema and
belief_runtime.py's Action construction (parameters={"description":
step.description} unconditionally) have nowhere to put it. This is why
a full chain through Checkout/Payment/Fulfillment doesn't complete
end-to-end yet — ProductSelectionCapability correctly returns
"decision_required" candidates, but nothing downstream can act on a
decision the plan format has no way to express. Fixing that is its own
follow-up (a PlanStep.parameters field + system prompt schema change +
Action construction change), not attempted here.
"""

from __future__ import annotations

import httpx
import pytest

from src.monkey_brain.kernel.compile import _obs
from src.monkey_brain.kernel.compile.cognitive_actor import CognitiveActor
from src.monkey_brain.kernel.domains import (
    grocery,
)  # noqa: F401 -- registers the grocery vertical
from src.monkey_brain.kernel.domains.commerce import list_product, onboard_merchant
from src.monkey_brain.kernel.domains.vertical_router import build_runtime_engine
from src.monkey_brain.kernel.knowledge_graph import EntityType, KnowledgeGraph
from src.monkey_brain.kernel.pipeline.planning.context_engine import (
    ContextConstructionEngine,
)
from src.monkey_brain.kernel.society.context_stream import ContextEventType
from src.monkey_brain.kernel.society.domain import (
    ActorIdentity,
    ActorProfile,
    ActorType,
)
from src.monkey_brain.kernel.society.integration import PlanetaryRuntime

PROMPT_TEXT = "I need a wireless gaming mouse under $100 with next-day delivery."


def _ollama_reachable(base_url: str = "http://localhost:11434") -> bool:
    try:
        return httpx.get(f"{base_url}/api/tags", timeout=2.0).status_code == 200
    except Exception:
        return False


def _build_real_scenario():
    """A real, wired PlanetaryRuntime + a matching commerce KG: a
    wireless gaming mouse priced under $100, a wallet, a payment
    processor, a delivery rider — everything the customer's stated
    criteria and the checkout pipeline actually need."""
    marketplace = PlanetaryRuntime()
    identity = ActorIdentity(name="Alice", actor_type=ActorType.HUMAN)
    actor_id = identity.actor_id
    profile = ActorProfile(identity=identity)

    kg = KnowledgeGraph()
    store_id = onboard_merchant(
        kg,
        "merchant_bob",
        "Bob's Store",
        delivery_fee=0.0,
        address="200 W 23rd St, New York, NY",
    )["store_id"]
    product_id = list_product(kg, store_id, "merchant_bob", "Wireless Gaming Mouse", price=59.99, quantity=25)[
        "product_id"
    ]
    kg.add_entity(
        "addr_home",
        EntityType.ADDRESS,
        "Home",
        {
            "street": "123 Main St",
            "city": "New York",
            "state": "NY",
            "zip_code": "10001",
            "is_primary": True,
        },
    )
    kg.add_entity(
        "wallet_alice",
        EntityType.ACCOUNT,
        "Alice Wallet",
        {
            "account_type": "debit",
            "balance": 500.0,
            "owner": actor_id,
        },
    )
    kg.add_entity(
        "proc_stripe",
        EntityType.ORGANIZATION,
        "Stripe",
        {"type": "payment_processor", "priority": 0},
    )
    kg.add_entity(
        "rider_1",
        EntityType.PERSON,
        "Ravi",
        {
            "status": "available",
            "rating": 4.8,
            "vehicle": "bike",
            "estimated_minutes": 20,
        },
    )

    engine = build_runtime_engine(None, name="grocery")
    engine._context_engine = ContextConstructionEngine(planetary_runtime=marketplace, knowledge_graph=kg)
    my_actor = CognitiveActor(
        entity_id=actor_id,
        engine=engine,
        # context_factory receives this tick's triggering question (see
        # runtime.py::register_actor's own "Context-Aware Personalized
        # Planning refactor" comment) -- this scenario's question is
        # always the fixed PROMPT_TEXT below, so the real per-tick value
        # is accepted but intentionally unused, not ignored by omitting
        # the parameter (which crashed: "takes 0 positional arguments
        # but 1 was given").
        context_factory=lambda question: {
            "knowledge_graph": kg,
            "actor_id": actor_id,
            "question": PROMPT_TEXT,
        },
    )
    alice_state = marketplace.register_actor(profile, actor=my_actor)
    return marketplace, alice_state, kg, product_id


# ── Deterministic: the capability-vocabulary wiring itself ────────────


def test_mb3060_available_capabilities_lists_only_real_invokable_names():
    engine = ContextConstructionEngine()

    names = engine._retrieve_available_capabilities()

    assert "ProductSelection" in names
    assert "OrderCreation" in names
    assert "Payment" in names
    assert "Delivery" in names
    # DomainCapability containers (invoke()-only, no real .handle()) must
    # never be offered — ActionExecutor would crash on them.
    assert "commerce" not in names
    assert "logistics" not in names
    assert "finance" not in names


def test_mb3060_llm_prompt_renders_available_actions_verbatim():
    from src.monkey_brain.kernel.pipeline.belief_state import Goal
    from src.monkey_brain.kernel.pipeline.llm_planner import LLMPlanner
    from src.monkey_brain.kernel.pipeline.planning.domain import PlanningContext

    planner = LLMPlanner.__new__(LLMPlanner)  # skip backend construction
    context = PlanningContext(
        actor_id="alice",
        goal=Goal(name="buy mouse"),
        available_capabilities=("ProductSelection", "OrderCreation", "Payment"),
    )

    prompt = planner._build_prompt(context.goal, [], context)

    assert "Available actions" in prompt
    assert "- ProductSelection" in prompt
    assert "- OrderCreation" in prompt
    assert "- Payment" in prompt


def test_mb3060_permission_schema_echo_is_normalized_to_empty():
    from src.monkey_brain.kernel.pipeline.llm_planner import (
        _normalize_required_permission,
    )

    assert _normalize_required_permission("resource:delivery or empty if none needed") == ""
    assert _normalize_required_permission("resource:payment or empty if none needed") == ""
    # A genuine permission string is left untouched.
    assert _normalize_required_permission("payment:creditcard") == "payment:creditcard"


# ── Real local LLM: the full wired pipeline ────────────────────────────


def _force_ollama_backend(monkeypatch) -> None:
    """conftest.py's autouse _default_test_llm_backend fixture patches
    model_backend.get_backend itself (the function LLMPlanner.__init__
    actually calls) to always return a fake, synchronous
    _GenericPlanningBackend — deliberately, so the rest of the suite
    never hits a real LLM. Patching _default_backend (the module
    attribute get_backend() would otherwise lazily populate) does
    nothing once get_backend itself has been replaced — confirmed live:
    self._backend resolved to _GenericPlanningBackend regardless,
    and awaiting its synchronous, non-coroutine complete() raised
    "'str' object can't be awaited". monkeypatch is function-scoped and
    shared between autouse fixtures and the test body, so patching the
    SAME symbol (get_backend) here, after the autouse fixture already
    ran, wins for the remainder of this test — matching the escape
    hatch _GenericPlanningBackend's own docstring documents."""
    from src.monkey_brain.kernel.execute.provider import (
        model_backend as model_backend_module,
    )

    monkeypatch.setattr(
        model_backend_module,
        "get_backend",
        lambda: model_backend_module.ModelBackend(provider="ollama"),
    )


@pytest.mark.asyncio
async def test_mb3060_real_prompt_produces_a_plan_using_only_real_capabilities(
    monkeypatch,
):
    if not _ollama_reachable():
        pytest.skip("no local Ollama server reachable at localhost:11434")
    _force_ollama_backend(monkeypatch)

    marketplace, alice_state, kg, product_id = _build_real_scenario()
    available = set(ContextConstructionEngine()._retrieve_available_capabilities())

    result = await marketplace.execute_actor_request(alice_state.actor_id, {"question": PROMPT_TEXT})

    # Intent resolution / Planning correctness.
    assert result.plan.planner == "llm"
    assert result.plan.confidence > 0.0
    assert len(result.plan.steps) > 0
    plan_text = " ".join(f"{s.action} {s.description}".lower() for s in result.plan.steps)
    assert any(kw in plan_text for kw in ("mouse", "gaming", "wireless"))
    # The old free-text failure mode ("search"/"filter"/"select"/
    # "purchase" — none of them real capabilities) no longer happens:
    # every step's action is a genuinely invokable capability name.
    for step in result.plan.steps:
        assert step.action in available, f"{step.action!r} is not a real capability"


@pytest.mark.asyncio
async def test_mb3060_at_least_one_step_executes_for_real_not_simulated(monkeypatch):
    if not _ollama_reachable():
        pytest.skip("no local Ollama server reachable at localhost:11434")
    _force_ollama_backend(monkeypatch)

    marketplace, alice_state, kg, product_id = _build_real_scenario()

    result = await marketplace.execute_actor_request(alice_state.actor_id, {"question": PROMPT_TEXT})

    real_outcomes = [
        a for a in result.actions if isinstance(a.get("result"), dict) and not a["result"].get("simulated")
    ]
    assert real_outcomes, "every action was simulated -- the capability bus never actually ran"


@pytest.mark.asyncio
async def test_mb3060_product_selection_genuinely_queries_the_real_catalog(monkeypatch):
    if not _ollama_reachable():
        pytest.skip("no local Ollama server reachable at localhost:11434")
    _force_ollama_backend(monkeypatch)

    marketplace, alice_state, kg, product_id = _build_real_scenario()

    result = await marketplace.execute_actor_request(alice_state.actor_id, {"question": PROMPT_TEXT})

    # ProductSelectionCapability.handle()'s real success shape (grocery.py)
    # is a flat {"success": True, "selected": [{"id": ..., ...}], ...} --
    # not the nested candidates -> products structure this assertion
    # previously checked, which doesn't match any current branch of that
    # capability (confirmed live, repeatedly, this session's own testing).
    product_selection_outcomes = [
        a for a in result.actions if isinstance(a.get("result"), dict) and a["result"].get("selected")
    ]
    assert product_selection_outcomes, "no step returned a real product selection"
    found_ids = {p.get("id") for outcome in product_selection_outcomes for p in outcome["result"]["selected"]}
    assert product_id in found_ids


# ── System-level: society coordination, presence, membership, context,
#    metrics, planetary cycle -- all independent of what the LLM plans ──


@pytest.mark.asyncio
async def test_mb3060_actor_cognition_and_society_coordination():
    marketplace, alice_state, kg, product_id = _build_real_scenario()
    before = alice_state.cycle_count

    await marketplace.execute_actor_request(alice_state.actor_id, {"question": PROMPT_TEXT})

    home = marketplace.get_society_runtime(marketplace.society.society_id)
    after = home.get_actor(alice_state.actor_id).cycle_count
    assert after > before


@pytest.mark.asyncio
async def test_mb3060_presence_and_effective_membership_are_real():
    marketplace, alice_state, kg, product_id = _build_real_scenario()

    await marketplace.execute_actor_request(alice_state.actor_id, {"question": PROMPT_TEXT})

    presence = marketplace.presence.current(alice_state.actor_id)
    assert presence is not None
    assert presence.is_open()
    assert marketplace.society.society_id in marketplace.effective_societies(alice_state.actor_id)


@pytest.mark.asyncio
async def test_mb3060_context_propagation_publishes_real_events():
    marketplace, alice_state, kg, product_id = _build_real_scenario()
    events_before = marketplace.context_stream.event_count

    await marketplace.execute_actor_request(alice_state.actor_id, {"question": PROMPT_TEXT})

    events_after = marketplace.context_stream.event_count
    new_events = marketplace.context_stream.events(limit=events_after - events_before)
    assert events_after > events_before
    assert any(e.event_type is ContextEventType.OBSERVATION and e.actor_id == alice_state.actor_id for e in new_events)


@pytest.mark.asyncio
async def test_mb3060_lemon_metrics_and_planetary_cycle_completion():
    marketplace, alice_state, kg, product_id = _build_real_scenario()
    await marketplace.execute_actor_request(alice_state.actor_id, {"question": PROMPT_TEXT})

    recorder = _obs.Recorder()
    _obs.set_sink(recorder)
    try:
        cycle_result = await marketplace.cycle()
    finally:
        _obs.clear_sink()

    # Planetary cycle completion.
    assert cycle_result is not None
    # Lemon metrics -- a separate operation from execute_actor_request()
    # (see module docstring), genuinely published by a real cycle().
    names = recorder.names()
    assert any(n.startswith("planetary.") for n in names)
    assert any(n.startswith("governance.") for n in names)
    assert any(n.startswith("presence.") for n in names)


@pytest.mark.asyncio
async def test_mb3060_response_is_returned_and_reports_the_real_outcome():
    """Correct user response: honestly scoped to what's actually
    synthesized today (api/routes/prompt.py's own response shape, not
    touched by this ticket) — a real, structured outcome reflecting
    whether the actor's request actually succeeded, not a fabricated
    "success" regardless of what happened."""
    marketplace, alice_state, kg, product_id = _build_real_scenario()

    result = await marketplace.execute_actor_request(alice_state.actor_id, {"question": PROMPT_TEXT})

    assert result.actual_outcome is not None
    assert "goal_achieved" in result.actual_outcome
    assert result.actions is not None
