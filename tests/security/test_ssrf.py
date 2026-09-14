"""SSRF protection tests — validate URL blocking for private IPs, blocked schemes, and edge cases."""

from __future__ import annotations

import os
import sys

import pytest

_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)
for p in (
    "src",
    "packages/cerebellum",
    "packages/broca",
    "packages/soma-cli",
    "domains/manufacturing/knowledge",
):
    _full = os.path.join(_root, p)
    if _full not in sys.path:
        sys.path.insert(0, _full)


class TestSSRFProtection:
    """Test the _is_safe_url validator in webhook.py."""

    def _import_validator(self):
        from cerebellum.capabilities.api.webhook import _is_safe_url

        return _is_safe_url

    def test_blocked_schemes_file(self):
        fn = self._import_validator()
        assert fn("file:///etc/passwd") is False

    def test_blocked_schemes_ftp(self):
        fn = self._import_validator()
        assert fn("ftp://internal-host/data") is False

    def test_blocked_schemes_gopher(self):
        fn = self._import_validator()
        assert fn("gopher://localhost:6379/1/info") is False

    def test_blocked_schemes_dict(self):
        fn = self._import_validator()
        assert fn("dict://localhost/db") is False

    def test_allowed_http(self):
        fn = self._import_validator()
        assert fn("http://example.com/hook") is True

    def test_allowed_https(self):
        fn = self._import_validator()
        assert fn("https://example.com/hook") is True

    def test_blocked_localhost(self):
        fn = self._import_validator()
        assert fn("http://localhost:8080/hook") is False

    def test_blocked_127(self):
        fn = self._import_validator()
        assert fn("http://127.0.0.1/api") is False

    def test_blocked_loopback_ipv6(self):
        fn = self._import_validator()
        assert fn("http://[::1]/api") is False

    def test_blocked_metadata_endpoint(self):
        fn = self._import_validator()
        assert fn("http://169.254.169.254/latest/meta-data/") is False

    def test_blocked_private_10(self):
        fn = self._import_validator()
        assert fn("http://10.0.0.1:6379/keys") is False

    def test_blocked_private_172(self):
        fn = self._import_validator()
        assert fn("http://172.16.0.1/api") is False

    def test_blocked_private_192(self):
        fn = self._import_validator()
        assert fn("http://192.168.1.1/admin") is False

    def test_blocked_0_0_0_0(self):
        fn = self._import_validator()
        assert fn("http://0.0.0.0/") is False

    def test_unsafe_url_empty(self):
        fn = self._import_validator()
        assert fn("") is False

    def test_unsafe_url_not_a_url(self):
        fn = self._import_validator()
        assert fn("not-a-url") is False

    def test_blocked_unusual_schemes(self):
        fn = self._import_validator()
        assert fn("javascript:alert(1)") is False
        assert fn("data:text/html,<script>") is False

    def test_url_with_auth_bypass_attempt(self):
        fn = self._import_validator()
        assert fn("http://evil.com@127.0.0.1/") is False

    def test_url_with_port_on_private(self):
        fn = self._import_validator()
        assert fn("http://127.0.0.1:8080/attack") is False

    def test_url_with_path_traversal(self):
        fn = self._import_validator()
        assert fn("http://example.com/../../etc/passwd") is True

    def test_process_def_validator_same_rules(self):
        import importlib

        utils = importlib.import_module("services.process_definitions.utils")
        validate_outbound_url = utils.validate_outbound_url
        assert validate_outbound_url("http://example.com/hook") is True
        assert validate_outbound_url("http://169.254.169.254/") is False
        assert validate_outbound_url("http://localhost/") is False
        assert validate_outbound_url("file:///etc/passwd") is False
        assert validate_outbound_url("http://10.0.0.1:6379/") is False

    def test_validate_blocks_internal_ranges(self):
        import importlib

        utils = importlib.import_module("services.process_definitions.utils")
        validate_outbound_url = utils.validate_outbound_url
        blocked = [
            "http://10.0.0.1:27017/",
            "http://172.16.0.1:6379/",
            "http://192.168.1.1/",
            "http://127.0.0.1/",
            "http://localhost/",
            "http://[::1]/",
            "http://169.254.169.254/latest",
        ]
        for url in blocked:
            assert validate_outbound_url(url) is False, f"Expected blocked: {url}"

    def test_validate_allows_public(self):
        import importlib

        utils = importlib.import_module("services.process_definitions.utils")
        validate_outbound_url = utils.validate_outbound_url
        allowed = [
            "https://api.example.com/webhook",
            "https://hooks.slack.com/services/xxx",
            "https://api.openai.com/v1/chat",
        ]
        for url in allowed:
            assert validate_outbound_url(url) is True, f"Expected allowed: {url}"
