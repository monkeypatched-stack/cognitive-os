"""End-to-end tests for approval gate in execution pipeline.

Tests the three main flows:
1. AUTO_APPROVE: Operations execute immediately
2. HUMAN_APPROVAL_REQUIRED: Operations queue for human approval
3. DENY: Operations are blocked immediately
"""

import asyncio
import time
from unittest.mock import patch

import pytest

from src.monkey_brain.kernel.approval import (
    ApprovalArtifact,
    ApprovalMode,
    ApprovalSource,
    ApprovalStatus,
    execute_with_approval,
    get_approval_store,
)
from src.monkey_brain.kernel.security_boundary import (
    HumanApprovalRequired,
    SecurityBoundaryDenied,
    run_governed_mutation,
)
from src.monkey_brain.kernel.security_operation import (
    SecurityOperationState,
    get_operation_ledger,
    new_operation_id,
    reset_operation_ledger_for_tests,
)
from src.monkey_brain.kernel.trusted_auth import TrustedAuthEvidence


def make_trusted_auth(principal_id: str, principal_type: str = "human"):
    """Create a TrustedAuthEvidence for testing."""
    return TrustedAuthEvidence(
        authenticated=True,
        token_valid=True,
        principal_id=principal_id,
        principal_type=principal_type,
        mfa_status="satisfied",
    )


class TestAutoApproveFlow:
    """AUTO_APPROVE operations execute immediately."""

    def setup_method(self):
        """Reset stores before each test."""
        from src.monkey_brain.kernel.approval import reset_approval_store

        reset_approval_store()
        reset_operation_ledger_for_tests()

    @pytest.mark.asyncio
    async def test_auto_approve_executes_immediately(self):
        """AUTO_APPROVE operations should execute without waiting."""
        mutation_called = False
        operation_id = new_operation_id()

        async def mutate():
            nonlocal mutation_called
            mutation_called = True
            return {"result": "success"}

        async def mock_authorize(action, resource, extra, *, verified_delegation=None):
            return {
                "allowed": True,
                "reason": "policy_permit",
                "approval_mode": "AUTO_APPROVE",
                "approval_source": "POLICY_AUTOMATIC",
                "risk_level": "LOW",
                "policy_rule": "default_allow",
                "requires_hitl": False,
            }

        with patch(
            "src.monkey_brain.kernel.security_boundary._authorize",
            side_effect=mock_authorize,
        ):
            with patch("src.monkey_brain.kernel.trusted_auth.get_trusted_auth") as mock_auth:
                mock_auth.return_value = make_trusted_auth("user:test")

                result = await run_governed_mutation(
                    action="mutation.execute",
                    resource="test_resource",
                    mutate=mutate,
                    operation_id=operation_id,
                )

        assert mutation_called, "Mutation should have been called"
        assert result == {"result": "success"}

    @pytest.mark.asyncio
    async def test_auto_approve_creates_artifact(self):
        """AUTO_APPROVE operations should create approval artifact."""
        operation_id = new_operation_id()

        async def mutate():
            return "done"

        async def mock_authorize(action, resource, extra, *, verified_delegation=None):
            return {
                "allowed": True,
                "reason": "policy_permit",
                "approval_mode": "AUTO_APPROVE",
                "approval_source": "POLICY_AUTOMATIC",
                "risk_level": "LOW",
                "policy_rule": "default_allow",
                "requires_hitl": False,
            }

        with patch(
            "src.monkey_brain.kernel.security_boundary._authorize",
            side_effect=mock_authorize,
        ):
            with patch("src.monkey_brain.kernel.trusted_auth.get_trusted_auth") as mock_auth:
                mock_auth.return_value = make_trusted_auth("user:test")

                await run_governed_mutation(
                    action="mutation.create",
                    resource="document",
                    mutate=mutate,
                    operation_id=operation_id,
                )

        store = get_approval_store()
        artifacts = store.get_for_operation(operation_id)

        assert len(artifacts) == 1
        artifact = artifacts[0]
        assert artifact.approval_mode == ApprovalMode.AUTO_APPROVE
        assert artifact.approval_source == ApprovalSource.POLICY_AUTOMATIC


