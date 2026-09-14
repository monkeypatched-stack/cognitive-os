"""Credentials must never leave Mongo for the graph store (D-4).

The users mirror copied the whole Mongo document into Neo4j — hashed_password and
refresh_tokens included. Neo4j is a SECONDARY store with its own (often broader) access
controls and is queried for analytics: a mirrored password hash is an offline-cracking
target, and a mirrored refresh token is a session-hijack primitive.
"""

from __future__ import annotations

import pytest

from services.common.neo4j_mirror import strip_sensitive, _SENSITIVE_FIELDS


def test_credentials_are_stripped_before_mirroring():
    doc = {
        "user_id": "u1",
        "name": "Alice",
        "email": "a@example.com",
        "role": "operator",
        "hashed_password": "$2b$12$abcdefghijklmnopqrstuv",
        "password": "hunter2",
        "refresh_tokens": ["rt-aaa", "rt-bbb"],
    }
    out = strip_sensitive(doc)

    # business fields survive
    assert out["user_id"] == "u1" and out["email"] == "a@example.com" and out["role"] == "operator"
    # credentials do not
    assert "hashed_password" not in out
    assert "password" not in out
    assert "refresh_tokens" not in out
    assert "hunter2" not in str(out) and "$2b$12$" not in str(out) and "rt-aaa" not in str(out)


@pytest.mark.parametrize(
    "field",
    [
        "password",
        "hashed_password",
        "refresh_tokens",
        "access_token",
        "api_key",
        "client_secret",
        "private_key",
        "totp_secret",
    ],
)
def test_every_credential_field_class_is_stripped(field):
    assert field in _SENSITIVE_FIELDS
    assert field not in strip_sensitive({"user_id": "u", field: "SECRET-VALUE"})


def test_case_insensitive_and_non_dict_safe():
    assert "SECRET-VALUE" not in str(strip_sensitive({"Hashed_Password": "SECRET-VALUE"}))
    assert strip_sensitive(None) is None  # defensive: never raises on a non-dict


def test_auth_router_projects_credentials_out_before_mirroring():
    """The call site must not even READ the secrets back out of Mongo."""
    from pathlib import Path

    src = Path("domains/manufacturing/knowledge/services/auth/routers/auth.py").read_text()
    i = src.index('safe_mirror(mirror_document("users"')
    window = src[max(0, i - 500) : i]
    assert '"hashed_password": 0' in window and '"refresh_tokens": 0' in window
