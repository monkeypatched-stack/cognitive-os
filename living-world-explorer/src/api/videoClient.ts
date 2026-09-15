import { apiClient } from './client'

// Wraps the drone camera video session REST surface (src/monkey_brain/api/
// routes/video.py) -- session setup / token issuance / status polling only.
// Realtime video itself never goes through this client or apiClient: the
// browser SUBSCRIBES to the drone's camera track straight from LiveKit
// using the returned token (see VideoPanel.tsx) -- this client never
// carries a video frame, matching voiceClient.ts's own "REST for session
// setup, LiveKit for realtime media" split.

export interface VideoSession {
  session_id: string
  room: string
  actor_id: string
  camera_track_name: string
  participant_identity: string
  status: string
  token: string
}

export interface VideoSessionStatus {
  session_id: string
  status: string
  stream_available: boolean
  last_observation_attribute: string | null
  last_observation_value: unknown
  last_observation_confidence: number | null
  error: string | null
}

export function createVideoSession(actorId: string): Promise<VideoSession> {
  return apiClient.request('/video/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ actor_id: actorId }),
  })
}

export function fetchVideoSessionStatus(sessionId: string): Promise<VideoSessionStatus> {
  return apiClient.request(`/video/sessions/${sessionId}`)
}

export function stopVideoSession(sessionId: string): Promise<{ session_id: string; stopped: boolean }> {
  return apiClient.request(`/video/sessions/${sessionId}`, { method: 'DELETE' })
}