class TestHumanApprovalRequiredFlow:
    """HUMAN_APPROVAL_REQUIRED operations queue for approval."""

    def setup_method(self):
        """Reset stores before each test."""
        from src.monkey_brain.kernel.approval import reset_approval_store

        reset_approval_store()
        reset_operation_ledger_for_tests()

    @pytest.mark.asyncio
    async def test_human_approval_required_raises_exception(self, monkeypatch):
        """HUMAN_APPROVAL_REQUIRED should raise HumanApprovalRequired."""
        # OPA_REQUIRED=true forces require_opa() to honor OPA even under
        # insecure-dev (production_gates.py) -- without it, _authorize()
        # below is never even called (a documented, separate insecure-dev
        # relaxation), so mocking it would silently prove nothing.
        monkeypatch.setenv("OPA_REQUIRED", "true")
        operation_id = new_operation_id()

        async def mutate():
            return "should not execute"

        # Patch the _authorize method directly in the module
        async def mock_authorize(action, resource, extra, *, verified_delegation=None):
            return {
                "allowed": True,
                "reason": "policy_requires_human_approval",
                "approval_mode": "HUMAN_APPROVAL_REQUIRED",
                "approval_source": "POLICY_AUTOMATIC",
                "risk_level": "MEDIUM",
                "policy_rule": "sensitive_operation",
                "requires_hitl": True,
            }

        with patch("src.monkey_brain.kernel.security_boundary._authorize", new=mock_authorize):
            with patch("src.monkey_brain.kernel.trusted_auth.get_trusted_auth") as mock_auth:
                mock_auth.return_value = make_trusted_auth("agent:processor", "service")

                with pytest.raises(HumanApprovalRequired) as exc_info:
                    await run_governed_mutation(
                        action="mutation.delete",
                        resource="sensitive_data",
                        mutate=mutate,
                        operation_id=operation_id,
                    )

        assert exc_info.value.operation_id == operation_id
        assert exc_info.value.approval_id is not None

    @pytest.mark.asyncio
    async def test_human_approval_required_creates_artifact(self, monkeypatch):
        """HUMAN_APPROVAL_REQUIRED should create approval artifact."""
        monkeypatch.setenv("OPA_REQUIRED", "true")
        operation_id = new_operation_id()

        async def mutate():
            return "should not execute"

        async def mock_authorize(action, resource, extra, *, verified_delegation=None):
            return {
                "allowed": True,
                "reason": "policy_requires_human_approval",
                "approval_mode": "HUMAN_APPROVAL_REQUIRED",
                "approval_source": "POLICY_AUTOMATIC",
                "risk_level": "MEDIUM",
                "policy_rule": "sensitive_operation",
                "requires_hitl": True,
            }

        with patch("src.monkey_brain.kernel.security_boundary._authorize", new=mock_authorize):
            with patch("src.monkey_brain.kernel.trusted_auth.get_trusted_auth") as mock_auth:
                mock_auth.return_value = make_trusted_auth("agent:test", "service")

                try:
                    await run_governed_mutation(
                        action="mutation.transfer",
                        resource="account:123",
                        mutate=mutate,
                        operation_id=operation_id,
                    )
                except HumanApprovalRequired:
                    pass

        store = get_approval_store()
        artifacts = store.get_for_operation(operation_id)

        assert len(artifacts) == 1
        artifact = artifacts[0]
        assert artifact.approval_mode == ApprovalMode.HUMAN_APPROVAL_REQUIRED
        assert artifact.approval_source == ApprovalSource.POLICY_AUTOMATIC

    @pytest.mark.asyncio
    async def test_human_approval_required_queues_operation(self, monkeypatch):
        """HUMAN_APPROVAL_REQUIRED should transition to AWAITING_APPROVAL."""
        monkeypatch.setenv("OPA_REQUIRED", "true")
        operation_id = new_operation_id()

        async def mutate():
            return "should not execute"

        async def mock_authorize(action, resource, extra, *, verified_delegation=None):
            return {
                "allowed": True,
                "reason": "policy_requires_human_approval",
                "approval_mode": "HUMAN_APPROVAL_REQUIRED",
                "approval_source": "POLICY_AUTOMATIC",
                "risk_level": "HIGH",
                "policy_rule": "critical_operation",
                "requires_hitl": True,
            }

        with patch("src.monkey_brain.kernel.security_boundary._authorize", new=mock_authorize):
            with patch("src.monkey_brain.kernel.trusted_auth.get_trusted_auth") as mock_auth:
                mock_auth.return_value = make_trusted_auth("user:admin")

                try:
                    await run_governed_mutation(
                        action="mutation.admin",
                        resource="system",
                        mutate=mutate,
                        operation_id=operation_id,
                    )
                except HumanApprovalRequired:
                    pass

        ledger = get_operation_ledger()
        op = ledger.get(operation_id)

        assert op is not None
        assert op.state == SecurityOperationState.AWAITING_APPROVAL


