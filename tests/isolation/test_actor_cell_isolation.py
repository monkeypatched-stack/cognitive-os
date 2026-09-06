"""Actor Cell isolation tests — proves two actors (A, B) co-resident in one
process never share identity, knowledge, memory, belief, runtime state, or
ROS binding, and that a failure in one actor's tick cannot affect the other.

Closes the gaps docs/ACTOR_CELL_ARCHITECTURE.md identified (Sections C/D/J)
and this migration's own three required capabilities: per-actor identity
(kernel/actor_identity.py), CognitiveActor's per-actor KnowledgeGraph wired
into a real read/write path (kernel/domains/grocery.py's record_rejection/
get_rejected_keywords), and ROS Adapter actor-specific binding
(kernel/edge/ros_integration.py).

Run with: pytest tests/isolation/ -v
"""
from __future__ import annotations

import asyncio
import time

import pytest

from src.monkey_brain.kernel.actor_identity import (
    ActorCellIdentityCache,
    ActorIdentityError,
    mint_actor_cell_identity,
    verify_actor_cell_identity,
)
from src.monkey_brain.kernel.approval import reset_approval_store
from src.monkey_brain.kernel.compile.cognitive_actor import CognitiveActor
from src.monkey_brain.kernel.delegation import reset_delegation_store_for_tests
from src.monkey_brain.kernel.domains.grocery import get_rejected_keywords, record_rejection
from src.monkey_brain.kernel.edge.ros_integration import (
    FakeRosExecutionAdapter,
    RosUnavailableError,
    run_ros_action_if_governed,
)
from src.monkey_brain.kernel.learn.memory.manager import MemoryManager
from src.monkey_brain.kernel.learn.memory.primitives import ProvenanceToken
from src.monkey_brain.kernel.security_boundary import reset_governed_pipeline_for_tests
from src.monkey_brain.kernel.society.actor_cell import ActorCell
from src.monkey_brain.kernel.society.domain import ActorIdentity, ActorProfile, ActorType
from src.monkey_brain.kernel.society.runtime import ActorRuntimeState, SocietyRuntime
from src.monkey_brain.kernel.trusted_auth import TrustedAuthEvidence, bind_trusted_auth


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    # Same posture as tests/unit/test_edge_ros_integration.py -- insecure
    # dev mode + a bound service principal, so the ROS-isolation tests'
    # calls into the real ensure_governed/run_governed_mutation pipeline
    # resolve without needing a live OPA/Redis/Mongo deployment.
    monkeypatch.setenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", "true")
    bind_trusted_auth(TrustedAuthEvidence(
        authenticated=True, token_valid=True, principal_id="test-process",
        principal_type="service", mfa_status="satisfied",
    ))
    reset_approval_store()
    reset_governed_pipeline_for_tests()
    reset_delegation_store_for_tests()
    yield
    reset_approval_store()
    reset_governed_pipeline_for_tests()
    reset_delegation_store_for_tests()


def _make_cell(actor_id: str, *, issuer: str = "test-issuer") -> ActorCell:
    actor = CognitiveActor(entity_id=actor_id)
    state = ActorRuntimeState(actor_id=actor_id, actor=actor)
    credential = mint_actor_cell_identity(actor_id, issuer=issuer)
    return ActorCell(
        actor_id=actor_id, identity=credential, actor=actor, runtime_state=state,
        ros_adapter=FakeRosExecutionAdapter(actor_id=actor_id),
    )


class TestIdentityIsolation:
    def test_two_actors_get_distinct_credentials(self):
        a = _make_cell("actor-A")
        b = _make_cell("actor-B")
        assert a.identity.delegation_id != b.identity.delegation_id
        assert a.identity.delegate == "actor-A"
        assert b.identity.delegate == "actor-B"

    def test_actor_a_credential_cannot_authorize_as_actor_b(self):
        """The core requirement: Actor A cannot authenticate as Actor B."""
        a = _make_cell("actor-A", issuer="same-process")
        result = verify_actor_cell_identity(
            a.identity, actor_id="actor-B", authenticated_issuer="same-process",
        )
        assert result.authorized is False
        assert "delegate" in result.failure_reason.lower()

    def test_actor_a_credential_verifies_for_actor_a(self):
        a = _make_cell("actor-A", issuer="same-process")
        result = verify_actor_cell_identity(
            a.identity, actor_id="actor-A", authenticated_issuer="same-process",
        )
        assert result.authorized is True

    def test_identity_cache_binds_trusted_auth_only_for_its_own_actor(self):
        cache_a = ActorCellIdentityCache(actor_id="actor-A")
        cache_a.ensure(issuer="proc-1")
        verified = cache_a.bind_trusted_auth(authenticated_issuer="proc-1")
        assert verified["delegate"] == "actor-A"

        # Presenting actor-A's cache's credential against a verification for
        # actor-B must fail (it's the same underlying enforcement
        # bind_actor_cell_trusted_auth uses, exercised from the cache path).
        with pytest.raises(ActorIdentityError):
            from src.monkey_brain.kernel.actor_identity import bind_actor_cell_trusted_auth
            bind_actor_cell_trusted_auth("actor-B", cache_a.credential, authenticated_issuer="proc-1")

    def test_actor_cell_rejects_construction_with_mismatched_identity(self):
        """ActorCell.__post_init__ itself refuses to hold another actor's
        credential -- defense in depth beyond the identity module."""
        credential_for_b = mint_actor_cell_identity("actor-B", issuer="proc-1")
        actor = CognitiveActor(entity_id="actor-A")
        state = ActorRuntimeState(actor_id="actor-A", actor=actor)
        with pytest.raises(ValueError):
            ActorCell(actor_id="actor-A", identity=credential_for_b, actor=actor, runtime_state=state)


