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
# the provider itself already produces at most one transcript per real
# Silero-VAD-detected utterance (scripts/voice_service/
# transcription_service.py), so this just needs to be short enough to pick
# each one up promptly without busy-polling.
_POLL_INTERVAL_SECONDS = 1.0


class VoiceCommandRuntime:
    """One instance per active VoiceSession. Started by POST
    /voice/sessions, stopped by DELETE /voice/sessions/{id}."""

    def __init__(self, session: VoiceSession, planetary_runtime: Any) -> None:
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

        # voice.transcription (spec's own causal-chain event name): one
        # event per completed transcript classification, never per audio
        # packet. session_id is the correlation key a human/trace tool can
        # follow through the matching actor.goal event below and into
        # last_goal_text on this same session.
        get_bridge().emit_counter(
            "voice.transcription",
            actor_id=self._session.actor_id,
            session_id=self._session.session_id,
            kind=result.kind,
        )

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

        # actionable — forwarded straight to actor_id's own dedicated
        # actor Pod (POST /prompt, kernel/edge/actor_prompt_forwarder.py),
        # the SAME call POST /actors/{id}/prompt makes for a typed/edited
        # command (api/routes/actors.py::prompt_actor) — NOT the lower-
        # level add_goal()+tick_one_actor() path against THIS process's
        # own PlanetaryRuntime, which for a robot-class actor like a drone
        # is a different process than the one that actually holds its
        # ROS_ADAPTER_KIND/ROS_BRIDGE_URL bindings. Never a shortcut into
        # governance or the drone adapter either way — that Pod's own
        # /prompt still runs through ensure_governed exactly like every
        # other entry point (see actor_runtime.py's own docstring).
        self._update_session(
            status="planning",
            last_transcript=result.transcript,
            last_goal_text=result.goal_text,
            clarification_reason=None,
        )
        # actor.goal (spec's own causal-chain event name): fired exactly
        # once per actionable transcript, right before add_goal() --
        # preserves the human-command -> goal link even though nothing
        # downstream (SocietyRuntime.tick_one_actor -> LLMPlanner ->
        # governance -> run_ros_action_if_governed) threads a shared
        # correlation id into OTel today (kernel/society/context_stream.py's
        # own _validate_causal_lineage() already documents that as a known,
        # currently-unaddressed gap this session doesn't attempt to close in
        # full -- session_id is the correlation key available here).
        get_bridge().emit_counter(
            "actor.goal",
            actor_id=self._session.actor_id,
            session_id=self._session.session_id,
        )
        try:
            from src.monkey_brain.kernel.edge.actor_prompt_forwarder import (
                ActorPromptForwardError,
                forward_prompt_to_actor_pod,
            )

            try:
                await forward_prompt_to_actor_pod(self._session.actor_id, result.goal_text)
            except ActorPromptForwardError as exc:
                self._update_session(status="idle", error=str(exc))
                return
            self._update_session(status="listening", error=None)
        except Exception as exc:
            logger.exception("VoiceCommandRuntime: prompt forward failed for session %s", self._session.session_id)
            self._update_session(status="idle", error=str(exc))
