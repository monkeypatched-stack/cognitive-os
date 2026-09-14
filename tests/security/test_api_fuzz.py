"""API fuzzing tests — send random/malformed inputs to core endpoints and validate no crashes."""

from __future__ import annotations

import asyncio
import json
import random
import string
import sys
import os

import pytest

_root = os.path.join(os.path.dirname(__file__), "..", "..")
for p in ("src", "packages/cerebellum", "domains/manufacturing/knowledge"):
    _full = os.path.join(_root, p)
    if _full not in sys.path:
        sys.path.insert(0, _full)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _rand_str(n=32):
    return "".join(random.choices(string.ascii_letters + string.digits, k=n))


def _rand_int():
    return random.randint(-999999, 999999)


def _rand_float():
    return random.uniform(-999999.0, 999999.0)


def _rand_bool():
    return random.choice([True, False])


def _fuzz_string():
    return random.choice(
        [
            "",
            " ",
            "\n",
            "\t",
            "\x00",
            "\r\n",
            "NULL",
            "None",
            "undefined",
            "<script>alert(1)</script>",
            "'; DROP TABLE users; --",
            "../../../etc/passwd",
            "A" * 10000,
            "\u0000\u0000\u0000",
            "🎉🔥💀",
            "\\n\\r\\t",
            '{"injection": true}',
            "[1,2,3]",
            "null",
        ]
    )


def _fuzz_dict():
    d = {}
    for _ in range(random.randint(0, 5)):
        k = _rand_str(random.randint(1, 20))
        v = random.choice([_rand_str(), _rand_int(), _rand_float(), _rand_bool(), None, [], {}])
        d[k] = v
    return d


class TestFuzzHealthEndpoints:
    """Fuzz health check endpoints with various inputs."""

    def test_health_get_with_query_params(self):
        from httpx import AsyncClient, ASGITransport
        from src.monkey_brain.api.main import app

        transport = ASGITransport(app=app)

        async def run():
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                for _ in range(20):
                    params = {_rand_str(5): _fuzz_string() for _ in range(3)}
                    resp = await client.get("/health", params=params)
                    assert resp.status_code in (200, 422)

        _run(run())

    def test_health_with_malformed_headers(self):
        from httpx import AsyncClient, ASGITransport
        from src.monkey_brain.api.main import app

        transport = ASGITransport(app=app)

        async def run():
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                for _ in range(20):
                    try:
                        headers = {_rand_str(10): _rand_str(20) for _ in range(3)}
                        resp = await client.get("/health", headers=headers)
                        assert resp.status_code in (200, 422)
                    except (UnicodeEncodeError, ValueError):
                        pass

        _run(run())


class TestFuzzQueryEndpoint:
    """Fuzz the /api/v1/agentos/query endpoint."""

    def test_fuzz_query_with_random_strings(self):
        from httpx import AsyncClient, ASGITransport
        from src.monkey_brain.api.main import app

        transport = ASGITransport(app=app)

        async def run():
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                for _ in range(30):
                    payload = {"question": _fuzz_string(), "context": _fuzz_dict()}
                    resp = await client.post("/api/v1/agentos/query", json=payload)
                    assert resp.status_code in (200, 404, 422, 401, 403)

        _run(run())

    def test_fuzz_query_with_nested_json(self):
        from httpx import AsyncClient, ASGITransport
        from src.monkey_brain.api.main import app

        transport = ASGITransport(app=app)

        async def run():
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                for _ in range(20):
                    payload = {
                        "question": json.dumps(_fuzz_dict()),
                        "nested": _fuzz_dict(),
                    }
                    resp = await client.post("/api/v1/agentos/query", json=payload)
                    assert resp.status_code in (200, 404, 422, 401, 403)

        _run(run())

    def test_fuzz_query_with_huge_payload(self):
        from httpx import AsyncClient, ASGITransport
        from src.monkey_brain.api.main import app

        transport = ASGITransport(app=app)

        async def run():
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                huge = {
                    "question": "A" * 100000,
                    "data": [_rand_str(1000) for _ in range(100)],
                }
                resp = await client.post("/api/v1/agentos/query", json=huge)
                assert resp.status_code in (200, 404, 422, 401, 413)

        _run(run())


class TestFuzzPromptEndpoint:
    """Fuzz the /api/v1/agentos/prompt endpoint."""

    def test_fuzz_prompt_random_payloads(self):
        from httpx import AsyncClient, ASGITransport
        from src.monkey_brain.api.main import app

        transport = ASGITransport(app=app)

        async def run():
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                for _ in range(30):
                    payload = {"spec": _fuzz_string(), "config": _fuzz_dict()}
                    resp = await client.post("/api/v1/agentos/prompt", json=payload)
                    assert resp.status_code in (200, 422, 401, 403)

        _run(run())


class TestFuzzAgentsEndpoint:
    """Fuzz agent discovery endpoints."""

    def test_fuzz_agents_list(self):
        from httpx import AsyncClient, ASGITransport
        from src.monkey_brain.api.main import app

        transport = ASGITransport(app=app)

        async def run():
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/v1/agentos/agents")
                assert resp.status_code in (200, 401, 403)

        _run(run())

    def test_fuzz_agents_with_path_traversal(self):
        from httpx import AsyncClient, ASGITransport
        from src.monkey_brain.api.main import app

        transport = ASGITransport(app=app)

        async def run():
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                traversal_payloads = [
                    "../../../etc/passwd",
                    "..%2F..%2F..%2Fetc/passwd",
                    "%2e%2e%2f%2e%2e%2f",
                    "admin/config",
                    "null",
                    "",
                ]
                for payload in traversal_payloads:
                    resp = await client.get(f"/api/v1/agentos/agents/{payload}")
                    assert resp.status_code in (200, 307, 404, 422, 401, 403)

        _run(run())


class TestFuzzCodegenEndpoint:
    """Fuzz the codegen endpoint."""

    def test_fuzz_codegen_random_spec(self):
        from httpx import AsyncClient, ASGITransport
        from src.monkey_brain.api.main import app

        transport = ASGITransport(app=app)

        async def run():
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                for _ in range(20):
                    payload = {
                        "name": _fuzz_string(),
                        "schema": _fuzz_dict(),
                        "description": _fuzz_string(),
                    }
                    resp = await client.post("/api/v1/codegen/microservice", json=payload)
                    assert resp.status_code in (200, 422, 401, 403)

        _run(run())


class TestFuzzContentTypes:
    """Test sending various content types to endpoints."""

    def test_non_json_body(self):
        from httpx import AsyncClient, ASGITransport
        from src.monkey_brain.api.main import app

        transport = ASGITransport(app=app)

        async def run():
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                for body in [b"\x00\x01\x02", b"plain text", b"<xml/>", b""]:
                    resp = await client.post(
                        "/api/v1/agentos/query",
                        content=body,
                        headers={"Content-Type": "application/octet-stream"},
                    )
                    assert resp.status_code in (200, 404, 422, 401, 415)

        _run(run())

    def test_form_data_to_json_endpoint(self):
        from httpx import AsyncClient, ASGITransport
        from src.monkey_brain.api.main import app

        transport = ASGITransport(app=app)

        async def run():
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/agentos/query",
                    data={"question": "test"},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                assert resp.status_code in (200, 404, 422, 401, 415)

        _run(run())
