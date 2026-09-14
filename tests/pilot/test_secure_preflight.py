"""Secure-mode preflight — a pilot must not silently run open."""

from __future__ import annotations

import pytest

from src.monkey_brain.kernel.compile.network import secure_mode_preflight


def test_preflight_flags_insecure_defaults(monkeypatch):
    for var in (
        "AGENTOS_CA_STORE",
        "AGENTOS_EXCHANGE_TOKEN",
        "AGENTOS_REQUIRE_MTLS",
        "AGENTOS_KEY_PASSWORD",
    ):
        monkeypatch.delenv(var, raising=False)
    issues = secure_mode_preflight(strict=False)
    assert len(issues) == 4  # every gap surfaced, not hidden


def test_preflight_strict_mode_fails_fast(monkeypatch):
    monkeypatch.delenv("AGENTOS_CA_STORE", raising=False)
    with pytest.raises(RuntimeError, match="insecure"):
        secure_mode_preflight(strict=True)


def test_preflight_passes_when_fully_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTOS_CA_STORE", str(tmp_path / "ca.json"))
    monkeypatch.setenv("AGENTOS_EXCHANGE_TOKEN", "tok")
    monkeypatch.setenv("AGENTOS_REQUIRE_MTLS", "1")
    monkeypatch.setenv("AGENTOS_KEY_PASSWORD", "managed-secret")
    assert secure_mode_preflight(strict=True) == []
