"use client";

// Voice -> simulated drone flight + live camera, primary demo surface
// (docs/VOICE_DRONE_FLIGHT_DEMO.md has the full runbook). Two INDEPENDENT
// LiveKit Room connections on purpose -- voice (publish mic, drives real
// goals via voiceSessionClient.ts -> api/routes/voice.py ->
// VoiceCommandRuntime -> ActorRuntime.add_goal() -> SocietyRuntime.
// tick_one_actor(), never a REST shortcut into PX4) and camera (subscribe-
// only, via videoClient.ts -> api/routes/video.py, resolved server-side
// from camera_state.py's CameraIdentity) -- so the camera keeps streaming
// regardless of whether voice succeeds, fails, or was never connected at
// all, not merely asserted to.
//
// No @livekit/components-react in this workspace (see package.json) --
// the camera track is attached to a plain <video> element by hand via
// livekit-client's own track.attach()/detach(), the same primitive
// voice/page.tsx already uses for mic control.
import { useEffect, useRef, useState } from "react";
import { Room, RoomEvent, type RemoteTrack, type RemoteTrackPublication } from "livekit-client";
import { RequireAuth } from "../../components/RequireAuth";
import { fetchAllActors, type Actor } from "../../lib/actorClient";
import { LIVEKIT_URL } from "../../lib/livekitClient";
import { createVideoSession, fetchVideoSessionStatus, type VideoSessionResponse } from "../../lib/videoClient";
import {
  createVoiceSession,
  fetchVoiceSessionStatus,
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
    setVideoSession(null);
    setCameraLive(false);
  };

  return (
    <div className="page">
      <h1>Drone Flight</h1>
      <p>
        Voice command drives the real Actor goal/plan/governance path; camera streams independently. Say something
        like: <em>&ldquo;{EXAMPLE_COMMAND}&rdquo;</em>
      </p>
      {actors.length > 0 && (
        <select className="actor-picker" value={selectedActorId} onChange={(e) => setSelectedActorId(e.target.value)}>
          {actors.map((actor) => (
            <option key={actor.actor_id} value={actor.actor_id}>
              {actor.name || actor.actor_id}
            </option>
          ))}
        </select>
      )}
      {error && <div className="empty-state">{error}</div>}

      <div className="login-card" style={{ width: "auto", maxWidth: 640 }}>
        <p>
          <strong>{selectedActorId || "—"}</strong>
        </p>
        <p>
          Connection: <strong>{voiceStatus === "connected" || cameraStatus === "connected" ? "Connected" : "Disconnected"}</strong>
        </p>
        <p>
          Flight state: <strong>{flightPhase}</strong>
        </p>
        <p>
          Camera: <strong>{cameraLive ? "LIVE" : "not connected"}</strong>
        </p>

        <h3>Camera</h3>
        {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
        <video ref={videoRef} autoPlay playsInline muted style={{ width: "100%", maxWidth: 480, background: "#000" }} />
        <div className="actions">
          {cameraStatus !== "connected" ? (
            <button type="button" onClick={connectCamera} disabled={!selectedActorId || cameraStatus === "connecting"}>
              {cameraStatus === "connecting" ? "Connecting…" : "Connect camera"}
            </button>
          ) : (
            <button type="button" onClick={disconnectCamera}>
              Disconnect camera
            </button>
          )}
        </div>

        <h3>Voice</h3>
        <label>
          Room
          <input value={room} onChange={(e) => setRoom(e.target.value)} disabled={voiceStatus !== "disconnected"} />
        </label>
        <div className="actions">
          {voiceStatus === "disconnected" && (
            <button type="button" onClick={connectVoice} disabled={!selectedActorId}>
              Connect voice
            </button>
          )}
          {voiceStatus === "connecting" && (
            <button type="button" disabled>
              Connecting…
            </button>
          )}
          {voiceStatus === "connected" && (
            <>
              <button type="button" onClick={toggleMic}>
                {micEnabled ? "Mute" : "Unmute"}
              </button>
              <button type="button" onClick={disconnectVoice}>
                Disconnect voice
              </button>
            </>
          )}
        </div>

        <h3>Transcript</h3>
        <p>{voiceState?.last_transcript ?? "—"}</p>
        {voiceState?.clarification_reason && <p className="login-error">{voiceState.clarification_reason}</p>}
        {voiceState?.error && <p className="login-error">{voiceState.error}</p>}
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
