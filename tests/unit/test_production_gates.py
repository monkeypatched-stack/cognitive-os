"""Production gate and hardened execution path tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.monkey_brain.kernel.pipeline.execution import Action
from src.monkey_brain.kernel.pipeline.execution_runtime.integration import (
    IntegratedExecutionEngine,
)
from src.monkey_brain.kernel.production_gates import (
    block_direct_world_api_mutations,
    capability_dispatch_dedup_enabled,
    idempotency_fail_closed,
    insecure_dev_mode,
    production_mode_enabled,
    require_opa,
    require_redis,
    validate_production_gates,
)


class TestProductionGates:
    def test_secure_defaults_without_production_mode(self, monkeypatch):
        monkeypatch.delenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", raising=False)
        monkeypatch.delenv("COGNITIVEOS_PRODUCTION_MODE", raising=False)
        assert production_mode_enabled() is False
        assert require_redis() is True
        assert require_opa() is True
        assert idempotency_fail_closed() is True
        assert block_direct_world_api_mutations() is True
        assert capability_dispatch_dedup_enabled() is True

    def test_insecure_dev_relaxes_gates(self, monkeypatch):
        monkeypatch.delenv("COGNITIVEOS_PRODUCTION_MODE", raising=False)
        monkeypatch.setenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", "true")
        assert insecure_dev_mode() is True
        assert require_redis() is False
        assert require_opa() is False
        monkeypatch.setenv("ALLOW_DIRECT_WORLD_API", "true")
        assert block_direct_world_api_mutations() is False

    def test_validate_raises_without_redis(self, monkeypatch):
        monkeypatch.delenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", raising=False)
        with pytest.raises(RuntimeError, match="Redis"):
            validate_production_gates(redis_available=False, opa_configured=True)

    def test_validate_raises_without_opa(self, monkeypatch):
        monkeypatch.delenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", raising=False)
        with pytest.raises(RuntimeError, match="OPA"):
            validate_production_gates(redis_available=True, opa_configured=False)

    def test_auth_off_requires_insecure_dev(self, monkeypatch):
        monkeypatch.delenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", raising=False)
        monkeypatch.setenv("AGENTOS_AUTH_REQUIRED", "false")
        with pytest.raises(RuntimeError, match="AGENTOS_AUTH_REQUIRED"):
            validate_production_gates(redis_available=True, opa_configured=True)

    def test_allow_direct_world_api_ignored_in_production(self, monkeypatch):
        monkeypatch.setenv("COGNITIVEOS_PRODUCTION_MODE", "true")
        monkeypatch.delenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", raising=False)
        monkeypatch.setenv("ALLOW_DIRECT_WORLD_API", "true")
        assert block_direct_world_api_mutations() is True


class TestIntegratedExecutionEngineGraph:
    @pytest.mark.asyncio
    async def test_forwards_execution_graph_to_fallback(self):
        fallback = MagicMock()
        fallback.execute = AsyncMock(return_value=MagicMock(goal_achieved=True, actions=()))
        engine = IntegratedExecutionEngine(fallback=fallback)
        graph = object()
        actions = (Action(action_id="a1", capability="UnknownCap"),)

        await engine.execute(actions, {}, execution_graph=graph)

        fallback.execute.assert_awaited_once_with(actions, {}, execution_graph=graph)
