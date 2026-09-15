"""Visual perception primitive (CognitiveOS Drone Camera Video Telemetry,
spec section "PERCEPTION PROVIDER" / "Do NOT fabricate VLM/CV capabilities
that do not exist in the repository").

What this repo actually has, found by inspection before writing this file:
no object detector, no bounding-box localizer, no image-captioning model,
no fine-tuned classifier anywhere in the codebase. What it DOES have is a
real CLIP encoder pair already wired for exactly this kind of comparison --
kernel/plan/embedding/image.py::CLIPImageEmbedder and kernel/plan/embedding/
text.py::CLIPTextEmbedder, both `openai/clip-vit-base-patch32` via
HuggingFace transformers, sharing the same random-projection seed (2025) so
"image and text embeddings are in the same latent space and directly
comparable" (that module's own docstring). That shared-latent-space
property is a real, standard CLIP capability: zero-shot classification by
comparing an image embedding's cosine similarity against a small set of
candidate text prompts. This module is that comparison, nothing more.

Deliberately NOT implemented, because nothing in the repository supports
them without fabricating new ML capability: vehicle_detected, target_detected,
waypoint_visible, landing_zone_detected (kept: landing_zone_clear, the one
directional binary a similarity threshold can support), visual_target_position
(needs real localization/bounding boxes -- CLIP embedding similarity has no
notion of WHERE in the frame something is), scene_description (needs a
captioning model, not a similarity score), visual_anomaly (needs a baseline
model of "normal" trained on real footage, not available here).

Confidence caveat, stated plainly rather than presented as calibrated: the
similarity score below is cosine similarity in a 32-dim random-projected,
tanh-compressed space (not raw CLIP's own 512-dim space), and CLIPImageEmbedder/
CLIPTextEmbedder mix in a small amount of unrelated provenance/freshness
metadata into that projection. It is a real, working zero-shot signal, not a
validated detector -- do not read `confidence=0.83` as "the model is 83%
sure a real person is present" the way a trained detector's score would mean.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np

from src.introspection.otel_bridge import get_bridge

logger = logging.getLogger("agentos.edge.visual_perception")

# Only the observation types this module can actually produce without
# fabricating capability -- see module docstring for what was left out and
# why. Each prompt is what gets CLIP-text-embedded and compared against the
# frame's CLIP-image-embedding; PRESENCE_THRESHOLD is a similarity floor
# below which nothing is reported (silence, not a fabricated low-confidence
# detection).
_CANDIDATE_PROMPTS: dict[str, str] = {
    "obstacle_detected": "a photo of an obstacle blocking the path ahead",
    "person_detected": "a photo of a person",
    "landing_zone_clear": "a photo of a clear, open, empty landing area with no obstacles",
}
PRESENCE_THRESHOLD = 0.15


@dataclass(frozen=True)
class VisualPerceptionResult:
    attribute: str
    value: Any
    confidence: float
    method: str = "clip_zero_shot_similarity"


class VisualPerceptionUnavailableError(RuntimeError):
    """Raised when the underlying CLIP embedders cannot run (transformers/
    torch not installed, or model weights not fetchable) -- callers must
    treat this the same as a transient camera failure (spec: "never crash
    the Actor runtime because a frame or inference fails"), never as a
    reason to fabricate a result."""


class VisualPerceptionEngine:
    """Thin, stateful wrapper so the (slow-to-load) CLIP model and the
    fixed prompt embeddings are loaded once, not per frame. One instance is
    shared across all sampled frames for a session -- construction is
    cheap; `_ensure_loaded()` (the actual model load) is lazy and happens
    once, on the first real frame, off whatever thread calls `classify()`
    (the caller is responsible for running this off the realtime video
    thread -- see kernel/edge/livekit_video_adapter.py's own executor use,
    mirroring LiveKitVoiceObservationProvider's run_in_executor pattern for
    Whisper)."""

    def __init__(self, prompts: dict[str, str] | None = None, threshold: float = PRESENCE_THRESHOLD) -> None:
        self._prompts = dict(prompts or _CANDIDATE_PROMPTS)
        self._threshold = threshold
        self._image_embedder: Any = None
        self._prompt_vectors: dict[str, np.ndarray] | None = None

    def _ensure_loaded(self) -> None:
        if self._image_embedder is not None and self._prompt_vectors is not None:
            return
        from src.monkey_brain.kernel.plan.embedding.image import CLIPImageEmbedder
        from src.monkey_brain.kernel.plan.embedding.text import CLIPTextEmbedder

        text_embedder = CLIPTextEmbedder()
        self._prompt_vectors = {attr: text_embedder.embed(prompt).vector for attr, prompt in self._prompts.items()}
        self._image_embedder = CLIPImageEmbedder()

    def classify(self, frame: Any) -> list[VisualPerceptionResult]:
        """`frame` is anything kernel/plan/embedding/image.py::load_pil
        already accepts (PIL.Image, np.ndarray HxWxC, raw bytes). Returns
        one result per candidate prompt whose similarity clears the
        threshold -- may be empty (nothing recognized this frame, which is
        the common case, not an error). Never raises on a per-frame
        failure; raises VisualPerceptionUnavailableError only if the model
        itself cannot be loaded at all (caller decides how to degrade)."""
        # One real OTel span per classify() call -- this already runs at
        # perception_fps (kernel/edge/livekit_video_adapter.py samples
        # before calling this, never at camera FPS), so this is "a
        # meaningful event", not a per-frame one. A span's own start/end
        # timestamps ARE "started"/"completed" (spec's two separate event
        # names) -- modeled as one real span with a status, not two
        # synthetic zero-width point events, since that is what a span
        # already natively represents; "failed" is the same span's
        # exception path (get_bridge().span() records the exception and
        # sets ERROR status on raise, see otel_bridge.py). No-op with zero
        # overhead when OTEL_EXPORTER_OTLP_ENDPOINT is not set (see that
        # module's own docstring) -- this never requires OTel infra to be
        # running.
        with get_bridge().span("visual_perception", layer="perception") as span:
            try:
                self._ensure_loaded()
            except Exception as exc:  # noqa: BLE001
                get_bridge().emit_counter("visual_perception.failed", reason="load_failed")
                raise VisualPerceptionUnavailableError(str(exc)) from exc

            try:
                image_vec = self._image_embedder.embed(frame).vector
            except Exception:
                logger.exception("visual_perception: frame embedding failed")
                span.set_attribute("outcome", "embedding_failed")
                get_bridge().emit_counter("visual_perception.failed", reason="embedding_failed")
                return []

            results: list[VisualPerceptionResult] = []
            for attribute, prompt_vec in (self._prompt_vectors or {}).items():
                similarity = _cosine_similarity(image_vec, prompt_vec)
                if similarity >= self._threshold:
                    results.append(
                        VisualPerceptionResult(
                            attribute=attribute,
                            value=True,
                            confidence=max(0.0, min(1.0, similarity)),
                        )
                    )

            # visual_observation_emitted (spec's own event name) as an
            # attribute on this same span rather than a second span per
            # classify() call -- queryable (count, attribute names), no
            # raw frame data, no per-attribute span spam.
            span.set_attribute("outcome", "ok")
            span.set_attribute("observations_emitted", len(results))
            if results:
                span.set_attribute("attributes_detected", ",".join(r.attribute for r in results))
                get_bridge().emit_counter("visual_observation.emitted", value=len(results))
            return results


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom < 1e-8:
        return 0.0
    return float(np.dot(a, b) / denom)
