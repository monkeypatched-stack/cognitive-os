"""Unit tests for kernel/edge/livekit_video_adapter.py's
LiveKitVideoObservationProvider -- the wiring point between sampled drone
camera frames and either the LoFTR landmark matcher or the CLIP perception
engine (CognitiveOS Drone Camera Video Telemetry). Never touches real
livekit/torch/kornia/cv2 -- the `livekit` package import is stubbed via
sys.modules, matching this repo's own "guarded import at call time" shape."""

from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.monkey_brain.kernel.edge.livekit_video_adapter import LiveKitVideoObservationProvider
from src.monkey_brain.kernel.pipeline.observations import Observation, Provenance


class _FakeFrame:
    """Stands in for a livekit rtc.VideoFrame -- just enough shape for
    _video_frame_to_array to decode it as a 2x2 RGB image."""

    def __init__(self, width: int = 2, height: int = 2, channels: int = 3):
        self.width = width
        self.height = height
        self.data = bytes(width * height * channels)


def _install_fake_livekit(monkeypatch) -> type:
    """Installs a fake `livekit.rtc.VideoStream` that simply async-iterates
    the frames given at construction, wrapping each as `event.frame`."""

    class _FakeVideoStream:
        def __init__(self, track):
            self._frames = track

        async def __aiter__(self):
            for frame in self._frames:
                yield types.SimpleNamespace(frame=frame)

    fake_rtc = types.SimpleNamespace(VideoStream=_FakeVideoStream)
    fake_livekit = types.ModuleType("livekit")
    fake_livekit.rtc = fake_rtc
    monkeypatch.setitem(sys.modules, "livekit", fake_livekit)
    return _FakeVideoStream


@pytest.mark.asyncio
async def test_classify_frame_uses_landmark_matcher_when_provided():
    fake_observations = (
        Observation(
            entity="drone-a",
            attribute="visual_landmark_match",
            value={"landmark_id": "house_alpha"},
            provenance=Provenance(source="drone_camera", method="loftr_geometric_match"),
        ),
    )
    fake_matcher = MagicMock()
    fake_matcher.observations = MagicMock(return_value=fake_observations)
    provider = LiveKitVideoObservationProvider("room", "track", landmark_matcher=fake_matcher)

    await provider._classify_frame(_FakeFrame())

    fake_matcher.observations.assert_called_once()
    assert list(provider._buffer) == list(fake_observations)
    assert provider._engine is None  # CLIP path never touched


@pytest.mark.asyncio
async def test_classify_frame_falls_back_to_visual_perception_engine_when_no_matcher(monkeypatch):
    fake_engine = MagicMock()
    fake_engine.classify = MagicMock(return_value=[])
    fake_engine_cls = MagicMock(return_value=fake_engine)
    monkeypatch.setattr("src.monkey_brain.kernel.edge.visual_perception.VisualPerceptionEngine", fake_engine_cls)
    provider = LiveKitVideoObservationProvider("room", "track", landmark_matcher=None)

    await provider._classify_frame(_FakeFrame())

    fake_engine_cls.assert_called_once()
    fake_engine.classify.assert_called_once()


@pytest.mark.asyncio
async def test_classify_frame_landmark_matcher_exception_does_not_crash():
    fake_matcher = MagicMock()
    fake_matcher.observations = MagicMock(side_effect=RuntimeError("boom"))
    provider = LiveKitVideoObservationProvider("room", "track", landmark_matcher=fake_matcher)

    await provider._classify_frame(_FakeFrame())  # must not raise

    assert provider._inference_busy.is_set() is False
    assert provider._buffer == []


@pytest.mark.asyncio
async def test_bounded_sampling_drops_frames_faster_than_perception_fps(monkeypatch):
    provider = LiveKitVideoObservationProvider("room", "track", perception_fps=2.0)  # min_interval=0.5s
    provider._classify_frame = AsyncMock()
    _install_fake_livekit(monkeypatch)

    times = iter([10.0, 10.1, 10.6])  # 2nd frame arrives too soon, 3rd doesn't
    monkeypatch.setattr("src.monkey_brain.kernel.edge.livekit_video_adapter.time.monotonic", lambda: next(times))

    await provider._consume_video_track([object(), object(), object()])
    await asyncio.sleep(0)  # let the ensure_future'd classify tasks run

    assert provider._classify_frame.call_count == 2


@pytest.mark.asyncio
async def test_inference_busy_causes_frame_drop_not_queue(monkeypatch):
    provider = LiveKitVideoObservationProvider("room", "track", perception_fps=2.0)
    provider._classify_frame = AsyncMock()
    provider._inference_busy.set()
    _install_fake_livekit(monkeypatch)
    monkeypatch.setattr("src.monkey_brain.kernel.edge.livekit_video_adapter.time.monotonic", lambda: 100.0)

    await provider._consume_video_track([object()])
    await asyncio.sleep(0)

    provider._classify_frame.assert_not_called()
    assert provider._last_sample_time == 0.0  # never advanced -- a still-busy frame is dropped, not queued


def test_camera_disconnect_emits_stream_unavailable_observation():
    provider = LiveKitVideoObservationProvider("room", "track")
    provider._emit_stream_available(True)
    provider._emit_stream_available(False)

    observations = provider.observe("drone-a", None).observations
    values = [(o.attribute, o.value) for o in observations]
    assert ("camera_stream_available", True) in values
    assert ("camera_stream_available", False) in values
