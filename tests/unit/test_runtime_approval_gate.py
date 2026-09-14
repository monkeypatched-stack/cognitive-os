"""Comprehensive tests for runtime approval gate with three approval modes.

Tests cover:
1. AUTO_APPROVE mode: policy-permitted operations bypass human review
2. HUMAN_APPROVAL_REQUIRED mode: operations blocked until human approves
3. DENY mode: operations always blocked
4. Expiration: approvals expire after TTL
5. Revocation: humans can revoke approvals
6. Scope validation: approvals are scoped to specific operations/resources
7. Self-approval prevention: agents cannot approve their own requests
8. Artifact immutability: approvals cannot be modified after creation
"""

import pytest
import time
from uuid import uuid4

from src.monkey_brain.kernel.approval import (
    ApprovalMode,
    ApprovalSource,
    ApprovalStatus,
    ApprovalArtifact,
    ApprovalArtifactStore,
    create_approval_artifact_from_policy,
    validate_approval_for_execution,
    prevent_self_approval,
    reset_approval_store,
    get_approval_store,
)


class TestApprovalArtifact:
    """Test ApprovalArtifact immutability and validation."""

    def test_artifact_is_frozen(self):
        """Verify ApprovalArtifact is immutable (frozen dataclass)."""
        artifact = ApprovalArtifact(
            approval_id="test-1",
            approval_mode=ApprovalMode.AUTO_APPROVE,
            requesting_principal="agent-1",
            target_operation="test.op",
        )

        # Attempt to modify should fail
        with pytest.raises(AttributeError):
            artifact.approval_mode = ApprovalMode.DENY

    def test_artifact_serialization(self):
        """Test artifact can be serialized and deserialized."""
        artifact = ApprovalArtifact(
            approval_id="test-1",
            operation_id="op-123",
            approval_mode=ApprovalMode.AUTO_APPROVE,
            approval_source=ApprovalSource.POLICY_AUTOMATIC,
            requesting_principal="agent-1",
            approving_principal="runtime:governance",
            target_operation="test.op",
            target_resource="resource-1",
            risk_level="LOW",
        )

        # Serialize and deserialize
        data = artifact.to_dict()
        restored = ApprovalArtifact.from_dict(data)

        assert restored.approval_id == artifact.approval_id
        assert restored.approval_mode == artifact.approval_mode
        assert restored.requesting_principal == artifact.requesting_principal

    def test_artifact_expiration_check(self):
        """Test is_valid() detects expired approvals."""
        now = time.time()

        # Non-expired approval
        artifact = ApprovalArtifact(
            approval_id="test-1",
            approval_mode=ApprovalMode.AUTO_APPROVE,
            expires_at=now + 3600,  # 1 hour in future
        )
        assert artifact.is_valid()

        # Expired approval
        expired = ApprovalArtifact(
            approval_id="test-2",
            approval_mode=ApprovalMode.AUTO_APPROVE,
            expires_at=now - 3600,  # 1 hour in past
        )
        assert not expired.is_valid()

    def test_artifact_scope_matching(self):
        """Test matches_scope() validates operation/resource scope."""
        artifact = ApprovalArtifact(
            approval_id="test-1",
            target_operation="test.read",
            target_resource="document-1",
        )

        # Matching scope
        assert artifact.matches_scope("test.read", "document-1")

        # Non-matching operation
        assert not artifact.matches_scope("test.write", "document-1")

        # Non-matching resource
        assert not artifact.matches_scope("test.read", "document-2")

        # Empty target_operation/resource means no scope constraint
        unrestricted = ApprovalArtifact(
            approval_id="test-2",
            target_operation="",
            target_resource="",
        )
        assert unrestricted.matches_scope("any.op", "any.resource")