class TestKnowledgeGraphIsolation:
    def test_two_actors_have_distinct_kg_instances(self):
        a = _make_cell("actor-A")
        b = _make_cell("actor-B")
        assert a.actor._knowledge_graph is not b.actor._knowledge_graph
        assert a.actor._knowledge_graph.person_id != b.actor._knowledge_graph.person_id

    def test_rejection_recorded_in_a_does_not_leak_into_b(self):
        a = _make_cell("actor-A")
        b = _make_cell("actor-B")

        record_rejection(a.actor._knowledge_graph, "actor-A", "almond")
        record_rejection(a.actor._knowledge_graph, "actor-A", "almond")

        assert "almond" in get_rejected_keywords(a.actor._knowledge_graph, "actor-A")
        assert "almond" not in get_rejected_keywords(b.actor._knowledge_graph, "actor-B")
        # B's graph was never written to at all.
        assert b.actor._knowledge_graph._entities == {}


class TestMemoryIsolation:
    def test_working_memory_keyed_by_actor_and_task_never_collides(self):
        manager = MemoryManager(vector_client=None, graph_client=None)
        token = ProvenanceToken(trace_id="t", policy_path="p", auth_hash="h")

        node_a = manager.allocate_working_context("actor-A", "task-1", {"item": "A's state"}, token)
        node_b = manager.allocate_working_context("actor-B", "task-1", {"item": "B's state"}, token)

        assert node_a is not node_b
        assert manager.working_memory[("actor-A", "task-1")].payload == {"item": "A's state"}
        assert manager.working_memory[("actor-B", "task-1")].payload == {"item": "B's state"}


class TestBeliefIsolation:
    def test_two_actors_have_distinct_belief_tensors(self):
        a = _make_cell("actor-A")
        b = _make_cell("actor-B")
        assert a.actor.belief is not b.actor.belief

    def test_mutating_one_actors_belief_does_not_affect_the_other(self):
        a = _make_cell("actor-A")
        b = _make_cell("actor-B")
        assert a.actor.belief.nnz() == 0
        assert b.actor.belief.nnz() == 0
        a.actor.belief.observe("state-1", "state-2")
        assert a.actor.belief.nnz() == 1
        assert b.actor.belief.nnz() == 0


class TestRuntimeIsolation:
    def test_two_actors_have_distinct_runtime_state_and_no_shared_containers(self):
        a = _make_cell("actor-A")
        b = _make_cell("actor-B")
        assert a.runtime_state is not b.runtime_state
        assert a.runtime_state.cognitive_stages is not b.runtime_state.cognitive_stages
        a.runtime_state.cognitive_stages["x"] = lambda s: s
        assert "x" not in b.runtime_state.cognitive_stages


class TestRosIsolation:
    def test_bound_adapter_refuses_to_execute_for_a_different_actor(self):
        a = _make_cell("actor-A")
        with pytest.raises(RosUnavailableError):
            asyncio.run(run_ros_action_if_governed(
                capability="noop", resource="robot", parameters={},
                adapter=a.ros_adapter, actor_id="actor-B",
                local_policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE"},
            ))

    def test_bound_adapter_executes_for_its_own_actor(self):
        a = _make_cell("actor-A")
        result = asyncio.run(run_ros_action_if_governed(
            capability="noop", resource="robot", parameters={},
            adapter=a.ros_adapter, actor_id="actor-A",
            local_policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE"},
        ))
        assert result["success"] is True

    def test_unbound_adapter_and_call_preserve_prior_behavior(self):
        """actor_id="" (the default, matching every pre-existing caller)
        must never trigger the new binding check."""
        adapter = FakeRosExecutionAdapter()
        result = asyncio.run(run_ros_action_if_governed(
            capability="noop", resource="robot", parameters={}, adapter=adapter,
            local_policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE"},
        ))
        assert result["success"] is True


