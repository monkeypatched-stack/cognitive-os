"""
MonkeyBrain Client — Python SDK for the MonkeyBrain Cognitive Runtime.

Wraps all REST API endpoints into a clean, typed Python interface.

Usage:
    from monkeypatched_sdk.client import MonkeyBrain

    mb = MonkeyBrain("http://localhost:8000")

    # Register an actor
    alice = mb.create_actor("Alice", objective="cost", goals=["buy_milk"])

    # Tick the actor
    result = mb.tick(alice.actor_id)
    print(result.plan_steps)

    # Run planetary cycle
    cycle = mb.planet_tick()

    # Tune the pipeline
    mb.update_tuning(reward={"goal_achieved": 0.85})

    # Check health
    print(mb.health())
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from dataclasses import dataclass, field

logger = logging.getLogger("monkeybrain.client")


@dataclass
class ActorInfo:
    """Actor information from the API."""

    actor_id: str
    name: str
    actor_type: str
    goals: list[str]
    objective: str
    status: str = ""
    cycle_count: int = 0

    @classmethod
    def from_dict(cls, d: dict) -> "ActorInfo":
        return cls(
            actor_id=d.get("actor_id", ""),
            name=d.get("name", ""),
            actor_type=d.get("actor_type", ""),
            goals=d.get("goals", []),
            objective=d.get("objective", ""),
            status=d.get("status", ""),
            cycle_count=d.get("cycle_count", 0),
        )


@dataclass
class TickResult:
    """Result of an actor tick."""

    actor_id: str
    tick_count: int
    plan_steps: list[dict]
    belief_updated: bool = False
    goal_achieved: bool = False
    actions_executed: int = 0
    success_count: int = 0
    failure_count: int = 0

    @classmethod
    def from_dict(cls, d: dict) -> "TickResult":
        r = d.get("result", {})
        outcome = r.get("outcome", {})
        steps = r.get("plan", {}).get("steps", [])
        return cls(
            actor_id=d.get("actor_id", ""),
            tick_count=d.get("tick_count", 0),
            plan_steps=steps,
            belief_updated=r.get("belief_updated", False),
            goal_achieved=outcome.get("goal_achieved", False),
            actions_executed=outcome.get("actions_executed", 0),
            success_count=outcome.get("success_count", 0),
            failure_count=outcome.get("failure_count", 0),
        )


class MonkeyBrain:
    """Client for the MonkeyBrain Cognitive Runtime.

    Args:
        base_url: Server URL (e.g., "http://localhost:8000")
        auth_token: Optional Bearer token for authentication
    """

    def __init__(self, base_url: str = "http://localhost:8000", auth_token: str = ""):
        self.base_url = base_url.rstrip("/")
        self.api_base = f"{self.base_url}/api/v1/agentos"
        self.auth_token = auth_token
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import httpx

                headers = {}
                if self.auth_token:
                    headers["Authorization"] = f"Bearer {self.auth_token}"
                self._client = httpx.Client(headers=headers, timeout=30.0)
            except ImportError:
                import subprocess

                self._client = None
                logger.warning("httpx not available, using curl fallback")
        return self._client

    def _request(self, method: str, path: str, body: dict = None) -> dict:
        """Make an API request."""
        url = f"{self.api_base}{path}"
        client = self._get_client()
        if client:
            try:
                if method == "GET":
                    r = client.get(url)
                elif method == "POST":
                    r = client.post(url, json=body)
                elif method == "PATCH":
                    r = client.patch(url, json=body)
                elif method == "DELETE":
                    r = client.delete(url)
                else:
                    raise ValueError(f"Unsupported method: {method}")
                r.raise_for_status()
                return r.json()
            except Exception as e:
                logger.error(f"API request failed: {e}")
                return {"error": str(e)}
        else:
            import subprocess, json as _json

            cmd = [
                "curl",
                "-s",
                "-X",
                method,
                url,
                "-H",
                "Content-Type: application/json",
            ]
            if body:
                cmd.extend(["-d", _json.dumps(body)])
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            try:
                return _json.loads(r.stdout)
            except:
                return {"error": r.stdout}

    # ═══════════════════════════════════════════════════════════════
    # Health
    # ═══════════════════════════════════════════════════════════════

    def health(self) -> dict:
        """Check server health."""
        url = f"{self.base_url}/health"
        client = self._get_client()
        if client:
            try:
                r = client.get(url)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                return {"error": str(e)}
        else:
            import subprocess, json as _json

            r = subprocess.run(["curl", "-s", url], capture_output=True, text=True, timeout=10)
            try:
                return _json.loads(r.stdout)
            except:
                return {"error": r.stdout}

    # ═══════════════════════════════════════════════════════════════
    # Actor Management
    # ═══════════════════════════════════════════════════════════════

    def create_actor(
        self,
        name: str,
        objective: str = "",
        goals: list[str] = None,
        actor_type: str = "ai_agent",
        **kwargs,
    ) -> ActorInfo:
        """Create an actor through the API.

        If an actor with this name already exists, returns the existing actor.
        To create a new actor, use a unique name or update the existing one.
        """
        body = {"name": name, "actor_type": actor_type, "objective": objective}
        if goals:
            body["goals"] = goals
        body.update(kwargs)
        r = self._request("POST", "/actors", body)
        result = ActorInfo.from_dict(r)

        # If objective was specified but not returned (existing actor), update it
        if objective and not result.objective:
            result = self.update_actor(result.actor_id, objective=objective, goals=goals or [])

        return result

    def get_actor(self, actor_id: str) -> ActorInfo:
        """Get actor information."""
        r = self._request("GET", f"/actors/{actor_id}")
        return ActorInfo.from_dict(r)

    def list_actors(self) -> list[ActorInfo]:
        """List all actors."""
        r = self._request("GET", "/planet/actors")
        return [ActorInfo.from_dict(a) for a in r]

    def update_actor(self, actor_id: str, **kwargs) -> ActorInfo:
        """Update actor properties."""
        r = self._request("PATCH", f"/actors/{actor_id}", kwargs)
        return ActorInfo.from_dict(r)

    def delete_actor(self, actor_id: str) -> bool:
        """Delete an actor."""
        r = self._request("DELETE", f"/actors/{actor_id}")
        return r.get("deleted", False)

    def tick(self, actor_id: str, goal: str = "", start: str = "") -> TickResult:
        """Tick an actor and return the result."""
        r = self._request(
            "POST",
            f"/actors/{actor_id}/tick",
            {"start": start, "goal": goal, "reward": 1.0},
        )
        return TickResult.from_dict(r)

    # ═══════════════════════════════════════════════════════════════
    # Planet Management
    # ═══════════════════════════════════════════════════════════════

    def planet_status(self) -> dict:
        """Get planet status."""
        return self._request("GET", "/planet/status")

    def planet_statistics(self) -> dict:
        """Get planet statistics."""
        return self._request("GET", "/planet/statistics")

    def planet_tick(self) -> dict:
        """Run a planetary cycle."""
        return self._request("POST", "/planet/tick")

    def planet_refresh(self) -> dict:
        """Refresh world state from Redis."""
        return self._request("POST", "/planet/refresh")

    def planet_actors(self) -> list[ActorInfo]:
        """Get all actors across all societies."""
        r = self._request("GET", "/planet/actors")
        return [ActorInfo.from_dict(a) for a in r]

    # ═══════════════════════════════════════════════════════════════
    # Tuning
    # ═══════════════════════════════════════════════════════════════

    def get_tuning(self) -> dict:
        """Get current pipeline tuning configuration."""
        return self._request("GET", "/tuning")

    def update_tuning(self, **kwargs) -> dict:
        """Update pipeline tuning (hot-reload)."""
        return self._request("POST", "/tuning", kwargs)

    # ═══════════════════════════════════════════════════════════════
    # Societies
    # ═══════════════════════════════════════════════════════════════

    def list_societies(self) -> list[dict]:
        """List all societies."""
        return self._request("GET", "/societies")

    def tick_society(self, society_id: str) -> dict:
        """Run a society tick."""
        return self._request("POST", f"/societies/{society_id}/tick")

    def add_policy(self, society_id: str, policy: str) -> dict:
        """Add a policy to a society."""
        return self._request("POST", f"/societies/{society_id}/policies", {"policy": policy})