class TestDenyFlow:
    """DENY operations are blocked immediately."""

    def setup_method(self):
        """Reset stores before each test."""
        from src.monkey_brain.kernel.approval import reset_approval_store

        reset_approval_store()
        reset_operation_ledger_for_tests()

    @pytest.mark.asyncio
    async def test_deny_raises_security_boundary_denied(self, monkeypatch):
        """DENY should raise SecurityBoundaryDenied."""
        monkeypatch.setenv("OPA_REQUIRED", "true")
        operation_id = new_operation_id()
        mutation_called = False

        async def mutate():
            nonlocal mutation_called
            mutation_called = True
            return "should not execute"

        async def mock_authorize(action, resource, extra, *, verified_delegation=None):
            return {
                "allowed": False,
                "reason": "policy_deny",
                "approval_mode": "DENY",
                "approval_source": "POLICY_AUTOMATIC",
                "risk_level": "CRITICAL",
                "policy_rule": "forbidden_action",
                "requires_hitl": False,
            }

        with patch("src.monkey_brain.kernel.security_boundary._authorize", new=mock_authorize):
            with patch("src.monkey_brain.kernel.trusted_auth.get_trusted_auth") as mock_auth:
                mock_auth.return_value = make_trusted_auth("user:test")

                with pytest.raises(SecurityBoundaryDenied) as exc_info:
                    await run_governed_mutation(
                        action="mutation.forbidden",
                        resource="protected_resource",
                        mutate=mutate,
                        operation_id=operation_id,
                    )

        assert not mutation_called, "Mutation should not execute"
        assert "denied by policy" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_deny_creates_artifact_for_audit(self, monkeypatch):
        """DENY should still create artifact for audit trail."""
        monkeypatch.setenv("OPA_REQUIRED", "true")
        operation_id = new_operation_id()

        async def mutate():
            return "should not execute"

        async def mock_authorize(action, resource, extra, *, verified_delegation=None):
            return {
                "allowed": False,
                "reason": "insufficient_permissions",
                "approval_mode": "DENY",
                "approval_source": "POLICY_AUTOMATIC",
                "risk_level": "CRITICAL",
                "policy_rule": "unauthorized_action",
                "requires_hitl": False,
            }

        with patch("src.monkey_brain.kernel.security_boundary._authorize", new=mock_authorize):
            with patch("src.monkey_brain.kernel.trusted_auth.get_trusted_auth") as mock_auth:
                mock_auth.return_value = make_trusted_auth("user:unprivileged")

                try:
                    await run_governed_mutation(
                        action="mutation.admin",
                        resource="system_config",
                        mutate=mutate,
                        operation_id=operation_id,
                    )
                except SecurityBoundaryDenied:
                    pass

        store = get_approval_store()
        artifacts = store.get_for_operation(operation_id)

        assert len(artifacts) == 1
        assert artifacts[0].approval_mode == ApprovalMode.DENY