class TestHeartbeatCapability:
    """kernel/domains/robot.py -- the one real, governed, minimal ROS
    capability wired through ActionExecutor's real dispatch shape
    (context/parameters dict in, result dict out)."""

    def test_no_adapter_degrades_honestly(self):
        from src.monkey_brain.kernel.domains.robot import HeartbeatCapability

        result = asyncio.run(HeartbeatCapability().handle({
            "context": {"actor_id": "actor-A", "ros_adapter": None},
        }))
        assert result["success"] is False
        assert "ros" in result["error"].lower()

    def test_bound_adapter_executes_through_real_governance(self):
        from src.monkey_brain.kernel.domains.robot import HeartbeatCapability

        a = _make_cell("actor-A")
        result = asyncio.run(HeartbeatCapability().handle({
            "context": {"actor_id": "actor-A", "ros_adapter": a.ros_adapter},
        }))
        assert result["success"] is True
        assert a.ros_adapter.calls[0]["capability"] == "Heartbeat"

    def test_actor_a_context_with_actor_b_adapter_fails_closed(self):
        """The cross-actor mismatch this capability must never allow: A's
        context carrying B's adapter (e.g. a future wiring bug) must still
        fail closed at run_ros_action_if_governed, not silently execute."""
        from src.monkey_brain.kernel.domains.robot import HeartbeatCapability

        b = _make_cell("actor-B")
        with pytest.raises(RosUnavailableError):
            asyncio.run(HeartbeatCapability().handle({
                "context": {"actor_id": "actor-A", "ros_adapter": b.ros_adapter},
            }))


class TestCrashIsolation:
    def test_actor_a_tick_failure_does_not_affect_actor_b(self):
        society = SocietyRuntime()
        society.register_actor(ActorProfile(identity=ActorIdentity(
            actor_id="actor-A", name="A", actor_type=ActorType.AI_AGENT,
        )))
        society.register_actor(ActorProfile(identity=ActorIdentity(
            actor_id="actor-B", name="B", actor_type=ActorType.AI_AGENT,
        )))

        async def _boom(actor_state, observation, prompt_request):
            if actor_state.actor_id == "actor-A":
                raise RuntimeError("simulated capability crash for actor-A")
            actor_state.last_tick_result = {"ok": True}

        society._coordinate_actor = _boom  # type: ignore[method-assign]

        result_a = asyncio.run(society.tick_one_actor("actor-A"))
        result_b = asyncio.run(society.tick_one_actor("actor-B"))

        # A's crash was caught (tick_one_actor never propagates it, and
        # reports it via its own None-on-failure contract) while B's own
        # tick still ran normally afterward, in the same process.
        assert result_a is None
        assert result_b is True
        assert society.get_actor("actor-B").last_tick_result == {"ok": True}
        assert society.get_actor("actor-A").last_tick_result != {"ok": True}


class TestTickConcurrency:
    """Swarm-readiness audit, blocker 3 -- 'in-process tick concurrency
    doesn't exist': proves SocietyRuntime.tick()/tick_team() actually
    overlap 3 actors' ticks in wall-clock time now, not just that they
    still produce correct results (already covered by
    tests/scenarios/test_actor_isolation_audit.py::
    test_F_concurrent_actor_execution_no_contamination)."""

    _DELAY = 0.2

    def _three_actor_society(self) -> SocietyRuntime:
        society = SocietyRuntime()
        for actor_id in ("drone-A", "drone-B", "drone-C"):
            society.register_actor(ActorProfile(identity=ActorIdentity(
                actor_id=actor_id, name=actor_id, actor_type=ActorType.AI_AGENT,
            )))

        async def _slow_coordinate(actor_state, observation, prompt_request):
            await asyncio.sleep(self._DELAY)
            actor_state.last_tick_result = {"ok": True}

        society._coordinate_actor = _slow_coordinate  # type: ignore[method-assign]
        return society

    def test_society_tick_overlaps_three_actors(self):
        society = self._three_actor_society()
        start = time.monotonic()
        result = asyncio.run(society.tick())
        elapsed = time.monotonic() - start

        assert result.actors_ticked == 3
        # Sequential would take >= 3 * _DELAY; concurrent should take
        # roughly 1 * _DELAY. 2x is a generous margin for CI jitter while
        # still failing hard if this regresses to sequential.
        assert elapsed < self._DELAY * 2

    def test_tick_team_overlaps_three_actors(self):
        society = self._three_actor_society()
        team = society.create_team("drone-team")
        for actor_id in ("drone-A", "drone-B", "drone-C"):
            society.add_actor_to_team(team.team_id, actor_id)

        start = time.monotonic()
        result = asyncio.run(society.tick_team(team.team_id))
        elapsed = time.monotonic() - start

        assert set(result.actors_ticked) == {"drone-A", "drone-B", "drone-C"}
        assert elapsed < self._DELAY * 2