class TestApprovalArtifactStore:
    """Test in-memory approval artifact store."""

    def test_create_and_retrieve(self):
        """Test creating and retrieving artifacts."""
        store = ApprovalArtifactStore()

        artifact = ApprovalArtifact(
            approval_id="test-1",
            operation_id="op-123",
            requesting_principal="agent-1",
        )

        stored = store.create(artifact)
        retrieved = store.get("test-1")

        assert retrieved is not None
        assert retrieved.approval_id == "test-1"
        assert retrieved.operation_id == "op-123"

    def test_duplicate_prevents_creation(self):
        """Test duplicate approval_id raises ValueError."""
        store = ApprovalArtifactStore()

        artifact1 = ApprovalArtifact(approval_id="test-1")
        store.create(artifact1)

        # Attempt to create duplicate
        artifact2 = ApprovalArtifact(approval_id="test-1")
        with pytest.raises(ValueError, match="already exists"):
            store.create(artifact2)

    def test_get_for_operation(self):
        """Test retrieving all approvals for an operation."""
        store = ApprovalArtifactStore()

        # Create multiple approvals for same operation
        for i in range(3):
            artifact = ApprovalArtifact(
                approval_id=f"test-{i}",
                operation_id="op-123",
            )
            store.create(artifact)

        # Create approval for different operation
        other = ApprovalArtifact(
            approval_id="other-1",
            operation_id="op-456",
        )
        store.create(other)

        # Query by operation
        results = store.get_for_operation("op-123")
        assert len(results) == 3

        results2 = store.get_for_operation("op-456")
        assert len(results2) == 1

    def test_revoke_approval(self):
        """Test revoking an approval."""
        store = ApprovalArtifactStore()

        artifact = ApprovalArtifact(approval_id="test-1")
        store.create(artifact)

        # Revoke
        success = store.revoke("test-1", "policy violation")
        assert success

        # Verify revoked
        revoked = store.get("test-1")
        assert revoked.approval_status == ApprovalStatus.REVOKED
        assert "policy violation" in revoked.revocation_reason
        assert not revoked.is_valid()

    def test_validate_approval(self):
        """Test approval validation."""
        store = ApprovalArtifactStore()

        artifact = ApprovalArtifact(
            approval_id="test-1",
            approval_status=ApprovalStatus.ACTIVE,
            expires_at=time.time() + 3600,
        )
        store.create(artifact)

        # Valid approval
        is_valid, reason = store.validate("test-1")
        assert is_valid

        # Non-existent approval
        is_valid, reason = store.validate("nonexistent")
        assert not is_valid
        assert "not found" in reason

        # Revoked approval
        store.revoke("test-1", "test")
        is_valid, reason = store.validate("test-1")
        assert not is_valid
        assert "revoked" in reason


class TestApprovalModes:
    """Test the three approval modes."""

    def test_auto_approve_creation(self):
        """Test AUTO_APPROVE mode artifact creation."""
        policy = {
            "approval_mode": "AUTO_APPROVE",
            "risk_level": "LOW",
            "policy_rule": "rule-1",
        }

        artifact = create_approval_artifact_from_policy(
            operation_id="op-1",
            action="test.op",
            resource="res-1",
            policy_decision=policy,
            requesting_principal="agent-1",
        )

        assert artifact.approval_mode == ApprovalMode.AUTO_APPROVE
        assert artifact.approval_source == ApprovalSource.POLICY_AUTOMATIC
        assert artifact.approving_principal == "runtime:governance"

    def test_human_approval_required_creation(self):
        """Test HUMAN_APPROVAL_REQUIRED mode artifact creation."""
        policy = {
            "approval_mode": "HUMAN_APPROVAL_REQUIRED",
            "risk_level": "HIGH",
            "policy_rule": "rule-2",
        }

        artifact = create_approval_artifact_from_policy(
            operation_id="op-2",
            action="test.op",
            resource="res-1",
            policy_decision=policy,
            requesting_principal="agent-1",
        )

        assert artifact.approval_mode == ApprovalMode.HUMAN_APPROVAL_REQUIRED
        assert artifact.approving_principal == ""  # Not yet approved
        assert artifact.expires_at > time.time()  # Has expiration

    def test_deny_mode_creation(self):
        """Test DENY mode artifact creation."""
        policy = {
            "approval_mode": "DENY",
            "risk_level": "CRITICAL",
            "policy_rule": "rule-3",
        }

        artifact = create_approval_artifact_from_policy(
            operation_id="op-3",
            action="test.op",
            resource="res-1",
            policy_decision=policy,
            requesting_principal="agent-1",
        )

        assert artifact.approval_mode == ApprovalMode.DENY

    def test_ttl_based_on_risk_level(self):
        """Test TTL is set based on risk level."""
        now = time.time()

        # LOW risk: 24 hours
        low_risk = create_approval_artifact_from_policy(
            operation_id="op-1",
            action="test",
            resource="res",
            policy_decision={"approval_mode": "AUTO_APPROVE", "risk_level": "LOW"},
            requesting_principal="agent-1",
        )
        assert low_risk.expires_at > now + 86400 - 10  # ~24 hours

        # HIGH risk: 6 hours
        high_risk = create_approval_artifact_from_policy(
            operation_id="op-2",
            action="test",
            resource="res",
            policy_decision={"approval_mode": "AUTO_APPROVE", "risk_level": "HIGH"},
            requesting_principal="agent-1",
        )
        assert high_risk.expires_at < low_risk.expires_at


