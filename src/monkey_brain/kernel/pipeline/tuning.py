"""PipelineTuning — single source of truth for all pipeline parameters.

Collects every tunable knob across the cognitive lifecycle into one
configurable object, with Redis persistence and hot-reload support.

Tunable stages:
    1. Reward         — goal/speed/budget/error weights
    2. Learning       — strategy, confidence threshold, conservative gate
    3. Prediction     — rejection threshold, time horizon, counterfactual depth
    4. Risk           — success/failure utility, low-probability threshold
    5. Comparison     — world/policy loss weights
    6. Execution      — timeouts, retry limits
    7. Auto-tick      — tick interval, cycle timeout

Usage:
    tuning = PipelineTuning()
    await tuning.load()                    # from Redis
    tuning.reward.goal_achieved = 0.8      # hot-reload a weight
    await tuning.save()                    # persist back to Redis
    config = tuning.to_dict()              # export for API / logging
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass
from typing import Any

from src.monkey_brain.kernel.pipeline.prediction.scenarios import (
    DEFAULT_REJECTION_THRESHOLD,
)

logger = logging.getLogger("agentos.pipeline.tuning")

REDIS_KEY = "monkeybrain:pipeline_tuning"

# Module-level singleton + lock. Declared here rather than lazily via a
# try/except-NameError read in get_tuning(): the previous pattern had no
# lock, so two threads racing the first call could each construct their
# own PipelineTuning (one silently discarded) and each fire a Redis GET.
_TUNING_LOCK = threading.Lock()
_tuning_singleton: PipelineTuning | None = None
_tuning_loaded = False


@dataclass
class RewardConfig:
    goal_achieved: float = 0.7
    partial_credit: float = 0.3
    speed_bonus: float = 0.2
    budget_bonus: float = 0.1
    error_penalty: float = 0.15


@dataclass
class LearningConfig:
    strategy: str = "reinforcement"
    confidence_threshold: float = 0.3
    belief_update_rate: float = 1.0
    world_update_rate: float = 1.0
    decay_rate: float = 0.05
    hypothesis_prune_threshold: float = 0.2
    fact_prune_threshold: float = 0.1
    max_hypotheses: int = 50


@dataclass
class PredictionConfig:
    # Real gap this closed: this was an independent 0.3 literal, never
    # synced with scenarios.py::DEFAULT_REJECTION_THRESHOLD -- and
    # apply_to_policy() below unconditionally overwrites a policy's own
    # _rejection_threshold with THIS value on every single tick
    # (belief_formation.py's run() calls it every cycle), so a fix made
    # anywhere else in the prediction pipeline (scenarios.py, policies.py,
    # comparison/integration.py's constructors) was silently reverted the
    # next tick regardless. Confirmed live: an actor's every multi-step
    # plan kept getting rejected at "below acceptance threshold 30%" even
    # after every other rejection_threshold default in the codebase was
    # lowered to 0.15. THIS is the real, live-authoritative value.
    rejection_threshold: float = DEFAULT_REJECTION_THRESHOLD
    time_horizon: float = 0.0
    counterfactual_depth: int = 1
    enable_mcts: bool = False
    mcts_iterations: int = 100


@dataclass
class RiskConfig:
    utility_if_success: float = 1.0
    utility_if_failure: float = -1.0
    low_probability_threshold: float = 0.2


@dataclass
class ComparisonConfig:
    world_loss_weight: float = 0.7
    policy_loss_weight: float = 0.3
    topology_weight: float = 0.5
    epistemic_weight: float = 0.5


@dataclass
class ExecutionConfig:
    actor_tick_timeout: float = 60.0
    stage_timeout: float = 30.0
    max_retries: int = 3
    failure_rate: float = 0.0


@dataclass
class AutoTickConfig:
    interval_seconds: float = 300.0
    cycle_timeout: float = 300.0
    enabled: bool = True


class PipelineTuning:
    """Central configuration for all pipeline parameters.

    Each sub-config maps to one pipeline stage's tunable knobs.
    Changes take effect on the next tick (hot-reload) — no restart needed.
    """

    def __init__(self) -> None:
        self.reward = RewardConfig()
        self.learning = LearningConfig()
        self.prediction = PredictionConfig()
        self.risk = RiskConfig()
        self.comparison = ComparisonConfig()
        self.execution = ExecutionConfig()
        self.auto_tick = AutoTickConfig()

        # Override from env vars
        self._apply_env_overrides()

    def _apply_env_overrides(self) -> None:
        """Apply environment variable overrides."""
        if v := os.getenv("AGENTOS_TICK_INTERVAL"):
            self.auto_tick.interval_seconds = float(v)
        if v := os.getenv("AGENTOS_CYCLE_TIMEOUT"):
            self.auto_tick.cycle_timeout = float(v)
        if v := os.getenv("AGENTOS_REJECTION_THRESHOLD"):
            self.prediction.rejection_threshold = float(v)
        if v := os.getenv("AGENTOS_LEARNING_STRATEGY"):
            self.learning.strategy = v
        if v := os.getenv("AGENTOS_CONFIDENCE_THRESHOLD"):
            self.learning.confidence_threshold = float(v)
        if v := os.getenv("AGENTOS_ACTOR_TICK_TIMEOUT"):
            self.execution.actor_tick_timeout = float(v)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reward": asdict(self.reward),
            "learning": asdict(self.learning),
            "prediction": asdict(self.prediction),
            "risk": asdict(self.risk),
            "comparison": asdict(self.comparison),
            "execution": asdict(self.execution),
            "auto_tick": asdict(self.auto_tick),
        }

    def apply_to_policy(self, policy: Any) -> None:
        """Apply tuning to a CognitivePolicy (ComparisonIntegratedPolicy, etc.)."""
        # Replace frozen LearningPolicy with new instance using tuned values
        if hasattr(policy, "_learning_policy"):
            from src.monkey_brain.kernel.pipeline.learning.domain import LearningPolicy

            policy._learning_policy = LearningPolicy(
                strategy=self.learning.strategy,
                parameters={"confidence_threshold": self.learning.confidence_threshold},
            )

        # Apply prediction config
        if hasattr(policy, "_rejection_threshold"):
            policy._rejection_threshold = self.prediction.rejection_threshold
        if hasattr(policy, "_time_horizon"):
            policy._time_horizon = self.prediction.time_horizon

    def apply_to_reward_engine(self, engine: Any) -> None:
        """Apply tuning to an ExperienceRewardEngine."""
        from src.monkey_brain.kernel.pipeline.learning.reward import RewardWeights

        if hasattr(engine, "_weights"):
            object.__setattr__(
                engine,
                "_weights",
                RewardWeights(
                    goal_achieved=self.reward.goal_achieved,
                    partial_credit=self.reward.partial_credit,
                    speed_bonus=self.reward.speed_bonus,
                    budget_bonus=self.reward.budget_bonus,
                    error_penalty=self.reward.error_penalty,
                ),
            )

    def apply_to_risk_engine(self, engine: Any) -> None:
        """Apply tuning to a RiskEngine."""
        if hasattr(engine, "_utility_if_success"):
            object.__setattr__(engine, "_utility_if_success", self.risk.utility_if_success)
        if hasattr(engine, "_utility_if_failure"):
            object.__setattr__(engine, "_utility_if_failure", self.risk.utility_if_failure)
        if hasattr(engine, "_low_probability_threshold"):
            object.__setattr__(
                engine,
                "_low_probability_threshold",
                self.risk.low_probability_threshold,
            )

    def apply_to_executor(self, executor: Any) -> None:
        """Apply tuning to an ActionExecutor."""
        if hasattr(executor, "_failure_rate"):
            object.__setattr__(executor, "_failure_rate", self.execution.failure_rate)

    def decay_and_prune(self, belief_state: Any) -> dict[str, int]:
        """Apply belief decay and hypothesis pruning to a BeliefState."""
        return belief_state.decay_and_prune(
            decay_rate=self.learning.decay_rate,
            hypothesis_prune_threshold=self.learning.hypothesis_prune_threshold,
            fact_prune_threshold=self.learning.fact_prune_threshold,
            max_hypotheses=self.learning.max_hypotheses,
        )

    async def save(self) -> None:
        """Persist tuning to Redis."""
        try:
            import redis as _redis

            r = _redis.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                decode_responses=True,
            )
            r.set(REDIS_KEY, json.dumps(self.to_dict()))
            logger.info("Pipeline tuning saved to Redis")
        except Exception as e:
            logger.warning("Failed to save pipeline tuning: %s", e)

    async def load(self) -> bool:
        """Load tuning from Redis. Returns True if loaded successfully."""
        try:
            import redis as _redis

            r = _redis.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                decode_responses=True,
            )
            data = r.get(REDIS_KEY)
            if not data:
                return False
            config = json.loads(data)
            self._from_dict(config)
            logger.info("Pipeline tuning loaded from Redis")
            return True
        except Exception as e:
            logger.warning("Failed to load pipeline tuning: %s", e)
            return False

    def _from_dict(self, data: dict[str, Any]) -> None:
        """Apply a config dict to this instance."""
        from dataclasses import fields as dc_fields

        config_map = {
            "reward": (self.reward, RewardConfig),
            "learning": (self.learning, LearningConfig),
            "prediction": (self.prediction, PredictionConfig),
            "risk": (self.risk, RiskConfig),
            "comparison": (self.comparison, ComparisonConfig),
            "execution": (self.execution, ExecutionConfig),
            "auto_tick": (self.auto_tick, AutoTickConfig),
        }
        for section_name, section_data in data.items():
            if section_name not in config_map:
                continue
            target, cls_ = config_map[section_name]
            valid_fields = {f.name for f in dc_fields(cls_)}
            for key, value in section_data.items():
                if key in valid_fields:
                    setattr(target, key, value)


def get_tuning() -> PipelineTuning:
    """Module-level singleton. Loads persisted config from Redis on first access.

    Thread-safe: the instance is created and the one-time Redis load is
    performed exactly once even under concurrent first access. A failed
    Redis load is remembered (``_tuning_loaded``) so a later call does not
    re-attempt it on every tick; call ``reload_tuning()`` to force a
    re-read (e.g. an admin hot-reload).
    """
    global _tuning_singleton, _tuning_loaded
    with _TUNING_LOCK:
        if _tuning_singleton is None:
            _tuning_singleton = PipelineTuning()
        if not _tuning_loaded:
            _tuning_loaded = True
            try:
                import redis as _redis

                r = _redis.Redis(
                    host=os.getenv("REDIS_HOST", "localhost"),
                    port=int(os.getenv("REDIS_PORT", "6379")),
                    decode_responses=True,
                    socket_connect_timeout=1,
                )
                data = r.get(REDIS_KEY)
                if data:
                    _tuning_singleton._from_dict(json.loads(data))
                    logger.info("Pipeline tuning loaded from Redis")
            except Exception:
                logger.debug("get_tuning: suppressed exception", exc_info=True)
        return _tuning_singleton


def reload_tuning() -> PipelineTuning:
    """Force a fresh Redis read for the singleton (admin hot-reload)."""
    global _tuning_loaded
    with _TUNING_LOCK:
        _tuning_loaded = False
    return get_tuning()
