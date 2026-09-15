"""Voice Command Runtime (CognitiveOS LiveKit Voice Command Integration,
spec sections 6/9/10/11/13/14/26).

The bridge component: owns a LiveKitVoiceObservationProvider (kernel/edge/
livekit_adapter.py) for one (room, actor) pair, drains its transcripts on a
short interval, and for each one:

  - runs it through voice_intent.interpret_voice_transcript() (a narrow
    ambiguity gate, NOT a second planner -- see that module's own
    docstring for why)
  - "non_actionable" -> dropped, nothing happens
  - "ambiguous" -> session marked clarification_required, NOTHING is
    queued or executed (sections 10/11: voice must never guess a
    consequential command, and "never bypass governance" is trivially true
    here because nothing reaches governance at all)
  - "actionable" -> ActorRuntime.add_goal(transcript) (kernel/compile/
    cognitive_actor.py -- the SAME real, persistent, priority-ordered goal
    queue POST /actors/{id}/goals already uses) followed by
    SocietyRuntime.tick_one_actor(actor_id) (the SAME coordinated tick path
    POST /actors/{id}/tick already uses: observe, fuse belief, plan via the
    existing domain-agnostic LLMPlanner, then execute through the existing
    governed capability path -- run_ros_action_if_governed for a drone
    actor). This module never touches a drone adapter, ActionExecutor, or
    governance call directly; it only ever adds a goal and asks the actor
    to tick, exactly as a human typing that same goal into the UI would.

Note on a real gap found while wiring this, since fixed (kernel/pipeline/
observations.py's register_observation_source() -- read that module's own
comment for the full story): SocietyRuntime.tick_one_actor()'s own society-
level observation step (get_observation -> kernel/society/observation.py's
ObservationProvider) is a genuinely different, world-entity-only provider
that is NOT what drives real planning -- the canonical per-actor engine
(CognitiveActor.tick -> BeliefFormation -> build_comparison_integrated_
runtime(), see that factory's docstring) always used a bare
WorldPollingProvider(), which had no way to see this module's own
LiveKitVoiceObservationProvider output. start()/stop() below now register/
unregister THIS RUNTIME (not the raw provider) into WorldPollingProvider's
auxiliary-source registry, so every voice transcript -- actionable,
ambiguous, or not -- reaches the actor's REAL belief on any tick of this
actor (this module's own tick below, or any other trigger), not just the
goal path. Deliberately not the raw provider: LiveKitVoiceObservationProvider
.observe() destructively drains its own buffer, and this module's own
_drain_once() poll loop already calls it every second for intent detection
-- registering the same provider object twice would race the two
consumers, each stealing transcripts from the other essentially at random.
This class implements its own observe() instead, backed by a second, purely
additive buffer that _drain_once() feeds every time it drains the real
provider, so the intent-detection path and the belief path both reliably
see every transcript once, from one single point of drain.

This module still drains transcripts itself and still explicitly calls
add_goal()+tick_one_actor() for actionable ones: turning a spoken command
into a NEW standing goal is a genuinely different concern from "this
observation should be visible in belief" that the registry alone does not
replace, and nothing else triggers a prompt tick reaction to a fresh
voice session by itself.

Never crashes the host process: every step from observe() through
tick_one_actor() is wrapped so a transient LiveKit/Whisper/LLM/governance
failure degrades this one voice session, never the actor's own cognition
loop or any other session.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

from src.introspection.otel_bridge import get_bridge
from src.monkey_brain.kernel.edge.livekit_adapter import LiveKitVoiceObservationProvider
from src.monkey_brain.kernel.edge.voice_intent import interpret_voice_transcript
from src.monkey_brain.kernel.edge.voice_session import VoiceSession, get_voice_session_store
from src.monkey_brain.kernel.pipeline.observations import (
    ObservationSet,
    register_observation_source,
    unregister_observation_source,
)

_AUX_SOURCE_ID = "livekit_voice"

logger = logging.getLogger("agentos.edge.voice_command_runtime")

# Draining on a short interval, not per audio frame (spec section 14/15) --
# the provider itself already only produces a transcript once per
# _AUDIO_WINDOW_SECONDS (4s) window, so this just needs to be shorter than
# that to pick each one up promptly without busy-polling.
_POLL_INTERVAL_SECONDS = 1.0


def _find_actor_state(pr: Any, actor_id: str) -> tuple[Any, Any] | None:
    """Same tiny lookup api/routes/actors.py's own _find_actor_state does --
    not imported from there because routes depend on kernel, never the
    reverse."""
    for sr in pr.all_societies():
        state = sr.get_actor(actor_id)
        if state is not None:
            return sr, state
    return None


class VoiceCommandRuntime:
    """One instance per active VoiceSession. Started by POST
    /voice/sessions, stopped by DELETE /voice/sessions/{id}."""

    def __init__(self, session: VoiceSession, planetary_runtime: Any, *, whisper_model: str = "tiny") -> None:
        self._session = session
        self._pr = planetary_runtime
        # A distinct room identity for CognitiveOS's own listening
        # participant, scoped to this session -- never the human's own
        # identity (session.participant_identity), which belongs to the
        # browser's publishing participant. Two rooms sharing a room name
        # would otherwise collide on LiveKit's default "cognitiveos-listener".
        self._provider = LiveKitVoiceObservationProvider(
            session.room,
            participant_identity=f"cognitiveos-listener-{session.session_id}",
            whisper_model=whisper_model,
        )
        self._task: asyncio.Task | None = None
        self._store = get_voice_session_store()
        # Purely additive tee of whatever _drain_once() pulls from
        # self._provider — see module docstring for why this class, not the
        # raw provider, is what gets registered as the belief-visible
        # observation source below.
        self._belief_buffer: list[Any] = []
        self._belief_buffer_lock = threading.Lock()
        self._started_at: float = 0.0

    def start(self) -> None:
        self._started_at = time.monotonic()
        get_bridge().emit_counter("voice.session.started", actor_id=self._session.actor_id)
        self._provider.start()
        # Registers THIS RUNTIME, not self._provider — see module docstring.
        # Belief visibility for this session's transcripts depends on
        # _drain_once() actually running, same as intent detection already
        # did before this fix; if the event-loop check below fails and
        # self._task never starts, neither did before.
        register_observation_source(self._session.actor_id, _AUX_SOURCE_ID, self)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning(
                "VoiceCommandRuntime.start() called outside a running event loop for session %s — voice bridge will not run",
                self._session.session_id,
            )
            return
        self._task = loop.create_task(self._run())

    async def stop(self) -> None:
        unregister_observation_source(self._session.actor_id, _AUX_SOURCE_ID)
        if self._task is not None:
            self._task.cancel()
            self._task = None
        await self._provider.stop()
        # voice.session (spec's own event name) -- emitted once, at session
        # end, when total duration is known, via the bridge's own
        # non-context-manager emit_span() form (see otel_bridge.py) rather
        # than holding a context-manager span open across start()/stop()
        # (two separate async calls, sometimes minutes apart).
        get_bridge().emit_span(
            "voice.session",
            layer="realtime",
            duration_ms=(time.monotonic() - self._started_at) * 1000.0 if self._started_at else 0.0,
            actor_id=self._session.actor_id,
            session_id=self._session.session_id,
        )

    async def _run(self) -> None:
        try:
            while True:
                await asyncio.sleep(_POLL_INTERVAL_SECONDS)
                await self._drain_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("VoiceCommandRuntime polling loop crashed for session %s", self._session.session_id)

    def observe(self, actor_id: str, world: Any) -> ObservationSet:
        """The belief-visible side of the tee — called by WorldPollingProvider
        (kernel/pipeline/observations.py) during a real tick's observe stage,
        NEVER by this class's own _drain_once(). Never raises, matching every
        other ObservationProvider's contract."""
        try:
            with self._belief_buffer_lock:
                observations = tuple(self._belief_buffer)
                self._belief_buffer.clear()
        except Exception:
            logger.debug("VoiceCommandRuntime.observe: suppressed exception", exc_info=True)
            observations = ()
        return ObservationSet(observations=observations, actor_id=actor_id)

    def _update_session(self, **fields: Any) -> None:
        """Keep direct session objects and the process store consistent."""
        for key, value in fields.items():
            setattr(self._session, key, value)
        self._session.updated_at = time.time()
        self._store.update(self._session.session_id, **fields)

    async def _drain_once(self) -> None:
        try:
            observations = self._provider.observe(self._session.actor_id, None)
        except Exception:
            logger.exception("VoiceCommandRuntime: observe() failed for session %s", self._session.session_id)
            return
        if observations.observations:
            with self._belief_buffer_lock:
                self._belief_buffer.extend(observations.observations)

        for obs in observations.observations:
            if obs.attribute != "voice_transcript":
                continue
            await self._handle_transcript(str(obs.value))

    async def _handle_transcript(self, transcript: str) -> None:
        try:
            result = interpret_voice_transcript(transcript)
        except Exception:
            logger.exception(
                "VoiceCommandRuntime: intent interpretation failed for session %s", self._session.session_id
            )
            self._update_session(status="idle", error="intent interpretation failed")
            return

        if result.kind == "non_actionable":
            self._update_session(last_transcript=result.transcript)
            return

        if result.kind == "ambiguous":
            self._update_session(
                status="clarification_required",
                last_transcript=result.transcript,
                clarification_reason=result.clarification_reason,
            )
            logger.info(
                "VoiceCommandRuntime: session %s needs clarification: %s",
                self._session.session_id,
                result.clarification_reason,
            )
            return

        # actionable — reuse the EXACT existing goal + tick path (POST
        # /actors/{id}/goals + POST /actors/{id}/tick), never a shortcut
        # into governance or the drone adapter.
        self._update_session(
            status="planning",
            last_transcript=result.transcript,
            last_goal_text=result.goal_text,
            clarification_reason=None,
        )
        try:
            found = _find_actor_state(self._pr, self._session.actor_id)
            if found is None:
                self._update_session(status="idle", error="actor not found")
                return
            sr, state = found
            if state.actor_runtime is not None:
                state.actor_runtime.add_goal(result.goal_text)
            coordinated = await sr.tick_one_actor(self._session.actor_id)
            self._update_session(
                status="listening" if coordinated else "idle",
                error=None if coordinated else "tick did not complete",
            )
        except Exception as exc:
            logger.exception("VoiceCommandRuntime: goal/tick failed for session %s", self._session.session_id)
            self._update_session(status="idle", error=str(exc))