class TestApprovalValidation:
    """Test approval validation before execution."""

    def test_auto_approve_always_valid(self):
        """Test AUTO_APPROVE mode always validates."""
        reset_approval_store()

        is_valid, reason = validate_approval_for_execution(
            operation_id="op-1",
            approval_mode="AUTO_APPROVE",
        )

        assert is_valid
        assert reason == ""

    def test_deny_never_valid(self):
        """Test DENY mode never validates."""
        reset_approval_store()

        is_valid, reason = validate_approval_for_execution(
            operation_id="op-1",
            approval_mode="DENY",
        )

        assert not is_valid
        assert "denied" in reason

    def test_human_approval_requires_artifact(self):
        """Test HUMAN_APPROVAL_REQUIRED requires valid approved artifact."""
        reset_approval_store()
        store = get_approval_store()

        # No artifact yet
        is_valid, reason = validate_approval_for_execution(
            operation_id="op-1",
            approval_mode="HUMAN_APPROVAL_REQUIRED",
        )
        assert not is_valid
        assert "no approval found" in reason

        # Create artifact but not approved (POLICY_AUTOMATIC)
        artifact = ApprovalArtifact(
            approval_id="test-1",
            operation_id="op-1",
            approval_mode=ApprovalMode.HUMAN_APPROVAL_REQUIRED,
            approval_source=ApprovalSource.POLICY_AUTOMATIC,
            requesting_principal="agent-1",
        )
        store.create(artifact)

        # Still not valid (not approved by human)
        is_valid, reason = validate_approval_for_execution(
            operation_id="op-1",
            approval_mode="HUMAN_APPROVAL_REQUIRED",
        )
        assert not is_valid
        assert "awaiting human approval" in reason

    def test_human_approved_artifact_validates(self):
        """Test human-approved artifact passes validation."""
        reset_approval_store()
        store = get_approval_store()

        # Create human-approved artifact
        artifact = ApprovalArtifact(
            approval_id="test-1",
            operation_id="op-1",
            approval_mode=ApprovalMode.AUTO_APPROVE,  # Upgraded to AUTO_APPROVE
            approval_source=ApprovalSource.HUMAN,  # Approved by human
            requesting_principal="agent-1",
            approving_principal="human-1",
        )
        store.create(artifact)

        # Should validate
        is_valid, reason = validate_approval_for_execution(
            operation_id="op-1",
            approval_mode="HUMAN_APPROVAL_REQUIRED",
        )
        assert is_valid

    def test_expired_approval_fails_validation(self):
        """Test expired approval fails validation."""
        reset_approval_store()
        store = get_approval_store()

        # Create human-approved but expired approval
        artifact = ApprovalArtifact(
            approval_id="test-1",
            operation_id="op-1",
            approval_mode=ApprovalMode.AUTO_APPROVE,
            approval_source=ApprovalSource.HUMAN,  # Must be HUMAN to pass approval_source check
            expires_at=time.time() - 3600,  # Expired 1 hour ago
            requesting_principal="agent-1",
            approving_principal="human-1",
        )
        store.create(artifact)

        is_valid, reason = validate_approval_for_execution(
            operation_id="op-1",
            approval_mode="HUMAN_APPROVAL_REQUIRED",
        )
        assert not is_valid
        assert "expired" in reason

    def test_scope_validation(self):
        """Test approval scope validation."""
        reset_approval_store()
        store = get_approval_store()

        # Create scoped approval
        artifact = ApprovalArtifact(
            approval_id="test-1",
            operation_id="op-1",
            approval_mode=ApprovalMode.AUTO_APPROVE,
            approval_source=ApprovalSource.HUMAN,
            target_operation="read.document",
            target_resource="doc-123",
            requesting_principal="agent-1",
            approving_principal="human-1",
        )
        store.create(artifact)

        # Matching scope
        is_valid, reason = validate_approval_for_execution(
            operation_id="op-1",
            approval_mode="HUMAN_APPROVAL_REQUIRED",
            action="read.document",
            resource="doc-123",
        )
        assert is_valid

        # Non-matching operation
        is_valid, reason = validate_approval_for_execution(
            operation_id="op-1",
            approval_mode="HUMAN_APPROVAL_REQUIRED",
            action="write.document",
            resource="doc-123",
        )
        assert not is_valid
        assert "scope" in reason


