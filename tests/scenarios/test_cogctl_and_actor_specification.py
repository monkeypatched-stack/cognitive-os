"""ActorSpecification + cogctl + the Control API's declarative endpoints
(`POST /actors/apply`, `POST /actors/{id}/restart`) — Final Architectural
Convergence, Phases 5/6.

Covers:
  ActorSpecification.from_dict: valid minimal/full specs         -> test_01, test_02
  ActorSpecification: rejects wrong kind/apiVersion               -> test_03, test_04
  ActorSpecification: requires an identity                        -> test_05
  ActorSpecification: capacity validation                         -> test_06
  ActorSpecification: node_class defaults unconstrained            -> test_07
  ActorSpecification: to_dict round-trips                          -> test_08
  apply route: creates a brand-new actor                           -> test_09
  apply route: never registers with an empty actor_id (regression) -> test_10
  apply route: idempotent update of an existing actor's placement  -> test_11
  apply route: rejects an unrecognized node_class with 422         -> test_12
  apply route: claim_node explicitly places the actor              -> test_13
  apply route: never starts the actor process directly             -> test_14
  restart route: suspend then resume, same actor_id                -> test_15
  restart route: 404 for an unknown actor                          -> test_16
  cogctl: create-actor spec building (pure, no network)            -> test_17
  cogctl: auth header selection                                     -> test_18
  cogctl: apply reads a YAML file correctly                        -> test_19

Per this repo's session convention, this file is written but not executed
by the assistant. Run with:
    python -m pytest tests/scenarios/test_cogctl_and_actor_specification.py -v
"""

from __future__ import annotations

import json
import os
import time
from types import SimpleNamespace

import pytest

os.environ["AGENTOS_AUTH_REQUIRED"] = "false"
os.environ["RATE_LIMIT_RPS"] = "100000"
os.environ["RATE_LIMIT_BURST"] = "200000"

from src.monkey_brain.kernel.society.integration import PlanetaryRuntime
from src.monkey_brain.kernel.society.actor_scheduler import ExecutionNode
from src.monkey_brain.kernel.society.actor_specification import (
    ActorSpecification,
    ActorSpecificationError,
)
from src.monkey_brain.kernel.society.domain import (
    ActorProfile,
    ActorIdentity,
    ActorStatus,
)
from src.monkey_brain.api.routes.actors import apply_actor_specification, restart_actor
from fastapi import HTTPException


