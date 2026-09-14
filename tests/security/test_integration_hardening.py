"""Service-integration hardening (I-1..I-3).

I-1  connection URLs embed credentials — they must be redacted before logging
I-2  Redis had no socket timeout (redis-py default is None → a hung server blocks forever)
I-3  Mongo clients had no explicit timeouts (30s default server-selection stalls every op)
"""

from __future__ import annotations

import os

from src.monkey_brain.persistence.client_options import redact_url, mongo_client_options

# ── I-1: credential redaction ────────────────────────────────────────────────────


def test_credentials_are_stripped_from_urls():
    assert redact_url("mongodb://alice:s3cret@db:27017/app") == "mongodb://***@db:27017/app"
    assert redact_url("https://elastic:pw@es.internal:9200") == "https://***@es.internal:9200"
    assert redact_url("nats://user:pw@nats:4222") == "nats://***@nats:4222"


def test_urls_without_credentials_are_unchanged():
    assert redact_url("mongodb://localhost:27017") == "mongodb://localhost:27017"
    assert redact_url("http://localhost:9200") == "http://localhost:9200"
    assert redact_url("") == ""


def test_redaction_never_leaks_the_secret():
    for url in ("mongodb://u:p@h/x", "redis://:onlypassword@h:6379"):
        assert "p" not in redact_url(url).split("***")[-1] or "***" in redact_url(url)
        assert "onlypassword" not in redact_url(url)


def test_domain_db_redactor_matches():
    from services.common.db import _redact_url

    assert _redact_url("mongodb://u:pw@h:27017/db") == "mongodb://***@h:27017/db"
    assert "pw" not in _redact_url("mongodb://u:pw@h:27017/db")


# ── I-3: Mongo timeouts are explicit and bounded ─────────────────────────────────


def test_mongo_options_bound_every_wait():
    o = mongo_client_options()
    for key in (
        "serverSelectionTimeoutMS",
        "connectTimeoutMS",
        "socketTimeoutMS",
        "maxPoolSize",
    ):
        assert key in o and o[key] > 0
    # must be tighter than the 30s driver default that stalled every op
    assert o["serverSelectionTimeoutMS"] <= 10_000


def test_mongo_options_are_env_tunable(monkeypatch):
    monkeypatch.setenv("MONGO_SERVER_SELECTION_TIMEOUT_MS", "1234")
    assert mongo_client_options()["serverSelectionTimeoutMS"] == 1234


# ── I-2: Redis client is constructed with bounded sockets ────────────────────────


def test_redis_adapter_sets_socket_timeouts(monkeypatch):
    import asyncio

    captured = {}

    class _FakeAioredis:
        @staticmethod
        def from_url(url, **kwargs):
            captured.update(kwargs)
            return object()

    import sys, types

    fake = types.ModuleType("redis.asyncio")
    fake.from_url = _FakeAioredis.from_url
    redis_mod = types.ModuleType("redis")
    redis_mod.asyncio = fake
    monkeypatch.setitem(sys.modules, "redis", redis_mod)
    monkeypatch.setitem(sys.modules, "redis.asyncio", fake)

    from src.monkey_brain.persistence.redis_adapter import RedisAdapter

    asyncio.run(RedisAdapter(url="redis://localhost:6379").connect())

    assert captured["socket_timeout"] > 0  # was absent → blocked forever on a hung server
    assert captured["socket_connect_timeout"] > 0
