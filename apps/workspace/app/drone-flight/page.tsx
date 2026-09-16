"use client";

// Voice -> simulated drone flight + live camera + mission/crash-test
// status, all on ONE page (no tabs -- this used to be split across
// /drone-flight and /drone-mission; merged per explicit request, since a
// Foxglove-style operator view shows every panel at once rather than
// switching screens). docs/VOICE_DRONE_FLIGHT_DEMO.md has the full
// runbook. Two INDEPENDENT LiveKit Room connections on purpose -- voice
// (publish mic, drives real goals via voiceSessionClient.ts ->
// api/routes/voice.py -> VoiceCommandRuntime -> ActorRuntime.add_goal(),
// never a REST shortcut into PX4) and camera (subscribe-only, via
// videoClient.ts -> api/routes/video.py, resolved server-side from
// camera_state.py's CameraIdentity) -- so the camera keeps streaming
// regardless of whether voice succeeds, fails, or was never connected at
// all, not merely asserted to. Mission/crash-test status is a THIRD,
// independent poll (droneClient.ts's fetchActorBeliefs) -- belief facts,
// not a LiveKit session, so it keeps working even with no LiveKit server
// configured at all.
//
// No @livekit/components-react in this workspace (see package.json) --
// the camera track is attached to a plain <video> element by hand via
// livekit-client's own track.attach()/detach(), the same primitive
// voice/page.tsx already uses for mic control.
import { useEffect, useRef, useState } from "react";
import { Room, RoomEvent, type RemoteTrack, type RemoteTrackPublication } from "livekit-client";
import { RequireAuth } from "../../components/RequireAuth";
import { fetchActorGoals, fetchAllActors, promptActor, type Actor } from "../../lib/actorClient";
import {
  approveRuntimeApproval,
  fetchPendingRuntimeApprovals,
  rejectRuntimeApproval,
  type PendingRuntimeApproval,
} from "../../lib/approvalClient";
import {
  deriveMissionStatus,
  deriveTelemetry,
  fetchActorBeliefs,
  type DroneMissionStatus,
  type DroneTelemetry,
} from "../../lib/droneClient";
import { LIVEKIT_URL } from "../../lib/livekitClient";
import {
  createVideoSession,
  fetchVideoSessionStatus,
  stopVideoSession,
  type VideoSessionResponse,
} from "../../lib/videoClient";
import {
  createVoiceSession,
  fetchVoiceSessionStatus,
  stopVoiceSession,
  type VoiceSessionResponse,
  type VoiceSessionStatusResponse,
} from "../../lib/voiceSessionClient";

const STATUS_POLL_MS = 2000;
const EXAMPLE_COMMAND = "Take drone one to waypoint Alpha.";

type FlightPhase = "IDLE" | "NAVIGATING" | "ARRIVED" | "FAILED";

// Derived ONLY from VoiceSession.status transitions VoiceCommandRuntime
// already exposes (planning -> listening/idle) -- see that module's own
// _handle_transcript(): "planning" is set before the governed goal/tick
// call, which BLOCKS until WaypointCapability's own arrival confirmation
// returns, so a planning -> listening transition means the flight step
// genuinely completed, not a guess dressed up as telemetry.
function nextFlightPhase(previous: FlightPhase, voice: VoiceSessionStatusResponse | null): FlightPhase {
  if (!voice) return previous;
  if (voice.status === "planning") return "NAVIGATING";
  if (voice.status === "idle" && voice.error) return "FAILED";
  if (previous === "NAVIGATING" && voice.status === "listening") return "ARRIVED";
  return previous;
}

function missionPhase(status: DroneMissionStatus): string {
  if (status.collision) return "Impact (simulated)";
  if (status.disabled) return "Disabled";
  if (status.landmark?.geometric_verified) return "Approaching → Impact";
  return "Awaiting visual identification";
}

