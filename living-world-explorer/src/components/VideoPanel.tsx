import { useEffect, useRef, useState } from 'react'
import { Room, RoomEvent, Track, type RemoteTrack, type RemoteTrackPublication } from 'livekit-client'
import { PanelContainer } from './PanelContainer'
import { createVideoSession, fetchVideoSessionStatus, stopVideoSession, type VideoSession, type VideoSessionStatus } from '../api/videoClient'
import './VideoPanel.css'

// Minimal drone-camera video viewer (CognitiveOS Drone Camera Video
// Telemetry spec's own "FRONTEND"-equivalent requirement, carried over
// from VoicePanel.tsx's precedent -- same deliberately-not-a-large-UI
// scope: connection state, the live feed, and whatever the backend's
// video session status reports (stream availability, last visual
// observation, errors). No manual camera controls, no recording UI.
//
// The browser here SUBSCRIBES to the drone's own camera track (published
// server-side by kernel/edge/livekit_video_adapter.py::
// RosCameraToLiveKitBridge) -- it never publishes its own camera, and the
// token videoClient.createVideoSession() returns is subscribe-only
// (can_publish=False), matching the backend route's own token grant.
const POLL_INTERVAL_MS = 1500

export function VideoPanel() {
  const [actorId, setActorId] = useState('')
  const [connecting, setConnecting] = useState(false)
  const [connected, setConnected] = useState(false)
  const [trackAttached, setTrackAttached] = useState(false)
  const [session, setSession] = useState<VideoSession | null>(null)
  const [status, setStatus] = useState<VideoSessionStatus | null>(null)
  const [error, setError] = useState('')
  const livekitRoomRef = useRef<Room | null>(null)
  const videoElRef = useRef<HTMLVideoElement | null>(null)

  useEffect(() => {
    if (!session) return
    let cancelled = false
    const poll = () => {
      fetchVideoSessionStatus(session.session_id)
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
      const newSession = await createVideoSession(actorId.trim())
      const livekitUrl = import.meta.env.VITE_LIVEKIT_URL as string | undefined
      if (!livekitUrl) throw new Error('VITE_LIVEKIT_URL is not configured')

      const lkRoom = new Room()
      lkRoom.on(RoomEvent.Disconnected, () => {
        setConnected(false)
        setTrackAttached(false)
      })
      lkRoom.on(RoomEvent.TrackSubscribed, (track: RemoteTrack, publication: RemoteTrackPublication) => {
        if (track.kind === Track.Kind.Video && publication.trackName === newSession.camera_track_name && videoElRef.current) {
          track.attach(videoElRef.current)
          setTrackAttached(true)
        }
      })
      lkRoom.on(RoomEvent.TrackUnsubscribed, (track: RemoteTrack) => {
        // Track loss becomes visible UI state, never a crash (spec
        // ERROR HANDLING: "Video loss must become an observation/error
        // condition rather than crashing the Actor" -- the backend's own
        // camera_stream_available observation covers the CognitiveOS
        // side; this covers the browser's own view of the same event).
        if (track.kind === Track.Kind.Video) {
          track.detach()
          setTrackAttached(false)
        }
      })

      await lkRoom.connect(livekitUrl, newSession.token)
      setConnected(true)
      livekitRoomRef.current = lkRoom
      setSession(newSession)
    } catch (err) {
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
    setTrackAttached(false)
    if (session) {
      try { await stopVideoSession(session.session_id) } catch { /* best-effort */ }
    }
    setSession(null)
    setStatus(null)
  }

  return <PanelContainer title="Drone Camera">
    <div className="lwe-video-panel">
      {!session && <div className="lwe-video-setup">
        <label>
          Actor id
          <input value={actorId} onChange={(e) => setActorId(e.target.value)} placeholder="drone-a" disabled={connecting} />
        </label>
        <button type="button" onClick={join} disabled={connecting}>
          {connecting ? 'Connecting…' : 'Watch Camera'}
        </button>
      </div>}

      {session && <div className="lwe-video-active">
        <div className="lwe-video-indicators">
          <span className={`lwe-video-dot ${connected ? 'lwe-video-dot-on' : 'lwe-video-dot-off'}`} />
          <span>{connected ? 'Connected to LiveKit' : 'Disconnected'}</span>
          <span className={`lwe-video-dot ${status?.stream_available ? 'lwe-video-dot-on' : 'lwe-video-dot-off'}`} />
          <span>{status?.stream_available ? 'Camera stream live' : 'Camera stream unavailable'}</span>
        </div>

        <video ref={videoElRef} className="lwe-video-feed" autoPlay playsInline muted />
        {!trackAttached && <div className="lwe-video-placeholder">Waiting for camera track…</div>}

        <div className="lwe-video-field">
          <div className="lwe-video-label">Last visual observation</div>
          <div className="lwe-video-value">
            {status?.last_observation_attribute
              ? `${status.last_observation_attribute} = ${String(status.last_observation_value)} (confidence ${status.last_observation_confidence?.toFixed(2) ?? '—'})`
              : '—'}
          </div>
        </div>

        {status?.error && <div className="lwe-video-error">{status.error}</div>}

        <button type="button" onClick={leave}>Leave</button>
      </div>}

      {error && <div className="lwe-video-error">{error}</div>}
    </div>
  </PanelContainer>
}
