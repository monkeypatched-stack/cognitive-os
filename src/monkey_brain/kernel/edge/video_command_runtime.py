"""Video Command Runtime (CognitiveOS Drone Camera Video Telemetry, spec
section "COGNITIVE LOOP"). Sibling of kernel/edge/voice_command_runtime.py
-- same bridge shape, different destination for what it drains.

    camera -> perception -> Observation -> belief/world update
    -> existing Actor reasoning -> plan/replan -> existing governance
    -> existing execution path.

Voice's own bridge has to fake a "goal" out of a transcript because a
spoken command genuinely IS a goal. A visual observation like
`obstacle_detected=True` is NOT a goal -- it is evidence that should update
belief and let the actor's EXISTING planning re-run with that evidence in
view, exactly what kernel/society/runtime.py::SocietyRuntime.tick_one_actor()
already does on every tick via `self._world.events()` /
`self._observation_provider.observe()` (kernel/society/observation.py).
So this bridge does NOT call `ActorRuntime.add_goal()` at all -- instead it
writes a real WorldEvent into the actor's own SharedWorld via the SAME
governed-commitment path api/routes/world.py's `POST /world/events` route
uses (`security_boundary.py::ensure_governed(..., skip_authz=True)` wrapping
`SharedWorld.record_event`, not a raw call to it -- SharedWorld.record_event
calls `_require_write` -> `assert_state_mutation_allowed`, which raises
SecurityBoundaryDenied outside an active commitment/insecure-dev-mode; a raw
call from this background bridge would fail that check every time outside
dev mode. `skip_authz=True` matches world.py's own route: this is a
system-internal evidence write with no human caller to authorize, but it
still runs the real AUDIT_INTENT-before-MUTATION commitment machinery, so
`shared_world_mutations_require_commitment`/
`policy_audit_intent_precedes_effect` (scripts/check_architecture_conformance.py)
hold for this write exactly as they do for every other one.), then calls
`sr.tick_one_actor(actor_id)` -- the identical tick path
POST /actors/{id}/tick already uses, so belief fusion, planning, and
governance all run completely unmodified. A camera observation never
reaches a drone adapter directly -- only through that existing tick.

Debounced, not per-frame (spec PERFORMANCE section: "no planner invocation
per frame"): a WorldEvent + tick only fires when an attribute's value
actually CHANGES from what was last recorded for this session, so a
steady `obstacle_detected=True` held across many sampled frames triggers
one replan, not one per frame.

Never crashes the host process: every step is wrapped, matching
VoiceCommandRuntime's own contract -- a transient LiveKit/CLIP/tick failure
degrades this one video session, never the actor's own cognition loop.

Update, gap fix: the WorldEvent write above reaches kernel/society/
observation.py's ObservationProvider (society-level, per-actor visibility
bookkeeping) -- but that layer's own BeliefFusion.fuse() (kernel/society/
belief.py) only ever folds ActorObservation.entities into belief, never
.events, so a written WorldEvent alone was NOT actually reaching any belief
representation the real planner reads (the REAL per-actor planning engine,
CognitiveActor.tick -> BeliefFormation -> build_comparison_integrated_
runtime(), uses a completely separate WorldPollingProvider whose own
`world.events()` handling turns an event into a generic, weakly-typed
`attribute="event", value=<description string>` observation at best, and
only if the `world` object passed to it happens to be this same SharedWorld
-- not guaranteed). start()/stop() below now ALSO register/unregister this
runtime (not the raw provider -- same drain-race reasoning as
VoiceCommandRuntime, see that module's docstring) into WorldPollingProvider's
auxiliary-source registry (kernel/pipeline/observations.py), so a properly
typed `Observation(attribute="obstacle_detected", value=True, confidence=...)`
reaches the REAL canonical belief directly, not just an opaque event string
via the society-level bookkeeping path. The WorldEvent write and
tick_one_actor() call stay exactly as they were: the tick trigger is still
needed for promptness (nothing else causes a tick soon after a new visual
observation), and the WorldEvent still gives the society-level layer
whatever value it has for multi-actor visibility -- this fix is additive,
not a replacement.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

from src.introspection.otel_bridge import get_bridge
from src.monkey_brain.kernel.edge.livekit_video_adapter import LiveKitVideoObservationProvider
from src.monkey_brain.kernel.edge.video_session import VideoSession, get_video_session_store
from src.monkey_brain.kernel.pipeline.observations import (
    ObservationSet,
    register_observation_source,
    unregister_observation_source,
)
from src.monkey_brain.kernel.society.world import EventType, WorldEvent

_AUX_SOURCE_ID = "livekit_video"

logger = logging.getLogger("agentos.edge.video_command_runtime")

# Same rationale as VoiceCommandRuntime's own _POLL_INTERVAL_SECONDS: shorter
# than the provider's own sampling interval (1 / perception_fps) so nothing
# sits in the provider's buffer longer than necessary, without busy-polling.
_POLL_INTERVAL_SECONDS = 0.5


def _find_actor_state(pr: Any, actor_id: str) -> tuple[Any, Any] | None:
    """Same tiny lookup voice_command_runtime.py's own _find_actor_state
    does (and api/routes/actors.py's own _find_actor_state) -- duplicated
    rather than imported for the same reason voice's copy is: routes/kernel
    edge modules depend on kernel, never the reverse, and this is three
    lines, not a second architecture."""
    for sr in pr.all_societies():
        state = sr.get_actor(actor_id)
        if state is not None:
            return sr, state
    return None


class VideoCommandRuntime:
    """One instance per active VideoSession. Started by POST
    /video/sessions, stopped by DELETE /video/sessions/{id}."""

    def __init__(
        self,
        session: VideoSession,
        planetary_runtime: Any,
        *,
        perception_fps: float = 2.0,
    ) -> None:
        self._session = session
        self._pr = planetary_runtime
        # Distinct listener identity, scoped to this session -- never the
        # human operator's own identity, mirroring VoiceCommandRuntime's
        # "cognitiveos-listener-{session_id}" precedent exactly.
        self._provider = LiveKitVideoObservationProvider(
            session.room,
            session.camera_track_name,
            participant_identity=f"cognitiveos-video-listener-{session.session_id}",
            perception_fps=perception_fps,
        )
        self._task: asyncio.Task | None = None
        self._store = get_video_session_store()
        # Debounce state: last value written into SharedWorld per
        # attribute, so a steadily-held detection doesn't retrigger a tick
        # every sample.
        self._last_written: dict[str, Any] = {}
        # Purely additive tee of whatever _drain_once() pulls from
        # self._provider — see module docstring for why this class, not the
        # raw provider, is what gets registered as the belief-visible
        # observation source below (same drain-race reasoning as
        # VoiceCommandRuntime).
        self._belief_buffer: list[Any] = []
        self._belief_buffer_lock = threading.Lock()
        self._started_at: float = 0.0

    def start(self) -> None:
        self._started_at = time.monotonic()
        get_bridge().emit_counter("video.session.started", actor_id=self._session.actor_id)
        self._provider.start()
        register_observation_source(self._session.actor_id, _AUX_SOURCE_ID, self)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning(
                "VideoCommandRuntime.start() called outside a running event loop for session %s — video bridge will not run",
                self._session.session_id,
            )
            return
        self._task = loop.create_task(self._run())

    def _update_session(self, **fields: Any) -> None:
        """Keep the caller's session object and the process store coherent.

        Tests and setup code may construct a ``VideoSession`` directly rather
        than through ``VideoSessionStore.create``; updating only the store
        silently left that live object stale.
        """
        for key, value in fields.items():
            setattr(self._session, key, value)
        self._session.updated_at = time.time()
        self._store.update(self._session.session_id, **fields)

    async def stop(self) -> None:
        unregister_observation_source(self._session.actor_id, _AUX_SOURCE_ID)
        if self._task is not None:
            self._task.cancel()
            self._task = None
        await self._provider.stop()
        # video.session (spec's own event name) -- same emit_span-at-end
        # shape as VoiceCommandRuntime.stop(), same rationale.
        get_bridge().emit_span(
            "video.session",
            layer="realtime",
            duration_ms=(time.monotonic() - self._started_at) * 1000.0 if self._started_at else 0.0,
            actor_id=self._session.actor_id,
            session_id=self._session.session_id,
        )

    def observe(self, actor_id: str, world: Any) -> ObservationSet:
        """The belief-visible side of the tee — called by WorldPollingProvider
        during a real tick's observe stage, NEVER by this class's own
        _drain_once(). Never raises, matching every other ObservationProvider's
        contract."""
        try:
            with self._belief_buffer_lock:
                observations = tuple(self._belief_buffer)
                self._belief_buffer.clear()
        except Exception:
            logger.debug("VideoCommandRuntime.observe: suppressed exception", exc_info=True)
            observations = ()
        return ObservationSet(observations=observations, actor_id=actor_id)

    async def _run(self) -> None:
        try:
            while True:
                await asyncio.sleep(_POLL_INTERVAL_SECONDS)
                await self._drain_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("VideoCommandRuntime polling loop crashed for session %s", self._session.session_id)

    async def _drain_once(self) -> None:
        try:
            observations = self._provider.observe(self._session.actor_id, None)
        except Exception:
            logger.exception("VideoCommandRuntime: observe() failed for session %s", self._session.session_id)
            return

        if observations.observations:
            # Every observation, including camera_stream_available -- belief
            # visibility for track loss (spec's own example) does not depend
            # on whether this particular attribute is also debounce-worthy
            # enough to trigger a WorldEvent/tick below.
            with self._belief_buffer_lock:
                self._belief_buffer.extend(observations.observations)

        for obs in observations.observations:
            await self._handle_observation(obs)

    async def _handle_observation(self, obs: Any) -> None:
        # camera_stream_available is display/error state (spec's own literal
        # example) -- reflected in the session for the frontend, never
        # written into the world/tick path; a lost track is not "evidence
        # about the environment" the actor should replan around.
        if obs.attribute == "camera_stream_available":
            self._update_session(
                stream_available=bool(obs.value),
                status="streaming" if obs.value else "degraded",
            )
            return

        self._update_session(
            last_observation_attribute=obs.attribute,
            last_observation_value=obs.value,
            last_observation_confidence=obs.confidence,
        )

        if self._last_written.get(obs.attribute) == obs.value:
            return  # debounce: no change since the last tick-triggering write
        self._last_written[obs.attribute] = obs.value

        try:
            found = _find_actor_state(self._pr, self._session.actor_id)
            if found is None:
                self._update_session(error="actor not found")
                return
            sr, _state = found
            from src.monkey_brain.kernel.security_boundary import ensure_governed

            event = WorldEvent(
                event_type=EventType.OBSERVATION,
                entity_id=self._session.actor_id,
                description=f"{obs.attribute}={obs.value}",
                attributes={obs.attribute: obs.value, "confidence": obs.confidence},
                confidence=obs.confidence,
                source_actor_id=self._session.actor_id,
            )
            await ensure_governed(
                "video.observation.record",
                event.entity_id,
                lambda: sr.world.record_event(event) or True,
                skip_authz=True,
            )
            coordinated = await sr.tick_one_actor(self._session.actor_id)
            self._update_session(
                error=None if coordinated else "tick did not complete",
            )
        except Exception as exc:
            logger.exception("VideoCommandRuntime: world write/tick failed for session %s", self._session.session_id)
            self._update_session(error=str(exc))
