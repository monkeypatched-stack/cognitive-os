"use client";

// The Human Experience <-> Realtime & Perception arrow: this page is the
// first (and, as of this MVP, only) real consumer of the LiveKit room a
// human can join to talk to an actor. The backend side of this pipeline
// (kernel/edge/livekit_adapter.py's LiveKitVoiceObservationProvider) has
// existed since earlier work in this same session; nothing in this
// frontend joined a room until now, which was the one genuinely missing
// arrow in the "Human Experience -> Realtime & Perception" layer of the
// 5-layer architecture.
import { useEffect, useRef, useState } from "react";
import { Room, RoomEvent, type RemoteParticipant } from "livekit-client";
import { RequireAuth } from "../../components/RequireAuth";
import { fetchLiveKitToken, LIVEKIT_URL } from "../../lib/livekitClient";

function VoiceContent() {
  const [roomName, setRoomName] = useState("cognitiveos-actor-1");
  const [status, setStatus] = useState<"disconnected" | "connecting" | "connected">("disconnected");
  const [micEnabled, setMicEnabled] = useState(false);
  const [participants, setParticipants] = useState<string[]>([]);
  const [error, setError] = useState("");
  const roomRef = useRef<Room | null>(null);

  useEffect(() => {
    return () => {
      roomRef.current?.disconnect();
    };
  }, []);

  const connect = async () => {
    if (!LIVEKIT_URL) {
      setError("NEXT_PUBLIC_LIVEKIT_URL is not set — no LiveKit server configured for this deployment.");
      return;
    }
    setError("");
    setStatus("connecting");
    try {
      const { token } = await fetchLiveKitToken(roomName);
      const room = new Room();

      room.on(RoomEvent.Connected, () => setStatus("connected"));
      room.on(RoomEvent.Disconnected, () => {
        setStatus("disconnected");
        setParticipants([]);
      });
      const refreshParticipants = () => {
        setParticipants(Array.from(room.remoteParticipants.values()).map((p: RemoteParticipant) => p.identity));
      };
      room.on(RoomEvent.ParticipantConnected, refreshParticipants);
      room.on(RoomEvent.ParticipantDisconnected, refreshParticipants);

      await room.connect(LIVEKIT_URL, token);
      roomRef.current = room;
      refreshParticipants();
    } catch (err) {
      setStatus("disconnected");
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const disconnect = async () => {
    await roomRef.current?.disconnect();
    roomRef.current = null;
    setMicEnabled(false);
  };

  const toggleMic = async () => {
    const room = roomRef.current;
    if (!room) return;
    const next = !micEnabled;
    await room.localParticipant.setMicrophoneEnabled(next);
    setMicEnabled(next);
  };

  return (
    <div className="page">
      <h1>Voice</h1>
      <p>Join a LiveKit room to talk to an actor directly.</p>
      <div className="login-card" style={{ width: "auto", maxWidth: 420 }}>
        <label>
          Room
          <input value={roomName} onChange={(e) => setRoomName(e.target.value)} disabled={status !== "disconnected"} />
        </label>
        {error && <div className="login-error">{error}</div>}
        <div className="actions">
          {status === "disconnected" && (
            <button type="button" onClick={connect}>
              Connect
            </button>
          )}
          {status === "connecting" && <button type="button" disabled>Connecting…</button>}
          {status === "connected" && (
            <>
              <button type="button" onClick={toggleMic}>
                {micEnabled ? "Mute" : "Unmute"}
              </button>
              <button type="button" onClick={disconnect}>
                Disconnect
              </button>
            </>
          )}
        </div>
        <p>
          Status: <strong>{status}</strong>
        </p>
        {status === "connected" && (
          <>
            <p>Participants in room:</p>
            {participants.length === 0 && <div className="empty-state">Just you so far.</div>}
            <ul>
              {participants.map((identity) => (
                <li key={identity}>{identity}</li>
              ))}
            </ul>
          </>
        )}
      </div>
    </div>
  );
}

export default function VoicePage() {
  return (
    <RequireAuth>
      <VoiceContent />
    </RequireAuth>
  );
}
