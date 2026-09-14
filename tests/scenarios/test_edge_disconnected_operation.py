"""Edge-First Architecture — the disconnected-operation acceptance test.

The single most important test this architecture requires: with the cloud
substrate (Redis) genuinely unreachable, an edge/device/robot node must
still be able to boot, load its Society/Actor/World/Presence/Catalog state,
plan, obtain authority for a consequential action via a locally-cached
signed policy snapshot, and commit/execute — using only kernel/edge/
local_store.py::EdgeLocalStore (SQLite). A second, independent process
construction (simulating a restart, still fully disconnected) must recover
everything from EdgeLocalStore alone, proving durability, not just
in-process convenience.

REDIS_PORT is pointed at a port nothing listens on (not a firewall — this
environment doesn't have one to configure, and a closed local port fails
exactly the same way a real unreachable Redis does: PlanetaryRuntime.
_init_persistence()'s own connect+ping raises, self._redis stays None) so
every _load_*/_save_* method in kernel/society/integration.py takes its
real edge-local fallback path, not a mocked one.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def disconnected_edge_env(tmp_path, monkeypatch):
    """Redis unreachable, node_class=edge, EdgeLocalStore pointed at a
    throwaway SQLite file (never the real ~/.monkeybrain/edge/local_store.db —
    kernel/edge/local_store.py's own reset_edge_local_store_for_tests
    docstring requirement)."""
    monkeypatch.setenv("REDIS_HOST", "127.0.0.1")
    monkeypatch.setenv("REDIS_PORT", "1")  # nothing listens here: instant refusal, not a timeout
    monkeypatch.setenv("ACTOR_NODE_CLASS", "edge")
    monkeypatch.setenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", "true")
    monkeypatch.setenv("AGENTOS_AUTH_REQUIRED", "false")
    monkeypatch.delenv("OFFLINE_SAFETY_GATE_ENABLED", raising=False)  # let the new edge-default apply

    from src.monkey_brain.kernel.edge.local_store import (
        reset_edge_local_store_for_tests,
    )

    db_path = str(tmp_path / "edge_local_store.db")
    reset_edge_local_store_for_tests(db_path)
    yield db_path
    reset_edge_local_store_for_tests(None)


def _profile(name: str):
    from src.monkey_brain.kernel.society.domain import (
        ActorIdentity,
        ActorProfile,
        ActorType,
    )

    return ActorProfile(identity=ActorIdentity(name=name, actor_type=ActorType.HUMAN))


class TestEdgeDisconnectedOperation:
    def test_redis_is_genuinely_unreachable(self, disconnected_edge_env):
        """Sanity check the test's own premise before trusting anything
        downstream of it: this is not a mocked disconnection."""
        from src.monkey_brain.kernel.society.integration import PlanetaryRuntime

        pr = PlanetaryRuntime()
        assert pr._redis is None, "test setup did not actually make Redis unreachable"
        assert pr._edge_local_store is not None, "offline-safety gate did not construct EdgeLocalStore for an edge node"

    def test_boot_society_actor_world_presence_chain(self, disconnected_edge_env):
        """boot -> society loads -> actors load -> world loads -> presence
        works, fully disconnected, verified end to end through a SECOND,
        independent PlanetaryRuntime construction (a real process restart)."""
        from src.monkey_brain.kernel.society.integration import PlanetaryRuntime

        pr_a = PlanetaryRuntime()
        state = pr_a.register_actor(_profile("Edge Test Actor"))
        actor_id = state.profile.identity.actor_id
        society_id = next(sid for sid, sr in pr_a._societies.items() if state in sr.all_actors())

        # World: a real, edge-local-persisted save/load round trip.
        pr_a._save_world()

        # Presence: registration itself already called move_actor() (see
        # PlanetaryRuntime.register_actor's own invariant #3) — confirm it
        # actually landed via EdgeLocalStore-backed TimelineStore, not just
        # in-process memory.
        presence = pr_a.presence.current(actor_id)
        assert presence is not None and presence.is_open(), "presence was not established at registration"

        # Restart: a brand-new instance, same disconnected environment.
        pr_b = PlanetaryRuntime()

        assert society_id in pr_b._societies, "society did not survive a disconnected restart"
        restored_state = pr_b._societies[society_id].get_actor(actor_id)
        assert restored_state is not None, "actor did not survive a disconnected restart"
        assert restored_state.profile.identity.name == "Edge Test Actor"

        restored_presence = pr_b.presence.current(actor_id)
        assert restored_presence is not None and restored_presence.is_open(), (
            "presence did not survive a disconnected restart"
        )
        assert restored_presence.space_id == presence.space_id

        geo_ids_a = {e.entity_id for e in pr_a._geo_registry.all()}
        geo_ids_b = {e.entity_id for e in pr_b._geo_registry.all()}
        assert geo_ids_a and geo_ids_a == geo_ids_b, "geography did not survive a disconnected restart"

    def test_catalog_survives_disconnected_restart(self, disconnected_edge_env):
        """The operational catalog (products/stores — KG entities) is part
        of the same edge-local persistence, not a separate mechanism."""
        from src.monkey_brain.kernel.domains.commerce import (
            onboard_merchant,
            list_product,
        )
        from src.monkey_brain.kernel.society.integration import PlanetaryRuntime

        pr_a = PlanetaryRuntime()
        store = onboard_merchant(pr_a.knowledge_graph, "merchant-edge-test", "Edge Test Store")
        created = list_product(
            pr_a.knowledge_graph,
            store["store_id"],
            "merchant-edge-test",
            "Edge-Local Widget",
            price=4.20,
            quantity=7,
        )
        assert created["success"], created

        pr_b = PlanetaryRuntime()
        entity = pr_b.knowledge_graph.get_entity(created["product_id"])
        assert entity is not None, "catalog entry did not survive a disconnected restart"
        assert entity.attributes["price"] == 4.20

    def test_approval_works_via_cached_signed_policy_snapshot(self, disconnected_edge_env):
        """planner works -> approval works -> commit, fully disconnected:
        a REQUIRES_AUTHORITY capability is refused when no local authority
        is cached, then allowed once a signed control-plane snapshot
        (issued while an earlier connection existed, per the architecture's
        own model) has been synced into EdgePolicyCache."""
        import asyncio
        from src.monkey_brain.kernel.edge.policy_cache import issue_policy_snapshot
        from src.monkey_brain.kernel.pipeline.action_executor import ActionExecutor
        from src.monkey_brain.kernel.society.integration import PlanetaryRuntime
        from src.monkey_brain.kernel.pipeline.execution import Action
        from src.monkey_brain.kernel.trusted_auth import (
            bind_trusted_auth,
            evidence_for_service,
        )

        pr = PlanetaryRuntime()
        assert pr._local_governance is not None, "edge_governance was not constructed for a disconnected edge node"

        principal = "spiffe://cognitiveos/edge-test-actor"
        bind_trusted_auth(evidence_for_service(principal))

        executor = ActionExecutor(
            capability_bus=None,  # simulated capability path — this test is about the GATE, not a real capability
            connectivity_check=pr._connectivity_check,
            edge_governance=pr._local_governance,
        )
        action = Action(action_id="a1", capability="PaymentCapability", parameters={})

        # No cached authority yet: REQUIRES_AUTHORITY must be refused, not
        # silently allowed just because a capability_bus is absent.
        result_before = asyncio.run(executor.execute((action,), context={}))
        assert result_before.actions[0].success is False, "REQUIRES_AUTHORITY ran with no cached local authority at all"

        # A signed, fresh, AUTO_APPROVE snapshot arrives (as if synced
        # earlier while connected) — store it in the SAME EdgePolicyCache
        # pr._local_governance consults.
        snapshot = issue_policy_snapshot(
            principal=principal,
            action="capability.PaymentCapability",
            resource="PaymentCapability",
            policy_decision={
                "allowed": True,
                "approval_mode": "AUTO_APPROVE",
                "policy_rule": "test-allow",
                "risk_level": "LOW",
            },
        )
        pr._edge_policy_cache.store_snapshot(snapshot)

        result_after = asyncio.run(executor.execute((action,), context={}))
        assert result_after.actions[0].success is True, (
            "cached, verified, fresh AUTO_APPROVE snapshot did not authorize a REQUIRES_AUTHORITY capability locally"
        )

    def test_ros_execution_works_offline(self, disconnected_edge_env):
        """commit -> ROS execution works, fully disconnected — no real ROS
        hardware exists in this environment (consistent with every other
        ROS test in this repo), so FakeRosExecutionAdapter stands in;
        the point is the governance boundary (ensure_governed) runs
        identically regardless of connectivity."""
        import asyncio
        from src.monkey_brain.kernel.edge.ros_integration import (
            FakeRosExecutionAdapter,
            run_ros_action_if_governed,
        )

        adapter = FakeRosExecutionAdapter()
        result = asyncio.run(
            run_ros_action_if_governed(
                capability="MoveArm",
                resource="MoveArm",
                parameters={"x": 1, "y": 2},
                adapter=adapter,
                local_policy_decision={
                    "allowed": True,
                    "approval_mode": "AUTO_APPROVE",
                    "reason": "test",
                    "policy_rule": "test-allow",
                    "risk_level": "LOW",
                },
            )
        )
        assert result.get("success") is True
        assert adapter.calls and adapter.calls[0]["capability"] == "MoveArm"
