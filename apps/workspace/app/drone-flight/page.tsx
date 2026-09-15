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
import { fetchAllActors, type Actor } from "../../lib/actorClient";
import { deriveMissionStatus, fetchActorBeliefs, type DroneMissionStatus } from "../../lib/droneClient";
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
          setMissionStatus(deriveMissionStatus(result.beliefs.facts ?? []));
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

  useEffect(() => {
    return () => {
      voiceRoomRef.current?.disconnect();
      cameraRoomRef.current?.disconnect();
    };
  }, []);

  const connectVoice = async () => {
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
            </div>
            <div className="fg-panel-body">
              <dl className="fg-kv">
                <dt>actor</dt>
                <dd>{selectedActorId || "—"}</dd>
                <dt>flight_state</dt>
                <dd>{flightPhase}</dd>
                <dt>voice</dt>
                <dd>{voiceStatus}</dd>
                <dt>camera</dt>
                <dd>{cameraLive ? "live" : "idle"}</dd>
              </dl>
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
                {voiceStatus === "disconnected" && (
                  <button type="button" className="fg-btn fg-btn--primary" onClick={connectVoice} disabled={!selectedActorId}>
                    Connect voice
                  </button>
                )}
                {voiceStatus === "connecting" && (
                  <button type="button" className="fg-btn" disabled>
                    Connecting…
                  </button>
                )}
                {voiceStatus === "connected" && (
                  <>
                    <button type="button" className="fg-btn" onClick={toggleMic}>
                      {micEnabled ? "Mute" : "Unmute"}
                    </button>
                    <button type="button" className="fg-btn" onClick={disconnectVoice}>
                      Disconnect voice
                    </button>
                  </>
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
