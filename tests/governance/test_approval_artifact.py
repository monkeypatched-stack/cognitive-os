"""Test matrix for the ApprovalArtifact governance schema, store, and
canonical validator (Section 26 of the approval-artifact spec).

These are DEV-PROCESS-TOOLING tests, not CognitiveOS security tests —
they live in tests/governance/, not tests/security/, deliberately, so a
reader never mistakes this package for a product security boundary.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from governance.approval_artifact import (
    ApprovalArtifact,
    ApprovalArtifactError,
    ApprovalDecision,
    ApprovalScope,
    ApprovalStatus,
    DiscoveryHandoff,
    InvalidApprovalTransition,
    create_artifact,
)
from governance.store import (
    ApprovalIntegrityError,
    ApprovalPersistenceError,
    ApprovalRecordStore,
)
from governance.validator import validate_approval

NOW = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)


def _handoff(**overrides) -> DiscoveryHandoff:
    defaults = dict(
        handoff_id="DH-test-001",
        repository_revision="deadbeef" * 5,
        scope=ApprovalScope(
            task="canonicalize execution-attempt state",
            files=("src/monkey_brain/kernel/execution_attempt.py",),
            behaviors=("shared isinstance type-guard utility",),
            security_boundaries=(),
        ),
    )
    defaults.update(overrides)
    return DiscoveryHandoff(**defaults)


def _artifact(handoff: DiscoveryHandoff | None = None, **overrides) -> ApprovalArtifact:
    handoff = handoff or _handoff()
    defaults = dict(
        handoff=handoff,
        approved_by="prashun",
        decision=ApprovalDecision.APPROVED,
        approval_id="APR-test-001",
        approved_at=NOW,
        lifetime=timedelta(hours=24),
    )
    defaults.update(overrides)
    return create_artifact(**defaults)


# ── Schema ────────────────────────────────────────────────────────────────


class TestSchema:
    def test_missing_approval_id_rejected(self):
        with pytest.raises(ApprovalArtifactError):
            _artifact(approval_id="")

    def test_missing_handoff_id_rejected(self):
        with pytest.raises(ApprovalArtifactError):
            _handoff(handoff_id="")

    def test_missing_approved_by_rejected(self):
        with pytest.raises(ApprovalArtifactError):
            _artifact(approved_by="")

    def test_missing_repository_revision_rejected(self):
        with pytest.raises(ApprovalArtifactError):
            _handoff(repository_revision="")

    def test_expires_at_not_after_approved_at_rejected(self):
        with pytest.raises(ApprovalArtifactError):
            _artifact(lifetime=timedelta(seconds=0))
        with pytest.raises(ApprovalArtifactError):
            _artifact(lifetime=timedelta(seconds=-1))

    def test_naive_datetime_rejected(self):
        with pytest.raises(ApprovalArtifactError):
            _artifact(approved_at=datetime(2026, 9, 5, 12, 0, 0))  # no tzinfo

    def test_missing_scope_field_from_dict_rejected(self):
        with pytest.raises(ApprovalArtifactError):
            ApprovalScope.from_dict({"files": ["x"]})  # no "task"

    def test_invalid_decision_rejected(self):
        with pytest.raises(ValueError):
            ApprovalDecision("maybe")

    def test_invalid_status_rejected(self):
        with pytest.raises(ValueError):
            ApprovalStatus("pending_review")

    def test_from_dict_missing_field_rejected(self):
        good = _artifact().to_dict()
        del good["approved_by"]
        with pytest.raises(ApprovalArtifactError):
            ApprovalArtifact.from_dict(good)

    def test_unsupported_artifact_version_rejected(self):
        good = _artifact().to_dict()
        good["artifact_version"] = 999
        with pytest.raises(ApprovalArtifactError):
            ApprovalArtifact.from_dict(good)


# ── Expiration ────────────────────────────────────────────────────────────


class TestExpiration:
    def test_now_before_expiry_is_valid_window(self):
        artifact = _artifact()
        assert artifact.approved_at <= NOW + timedelta(hours=1) < artifact.expires_at

    def test_now_equal_expires_at_is_expired(self, tmp_path):
        artifact = _artifact()
        store = ApprovalRecordStore(tmp_path)
        store.create(artifact, initial_status=ApprovalStatus.APPROVED)
        result = validate_approval(
            artifact.approval_id,
            store=store,
            handoff=_handoff(),
            current_revision=artifact.repository_revision,
            now=artifact.expires_at,
            requested_files=("src/monkey_brain/kernel/execution_attempt.py",),
            requested_behaviors=("shared isinstance type-guard utility",),
        )
        assert result.within_validity_window is False
        assert result.authorized is False

    def test_now_after_expiry_is_expired(self, tmp_path):
        artifact = _artifact()
        store = ApprovalRecordStore(tmp_path)
        store.create(artifact, initial_status=ApprovalStatus.APPROVED)
        result = validate_approval(
            artifact.approval_id,
            store=store,
            handoff=_handoff(),
            current_revision=artifact.repository_revision,
            now=artifact.expires_at + timedelta(hours=1),
        )
        assert result.within_validity_window is False
        assert result.authorized is False

    def test_now_before_approved_at_is_invalid(self, tmp_path):
        artifact = _artifact()
        store = ApprovalRecordStore(tmp_path)
        store.create(artifact, initial_status=ApprovalStatus.APPROVED)
        result = validate_approval(
            artifact.approval_id,
            store=store,
            handoff=_handoff(),
            current_revision=artifact.repository_revision,
            now=artifact.approved_at - timedelta(seconds=1),
        )
        assert result.within_validity_window is False
        assert result.authorized is False


# ── Binding ───────────────────────────────────────────────────────────────


class TestBinding:
    def test_wrong_handoff_rejected(self, tmp_path):
        artifact = _artifact()
        store = ApprovalRecordStore(tmp_path)
        store.create(artifact, initial_status=ApprovalStatus.APPROVED)
        wrong_handoff = _handoff(handoff_id="DH-different-999")
        result = validate_approval(
            artifact.approval_id,
            store=store,
            handoff=wrong_handoff,
            current_revision=artifact.repository_revision,
            now=NOW,
        )
        assert result.handoff_matches is False
        assert result.authorized is False

    def test_wrong_repository_revision_rejected(self, tmp_path):
        artifact = _artifact()
        store = ApprovalRecordStore(tmp_path)
        store.create(artifact, initial_status=ApprovalStatus.APPROVED)
        result = validate_approval(
            artifact.approval_id,
            store=store,
            handoff=_handoff(),
            current_revision="a" * 40,
            now=NOW,
        )
        assert result.revision_matches is False
        assert result.authorized is False

    def test_wrong_scope_rejected(self, tmp_path):
        artifact = _artifact()
        store = ApprovalRecordStore(tmp_path)
        store.create(artifact, initial_status=ApprovalStatus.APPROVED)
        result = validate_approval(
            artifact.approval_id,
            store=store,
            handoff=_handoff(),
            current_revision=artifact.repository_revision,
            now=NOW,
            requested_files=("src/monkey_brain/kernel/security_operation.py",),
        )
        assert result.scope_covers_request is False
        assert result.authorized is False
        assert any("security_operation.py" in r for r in result.reasons)


# ── Immutability ──────────────────────────────────────────────────────────


class TestImmutability:
    def test_scope_mutation_rejected(self):
        artifact = _artifact()
        with pytest.raises(dataclasses.FrozenInstanceError):
            artifact.scope = ApprovalScope(task="different")

    def test_expiration_mutation_rejected(self):
        artifact = _artifact()
        with pytest.raises(dataclasses.FrozenInstanceError):
            artifact.expires_at = artifact.expires_at + timedelta(days=365)

    def test_revision_mutation_rejected(self):
        artifact = _artifact()
        with pytest.raises(dataclasses.FrozenInstanceError):
            artifact.repository_revision = "b" * 40

    def test_approver_mutation_rejected(self):
        artifact = _artifact()
        with pytest.raises(dataclasses.FrozenInstanceError):
            artifact.approved_by = "someone-else"

    def test_scope_itself_is_frozen(self):
        scope = _handoff().scope
        with pytest.raises(dataclasses.FrozenInstanceError):
            scope.task = "different"


# ── Identity ──────────────────────────────────────────────────────────────


class TestIdentity:
    @pytest.mark.parametrize("bad_name", ["agent", "LLM", "Claude", "system", "anonymous", ""])
    def test_disallowed_approver_identity_flagged(self, tmp_path, bad_name):
        if bad_name == "":
            with pytest.raises(ApprovalArtifactError):
                _artifact(approved_by=bad_name)
            return
        artifact = _artifact(approved_by=bad_name)
        assert artifact.approver_identity_is_disallowed is True
        store = ApprovalRecordStore(tmp_path)
        store.create(artifact, initial_status=ApprovalStatus.APPROVED)
        result = validate_approval(
            artifact.approval_id,
            store=store,
            handoff=_handoff(),
            current_revision=artifact.repository_revision,
            now=NOW,
        )
        assert result.identity_plausible is False
        assert result.authorized is False

    def test_plausible_human_name_not_flagged(self):
        artifact = _artifact(approved_by="prashun")
        assert artifact.approver_identity_is_disallowed is False

    def test_approver_identity_is_not_authentication(self, tmp_path):
        """Gap 1 regression: 'plausible' must never be conflated with
        'authenticated'. A name passing the blocklist heuristic is
        evidence of nothing more than 'not an obvious placeholder' —
        this test fails if a future change starts treating it as identity
        verification."""
        import dataclasses

        from governance.approval_artifact import (
            AUTHENTICATED_APPROVER_AVAILABLE,
            AUTHENTICATED_APPROVER_UNAVAILABLE,
        )

        # The environment-capability flag must be explicit and False —
        # never silently flipped to True by adding a blocklist entry or
        # tightening the heuristic.
        assert AUTHENTICATED_APPROVER_AVAILABLE is False
        assert AUTHENTICATED_APPROVER_UNAVAILABLE == "AUTHENTICATED_APPROVER_UNAVAILABLE"

        artifact = _artifact(approved_by="alice")
        assert artifact.approver_identity_is_disallowed is False  # "plausible"

        store = ApprovalRecordStore(tmp_path)
        store.create(artifact, initial_status=ApprovalStatus.APPROVED)
        result = validate_approval(
            artifact.approval_id,
            store=store,
            handoff=_handoff(),
            current_revision=artifact.repository_revision,
            now=NOW,
        )
        assert result.identity_plausible is True

        # No field anywhere on the artifact or the validation result may
        # claim authentication. If one is ever added, this test must be
        # updated deliberately — it must never pass silently by accident.
        artifact_fields = {f.name for f in dataclasses.fields(artifact)}
        result_fields = {f.name for f in dataclasses.fields(result)}
        forbidden = {
            "identity_authenticated",
            "approved_by_authenticated",
            "authenticated_by",
        }
        assert not (artifact_fields & forbidden)
        assert not (result_fields & forbidden)
        assert "identity_plausible" in result_fields
        assert "identity_authenticated" not in result_fields


# ── Scope (in-scope allowed, out-of-scope blocked) ───────────────────────


class TestScopeCheck:
    def test_in_scope_change_allowed(self, tmp_path):
        artifact = _artifact()
        store = ApprovalRecordStore(tmp_path)
        store.create(artifact, initial_status=ApprovalStatus.APPROVED)
        result = validate_approval(
            artifact.approval_id,
            store=store,
            handoff=_handoff(),
            current_revision=artifact.repository_revision,
            now=NOW,
            requested_files=("src/monkey_brain/kernel/execution_attempt.py",),
            requested_behaviors=("shared isinstance type-guard utility",),
        )
        assert result.authorized is True

    def test_out_of_scope_file_blocked(self, tmp_path):
        artifact = _artifact()
        store = ApprovalRecordStore(tmp_path)
        store.create(artifact, initial_status=ApprovalStatus.APPROVED)
        result = validate_approval(
            artifact.approval_id,
            store=store,
            handoff=_handoff(),
            current_revision=artifact.repository_revision,
            now=NOW,
            requested_files=("src/monkey_brain/kernel/trusted_auth.py",),
        )
        assert result.scope_covers_request is False
        assert result.authorized is False

    def test_out_of_scope_behavior_blocked(self, tmp_path):
        artifact = _artifact()
        store = ApprovalRecordStore(tmp_path)
        store.create(artifact, initial_status=ApprovalStatus.APPROVED)
        result = validate_approval(
            artifact.approval_id,
            store=store,
            handoff=_handoff(),
            current_revision=artifact.repository_revision,
            now=NOW,
            requested_behaviors=("rewrite the OPA policy engine",),
        )
        assert result.scope_covers_request is False
        assert result.authorized is False

    def test_new_security_boundary_always_blocked_even_if_files_match(self, tmp_path):
        """A file-path match never implies authorization for a named
        security-boundary change living in that file (Section 8/12)."""
        artifact = _artifact()  # scope.security_boundaries is empty
        store = ApprovalRecordStore(tmp_path)
        store.create(artifact, initial_status=ApprovalStatus.APPROVED)
        result = validate_approval(
            artifact.approval_id,
            store=store,
            handoff=_handoff(),
            current_revision=artifact.repository_revision,
            now=NOW,
            requested_files=("src/monkey_brain/kernel/execution_attempt.py",),
            requested_security_boundaries=("MFA requirement",),
        )
        assert result.scope_covers_request is False
        assert result.authorized is False


# ── Renewal ───────────────────────────────────────────────────────────────


class TestRenewal:
    def test_expired_artifact_replaced_by_new_one_with_new_window(self, tmp_path):
        old = _artifact()
        store = ApprovalRecordStore(tmp_path)
        store.create(old, initial_status=ApprovalStatus.APPROVED)
        store.transition_status(old.approval_id, ApprovalStatus.EXPIRED, reason="24h elapsed")

        renewal_time = old.expires_at + timedelta(hours=2)
        new = create_artifact(
            handoff=_handoff(),
            approved_by="prashun",
            decision=ApprovalDecision.APPROVED,
            approval_id="APR-test-002",
            approved_at=renewal_time,
            supersedes_approval_id=old.approval_id,
        )
        assert new.approval_id != old.approval_id
        assert new.approved_at != old.approved_at
        assert new.expires_at != old.expires_at
        assert new.supersedes_approval_id == old.approval_id

        store.create(new, initial_status=ApprovalStatus.APPROVED)
        result = validate_approval(
            new.approval_id,
            store=store,
            handoff=_handoff(),
            current_revision=new.repository_revision,
            now=renewal_time,
        )
        assert result.authorized is True
        # The OLD approval_id is still on record as EXPIRED — renewal never
        # silently mutated it back to APPROVED.
        assert store.get(old.approval_id).status is ApprovalStatus.EXPIRED


# ── Persistence (fail-closed) ─────────────────────────────────────────────


class TestPersistenceFailClosed:
    def test_missing_approval_record_fails_closed(self, tmp_path):
        store = ApprovalRecordStore(tmp_path)
        result = validate_approval(
            "APR-does-not-exist",
            store=store,
            handoff=_handoff(),
            current_revision="deadbeef" * 5,
            now=NOW,
        )
        assert result.authorized is False
        assert result.schema_valid is False

    def test_corrupted_json_fails_closed(self, tmp_path):
        store = ApprovalRecordStore(tmp_path)
        (tmp_path / "APR-broken.json").write_text("{not valid json", encoding="utf-8")
        with pytest.raises(ApprovalPersistenceError):
            store.get("APR-broken")

    def test_tampered_content_fails_integrity_check(self, tmp_path):
        artifact = _artifact()
        store = ApprovalRecordStore(tmp_path)
        store.create(artifact, initial_status=ApprovalStatus.APPROVED)
        path = tmp_path / f"{artifact.approval_id}.json"
        data = json.loads(path.read_text())
        # Tamper with expires_at directly in the durable file, bypassing
        # the immutable Python object entirely.
        data["artifact"]["expires_at"] = (NOW + timedelta(days=365)).isoformat()
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ApprovalIntegrityError):
            store.get(artifact.approval_id)

    def test_duplicate_approval_id_rejected_not_overwritten(self, tmp_path):
        artifact = _artifact()
        store = ApprovalRecordStore(tmp_path)
        store.create(artifact, initial_status=ApprovalStatus.APPROVED)
        with pytest.raises(ApprovalPersistenceError):
            store.create(artifact, initial_status=ApprovalStatus.APPROVED)


# ── Status state machine ──────────────────────────────────────────────────


class TestStatusStateMachine:
    def test_valid_transitions(self, tmp_path):
        artifact = _artifact()
        store = ApprovalRecordStore(tmp_path)
        store.create(artifact, initial_status=ApprovalStatus.APPROVED)
        record = store.transition_status(artifact.approval_id, ApprovalStatus.REVOKED, reason="scope changed")
        assert record.status is ApprovalStatus.REVOKED

    @pytest.mark.parametrize(
        "terminal",
        [
            ApprovalStatus.EXPIRED,
            ApprovalStatus.REVOKED,
            ApprovalStatus.SUPERSEDED,
            ApprovalStatus.REJECTED,
        ],
    )
    def test_terminal_status_cannot_become_approved_again(self, tmp_path, terminal):
        artifact = _artifact()
        store = ApprovalRecordStore(tmp_path)
        store.create(artifact, initial_status=ApprovalStatus.APPROVED)
        if terminal is not ApprovalStatus.REJECTED:
            store.transition_status(artifact.approval_id, terminal)
        else:
            # REJECTED is reachable only from CREATED, not from APPROVED —
            # exercise the direct CREATED->REJECTED artifact instead.
            rejected = _artifact(approval_id="APR-rejected-001", decision=ApprovalDecision.REJECTED)
            store.create(rejected, initial_status=ApprovalStatus.REJECTED)
            with pytest.raises(InvalidApprovalTransition):
                store.transition_status(rejected.approval_id, ApprovalStatus.APPROVED)
            return
        with pytest.raises(InvalidApprovalTransition):
            store.transition_status(artifact.approval_id, ApprovalStatus.APPROVED)

    def test_decision_approved_but_status_expired_is_not_authorized(self, tmp_path):
        """Section 5: decision == APPROVED and status == EXPIRED must not
        authorize implementation — decision and status are not conflated."""
        artifact = _artifact()
        store = ApprovalRecordStore(tmp_path)
        store.create(artifact, initial_status=ApprovalStatus.APPROVED)
        store.transition_status(artifact.approval_id, ApprovalStatus.EXPIRED)
        result = validate_approval(
            artifact.approval_id,
            store=store,
            handoff=_handoff(),
            current_revision=artifact.repository_revision,
            now=NOW,
        )
        assert result.decision_approved is True
        assert result.status_approved is False
        assert result.authorized is False


# ── Security: this package cannot touch the real security boundary ──────


class TestNoCouplingToProductSecurityBoundary:
    def test_governance_package_imports_nothing_from_the_kernel(self):
        import ast

        pkg_dir = Path(__file__).resolve().parents[2] / "governance"
        for path in pkg_dir.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.ImportFrom) and node.module:
                    modules.append(node.module)
                elif isinstance(node, ast.Import):
                    modules.extend(alias.name for alias in node.names)
                for module in modules:
                    assert "monkey_brain" not in module, f"{path.name} imports {module}"

    def test_validate_approval_result_has_no_hook_into_mfa_opa_or_audit(self):
        """Structural sanity check: ApprovalValidationResult only reports
        facts about the approval artifact itself — it has no field or
        method that could be mistaken for an MFA/OPA/authentication/audit
        decision."""
        from governance.validator import ApprovalValidationResult

        fields = {f.name for f in dataclasses.fields(ApprovalValidationResult)}
        forbidden = {
            "mfa_satisfied",
            "opa_allowed",
            "authenticated",
            "audit_recorded",
            "authorized_by_opa",
        }
        assert not (fields & forbidden)
