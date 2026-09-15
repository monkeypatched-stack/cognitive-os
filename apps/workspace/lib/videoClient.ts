"use client";

// Drone camera video session (kernel/edge/livekit_video_adapter.py,
// api/routes/video.py). Mirrors droneClient.ts's shape exactly. Distinct
// from livekitClient.ts's fetchLiveKitToken (the generic /livekit/token
// room-join endpoint voice/page.tsx uses) -- this hits the drone-camera-
// specific route, which resolves actor_id -> room/camera_track_name
// server-side (kernel/edge/camera_state.py::get_camera_identity) and
// mints a SUBSCRIBE-ONLY token, never publish.
import { apiClient } from "./apiClient";

export interface VideoSessionResponse {
  session_id: string;
  room: string;
  actor_id: string;
  camera_track_name: string;
  participant_identity: string;
  status: string;
  token: string;
}

export interface VideoSessionStatusResponse {
  session_id: string;
  status: string;
  stream_available: boolean;
  last_observation_attribute: string | null;
  last_observation_value: unknown;
  last_observation_confidence: number | null;
  error: string | null;
}

export function createVideoSession(actorId: string, ttlSeconds = 120): Promise<VideoSessionResponse> {
  return apiClient.request<VideoSessionResponse>("/video/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ actor_id: actorId, ttl_seconds: ttlSeconds }),
  });
}

export function fetchVideoSessionStatus(sessionId: string): Promise<VideoSessionStatusResponse> {
  return apiClient.request<VideoSessionStatusResponse>(`/video/sessions/${sessionId}`);
}

export function stopVideoSession(sessionId: string): Promise<void> {
  return apiClient.request<void>(`/video/sessions/${sessionId}`, { method: "DELETE" });
}
