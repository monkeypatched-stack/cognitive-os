"""Configuration and process-wide singleton loading for visual landmark
matching (kernel/edge/loftr_landmarks.py).

Follows this repo's established settings convention (kernel/account.py's
plain dataclasses with a to_dict(), not pydantic-settings) and its
established "load an expensive model once, reuse across sessions" pattern
(kernel/edge/visual_perception.py::VisualPerceptionEngine,
kernel/plan/embedding/image.py::CLIPImageEmbedder).

Landmark matching is fully opt-in: if LANDMARK_REFERENCE_DIR is unset, or
reference/model loading fails for any reason, get_shared_landmark_resources()
returns None and every caller (video_command_runtime.py) falls back to no
landmark matching for that session -- never a boot failure.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Any

from src.monkey_brain.kernel.edge.loftr_landmarks import (
    LandmarkReference,
    LoFTRMatcher,
    LoFTRUnavailableError,
    load_landmarks,
)

logger = logging.getLogger("agentos.edge.landmark_config")


@dataclass(frozen=True)
class LandmarkMatcherConfig:
    reference_directory: str
    min_matches: int = 12
    min_inliers: int = 8
    min_inlier_ratio: float = 0.45
    min_score: float = 0.55
    confirmations: int = 1
    max_confirmation_gap_seconds: float = 10.0
    device: str = "auto"

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference_directory": self.reference_directory,
            "min_matches": self.min_matches,
            "min_inliers": self.min_inliers,
            "min_inlier_ratio": self.min_inlier_ratio,
            "min_score": self.min_score,
            "confirmations": self.confirmations,
            "max_confirmation_gap_seconds": self.max_confirmation_gap_seconds,
            "device": self.device,
        }


def _env_number(name: str, default: float, cast: type) -> Any:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return cast(raw)
    except (TypeError, ValueError):
        logger.warning("landmark_config: invalid %s=%r, using default %r", name, raw, default)
        return default


def load_landmark_config_from_env() -> LandmarkMatcherConfig | None:
    """None means landmark matching is disabled -- the normal state for a
    deployment that hasn't been given a reference directory yet."""
    reference_directory = os.environ.get("LANDMARK_REFERENCE_DIR", "").strip()
    if not reference_directory:
        return None
    defaults = LandmarkMatcherConfig(reference_directory=reference_directory)
    return LandmarkMatcherConfig(
        reference_directory=reference_directory,
        min_matches=_env_number("LANDMARK_MIN_MATCHES", defaults.min_matches, int),
        min_inliers=_env_number("LANDMARK_MIN_INLIERS", defaults.min_inliers, int),
        min_inlier_ratio=_env_number("LANDMARK_MIN_INLIER_RATIO", defaults.min_inlier_ratio, float),
        min_score=_env_number("LANDMARK_MIN_SCORE", defaults.min_score, float),
        confirmations=_env_number("LANDMARK_CONFIRMATIONS", defaults.confirmations, int),
        max_confirmation_gap_seconds=_env_number(
            "LANDMARK_MAX_CONFIRMATION_GAP_SECONDS", defaults.max_confirmation_gap_seconds, float
        ),
        device=os.environ.get("LANDMARK_DEVICE", defaults.device).strip() or defaults.device,
    )


_lock = threading.Lock()
_loaded = False
_resources: tuple[tuple[LandmarkReference, ...], LoFTRMatcher, LandmarkMatcherConfig] | None = None


def get_shared_landmark_resources() -> tuple[tuple[LandmarkReference, ...], LoFTRMatcher, LandmarkMatcherConfig] | None:
    """Process-wide singleton: reference images are loaded from disk and the
    LoFTRMatcher model wrapper is constructed exactly once, then reused by
    every VideoCommandRuntime session for the life of this process. Never
    raises -- a config/load failure just means no landmark matching."""
    global _loaded, _resources
    with _lock:
        if _loaded:
            return _resources
        _loaded = True
        config = load_landmark_config_from_env()
        if config is None:
            return None
        try:
            references = tuple(load_landmarks(config.reference_directory))
        except (LoFTRUnavailableError, OSError):
            logger.warning(
                "landmark_config: failed to load references from %s", config.reference_directory, exc_info=True
            )
            return None
        if not references:
            logger.warning("landmark_config: no landmark references found under %s", config.reference_directory)
            return None
        matcher = LoFTRMatcher(device=config.device)
        _resources = (references, matcher, config)
        logger.info(
            "landmark_config: loaded %d reference(s) across %d landmark(s), config=%s",
            len(references),
            len({ref.landmark_id for ref in references}),
            config.to_dict(),
        )
        return _resources
