import { useEffect, useRef, useState } from 'react'
import { Room, RoomEvent } from 'livekit-client'
import { PanelContainer } from './PanelContainer'
import { createVoiceSession, fetchVoiceSessionStatus, stopVoiceSession, type VoiceSession, type VoiceSessionStatus } from '../api/voiceClient'
import './VoicePanel.css'

// Minimal voice interaction (CognitiveOS LiveKit Voice Command Integration,
// spec section 17): join a LiveKit room, publish the microphone, show
// connection/mic state plus whatever the backend's voice session status
// reports (transcript, recognized goal, clarification, errors). This is
// deliberately NOT a conversational UI -- there is no chat history, no
// text input, no bidirectional dialogue; it shows what the CognitiveOS
// Actor is doing with what it heard, nothing more.
//
// Realtime audio never touches apiClient/fetch: the browser publishes
// microphone audio straight to the LiveKit room using the token
// voiceClient.createVoiceSession() returns (section 4/18) -- this
// component never sends raw audio through the REST API, and never
// receives a LiveKit secret (only a short-TTL, scoped room token).
const POLL_INTERVAL_MS = 1500

const STATUS_LABEL: Record<string, string> = {
  listening: 'Listening',
  clarification_required: 'Needs clarification',
  planning: 'Planning…',
  idle: 'Idle',
  stopped: 'Stopped',
}

export function VoicePanel() {
  const [room, setRoom] = useState('cognitiveos-voice')
  const [actorId, setActorId] = useState('')
  const [connecting, setConnecting] = useState(false)
  const [connected, setConnected] = useState(false)
  const [micEnabled, setMicEnabled] = useState(false)
  const [session, setSession] = useState<VoiceSession | null>(null)
  const [status, setStatus] = useState<VoiceSessionStatus | null>(null)
  const [error, setError] = useState('')
  const livekitRoomRef = useRef<Room | null>(null)

  useEffect(() => {
    if (!session) return
    let cancelled = false
    const poll = () => {
      fetchVoiceSessionStatus(session.session_id)
        .then((s) => { if (!cancelled) setStatus(s) })
        .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : String(err)) })
    }
    poll()
    const id = window.setInterval(poll, POLL_INTERVAL_MS)
    return () => { cancelled = true; window.clearInterval(id) }
  }, [session])

  const join = async () => {
    if (!actorId.trim()) { setError('Enter an actor id first.'); return }
    setError('')
    setConnecting(true)
    try {
      const newSession = await createVoiceSession(room.trim(), actorId.trim())
      const livekitUrl = import.meta.env.VITE_LIVEKIT_URL as string | undefined
      if (!livekitUrl) throw new Error('VITE_LIVEKIT_URL is not configured')

      const lkRoom = new Room()
      lkRoom.on(RoomEvent.Disconnected, () => {
        setConnected(false)
        setMicEnabled(false)
      })

      await lkRoom.connect(livekitUrl, newSession.token)
      setConnected(true)
      livekitRoomRef.current = lkRoom

      try {
        await lkRoom.localParticipant.setMicrophoneEnabled(true)
        setMicEnabled(true)
      } catch (micErr) {
        // Microphone unavailable (permission denied / no device) — visible
        // state, no fake transcript, connection to CognitiveOS stays up
        // (section 19).
        setError(`Microphone unavailable: ${micErr instanceof Error ? micErr.message : String(micErr)}`)
      }

      setSession(newSession)
    } catch (err) {
      // LiveKit/session-setup failure — visible UI error; CognitiveOS
      // itself remains operational, only this voice session failed to
      // start (section 19).
      setError(err instanceof Error ? err.message : String(err))
      setConnected(false)
    } finally {
      setConnecting(false)
    }
  }

  const leave = async () => {
    livekitRoomRef.current?.disconnect()
    livekitRoomRef.current = null
    setConnected(false)
    setMicEnabled(false)
    if (session) {
      try { await stopVoiceSession(session.session_id) } catch { /* best-effort */ }
    }
    setSession(null)
    setStatus(null)
  }

  return <PanelContainer title="Voice Command">
    <div className="lwe-voice-panel">
      {!session && <div className="lwe-voice-setup">
        <label>
          Room
          <input value={room} onChange={(e) => setRoom(e.target.value)} disabled={connecting} />
        </label>
        <label>
          Actor id
          <input value={actorId} onChange={(e) => setActorId(e.target.value)} placeholder="drone-a" disabled={connecting} />
        </label>
        <button type="button" onClick={join} disabled={connecting}>
          {connecting ? 'Connecting…' : 'Hold to Speak'}
        </button>
      </div>}

      {session && <div className="lwe-voice-active">
        <div className="lwe-voice-indicators">
          <span className={`lwe-voice-dot ${connected ? 'lwe-voice-dot-on' : 'lwe-voice-dot-off'}`} />
          <span>{connected ? 'Connected to LiveKit' : 'Disconnected'}</span>
          <span className={`lwe-voice-dot ${micEnabled ? 'lwe-voice-dot-on' : 'lwe-voice-dot-off'}`} />
          <span>{micEnabled ? 'Microphone live' : 'Microphone off'}</span>
        </div>

        <div className="lwe-voice-field">
          <div className="lwe-voice-label">You said</div>
          <div className="lwe-voice-value">{status?.last_transcript ?? '—'}</div>
        </div>

        <div className="lwe-voice-field">
          <div className="lwe-voice-label">Recognized goal</div>
          <div className="lwe-voice-value">{status?.last_goal_text ?? '—'}</div>
        </div>

        <div className="lwe-voice-field">
          <div className="lwe-voice-label">Status</div>
          <div className={`lwe-voice-status lwe-voice-status-${status?.status ?? 'idle'}`}>
            {STATUS_LABEL[status?.status ?? 'idle'] ?? status?.status ?? '—'}
          </div>
        </div>

        {status?.status === 'clarification_required' && status.clarification_reason && (
          <div className="lwe-voice-clarification">Actor: {status.clarification_reason}</div>
        )}

        {status?.error && <div className="lwe-voice-error">{status.error}</div>}

        <button type="button" onClick={leave}>Leave</button>
      </div>}

      {error && <div className="lwe-voice-error">{error}</div>}
    </div>
  </PanelContainer>
}