class _FakeRedis:
    """Same minimal in-memory redis-py subset established across this
    session's other test files."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._expiry: dict[str, float] = {}
        self._hashes: dict[str, dict[str, str]] = {}
        self._lists: dict[str, list[str]] = {}

    def _expired(self, key):
        exp = self._expiry.get(key)
        return exp is not None and time.time() > exp

    def ping(self):
        return True

    def set(self, key, value, nx=False, ex=None):
        if self._expired(key):
            self._store.pop(key, None)
        if nx and key in self._store:
            return False
        self._store[key] = value
        if ex is not None:
            self._expiry[key] = time.time() + ex
        else:
            self._expiry.pop(key, None)
        return True

    def get(self, key):
        if self._expired(key):
            self._store.pop(key, None)
            return None
        return self._store.get(key)

    def exists(self, key):
        return 1 if self.get(key) is not None else 0

    def delete(self, *keys):
        n = 0
        for k in keys:
            if k in self._store:
                del self._store[k]
                n += 1
        return n

    def hset(self, name, key, value):
        self._hashes.setdefault(name, {})[key] = value

    def hget(self, name, key):
        return self._hashes.get(name, {}).get(key)

    def hgetall(self, name):
        return dict(self._hashes.get(name, {}))

    def hdel(self, name, key):
        self._hashes.get(name, {}).pop(key, None)

    def rpush(self, name, *values):
        self._lists.setdefault(name, []).extend(values)
        return len(self._lists[name])

    def lpop(self, name, count=None):
        lst = self._lists.get(name, [])
        if not lst:
            return None if count is None else []
        if count is None:
            return lst.pop(0)
        popped, self._lists[name] = lst[:count], lst[count:]
        return popped

    def eval(self, script, numkeys, key, *args):
        if "cjson" in script:
            node_id, delta, ts = args
            hashes = self._hashes.get(key, {})
            raw = hashes.get(node_id)
            if raw is None:
                return -1
            node = json.loads(raw)
            delta = int(delta)
            capacity = int(node.get("capacity", 0))
            current = int(node.get("current_actor_count", 0))
            new_count = current + delta
            if delta > 0 and new_count > capacity:
                return -2
            new_count = max(0, new_count)
            node["current_actor_count"] = new_count
            node["updated_at"] = float(ts)
            hashes[node_id] = json.dumps(node)
            self._hashes[key] = hashes
            return new_count
        token = args[0] if args else None
        if self._store.get(key) == token and not self._expired(key):
            del self._store[key]
            return 1
        return 0


def _pr(redis=None, node_id: str = "") -> PlanetaryRuntime:
    pr = PlanetaryRuntime()
    if redis is not None:
        pr._redis = redis
    if node_id:
        pr._node_id = node_id
    return pr


def _fake_request(pr: PlanetaryRuntime):
    """A minimal stand-in for FastAPI's Request -- only
    request.app.state.planetary_runtime is ever read by these routes."""
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(planetary_runtime=pr)))


# ── 1-8: ActorSpecification ────────────────────────────────────────────────


def test_01_minimal_spec_parses():
    spec = ActorSpecification.from_dict(
        {
            "apiVersion": "cognitiveos/v1",
            "kind": "Actor",
            "metadata": {"name": "buyer-123"},
        }
    )
    assert spec.resolved_actor_id() == "buyer-123"
    assert spec.node_class == ""  # unconstrained by default


def test_02_full_spec_parses():
    doc = {
        "apiVersion": "cognitiveos/v1",
        "kind": "Actor",
        "metadata": {"name": "buyer-123", "actor_id": "explicit-id"},
        "spec": {
            "artifact": "cognitiveos-actor",
            "version": "1.4",
            "placement": {
                "node_class": "edge",
                "required_capabilities": ["camera"],
                "preferred_node_class": "cloud",
                "preferred_region": "us-east",
                "claim_node": "edge-node-4",
            },
            "resources": {"capacity": 3},
            "configuration": {
                "goals": ["g1", "g2"],
                "objective": "cost",
                "tenant_id": "acme",
            },
        },
    }
    spec = ActorSpecification.from_dict(doc)
    assert spec.resolved_actor_id() == "explicit-id"
    assert spec.artifact_version == "1.4"
    assert spec.node_class == "edge"
    assert spec.required_capabilities == ("camera",)
    assert spec.preferred_node_class == "cloud"
    assert spec.claim_node == "edge-node-4"
    assert spec.capacity_hint == 3
    assert spec.goals == ("g1", "g2")
    assert spec.objective == "cost"
    assert spec.tenant_id == "acme"


def test_03_rejects_wrong_kind():
    with pytest.raises(ActorSpecificationError, match="kind"):
        ActorSpecification.from_dict({"kind": "Pod", "metadata": {"name": "x"}})


def test_04_rejects_wrong_api_version():
    with pytest.raises(ActorSpecificationError, match="apiVersion"):
        ActorSpecification.from_dict(
            {
                "apiVersion": "v1",
                "kind": "Actor",
                "metadata": {"name": "x"},
            }
        )


def test_05_requires_name_or_actor_id():
    with pytest.raises(ActorSpecificationError):
        ActorSpecification.from_dict({"apiVersion": "cognitiveos/v1", "kind": "Actor", "metadata": {}})


def test_06_rejects_invalid_capacity():
    with pytest.raises(ActorSpecificationError, match="capacity"):
        ActorSpecification.from_dict(
            {
                "apiVersion": "cognitiveos/v1",
                "kind": "Actor",
                "metadata": {"name": "x"},
                "spec": {"resources": {"capacity": 0}},
            }
        )


def test_07_node_class_unconstrained_by_default():
    """A spec with no placement section at all must never silently
    impose a hard cloud-only constraint."""
    spec = ActorSpecification.from_dict(
        {
            "apiVersion": "cognitiveos/v1",
            "kind": "Actor",
            "metadata": {"name": "x"},
        }
    )
    assert spec.node_class == ""
    assert spec.preferred_node_class == ""


def test_08_to_dict_round_trips():
    original = ActorSpecification.from_dict(
        {
            "apiVersion": "cognitiveos/v1",
            "kind": "Actor",
            "metadata": {"name": "x"},
            "spec": {"placement": {"node_class": "device"}},
        }
    )
    round_tripped = ActorSpecification.from_dict(original.to_dict())
    assert round_tripped == original


# ── 9-14: apply route ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_09_apply_creates_a_new_actor():
    redis = _FakeRedis()
    pr = _pr(redis, "n1")
    pr.register_node(ExecutionNode(node_id="n1", capacity=5))
    request = _fake_request(pr)
    body = {
        "apiVersion": "cognitiveos/v1",
        "kind": "Actor",
        "metadata": {"name": "buyer-123"},
        "spec": {"configuration": {"goals": ["get_milk"]}},
    }
    result = await apply_actor_specification(body, request, user_id="test", _agent={})
    assert result["created"] is True
    assert result["actor_id"]
    entry = pr.locate_actor(result["actor_id"])
    assert entry is not None
    assert entry.name == "buyer-123"


@pytest.mark.asyncio
async def test_10_apply_never_registers_with_empty_actor_id():
    """Regression test for a real bug found while building this: passing
    ActorIdentity(actor_id="") explicitly bypasses its uuid4
    default_factory and would register an actor with a literal empty
    actor_id."""
    redis = _FakeRedis()
    pr = _pr(redis)
    request = _fake_request(pr)
    body = {
        "apiVersion": "cognitiveos/v1",
        "kind": "Actor",
        "metadata": {"name": "no-explicit-id"},
    }
    result = await apply_actor_specification(body, request, user_id="test", _agent={})
    assert result["actor_id"] != ""
    assert len(result["actor_id"]) > 0


@pytest.mark.asyncio
async def test_11_apply_is_idempotent_update_for_an_existing_actor():
    redis = _FakeRedis()
    pr = _pr(redis, "n1")
    pr.register_node(ExecutionNode(node_id="n1", capacity=5))
    request = _fake_request(pr)
    body = {
        "apiVersion": "cognitiveos/v1",
        "kind": "Actor",
        "metadata": {"name": "buyer-123"},
    }

    first = await apply_actor_specification(body, request, user_id="test", _agent={})
    actor_id = first["actor_id"]
    assert first["created"] is True

    body2 = {
        "apiVersion": "cognitiveos/v1",
        "kind": "Actor",
        "metadata": {"name": "buyer-123", "actor_id": actor_id},
        "spec": {"placement": {"required_capabilities": ["gpu"]}},
    }
    second = await apply_actor_specification(body2, request, user_id="test", _agent={})
    assert second["created"] is False
    assert second["actor_id"] == actor_id
    # Exactly one registry entry -- update, not a second registration.
    assert [e.actor_id for e in pr.list_registry()].count(actor_id) == 1
    reqs = pr.get_actor_placement_requirements(actor_id)
    assert reqs.required_capabilities == ("gpu",)


@pytest.mark.asyncio
async def test_12_apply_rejects_unrecognized_node_class():
    redis = _FakeRedis()
    pr = _pr(redis)
    request = _fake_request(pr)
    body = {
        "apiVersion": "cognitiveos/v1",
        "kind": "Actor",
        "metadata": {"name": "x"},
        "spec": {"placement": {"node_class": "not-a-real-class"}},
    }
    with pytest.raises(HTTPException) as exc_info:
        await apply_actor_specification(body, request, user_id="test", _agent={})
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_13_apply_claim_node_explicitly_places_the_actor():
    redis = _FakeRedis()
    pr = _pr(redis)
    pr.register_node(ExecutionNode(node_id="decoy-node", capacity=5))
    pr.register_node(ExecutionNode(node_id="specific-node", capacity=5))
    request = _fake_request(pr)
    body = {
        "apiVersion": "cognitiveos/v1",
        "kind": "Actor",
        "metadata": {"name": "claimed"},
        "spec": {"placement": {"claim_node": "specific-node"}},
    }
    result = await apply_actor_specification(body, request, user_id="test", _agent={})
    assert pr.get_actor_desired_node(result["actor_id"]) == "specific-node"


@pytest.mark.asyncio
async def test_14_apply_never_starts_the_actor_process_directly():
    """The cogctl invariant, verified directly: apply_actor_specification
    must never call activate_actor/tick or any cognition entry point --
    only registry/scheduler/desired-state writes. Confirmed by checking
    the actor's status stays REGISTERED (never ACTIVE) immediately after
    apply, in a process with no reconciliation loop running."""
    redis = _FakeRedis()
    pr = _pr(redis, "n1")
    pr.register_node(ExecutionNode(node_id="n1", capacity=5))
    request = _fake_request(pr)
    body = {
        "apiVersion": "cognitiveos/v1",
        "kind": "Actor",
        "metadata": {"name": "not-yet-running"},
    }
    result = await apply_actor_specification(body, request, user_id="test", _agent={})

    sr = pr._home_society_runtime(result["actor_id"])
    assert sr.get_actor(result["actor_id"]).status == ActorStatus.REGISTERED
    assert sr.get_actor(result["actor_id"]).is_active is False


# ── 15-16: restart route ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_15_restart_suspends_then_resumes_same_actor():
    redis = _FakeRedis()
    pr = _pr(redis, "n1")
    pr.register_node(ExecutionNode(node_id="n1", capacity=5))
    request = _fake_request(pr)
    state = pr.register_actor(ActorProfile(identity=ActorIdentity(name="restart_me")))
    assert pr.lifecycle.reconcile(state.actor_id).action == "start"

    result = await restart_actor(state.actor_id, request, user_id="test", _agent={})
    assert result["suspend"]["action"] == "suspend"
    assert result["suspend"]["succeeded"] is True
    assert result["resume"]["action"] == "resume"
    assert result["resume"]["succeeded"] is True

    sr = pr._home_society_runtime(state.actor_id)
    assert sr.get_actor(state.actor_id).status == ActorStatus.ACTIVE
    assert sr.get_actor(state.actor_id).actor_id == state.actor_id  # identity unchanged


@pytest.mark.asyncio
async def test_16_restart_unknown_actor_is_404():
    redis = _FakeRedis()
    pr = _pr(redis)
    request = _fake_request(pr)
    with pytest.raises(HTTPException) as exc_info:
        await restart_actor("nonexistent-actor", request, user_id="test", _agent={})
    assert exc_info.value.status_code == 404


# ── 17-19: cogctl (pure logic, no network) ─────────────────────────────────


def test_17_create_actor_builds_correct_spec(monkeypatch):
    import argparse
    from src.monkey_brain import cogctl

    captured = {}

    def _fake_request(method, path, json_body=None):
        captured["method"], captured["path"], captured["body"] = method, path, json_body
        return {"actor_id": "buyer-123", "created": True}

    monkeypatch.setattr(cogctl, "_request", _fake_request)

    args = argparse.Namespace(
        name="buyer-123",
        node_class="edge",
        artifact_version="1.4",
        claim_node="edge-4",
        region="us-east",
        capacity=2,
        tenant_id="acme",
        goal=["get_milk"],
        objective="cost",
        required_capability=["camera"],
    )
    rc = cogctl.cmd_create_actor(args)
    assert rc == 0
    assert captured["method"] == "POST"
    assert captured["path"] == "/actors/apply"
    body = captured["body"]
    assert body["metadata"]["name"] == "buyer-123"
    assert body["spec"]["placement"]["node_class"] == "edge"
    assert body["spec"]["placement"]["claim_node"] == "edge-4"
    assert body["spec"]["placement"]["required_capabilities"] == ["camera"]
    assert body["spec"]["resources"]["capacity"] == 2
    assert body["spec"]["configuration"]["goals"] == ["get_milk"]


def test_18_auth_header_selection(monkeypatch):
    from src.monkey_brain import cogctl

    monkeypatch.delenv("COGCTL_API_KEY", raising=False)
    monkeypatch.delenv("COGCTL_USER_ID", raising=False)
    assert "Authorization" not in cogctl._headers()
    assert "X-User-ID" not in cogctl._headers()

    monkeypatch.setenv("COGCTL_USER_ID", "alice")
    headers = cogctl._headers()
    assert headers["X-User-ID"] == "alice"
    assert "Authorization" not in headers

    monkeypatch.setenv("COGCTL_API_KEY", "secret-key")
    headers = cogctl._headers()
    assert headers["Authorization"] == "Bearer secret-key"
    # API key takes precedence over X-User-ID when both are set.
    assert "X-User-ID" not in headers


def test_19_apply_reads_yaml_file(tmp_path, monkeypatch):
    import argparse
    from src.monkey_brain import cogctl

    spec_file = tmp_path / "actor.yaml"
    spec_file.write_text("apiVersion: cognitiveos/v1\nkind: Actor\nmetadata:\n  name: buyer-123\n")
    captured = {}

    def _fake_request(method, path, json_body=None):
        captured["body"] = json_body
        return {"actor_id": "buyer-123", "created": True}

    monkeypatch.setattr(cogctl, "_request", _fake_request)

    rc = cogctl.cmd_apply(argparse.Namespace(file=str(spec_file)))
    assert rc == 0
    assert captured["body"]["metadata"]["name"] == "buyer-123"
