"""Capability Bandit for SDK.

UCB1 bandit that learns which adapter performs best for each capability.
Adapters register. Bandit selects. Reward updates. Next time exploits best path.
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

UCB_C = 1.4


@dataclass
class AdapterState:
    adapter_id: str
    capability: str
    pull_count: int = 0
    reward_sum: float = 0.0
    avg_reward: float = 0.0
    last_reward: float | None = None


class CapabilityBandit:
    """UCB1 bandit for adapter selection per capability.

    Learns which adapter performs best for each capability.
    Routes more traffic to the best adapter over time.
    """

    def __init__(self, redis: Any = None):
        self._redis = redis
        self._prefix = "cap_bandit:adapter:"
        self._reward_log = "cap_bandit:rewards"

    def _key(self, adapter_id: str, capability: str) -> str:
        return f"{self._prefix}{adapter_id}:{capability}"

    async def get_state(self, adapter_id: str, capability: str) -> AdapterState:
        if self._redis:
            try:
                data = self._redis.get(self._key(adapter_id, capability))
                if data:
                    return AdapterState(**json.loads(data))
            except Exception:
                pass
        return AdapterState(adapter_id=adapter_id, capability=capability)

    async def update_state(self, adapter_id: str, capability: str, reward: float) -> AdapterState:
        state = await self.get_state(adapter_id, capability)
        state.pull_count += 1
        state.reward_sum += reward
        state.avg_reward = state.reward_sum / state.pull_count
        state.last_reward = reward

        if self._redis:
            try:
                self._redis.set(
                    self._key(adapter_id, capability),
                    json.dumps(
                        {
                            "adapter_id": state.adapter_id,
                            "capability": state.capability,
                            "pull_count": state.pull_count,
                            "reward_sum": state.reward_sum,
                            "avg_reward": state.avg_reward,
                            "last_reward": state.last_reward,
                        }
                    ),
                )
            except Exception:
                pass

        return state

    def _ucb1(self, avg_reward: float, pull_count: int, total_pulls: int) -> float:
        if pull_count == 0:
            return 999.0
        return avg_reward + UCB_C * math.sqrt(math.log(total_pulls + 1) / (pull_count + 1))

    async def select_adapter(self, capability: str, adapters: list[str]) -> str:
        """Select best adapter for a capability using UCB1."""
        total_pulls = 0
        scores: dict[str, float] = {}

        for adapter_id in adapters:
            state = await self.get_state(adapter_id, capability)
            total_pulls += state.pull_count
            scores[adapter_id] = self._ucb1(state.avg_reward, state.pull_count, total_pulls)

        if not scores:
            return adapters[0] if adapters else ""

        return max(scores, key=scores.get)

    async def record_reward(self, adapter_id: str, capability: str, reward: float) -> dict:
        """Record reward and update bandit state."""
        state = await self.update_state(adapter_id, capability, reward)

        if self._redis:
            try:
                self._redis.rpush(
                    self._reward_log,
                    json.dumps(
                        {
                            "adapter_id": adapter_id,
                            "capability": capability,
                            "reward": reward,
                            "timestamp": time.time(),
                        }
                    ),
                )
                self._redis.ltrim(self._reward_log, -500, -1)
            except Exception:
                pass

        return {
            "adapter_id": adapter_id,
            "capability": capability,
            "reward": reward,
            "avg_reward": state.avg_reward,
            "pull_count": state.pull_count,
        }

    async def summary(self) -> dict[str, Any]:
        caps: dict[str, dict] = {}
        if self._redis:
            try:
                keys = self._redis.keys(f"{self._prefix}*")
                for key in keys:
                    data = self._redis.get(key)
                    if data:
                        d = json.loads(data)
                        cap = d.get("capability", "")
                        caps.setdefault(cap, {})[d.get("adapter_id", "")] = {
                            "pull_count": d.get("pull_count", 0),
                            "avg_reward": round(d.get("avg_reward", 0.0), 4),
                        }
            except Exception:
                pass
        return {"capabilities": caps}