class TestApprovalExecution:
    """Test executing operations with pre-approved artifacts."""

    def setup_method(self):
        """Reset stores before each test."""
        from src.monkey_brain.kernel.approval import reset_approval_store

        reset_approval_store()
        reset_operation_ledger_for_tests()

    @pytest.mark.asyncio
    async def test_execute_with_approval_validates_scope(self):
        """execute_with_approval() should validate artifact scope."""
        operation_id = new_operation_id()
        approval_id = f"appr_test_{int(time.time())}"

        artifact = ApprovalArtifact(
            approval_id=approval_id,
            operation_id=operation_id,
            approval_mode=ApprovalMode.HUMAN_APPROVAL_REQUIRED,
            approval_source=ApprovalSource.HUMAN,
            approval_status=ApprovalStatus.ACTIVE,
            requesting_principal="agent:test",
            approving_principal="user:admin",
            target_operation="mutation.transfer",
            target_resource="account:123",
            expires_at=time.time() + 3600,
        )

        store = get_approval_store()
        store.create(artifact)

        async def mutate():
            return "should not execute"

        with pytest.raises(SecurityBoundaryDenied) as exc_info:
            await execute_with_approval(
                operation_id=operation_id,
                approval_id=approval_id,
                action="mutation.transfer",
                resource="account:999",  # Different account!
                mutate=mutate,
            )

        assert "scope does not match" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_execute_with_approval_rejects_expired(self):
        """execute_with_approval() should reject expired artifacts."""
        operation_id = new_operation_id()
        approval_id = f"appr_test_{int(time.time())}"

        artifact = ApprovalArtifact(
            approval_id=approval_id,
            operation_id=operation_id,
            approval_mode=ApprovalMode.HUMAN_APPROVAL_REQUIRED,
            approval_source=ApprovalSource.HUMAN,
            approval_status=ApprovalStatus.ACTIVE,
            requesting_principal="agent:test",
            approving_principal="user:admin",
            target_operation="mutation.transfer",
            target_resource="account:123",
            expires_at=time.time() - 3600,  # Expired!
        )

        store = get_approval_store()
        store.create(artifact)

        async def mutate():
            return "should not execute"

        with pytest.raises(SecurityBoundaryDenied) as exc_info:
            await execute_with_approval(
                operation_id=operation_id,
                approval_id=approval_id,
                action="mutation.transfer",
                resource="account:123",
                mutate=mutate,
            )

        assert "invalid, expired, or revoked" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_execute_with_approval_executes_valid_artifact(self):
        """execute_with_approval() should execute with valid artifact."""
        operation_id = new_operation_id()
        approval_id = f"appr_test_{int(time.time())}"

        artifact = ApprovalArtifact(
            approval_id=approval_id,
            operation_id=operation_id,
            approval_mode=ApprovalMode.HUMAN_APPROVAL_REQUIRED,
            approval_source=ApprovalSource.HUMAN,
            approval_status=ApprovalStatus.ACTIVE,
            requesting_principal="agent:test",
            approving_principal="user:admin",
            target_operation="mutation.transfer",
            target_resource="account:123",
            expires_at=time.time() + 3600,
        )

        store = get_approval_store()
        store.create(artifact)

        mutation_called = False

        async def mutate():
            nonlocal mutation_called
            mutation_called = True
            return {"result": "success"}

        result = await execute_with_approval(
            operation_id=operation_id,
            approval_id=approval_id,
            action="mutation.transfer",
            resource="account:123",
            mutate=mutate,
        )

        assert mutation_called, "Mutation should have been called"
        assert result == {"result": "success"}
