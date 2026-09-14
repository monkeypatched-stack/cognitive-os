"""Presence (#4) and Verification (#19) REST APIs.

New categories, both genuinely new but small, both wrapping real
functionality already built and tested this session:

  - Presence: where_is/history/who_is_in already existed as real,
    tested PresenceTimeline methods (MB-3053) — never reachable over
    HTTP before this.
  - Verification: kernel/society/verification.py::verify_world_invariants()
    checks four invariants this whole session's architecture already
    depends on (every Society has a Space, every Actor has exactly one
    open Presence, no orphaned geographic entities, no invalid
    Memberships) — read-only, never mutates anything.

Simplified from the original 4-endpoint /verify proposal (POST /verify,
GET /verify/world, GET /verify/graph, GET /verify/invariants) to two:
POST /verify and a GET /verify/invariants alias — the four checks are
one comprehensive report, not four separately-meaningful ones; splitting
them would mean either running the same checks four times or building
three thin wrappers with no real distinction.
"""

from __future__ import annotations

import os

os.environ.setdefault("AGENTOS_AUTH_REQUIRED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from src.monkey_brain.api.main import app  # noqa: E402


def test_presence_and_verify_apis_work_end_to_end():
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.post("/api/v1/agentos/actors", json={"name": "Alice", "actor_type": "human"})
        assert r.status_code == 200, r.text
        actor_id = r.json()["actor_id"]

        # ── Presence ──
        r = client.get("/api/v1/agentos/presence")
        assert r.status_code == 200, r.text
        assert any(o["actor_id"] == actor_id for o in r.json()["occupancy"])

        r = client.get(f"/api/v1/agentos/presence/actors/{actor_id}")
        assert r.status_code == 200, r.text
        space_id = r.json()["space_id"]
        assert space_id

        r = client.get(f"/api/v1/agentos/presence/spaces/{space_id}")
        assert r.status_code == 200, r.text
        assert actor_id in r.json()["actor_ids"]

        r = client.get(f"/api/v1/agentos/presence/history/{actor_id}")
        assert r.status_code == 200, r.text
        assert len(r.json()["history"]) == 1
        assert r.json()["history"][0]["space_id"] == space_id

        r = client.get(f"/api/v1/agentos/presence/history/space/{space_id}")
        assert r.status_code == 200, r.text
        assert any(h["actor_id"] == actor_id for h in r.json()["history"])

        r = client.get("/api/v1/agentos/presence/actors/does-not-exist")
        assert r.status_code == 404

        # ── Verify ──
        r = client.post("/api/v1/agentos/verify", json={})
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True
        assert r.json()["violations"] == []

        r = client.get("/api/v1/agentos/verify/invariants")
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True
