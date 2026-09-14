"""Live-path wiring: the out-of-core world (#4) and enforced agent timeout (#3).

These verify the standalone primitives are actually invoked by the running system —
world_tensor uses ShardedWorldStore when AGENTOS_WORLD_SHARD_DIR is set, and the agent
mesh cancels a runaway async task at the sandbox limit.
"""

from __future__ import annotations

import asyncio

from src.monkey_brain.kernel.execute.agent_mesh import Agent, AgentSpec, AgentRole

# ── #4: sharded world backs world_tensor ─────────────────────────────────────────


def test_world_tensor_uses_sharded_store(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOS_WORLD_SHARD_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTOS_WORLD_MAX_RESIDENT", "2")
    from src.monkey_brain.kernel.compile import world_tensor as wt

    wt.reset_for_test()
    try:
        tw = wt.get_tenant_world()
        for name in ("acme", "globex", "initech", "umbrella"):
            tw.observe(name, "A", "B", domain="d")
        s = tw.summary()
        assert s["tenants"] == 4 and s["resident"] == 2 and s["evictions"] >= 2
        # cold reload recovers an evicted tenant's data from its shard
        wt.reset_for_test()
        tw2 = wt.get_tenant_world()
        assert ("A", "B") in set(tw2.view("acme"))
    finally:
        wt.reset_for_test()


# ── #3: agent execution enforces the sandbox timeout ─────────────────────────────


class _SlowAgent(Agent):
    async def _execute_task(self, task):
        await asyncio.sleep(30)  # runaway — must be cancelled
        return {"done": True}


def test_agent_execution_timeout_enforced():
    spec = AgentSpec(role=AgentRole.WORKER, agent_type="untrusted")
    agent = _SlowAgent("a1", spec, knowledge_pack=None)
    agent._get_sandbox()._limits.timeout_seconds = 0.3  # shrink for the test
    res = asyncio.run(agent.execute({"type": "x"}))
    assert res["success"] is False
    assert res["error"] == "timeout_exceeded"
    assert "timeout_exceeded" in res.get("sandbox_violations", [])
