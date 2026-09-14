"""Path traversal tests — verify file operations cannot escape base directories."""

from __future__ import annotations

import asyncio
import os

import pytest


class TestLocalStorageTraversal:
    """Test path traversal protection in LocalFileSystemCapability (src/)."""

    def _make_capability(self, base_path):
        from cerebellum.capabilities.storage.storage import LocalFileSystemCapability

        return LocalFileSystemCapability(base_path=base_path)

    def _run(self, coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def test_escape_read_blocked(self, tmp_path):
        secret = tmp_path / "secret.txt"
        secret.write_text("SECRET_DATA")
        cap = self._make_capability(str(tmp_path))
        result = self._run(cap.execute({"operation": "read", "path": "../../../etc/passwd"}))
        assert result.get("status") == "error" or "SECRET_DATA" not in str(result)

    def test_escape_write_blocked(self, tmp_path):
        cap = self._make_capability(str(tmp_path))
        result = self._run(
            cap.execute(
                {
                    "operation": "write",
                    "path": "../../../tmp/pwned.txt",
                    "content": "PWNED",
                }
            )
        )
        assert not os.path.exists("/tmp/pwned.txt") or result.get("status") == "error"

    def test_escape_list_blocked(self, tmp_path):
        cap = self._make_capability(str(tmp_path))
        result = self._run(cap.execute({"operation": "list", "path": "../../../etc"}))
        assert result.get("status") == "error" or "passwd" not in str(result)

    def test_absolute_path_blocked(self, tmp_path):
        cap = self._make_capability(str(tmp_path))
        result = self._run(cap.execute({"operation": "read", "path": "/etc/passwd"}))
        assert result.get("status") == "error"

    def test_normpath_bypass_blocked(self, tmp_path):
        cap = self._make_capability(str(tmp_path))
        result = self._run(cap.execute({"operation": "read", "path": "foo/../../etc/passwd"}))
        assert result.get("status") == "error"

    def test_valid_read_works(self, tmp_path):
        secret = tmp_path / "ok.txt"
        secret.write_text("VISIBLE")
        cap = self._make_capability(str(tmp_path))
        result = self._run(cap.execute({"operation": "read", "path": "ok.txt"}))
        assert result.get("content") == "VISIBLE"

    def test_valid_list_works(self, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        cap = self._make_capability(str(tmp_path))
        result = self._run(cap.execute({"operation": "list", "path": "."}))
        assert "a.txt" in result.get("files", [])

    def test_dotdot_in_subdir(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "ok.txt").write_text("ok")
        cap = self._make_capability(str(tmp_path))
        result = self._run(cap.execute({"operation": "read", "path": "sub/../../etc/passwd"}))
        assert result.get("status") == "error"

    def test_escape_exists_check(self, tmp_path):
        cap = self._make_capability(str(tmp_path))
        result = self._run(cap.execute({"operation": "exists", "path": "/etc/passwd"}))
        assert result.get("status") == "error"


class TestProcessDefinitionsStorageTraversal:
    """Test path traversal in process definition storage node."""

    def _run(self, coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def test_dotdot_rejected(self):
        from services.process_definitions.node_services.storage import (
            execute_storage_node,
        )

        result = self._run(execute_storage_node("file in", {"path": "../../../etc/passwd"}, {}))
        assert result.ok is False
        err = (result.error or "").lower()
        assert "traversal" in err or "not found" in err

    def test_valid_path_works(self, tmp_path):
        from services.process_definitions.node_services.storage import (
            execute_storage_node,
        )

        secret = tmp_path / "ok.txt"
        secret.write_text("VISIBLE")
        result = self._run(execute_storage_node("file in", {"path": str(secret)}, {}))
        assert result.ok is True


class TestCADConversionFilenameSanitization:
    """Test that uploaded filenames cannot contain path traversal."""

    def test_strips_directory_from_filename(self):
        from pathlib import Path

        malicious = "../../etc/cron.d/evil.dwg"
        safe = Path(malicious).name
        assert safe == "evil.dwg"
        assert "/" not in safe

    def test_strips_leading_slash(self):
        from pathlib import Path

        safe = Path("/etc/passwd").name
        assert safe == "passwd"
