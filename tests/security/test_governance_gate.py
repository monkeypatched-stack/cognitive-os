"""Governance evaluates OPA fail-closed unless explicit insecure-dev mode."""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.governance import GovernanceEngine


@pytest.mark.asyncio
async def test_unconfigured_governance_denies_by_default(monkeypatch):
    monkeypatch.delenv("OPA_URL", raising=False)
    monkeypatch.delenv("COGNITIVEOS_ALLOW_INSECURE_DEV_MODE", raising=False)
    eng = GovernanceEngine()
    assert eng.is_configured() is False
    d = await eng.evaluate("any-user", "plan", {})
    assert d["allowed"] is False
    assert d["reason"] == "opa_required_but_not_configured"


@pytest.mark.asyncio
async def test_opa_denial_is_surfaced_as_a_real_governance_decision(monkeypatch):
    monkeypatch.setenv("OPA_URL", "http://opa.internal:8181")

    async def fake_evaluate_full(policy_path, input_data, *, default_allow=False, **kwargs):
        assert policy_path == "agentos/governance"
        assert input_data["runtime_id"] == "mallory"
        assert input_data["action"] == "execute"
        return {
            "allowed": False,
            "obligations": [],
            "reason": "runtime_blocked",
            "source": "opa",
        }

    monkeypatch.setattr("services.common.opa.evaluate_full", fake_evaluate_full)

    eng = GovernanceEngine()
    denied = await eng.evaluate("mallory", "execute", {})
    assert denied["allowed"] is False
    assert denied["reason"] == "runtime_blocked"
    assert denied["violations"] == [{"rule": "runtime_blocked", "type": "opa"}]


@pytest.mark.asyncio
async def test_opa_allow_is_surfaced_as_a_real_governance_decision(monkeypatch):
    monkeypatch.setenv("OPA_URL", "http://opa.internal:8181")

    async def fake_evaluate_full(policy_path, input_data, *, default_allow=False, **kwargs):
        return {"allowed": True, "obligations": [], "reason": "", "source": "opa"}

    monkeypatch.setattr("services.common.opa.evaluate_full", fake_evaluate_full)

    eng = GovernanceEngine()
    allowed = await eng.evaluate("alice", "plan", {})
    assert allowed["allowed"] is True
    assert allowed["reason"] == ""
    assert allowed["violations"] == []


def test_audit_decisions_records_both_allow_and_deny(monkeypatch):
    import asyncio

    monkeypatch.setenv("OPA_URL", "http://opa.internal:8181")

    async def fake_evaluate_full(policy_path, input_data, *, default_allow=False, **kwargs):
        return {
            "allowed": input_data["runtime_id"] != "mallory",
            "obligations": [],
            "reason": ("" if input_data["runtime_id"] != "mallory" else "runtime_blocked"),
            "source": "opa",
        }

    monkeypatch.setattr("services.common.opa.evaluate_full", fake_evaluate_full)

    eng = GovernanceEngine()
    asyncio.run(eng.evaluate("alice", "plan", {}))
    asyncio.run(eng.evaluate("mallory", "execute", {}))

    decisions = eng.audit_decisions()
    assert len(decisions) == 2
    assert decisions[0]["allowed"] is True
    assert decisions[1]["allowed"] is False
    assert decisions[1]["reason"] == "runtime_blocked"
