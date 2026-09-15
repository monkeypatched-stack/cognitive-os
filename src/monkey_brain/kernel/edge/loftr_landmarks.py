"""LoFTR-backed matching of sampled drone frames to registered landmarks.

The module is deliberately independent of LiveKit, ROS, planning, and
governance.  ``LoFTRMatcher`` is the optional model adapter; the registry and
confirmation layer operate on its small ``match`` interface and are therefore
straightforward to test without a GPU or model weights.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from src.introspection.otel_bridge import get_bridge
from src.monkey_brain.kernel.pipeline.observations import Observation, Provenance

logger = logging.getLogger("agentos.edge.loftr_landmarks")


@dataclass(frozen=True)
class LandmarkReference:
    landmark_id: str
    name: str
    image: Any
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MatchResult:
    landmark_id: str = ""
    candidate_matches: int = 0
    geometric_inliers: int = 0
    inlier_ratio: float = 0.0
    verified: bool = False
    score: float = 0.0
    reference_name: str = ""
    confidence_mean: float = 0.0
    homography: Any = None


class VisualMatcher(Protocol):
    def match(self, query_frame: Any, reference_image: Any) -> MatchResult: ...


class LoFTRUnavailableError(RuntimeError):
    pass


class LoFTRMatcher:
    """Lazy Kornia LoFTR adapter.  Model-specific details stop here."""

    def __init__(self, *, device: str = "auto", min_confidence: float = 0.0) -> None:
        self.device = device
        self.min_confidence = min_confidence
        self._model: Any = None
        self._lock = threading.Lock()

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        try:
            import torch
            from kornia.feature import LoFTR
        except ImportError as exc:
            raise LoFTRUnavailableError("LoFTR requires torch and kornia") from exc
        target = "cuda" if self.device == "auto" and torch.cuda.is_available() else self.device
        if target == "auto":
            target = "cpu"
        with self._lock:
            if self._model is None:
                self._model = LoFTR(pretrained="outdoor").eval().to(target)

    def match(self, query_frame: Any, reference_image: Any) -> MatchResult:
        self._ensure_loaded()
        import torch

        query = _gray_tensor(query_frame).to(next(self._model.parameters()).device)
        reference = _gray_tensor(reference_image).to(query.device)
        with torch.inference_mode():
            out = self._model({"image0": query, "image1": reference})
        confidence = out.get("confidence", torch.ones(len(out["keypoints0"]))).detach().cpu().numpy()
        keep = confidence >= self.min_confidence
        p0 = out["keypoints0"].detach().cpu().numpy()[keep]
        p1 = out["keypoints1"].detach().cpu().numpy()[keep]
        return _verify_correspondences(p0, p1, confidence[keep])


def _gray_tensor(image: Any) -> Any:
    import torch

    if hasattr(image, "convert"):
        image = np.asarray(image.convert("L"))
    arr = np.asarray(image)
    if arr.ndim == 3:
        arr = arr[..., :3].mean(axis=2)
    if arr.ndim != 2 or arr.size == 0:
        raise ValueError("reference/query image must be a non-empty HxW or HxWxC array")
    return torch.from_numpy(arr.astype("float32") / 255.0)[None, None]


def _verify_correspondences(
    p0: np.ndarray, p1: np.ndarray, confidence: np.ndarray, *, ransac_threshold: float = 5.0
) -> MatchResult:
    candidates = len(p0)
    if candidates < 4:
        return MatchResult(
            candidate_matches=candidates, confidence_mean=float(confidence.mean()) if candidates else 0.0
        )
    try:
        import cv2

        homography, mask = cv2.findHomography(p0, p1, cv2.RANSAC, ransac_threshold)
    except ImportError:
        return MatchResult(candidate_matches=candidates, confidence_mean=float(confidence.mean()))
    except cv2.error:
        return MatchResult(candidate_matches=candidates, confidence_mean=float(confidence.mean()))
    inliers = int(mask.sum()) if mask is not None else 0
    ratio = inliers / candidates
    return MatchResult(
        candidate_matches=candidates,
        geometric_inliers=inliers,
        inlier_ratio=ratio,
        confidence_mean=float(confidence.mean()),
        homography=homography,
    )


class LandmarkMatcher:
    """Matches a frame against a small explicit reference set."""

    def __init__(
        self,
        references: list[LandmarkReference],
        matcher: VisualMatcher | None = None,
        *,
        min_matches: int = 12,
        min_inliers: int = 8,
        min_inlier_ratio: float = 0.45,
        min_score: float = 0.55,
        confirmations: int = 1,
        actor_id: str = "",
    ) -> None:
        self.references = tuple(references)
        self.matcher = matcher or LoFTRMatcher()
        self.min_matches, self.min_inliers = min_matches, min_inliers
        self.min_inlier_ratio, self.min_score = min_inlier_ratio, min_score
        self.confirmations = max(1, confirmations)
        self.actor_id = actor_id
        self._history: dict[str, deque[bool]] = defaultdict(lambda: deque(maxlen=self.confirmations))

    def match_frame(self, frame: Any) -> list[MatchResult]:
        best: dict[str, MatchResult] = {}
        for ref in self.references:
            try:
                with get_bridge().span("visual_match", layer="perception", landmark_id=ref.landmark_id):
                    result = self.matcher.match(frame, ref.image)
            except Exception:
                logger.exception("landmark match failed for %s", ref.landmark_id)
                continue
            result = MatchResult(
                ref.landmark_id,
                result.candidate_matches,
                result.geometric_inliers,
                result.inlier_ratio,
                result.verified,
                result.score,
                ref.name,
                result.confidence_mean,
                result.homography,
            )
            score = min(1.0, max(0.0, 0.5 * result.inlier_ratio + 0.5 * result.confidence_mean))
            result = MatchResult(
                **{
                    **result.__dict__,
                    "score": score,
                    "verified": result.candidate_matches >= self.min_matches
                    and result.geometric_inliers >= self.min_inliers
                    and result.inlier_ratio >= self.min_inlier_ratio
                    and score >= self.min_score,
                }
            )
            if result.verified and (result.landmark_id not in best or result.score > best[result.landmark_id].score):
                best[result.landmark_id] = result
        return list(best.values())

    def observations(self, frame: Any) -> tuple[Observation, ...]:
        observations = []
        for result in self.match_frame(frame):
            history = self._history[result.landmark_id]
            history.append(True)
            if len(history) < self.confirmations:
                continue
            observations.append(
                Observation(
                    entity=self.actor_id,
                    attribute="visual_landmark_match",
                    value={
                        "landmark_id": result.landmark_id,
                        "match_score": result.score,
                        "inlier_ratio": result.inlier_ratio,
                        "geometric_verified": True,
                    },
                    confidence=result.score,
                    provenance=Provenance(
                        source="drone_camera",
                        method="loftr_geometric_match",
                        reliability=result.score,
                        metadata={
                            "candidate_matches": result.candidate_matches,
                            "geometric_inliers": result.geometric_inliers,
                            "reference_name": result.reference_name,
                        },
                    ),
                )
            )
        return tuple(observations)


def load_landmarks(directory: str | Path) -> list[LandmarkReference]:
    """Load ``<landmark_id>/*.(jpg|jpeg|png)`` references once at startup."""
    try:
        from PIL import Image
    except ImportError as exc:
        raise LoFTRUnavailableError("landmark loading requires Pillow") from exc
    root = Path(directory)
    refs = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() in {".jpg", ".jpeg", ".png"}:
                try:
                    refs.append(
                        LandmarkReference(
                            folder.name, folder.name, Image.open(path).convert("RGB"), {"path": str(path)}
                        )
                    )
                except Exception:
                    logger.warning("invalid landmark reference: %s", path)
    return refs