class TestSelfApprovalPrevention:
    """Test self-approval prevention."""

    def test_prevent_self_approval_same_principal(self):
        """Test prevent_self_approval rejects same principal."""
        is_valid, reason = prevent_self_approval("agent-1", "agent-1")
        assert not is_valid
        assert "self-approval" in reason

    def test_prevent_self_approval_different_principal(self):
        """Test prevent_self_approval allows different principal."""
        is_valid, reason = prevent_self_approval("agent-1", "human-1")
        assert is_valid
        assert reason == ""

    def test_prevent_self_approval_empty_principal(self):
        """Test prevent_self_approval allows empty principal."""
        is_valid, reason = prevent_self_approval("agent-1", "")
        assert is_valid

        is_valid, reason = prevent_self_approval("", "human-1")
        assert is_valid

    def test_validation_checks_self_approval(self):
        """Test validate_approval_for_execution checks self-approval."""
        reset_approval_store()
        store = get_approval_store()

        # Create self-approved artifact (agent approving own request)
        artifact = ApprovalArtifact(
            approval_id="test-1",
            operation_id="op-1",
            approval_mode=ApprovalMode.AUTO_APPROVE,
            approval_source=ApprovalSource.HUMAN,
            requesting_principal="agent-1",
            approving_principal="agent-1",  # SAME PRINCIPAL!
        )
        store.create(artifact)

        # Should fail validation due to self-approval
        is_valid, reason = validate_approval_for_execution(
            operation_id="op-1",
            approval_mode="HUMAN_APPROVAL_REQUIRED",
        )
        assert not is_valid
        assert "self-approval" in reason


class TestApprovalImmutability:
    """Test that approvals cannot be tampered with."""

    def test_artifact_cannot_be_modified_after_creation(self):
        """Test artifacts are immutable after creation."""
        artifact = ApprovalArtifact(
            approval_id="test-1",
            approval_mode=ApprovalMode.AUTO_APPROVE,
        )

        # Attempt to modify each field should fail
        with pytest.raises(AttributeError):
            artifact.approval_id = "modified"

        with pytest.raises(AttributeError):
            artifact.approval_mode = ApprovalMode.DENY

        with pytest.raises(AttributeError):
            artifact.requesting_principal = "hacker"

    def test_store_prevents_duplicate_ids(self):
        """Test store prevents duplicate approval IDs."""
        store = ApprovalArtifactStore()

        artifact = ApprovalArtifact(approval_id="test-1")
        store.create(artifact)

        # Cannot create another with same ID
        duplicate = ApprovalArtifact(approval_id="test-1")
        with pytest.raises(ValueError):
            store.create(duplicate)


class TestApprovalStoreSummary:
    """Test store summary statistics."""

    def test_summary_statistics(self):
        """Test store provides accurate summary."""
        store = ApprovalArtifactStore()

        # Create various approvals
        for i in range(3):
            artifact = ApprovalArtifact(
                approval_id=f"active-{i}",
                approval_mode=ApprovalMode.AUTO_APPROVE,
            )
            store.create(artifact)

        # Revoke one
        store.revoke("active-0", "test")

        summary = store.summary()
        assert summary["total_approvals"] == 3
        assert summary["active_approvals"] == 2
        assert summary["by_mode"]["AUTO_APPROVE"] == 3
