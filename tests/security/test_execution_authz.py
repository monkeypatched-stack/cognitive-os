"""Negative authorization tests for the execution-layer authz hook.

Covers GoalExecutor._authorize() — the defense-in-depth check that runs before
any goal executes (src/monkey_brain/kernel/plan/goals/executor.py). The hook is
inert unless AGENTOS_OPA_ENFORCE is enabled, and fail-closed when it is.

These are sync tests that drive the async staticmethod via asyncio.run, so they
don't depend on pytest-asyncio configuration.
"""

from __future__ import annotations

import asyncio
import sys
import types
from types import SimpleNamespace

import pytest

from src.monkey_brain.kernel.plan.goals.goal import GoalType
from src.monkey_brain.kernel.plan.goals.executor import GoalExecutor


def _install_fake_opa(monkeypatch, evaluate_fn):
    """Inject a stand-in services.common.opa so the hook's local

        from services.common.opa import evaluate

    resolves to our fake without pulling the real module's heavy transitive
    deps (keycloak/jwt/etc.). Restored automatically by monkeypatch teardown.
    """
    for name in ("services", "services.common", "services.common.opa"):
        if name not in sys.modules:
            monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    fake = types.ModuleType("services.common.opa")
    fake.evaluate = evaluate_fn
    monkeypatch.setitem(sys.modules, "services.common.opa", fake)


def _goal(name: str = "create_widget", goal_type: GoalType = GoalType.CREATE):
    # _authorize only reads goal.name and goal.goal_type.
    return SimpleNamespace(name=name, goal_type=goal_type)


def _ctx(user_id: str = "user-1"):
    # _authorize only reads context.user_id and context.run_id.
    return SimpleNamespace(user_id=user_id, run_id="run-1")


def _authorize(goal, ctx):
    return asyncio.run(GoalExecutor._authorize(goal, ctx))


def test_inert_when_flag_unset(monkeypatch):
    """With enforcement off (default), the hook permits (returns None)."""
    monkeypatch.delenv("AGENTOS_OPA_ENFORCE", raising=False)
    assert _authorize(_goal(), _ctx()) is None


def test_inert_when_flag_explicitly_false(monkeypatch):
    monkeypatch.setenv("AGENTOS_OPA_ENFORCE", "false")
    assert _authorize(_goal(), _ctx()) is None


def test_fail_closed_when_enabled_and_opa_denies(monkeypatch):
    """Enforcement on + OPA unconfigured/denying => execution is refused."""
    monkeypatch.setenv("AGENTOS_OPA_ENFORCE", "true")
    monkeypatch.delenv("OPA_URL", raising=False)
    result = _authorize(_goal(), _ctx())
    assert result is not None  # denied
    assert "denied" in result or "unavailable" in result or "failed" in result


def test_allows_when_policy_permits(monkeypatch):
    """Enforcement on + policy allows => permitted (returns None)."""

    async def _fake_evaluate(policy_path, input_data, *, default_allow=True):
        assert policy_path == "agentos/execute/allow"
        assert input_data["action"] == "create"
        assert input_data["resource"] == "create_widget"
        assert input_data["auth"]["principal"] == "user-1"
        assert input_data["principal"]["sub"] == "user-1"
        assert default_allow is False  # fail-closed under enforcement
        return True

    from src.monkey_brain.kernel.trusted_auth import (
        TrustedAuthEvidence,
        bind_trusted_auth,
    )

    bind_trusted_auth(
        TrustedAuthEvidence(
            authenticated=True,
            token_valid=True,
            principal_id="user-1",
            principal_type="human",
            mfa_status="satisfied",
        )
    )
    _install_fake_opa(monkeypatch, _fake_evaluate)
    monkeypatch.setenv("AGENTOS_OPA_ENFORCE", "true")
    assert _authorize(_goal(), _ctx()) is None


def test_denies_when_policy_refuses(monkeypatch):
    """Enforcement on + policy denies => refused with a policy reason."""

    async def _fake_evaluate(policy_path, input_data, *, default_allow=True):
        return False

    _install_fake_opa(monkeypatch, _fake_evaluate)
    monkeypatch.setenv("AGENTOS_OPA_ENFORCE", "true")
    result = _authorize(_goal(), _ctx())
    assert result == "authorization denied by policy"
