import { apiClient } from './client'

// Wraps the voice session REST surface (src/monkey_brain/api/routes/
// voice.py) -- session setup / token issuance / status polling only.
// Realtime audio itself never goes through this client or apiClient: the
// browser publishes microphone audio straight to LiveKit using the
// returned token (see VoicePanel.tsx), matching the backend's own
// "REST for session setup, LiveKit for realtime audio" split.

export interface VoiceSession {
  session_id: string
  room: string
  actor_id: string
  participant_identity: string
  status: string
  token: string
}

export interface VoiceSessionStatus {
  session_id: string
  status: string
  last_transcript: string | null
  last_goal_text: string | null
  clarification_reason: string | null
  error: string | null
}

export function createVoiceSession(room: string, actorId: string): Promise<VoiceSession> {
  return apiClient.request('/voice/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ room, actor_id: actorId }),
  })
}

export function fetchVoiceSessionStatus(sessionId: string): Promise<VoiceSessionStatus> {
  return apiClient.request(`/voice/sessions/${sessionId}`)
}

export function stopVoiceSession(sessionId: string): Promise<{ session_id: string; stopped: boolean }> {
  return apiClient.request(`/voice/sessions/${sessionId}`, { method: 'DELETE' })
}