const EMPTY_TELEMETRY: DroneTelemetry = {
  armed: null,
  positionX: null,
  positionY: null,
  positionZ: null,
  heading: null,
  battery: null,
  flightMode: null,
  gpsState: null,
};

// null means "no fresh PX4 reading" (server-side 5s staleness gate,
// droneClient.ts's own deriveTelemetry) -- render that as "—", never as 0
// or "false", so a stale/disconnected drone never looks like a real state.
function fmt(value: number | string | boolean | null, digits?: number): string {
  if (value === null) return "—";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "number" && digits !== undefined) return value.toFixed(digits);
  return String(value);
}

function DroneFlightContent() {
  const [actors, setActors] = useState<Actor[]>([]);
  const [selectedActorId, setSelectedActorId] = useState("");
  const [room, setRoom] = useState("mission-room");
  const [error, setError] = useState("");

  // Voice
  const [voiceStatus, setVoiceStatus] = useState<"disconnected" | "connecting" | "connected">("disconnected");
  const [micEnabled, setMicEnabled] = useState(false);
  const [voiceSession, setVoiceSession] = useState<VoiceSessionResponse | null>(null);
  const [voiceState, setVoiceState] = useState<VoiceSessionStatusResponse | null>(null);
  const voiceRoomRef = useRef<Room | null>(null);
  const [flightPhase, setFlightPhase] = useState<FlightPhase>("IDLE");

  // Camera
  const [cameraStatus, setCameraStatus] = useState<"disconnected" | "connecting" | "connected">("disconnected");
  const [videoSession, setVideoSession] = useState<VideoSessionResponse | null>(null);
  const [cameraLive, setCameraLive] = useState(false);
  const cameraRoomRef = useRef<Room | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);

  // Mission / crash-test status (belief facts, independent of LiveKit)
  const [missionStatus, setMissionStatus] = useState<DroneMissionStatus>({
    landmark: null,
    collision: null,
    disabled: false,
  });

  // Flight telemetry -- SAME belief-fact poll as missionStatus below (one
  // fetchActorBeliefs call, two derivations), not a second network request.
  const [telemetry, setTelemetry] = useState<DroneTelemetry>(EMPTY_TELEMETRY);

  // Goals -- folded in from the former standalone /goals tab, scoped to
  // whichever actor is selected above.
  const [goals, setGoals] = useState<string[]>([]);
  const [goalsError, setGoalsError] = useState("");

  // Approvals -- folded in from the former standalone /approvals tab.
  // Global (not actor-scoped), same as that page.
  const [approvals, setApprovals] = useState<PendingRuntimeApproval[]>([]);
  const [approvalsError, setApprovalsError] = useState("");
  const [busyApprovalId, setBusyApprovalId] = useState<string | null>(null);

  // Chat -- lets a spoken command be corrected before it's actually acted
  // on. Whisper's transcript still lands in voiceState.last_transcript
  // exactly as before (VoiceCommandRuntime keeps auto-acting on it
  // server-side, unchanged); this is a SEPARATE, parallel path: each new
  // transcript is logged here and pre-fills the editable draft below, but
  // nothing is sent from here until the operator explicitly hits Send --
  // at which point it goes through addActorGoal() (POST /actors/{id}/
  // goals), the same real CognitiveActor.add_goal() mechanism, just
  // typed/edited rather than spoken verbatim.
  type ChatMessage = { id: string; role: "transcript" | "sent" | "error"; text: string; at: number };
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [draftText, setDraftText] = useState("");
  const [sendingDraft, setSendingDraft] = useState(false);
  const lastTranscriptRef = useRef<string | null>(null);
  const chatLogRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchAllActors()
      .then((result) => {
        if (cancelled) return;
        setActors(result);
        if (result.length > 0) setSelectedActorId(result[0].actor_id);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Voice transcript/goal/flight-state polling -- independent of the
  // camera's own polling/connection lifecycle below.
  useEffect(() => {
    if (!voiceSession) return;
    let cancelled = false;
    const poll = () => {
      fetchVoiceSessionStatus(voiceSession.session_id)
        .then((result) => {
          if (cancelled) return;
          setVoiceState(result);
          setFlightPhase((previous) => nextFlightPhase(previous, result));
        })
        .catch(() => {
          /* transient polling failure -- keep last known state, never
             corrupt drone state from a voice-channel hiccup */
        });
    };
    poll();
    const interval = setInterval(poll, STATUS_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [voiceSession]);

  // New transcript -> chat log entry + pre-fill the editable draft. Keyed
  // off the transcript text itself (not voiceState as a whole) so this
  // fires once per genuinely new utterance, not every 2s poll tick.
  useEffect(() => {
    const transcript = voiceState?.last_transcript;
    if (!transcript || transcript === lastTranscriptRef.current) return;
    lastTranscriptRef.current = transcript;
    setChatMessages((current) => [
      ...current,
      { id: `t-${Date.now()}`, role: "transcript", text: transcript, at: Date.now() },
    ]);
    setDraftText(transcript);
  }, [voiceState?.last_transcript]);

  useEffect(() => {
    chatLogRef.current?.scrollTo({ top: chatLogRef.current.scrollHeight });
  }, [chatMessages]);

  // Camera stream-availability polling -- runs whether or not voice is
  // connected, proving the two are independent.
  useEffect(() => {
    if (!videoSession) return;
    let cancelled = false;
    const poll = () => {
      fetchVideoSessionStatus(videoSession.session_id)
        .then((result) => {
          if (!cancelled) setCameraLive(result.stream_available);
        })
        .catch(() => {
          if (!cancelled) setCameraLive(false);
        });
    };
    poll();
    const interval = setInterval(poll, STATUS_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [videoSession]);

  // Mission/crash-test belief-fact polling -- belief state, not a LiveKit
  // session, so this runs purely off the selected actor and keeps working
  // even when voice/camera are both disconnected (or no LiveKit server is
  // configured at all).
  useEffect(() => {
    if (!selectedActorId) return;
    let cancelled = false;
    const poll = () => {
      fetchActorBeliefs(selectedActorId)
        .then((result) => {
          if (cancelled) return;
          const facts = result.beliefs.facts ?? [];
          setMissionStatus(deriveMissionStatus(facts));
          setTelemetry(deriveTelemetry(facts));
        })
        .catch(() => {
          /* transient polling failure -- keep last known mission status */
        });
    };
    poll();
    const interval = setInterval(poll, STATUS_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [selectedActorId]);

  // Goals polling -- same fetchActorGoals the former /goals page used,
  // scoped to whichever actor is selected in the topbar picker above.
  useEffect(() => {
    if (!selectedActorId) {
      setGoals([]);
      return;
    }
    let cancelled = false;
    const poll = () => {
      fetchActorGoals(selectedActorId)
        .then((result) => {
          if (!cancelled) setGoals(result.goals);
        })
        .catch((err) => {
          if (!cancelled) setGoalsError(err instanceof Error ? err.message : String(err));
        });
    };
    poll();
    const interval = setInterval(poll, STATUS_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [selectedActorId]);

  // Approvals polling -- global queue, independent of the selected actor.
  useEffect(() => {
    let cancelled = false;
    const poll = () => {
      fetchPendingRuntimeApprovals()
        .then((result) => {
          if (!cancelled) setApprovals(result.approvals);
        })
        .catch((err) => {
          if (!cancelled) setApprovalsError(err instanceof Error ? err.message : String(err));
        });
    };
    poll();
    const interval = setInterval(poll, STATUS_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  const sendDraft = async () => {
    const text = draftText.trim();
    if (!text || !selectedActorId) return;
    setSendingDraft(true);
    try {
      // promptActor forwards straight to the actor's own dedicated Pod
      // (POST /prompt) -- the same call a spoken command triggers -- so
      // this actually flies the mission, not just queues it.
      await promptActor(selectedActorId, text);
      setChatMessages((current) => [...current, { id: `s-${Date.now()}`, role: "sent", text, at: Date.now() }]);
      setDraftText("");
      lastTranscriptRef.current = null;
      const result = await fetchActorGoals(selectedActorId);
      setGoals(result.goals);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setChatMessages((current) => [...current, { id: `e-${Date.now()}`, role: "error", text: message, at: Date.now() }]);
    } finally {
      setSendingDraft(false);
    }
  };

  const decideApproval = async (approvalId: string, action: "approve" | "reject") => {
    const reason = window.prompt(action === "approve" ? "Reason for approval (optional):" : "Reason for rejection:") ?? "";
    if (action === "reject" && !reason) return;
    setBusyApprovalId(approvalId);
    try {
      if (action === "approve") await approveRuntimeApproval(approvalId, reason);
      else await rejectRuntimeApproval(approvalId, reason);
      setApprovals((current) => current.filter((a) => a.approval_id !== approvalId));
    } catch (err) {
      setApprovalsError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyApprovalId(null);
    }
  };

  useEffect(() => {
    return () => {
      voiceRoomRef.current?.disconnect();
      cameraRoomRef.current?.disconnect();
    };
  }, []);

  // autoUnmute: the mic button's own "cold start" path -- clicking it while
  // disconnected should connect AND start listening in one action, rather
  // than making the operator click Connect, wait, then click Unmute
  // separately. liveKitRoom.connect() only resolves once the room is
  // actually connected, so enabling the mic right after it is safe.
  const connectVoice = async (autoUnmute = false) => {
    if (!LIVEKIT_URL) {
      setError("NEXT_PUBLIC_LIVEKIT_URL is not set — no LiveKit server configured for this deployment.");
      return;
    }
    if (!selectedActorId) return;
    setError("");
    setVoiceStatus("connecting");
    try {
      const session = await createVoiceSession(room, selectedActorId);
      const liveKitRoom = new Room();
      liveKitRoom.on(RoomEvent.Connected, () => setVoiceStatus("connected"));
      liveKitRoom.on(RoomEvent.Disconnected, () => setVoiceStatus("disconnected"));
      await liveKitRoom.connect(LIVEKIT_URL, session.token);
      voiceRoomRef.current = liveKitRoom;
      setVoiceSession(session);
      if (autoUnmute) {
        await liveKitRoom.localParticipant.setMicrophoneEnabled(true);
        setMicEnabled(true);
      }
    } catch (err) {
      setVoiceStatus("disconnected");
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const disconnectVoice = async () => {
    await voiceRoomRef.current?.disconnect();
    voiceRoomRef.current = null;
    setMicEnabled(false);
    // Also tears down the server-side VoiceCommandRuntime (DELETE
    // /voice/sessions/{id}) -- disconnecting only the browser's LiveKit
    // Room would otherwise leave that runtime's own listener
    // participant/polling loop running indefinitely.
    if (voiceSession) {
      stopVoiceSession(voiceSession.session_id).catch(() => {
        /* best-effort -- the LiveKit room is already disconnected either way */
      });
    }
    setVoiceSession(null);
    setVoiceState(null);
  };

  const toggleMic = async () => {
    const liveKitRoom = voiceRoomRef.current;
    if (!liveKitRoom) return;
    const next = !micEnabled;
    await liveKitRoom.localParticipant.setMicrophoneEnabled(next);
    setMicEnabled(next);
  };

  // Single entry point for the mic button below: cold-start (connect +
  // unmute) when disconnected, plain mute/unmute toggle once connected.
  const handleMicClick = () => {
    if (voiceStatus === "disconnected") {
      connectVoice(true);
    } else if (voiceStatus === "connected") {
      toggleMic();
    }
  };

  const connectCamera = async () => {
    if (!LIVEKIT_URL) {
      setError("NEXT_PUBLIC_LIVEKIT_URL is not set — no LiveKit server configured for this deployment.");
      return;
    }
    if (!selectedActorId) return;
    setError("");
    setCameraStatus("connecting");
    try {
      const session = await createVideoSession(selectedActorId);
      const liveKitRoom = new Room();
      liveKitRoom.on(RoomEvent.Connected, () => setCameraStatus("connected"));
      liveKitRoom.on(RoomEvent.Disconnected, () => {
        setCameraStatus("disconnected");
        setCameraLive(false);
      });
      liveKitRoom.on(
        RoomEvent.TrackSubscribed,
        (track: RemoteTrack, publication: RemoteTrackPublication) => {
          if (track.kind === "video" && publication.trackName === session.camera_track_name && videoRef.current) {
            track.attach(videoRef.current);
            setCameraLive(true);
          }
        },
      );
      liveKitRoom.on(RoomEvent.TrackUnsubscribed, (track: RemoteTrack) => {
        if (track.kind === "video") {
          track.detach();
          setCameraLive(false);
        }
      });
      await liveKitRoom.connect(LIVEKIT_URL, session.token);
      cameraRoomRef.current = liveKitRoom;
      setVideoSession(session);
    } catch (err) {
      setCameraStatus("disconnected");
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const disconnectCamera = async () => {
    if (videoRef.current?.srcObject) {
      videoRef.current.pause();
      videoRef.current.srcObject = null;
    }
    await cameraRoomRef.current?.disconnect();
    cameraRoomRef.current = null;
    if (videoSession) {
      stopVideoSession(videoSession.session_id).catch(() => {
        /* best-effort -- the LiveKit room is already disconnected either way */
      });
    }
    setVideoSession(null);
    setCameraLive(false);
  };

  const connected = voiceStatus === "connected" || cameraStatus === "connected";

  return (
    <div className="fg-shell">
      <div
        style={{
          background: "rgba(224, 82, 75, 0.18)",
          color: "#ff9d97",
          border: "1px solid var(--fg-danger)",
          padding: "6px 20px",
          fontWeight: 700,
          fontSize: 11,
          letterSpacing: "0.06em",
          textTransform: "uppercase",
        }}
      >
        Simulation only — never controls a real drone
      </div>

      <div className="fg-topbar">
        <div>
          <h1>Drone Flight</h1>
          <p>
            Say: <em>&ldquo;{EXAMPLE_COMMAND}&rdquo;</em>
          </p>
        </div>
        <div className="fg-row">
          {actors.length > 0 && (
            <select className="fg-input" value={selectedActorId} onChange={(e) => setSelectedActorId(e.target.value)}>
              {actors.map((actor) => (
                <option key={actor.actor_id} value={actor.actor_id}>
                  {actor.name || actor.actor_id}
                </option>
              ))}
            </select>
          )}
          <span className={`fg-badge ${connected ? "fg-badge--ok" : "fg-badge--muted"}`}>
            {connected ? "Connected" : "Disconnected"}
          </span>
        </div>
      </div>

      {error && <div className="fg-error">{error}</div>}

      <div className="fg-grid">
        <aside className="fg-sidebar">
          <div className="fg-sidebar-label">Topics</div>
          <div className="fg-topic-row">
            <span className={`fg-dot ${voiceState?.last_transcript ? "fg-dot--ok" : ""}`} />
            <span className="fg-topic-name">voice_transcript</span>
            <span className="fg-topic-type">string</span>
          </div>
          <div className="fg-topic-row">
            <span className={`fg-dot ${cameraLive ? "fg-dot--ok" : ""}`} />
            <span className="fg-topic-name">camera_stream_available</span>
            <span className="fg-topic-type">bool</span>
          </div>
          <div className="fg-topic-row">
            <span
              className={`fg-dot ${
                flightPhase === "FAILED" ? "fg-dot--danger" : flightPhase !== "IDLE" ? "fg-dot--ok" : ""
              }`}
            />
            <span className="fg-topic-name">flight_state</span>
            <span className="fg-topic-type">enum</span>
          </div>
          <div className="fg-topic-row">
            <span className={`fg-dot ${missionStatus.landmark?.geometric_verified ? "fg-dot--ok" : ""}`} />
            <span className="fg-topic-name">visual_landmark_match</span>
            <span className="fg-topic-type">struct</span>
          </div>
          <div className="fg-topic-row">
            <span className={`fg-dot ${missionStatus.collision ? "fg-dot--danger" : ""}`} />
            <span className="fg-topic-name">collision_event</span>
            <span className="fg-topic-type">struct</span>
          </div>
          <div className="fg-topic-row">
            <span className={`fg-dot ${telemetry.armed !== null ? "fg-dot--ok" : ""}`} />
            <span className="fg-topic-name">drone_telemetry</span>
            <span className="fg-topic-type">struct</span>
          </div>
        </aside>

        <div className="fg-main">
          <div className="fg-panel fg-panel--camera">
            <div className="fg-panel-header">
              <span>Camera{selectedActorId ? ` · ${selectedActorId}` : ""}</span>
              <span className={`fg-badge ${cameraLive ? "fg-badge--ok" : "fg-badge--muted"}`}>
                {cameraLive ? "LIVE" : "NO SIGNAL"}
              </span>
            </div>
            <div className="fg-panel-body">
              {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
              <video ref={videoRef} autoPlay playsInline muted className="fg-video" />
              <div className="fg-row" style={{ marginTop: 10 }}>
                {cameraStatus !== "connected" ? (
                  <button
                    type="button"
                    className="fg-btn fg-btn--primary"
                    onClick={connectCamera}
                    disabled={!selectedActorId || cameraStatus === "connecting"}
                  >
                    {cameraStatus === "connecting" ? "Connecting…" : "Connect camera"}
                  </button>
                ) : (
                  <button type="button" className="fg-btn" onClick={disconnectCamera}>
                    Disconnect camera
                  </button>
                )}
              </div>
            </div>
          </div>

          <div className="fg-panel">
            <div className="fg-panel-header">
              <span>Flight State</span>
              <span className={`fg-badge ${telemetry.armed ? "fg-badge--ok" : "fg-badge--muted"}`}>
                {telemetry.armed === null ? "no telemetry" : telemetry.armed ? "ARMED" : "disarmed"}
              </span>
            </div>
            <div className="fg-panel-body">
              <dl className="fg-kv">
                <dt>actor</dt>
                <dd>{selectedActorId || "—"}</dd>
                <dt>flight_state</dt>
                <dd>{flightPhase}</dd>
                <dt>flight_mode</dt>
                <dd>{fmt(telemetry.flightMode)}</dd>
                <dt>voice</dt>
                <dd>{voiceStatus}</dd>
                <dt>camera</dt>
                <dd>{cameraLive ? "live" : "idle"}</dd>
              </dl>
            </div>
          </div>

          <div className="fg-panel">
            <div className="fg-panel-header">
              <span>Telemetry</span>
              <span className={`fg-badge ${telemetry.armed !== null ? "fg-badge--ok" : "fg-badge--muted"}`}>
                {telemetry.armed !== null ? "live" : "no signal"}
              </span>
            </div>
            <div className="fg-panel-body">
              <dl className="fg-kv">
                <dt>position (x, y, z)</dt>
                <dd>
                  {fmt(telemetry.positionX, 2)}, {fmt(telemetry.positionY, 2)}, {fmt(telemetry.positionZ, 2)}
                </dd>
                <dt>heading</dt>
                <dd>{telemetry.heading !== null ? `${fmt(telemetry.heading, 1)}°` : "—"}</dd>
                <dt>battery</dt>
                <dd>{telemetry.battery !== null ? `${fmt(telemetry.battery, 0)}%` : "—"}</dd>
                <dt>gps_state</dt>
                <dd>{fmt(telemetry.gpsState)}</dd>
              </dl>
              {telemetry.armed === null && (
                <div className="fg-console" style={{ marginTop: 10 }}>
                  <span className="fg-console--empty">
                    No fresh PX4 telemetry — the drone isn&apos;t reporting state right now.
                  </span>
                </div>
              )}
            </div>
          </div>

          <div className="fg-panel">
            <div className="fg-panel-header">
              <span>Mission Status</span>
              <span className={`fg-badge ${missionStatus.collision ? "fg-badge--danger" : "fg-badge--muted"}`}>
                {missionPhase(missionStatus)}
              </span>
            </div>
            <div className="fg-panel-body">
              <dl className="fg-kv">
                <dt>target</dt>
                <dd>{missionStatus.landmark?.landmark_id ?? missionStatus.collision?.target_landmark ?? "—"}</dd>
                <dt>visual_verification</dt>
                <dd>{missionStatus.landmark?.geometric_verified ? "LoFTR ✓" : "not yet verified"}</dd>
                <dt>authorization</dt>
                <dd>simulation-only ✓</dd>
              </dl>
              {missionStatus.collision && (
                <div className="fg-console" style={{ marginTop: 10, borderColor: "var(--fg-danger)" }}>
                  Simulated collision recorded against {missionStatus.collision.target_landmark} — simulation_only=
                  {String(missionStatus.collision.simulation_only)}, crash_test={String(missionStatus.collision.crash_test)}
                </div>
              )}
            </div>
          </div>

          <div className="fg-panel">
            <div className="fg-panel-header">
              <span>Voice</span>
            </div>
            <div className="fg-panel-body">
              <label style={{ display: "block", fontSize: 11, color: "var(--fg-muted)" }}>
                room
                <input
                  className="fg-input"
                  style={{ display: "block", width: "100%", marginTop: 4 }}
                  value={room}
                  onChange={(e) => setRoom(e.target.value)}
                  disabled={voiceStatus !== "disconnected"}
                />
              </label>
              <div className="fg-row" style={{ marginTop: 8 }}>
                <button
                  type="button"
                  onClick={handleMicClick}
                  disabled={!selectedActorId || voiceStatus === "connecting"}
                  className={`fg-mic-btn ${
                    voiceStatus === "connected" ? (micEnabled ? "fg-mic-btn--live" : "fg-mic-btn--muted") : ""
                  }`}
                  aria-label={
                    voiceStatus !== "connected"
                      ? "Connect and speak a mission command"
                      : micEnabled
                        ? "Mute microphone"
                        : "Unmute microphone"
                  }
                  title={
                    voiceStatus === "disconnected"
                      ? "Click to speak a mission command"
                      : voiceStatus === "connecting"
                        ? "Connecting…"
                        : micEnabled
                          ? "Listening — click to mute"
                          : "Muted — click to unmute"
                  }
                >
                  🎤
                </button>
                <span className="fg-mic-status">
                  {voiceStatus === "disconnected" && "Click the mic to speak a command"}
                  {voiceStatus === "connecting" && "Connecting…"}
                  {voiceStatus === "connected" && (micEnabled ? "Listening…" : "Muted")}
                </span>
                {voiceStatus === "connected" && (
                  <button type="button" className="fg-btn" onClick={disconnectVoice}>
                    Disconnect
                  </button>
                )}
              </div>
              <div className="fg-console" style={{ marginTop: 10 }}>
                {voiceState?.last_transcript ? (
                  voiceState.last_transcript
                ) : (
                  <span className="fg-console--empty">no transcript yet</span>
                )}
              </div>
              {voiceState?.clarification_reason && (
                <p style={{ color: "var(--fg-warn)", fontSize: 12 }}>{voiceState.clarification_reason}</p>
              )}
              {voiceState?.error && <p style={{ color: "var(--fg-danger)", fontSize: 12 }}>{voiceState.error}</p>}
            </div>
          </div>

          <div className="fg-panel">
            <div className="fg-panel-header">
              <span>Chat{selectedActorId ? ` · ${selectedActorId}` : ""}</span>
            </div>
            <div className="fg-panel-body">
              <div className="fg-chat-log" ref={chatLogRef}>
                {chatMessages.length === 0 && (
                  <div className="fg-console">
                    <span className="fg-console--empty">Spoken commands will appear here — edit before sending.</span>
                  </div>
                )}
                {chatMessages.map((m) => (
                  <div key={m.id} className={`fg-chat-msg fg-chat-msg--${m.role}`}>
                    <span className="fg-chat-msg-label">
                      {m.role === "transcript" ? "heard" : m.role === "sent" ? "sent" : "error"}
                    </span>
                    <span>{m.text}</span>
                  </div>
                ))}
              </div>
              <div className="fg-row" style={{ marginTop: 8 }}>
                <input
                  className="fg-input"
                  style={{ flex: 1 }}
                  placeholder="Speak, or type a mission command…"
                  value={draftText}
                  onChange={(e) => setDraftText(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !sendingDraft) sendDraft();
                  }}
                />
                <button
                  type="button"
                  className="fg-btn fg-btn--primary"
                  onClick={sendDraft}
                  disabled={!draftText.trim() || !selectedActorId || sendingDraft}
                >
                  {sendingDraft ? "Sending…" : "Send"}
                </button>
              </div>
            </div>
          </div>

          <div className="fg-panel">
            <div className="fg-panel-header">
              <span>Goals{selectedActorId ? ` · ${selectedActorId}` : ""}</span>
            </div>
            <div className="fg-panel-body">
              {goalsError && <p style={{ color: "var(--fg-danger)", fontSize: 12 }}>{goalsError}</p>}
              {!goalsError && goals.length === 0 && (
                <div className="fg-console">
                  <span className="fg-console--empty">No goals recorded for this actor.</span>
                </div>
              )}
              {goals.length > 0 && (
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {goals.map((goal, i) => (
                    <li key={`${goal}-${i}`}>{goal}</li>
                  ))}
                </ul>
              )}
            </div>
          </div>

          <div className="fg-panel">
            <div className="fg-panel-header">
              <span>Approvals</span>
              <span className={`fg-badge ${approvals.length > 0 ? "fg-badge--danger" : "fg-badge--muted"}`}>
                {approvals.length > 0 ? `${approvals.length} pending` : "none pending"}
              </span>
            </div>
            <div className="fg-panel-body">
              {approvalsError && <p style={{ color: "var(--fg-danger)", fontSize: 12 }}>{approvalsError}</p>}
              {!approvalsError && approvals.length === 0 && (
                <div className="fg-console">
                  <span className="fg-console--empty">No operations awaiting approval.</span>
                </div>
              )}
              {approvals.map((a) => (
                <div key={a.approval_id} className="fg-console" style={{ marginTop: 8 }}>
                  <div>
                    <strong>{a.target_operation}</strong> on {a.target_resource}
                  </div>
                  <div style={{ color: "var(--fg-muted)", fontSize: 11 }}>
                    requested by {a.requesting_principal} · risk {a.risk_level} · rule {a.policy_rule}
                  </div>
                  <div className="fg-row" style={{ marginTop: 6 }}>
                    <button
                      type="button"
                      className="fg-btn fg-btn--primary"
                      disabled={busyApprovalId === a.approval_id}
                      onClick={() => decideApproval(a.approval_id, "approve")}
                    >
                      Approve
                    </button>
                    <button
                      type="button"
                      className="fg-btn"
                      disabled={busyApprovalId === a.approval_id}
                      onClick={() => decideApproval(a.approval_id, "reject")}
                    >
                      Reject
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function DroneFlightPage() {
  return (
    <RequireAuth>
      <DroneFlightContent />
    </RequireAuth>
  );
}
