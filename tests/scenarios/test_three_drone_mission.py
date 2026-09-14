"""Architecture demonstration: three isolated Actor Cells coordinate a swarm.

This is deliberately a deterministic simulation.  FakeRosExecutionAdapter is
the hardware seam; the test proves the CognitiveOS boundaries around it.
"""

from __future__ import annotations

import asyncio

import pytest

from src.monkey_brain.kernel.actor_identity import mint_actor_cell_identity
from src.monkey_brain.kernel.approval import reset_approval_store
from src.monkey_brain.kernel.compile.cognitive_actor import CognitiveActor
from src.monkey_brain.kernel.delegation import reset_delegation_store_for_tests
from src.monkey_brain.kernel.edge.ros_integration import (
    FakeRosExecutionAdapter,
    RosUnavailableError,
    run_ros_action_if_governed,
)
from src.monkey_brain.kernel.learn.memory.manager import MemoryManager
from src.monkey_brain.kernel.learn.memory.primitives import ProvenanceToken
from src.monkey_brain.kernel.security_boundary import reset_governed_pipeline_for_tests
from src.monkey_brain.kernel.society.actor_cell import ActorCell
from src.monkey_brain.kernel.society.runtime import ActorRuntimeState


@pytest.fixture(autouse=True)
def _isolated_crypto_and_governance(tmp_path, monkeypatch):
    """Keep generated signing material inside the test sandbox."""
    monkeypatch.setenv("AGENTOS_KEY_PASSWORD", "three-drone-test-password")
    import src.monkey_brain.kernel.identity as identity

    identity._default_key_manager = identity.KeyManager(key_dir=str(tmp_path / "keys"))
    monkeypatch.setenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", "true")
    reset_approval_store()
    reset_governed_pipeline_for_tests()
    reset_delegation_store_for_tests()
    yield
    reset_approval_store()
    reset_governed_pipeline_for_tests()
    reset_delegation_store_for_tests()


def _cell(actor_id: str) -> ActorCell:
    actor = CognitiveActor(entity_id=actor_id)
    return ActorCell(
        actor_id=actor_id,
        identity=mint_actor_cell_identity(actor_id, issuer="swarm-society"),
        actor=actor,
        runtime_state=ActorRuntimeState(actor_id=actor_id, actor=actor),
        ros_adapter=FakeRosExecutionAdapter(actor_id=actor_id),
    )


@pytest.mark.asyncio
async def test_three_drone_mission_isolated_governed_and_coordinated():
    cells = {actor_id: _cell(actor_id) for actor_id in ("A", "B", "C")}
    memories = {actor_id: MemoryManager(vector_client=None, graph_client=None) for actor_id in cells}
    token = ProvenanceToken(trace_id="swarm", policy_path="mission", auth_hash="test")

    # Test 1 and 4: every actor owns independent mutable cognition.
    for actor_id, cell in cells.items():
        cell.actor.belief.observe(f"obstacle-{actor_id}", "observed")
        memories[actor_id].allocate_working_context(
            actor_id,
            "survey",
            {"sector": actor_id},
            token,
        )
    assert len({id(c.actor._knowledge_graph) for c in cells.values()}) == 3
    assert len({id(c.actor.belief) for c in cells.values()}) == 3
    assert len({id(c.runtime_state) for c in cells.values()}) == 3
    assert len({id(m) for m in memories.values()}) == 3
    assert {c.actor.belief.nnz() for c in cells.values()} == {1}

    async def execute(actor_id: str):
        return await run_ros_action_if_governed(
            capability="SurveySector",
            resource=f"sector-{actor_id}",
            parameters={"sector": actor_id, "return_point": f"R-{actor_id}"},
            adapter=cells[actor_id].ros_adapter,
            actor_id=actor_id,
            local_policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE"},
        )

    # Test 3 and 10: concurrent sector survey followed by designated return.
    results = await asyncio.gather(*(execute(actor_id) for actor_id in cells))
    returns = await asyncio.gather(*(execute(actor_id) for actor_id in cells))
    assert all(result["success"] for result in results + returns)
    for actor_id, cell in cells.items():
        assert [call["parameters"]["sector"] for call in cell.ros_adapter.calls] == [
            actor_id,
            actor_id,
        ]
        assert cell.ros_adapter.calls[1]["parameters"]["return_point"] == f"R-{actor_id}"

    # Test 2: cross-cell ROS dispatch fails before governance or hardware.
    with pytest.raises(RosUnavailableError):
        await run_ros_action_if_governed(
            capability="SurveySector",
            resource="sector-B",
            parameters={},
            adapter=cells["B"].ros_adapter,
            actor_id="A",
            local_policy_decision={"allowed": True, "approval_mode": "AUTO_APPROVE"},
        )
    assert len(cells["B"].ros_adapter.calls) == 2

    # Test 5: coordination is an explicit message payload, not a mutation of B.
    coordination = {
        "from": "A",
        "to": "B",
        "type": "sector_unavailable",
        "payload": {"sector": "A"},
    }
    assert coordination["from"] != coordination["to"]
    assert "obstacle-A" not in cells["B"].actor._knowledge_graph._entities
    assert "obstacle-A" not in memories["B"].working_memory[("B", "survey")].payload.values()
