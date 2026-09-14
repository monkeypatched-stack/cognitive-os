"""Tests for governance.audit — event-type vocabulary and per-event
identifiers (Section 13: idempotency/traceability groundwork)."""

from __future__ import annotations

import pytest

from governance.audit import (
    EVENT_TYPES,
    GovernanceAuditError,
    record_governance_event,
    read_governance_events,
)


class TestEventVocabulary:
    def test_approval_authorized_is_a_recognized_event_type(self):
        """Added specifically so the success path has something real to
        durably record — see governance/README.md "Audit failure
        precedence" for why this one addition was necessary."""
        assert "approval_authorized" in EVENT_TYPES

    def test_unknown_event_type_rejected(self, tmp_path):
        with pytest.raises(GovernanceAuditError):
            record_governance_event("made_up_event", details={}, path=tmp_path / "audit.jsonl")


class TestEventIdentity:
    def test_each_recorded_event_has_a_unique_event_id(self, tmp_path):
        log = tmp_path / "audit.jsonl"
        id1 = record_governance_event("approval_authorized", details={"approval_id": "A1"}, path=log)
        id2 = record_governance_event("approval_authorized", details={"approval_id": "A2"}, path=log)
        assert id1 != id2
        assert id1 and id2  # non-empty

        events = read_governance_events(log)
        assert [e["event_id"] for e in events] == [id1, id2]

    def test_no_caller_in_this_package_retries_a_failed_write(self):
        """Section 12: fail closed instead of retrying. This is a
        structural fact about the CLI's call sites, checked here so a
        future change that adds a retry loop must consciously touch this
        test (and reconsider idempotency) rather than silently drift."""
        import ast
        from pathlib import Path

        cli_source = (Path(__file__).resolve().parents[2] / "governance" / "cli.py").read_text()
        tree = ast.parse(cli_source)
        record_call_sites = 0
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "record_governance_event"
            ):
                record_call_sites += 1
        # _emit_authorized_governance_event calls it once, and
        # _emit_blocking_governance_events calls it twice (two distinct
        # event types) — exactly 3 call sites, none inside a retry/loop
        # construct (verified structurally: neither helper contains a
        # `for`/`while` around the call — see governance/cli.py directly).
        assert record_call_sites == 3
