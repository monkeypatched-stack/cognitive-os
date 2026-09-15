"use client";

// Voice command session (kernel/edge/voice_command_runtime.py,
// api/routes/voice.py) -- the actual "spoken command becomes a goal" path,
// distinct from livekitClient.ts's fetchLiveKitToken (a generic room-join
// token with no CognitiveOS Observation/goal wiring behind it). The room
// is caller-supplied (server does not resolve it from actor_id the way
// video sessions do) -- for a drone actor, pass the SAME room its camera
// publishes into (see docs/VOICE_DRONE_FLIGHT_DEMO.md) so voice and video
// share one LiveKit room.
import { apiClient } from "./apiClient";

export interface VoiceSessionResponse {
  session_id: string;
  room: string;
  actor_id: string;
  participant_identity: string;
  status: string;
  token: string;
}

export interface VoiceSessionStatusResponse {
  session_id: string;
  status: string;
  last_transcript: string | null;
  last_goal_text: string | null;
  clarification_reason: string | null;
  error: string | null;
}

export function createVoiceSession(room: string, actorId: string, ttlSeconds = 120): Promise<VoiceSessionResponse> {
  return apiClient.request<VoiceSessionResponse>("/voice/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ room, actor_id: actorId, ttl_seconds: ttlSeconds }),
  });
}

export function fetchVoiceSessionStatus(sessionId: string): Promise<VoiceSessionStatusResponse> {
  return apiClient.request<VoiceSessionStatusResponse>(`/voice/sessions/${sessionId}`);
}

export function stopVoiceSession(sessionId: string): Promise<void> {
  return apiClient.request<void>(`/voice/sessions/${sessionId}`, { method: "DELETE" });
}
