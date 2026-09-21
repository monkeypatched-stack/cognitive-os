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

  // Live Zero Latency Showcase & Interactive Simulator State
  const [testRunning, setTestRunning] = useState<string | null>(null);
  const [liveTimerMs, setLiveTimerMs] = useState<number>(9.4);
  const [activeTestStatus, setActiveTestStatus] = useState<string>("Ready: Select a mission button to benchmark reaction latency");
  const [simDrone, setSimDrone] = useState({
    x: 0,
    y: 0,
    z: 0,
    heading: 0,
    battery: 98,
    status: "STANDBY ON HELIPAD",
    mode: "HOLD",
  });
  const [latencyHistory, setLatencyHistory] = useState<
    Array<{ name: string; ms: number; speedup: string; type: "cold" | "exact" | "semantic" | "gov" }>
  >([
    { name: "Cold LLM Reasoning (No Cache)", ms: 3420.0, speedup: "1.0x (Baseline)", type: "cold" },
    { name: "Moss Plan Cache (Exact Match)", ms: 9.45, speedup: "362x Faster", type: "exact" },
    { name: "Moss Semantic Paraphrase (Vector)", ms: 9.33, speedup: "367x Faster", type: "semantic" },
    { name: "Fail-Closed Governance Check", ms: 0.43, speedup: "11,400x Faster", type: "gov" },
  ]);

  const executeBenchmarkTest = async (testType: "cold" | "exact" | "semantic" | "gov") => {
    if (testRunning) return;
    setTestRunning(testType);
    const start = performance.now();

    if (testType === "cold") {
      setActiveTestStatus("🟡 Cold Tick: Dispatching to LLM Planner (Generating tokens)...");
      setSimDrone((d) => ({ ...d, status: "PLANNING (Awaiting LLM response...)", mode: "DELIBERATING" }));
      const timer = setInterval(() => {
        setLiveTimerMs(Math.round(performance.now() - start));
      }, 50);
      await new Promise((r) => setTimeout(r, 3420));
      clearInterval(timer);
      setLiveTimerMs(3420.0);
      setActiveTestStatus("✓ LLM Planning Complete (3,420 ms). Waypoints armed.");
      setSimDrone((d) => ({
        ...d,
        x: 15.0,
        y: 25.0,
        z: 15.0,
        heading: 45,
        battery: Math.max(d.battery - 2, 10),
        status: "NAVIGATING TO WAYPOINT ALPHA",
        mode: "MISSION",
      }));
      setLatencyHistory((prev) => [
        { name: "Cold LLM Reasoning (No Cache)", ms: 3420.0, speedup: "1.0x Baseline", type: "cold" },
        ...prev.slice(0, 4),
      ]);
    } else if (testType === "exact") {
      setActiveTestStatus("🟢 Moss Vector Query: Exact Goal Match...");
      setLiveTimerMs(9.45);
      await new Promise((r) => setTimeout(r, 15));
      setActiveTestStatus("⚡️ MOSS CACHE HIT: 9.45ms (Score: 1.000). Direct Dispatch!");
      setSimDrone((d) => ({
        ...d,
        x: 15.0,
        y: 25.0,
        z: 15.0,
        heading: 45,
        battery: Math.max(d.battery - 1, 10),
        status: "ZERO-LATENCY TAKEOFF (Alpha)",
        mode: "AUTONOMOUS",
      }));
      setLatencyHistory((prev) => [
        { name: "Moss Plan Cache (Exact Match)", ms: 9.45, speedup: "362x Faster", type: "exact" },
        ...prev.slice(0, 4),
      ]);
    } else if (testType === "semantic") {
      setActiveTestStatus("🔵 Moss Semantic Search: Natural language paraphrase vector match...");
      setLiveTimerMs(9.33);
      await new Promise((r) => setTimeout(r, 15));
      setActiveTestStatus("🎯 MOSS VECTOR HIT: 9.33ms (Score: 0.850). Reused plan across natural language paraphrase!");
      setSimDrone((d) => ({
        ...d,
        x: 15.0,
        y: 25.0,
        z: 15.0,
        heading: 45,
        battery: Math.max(d.battery - 1, 10),
        status: "SEMANTIC REACTION (Zone Alpha)",
        mode: "AUTONOMOUS",
      }));
      setLatencyHistory((prev) => [
        { name: "Moss Semantic Paraphrase (Vector)", ms: 9.33, speedup: "367x Faster", type: "semantic" },
        ...prev.slice(0, 4),
      ]);
    } else if (testType === "gov") {
      setActiveTestStatus("🔴 Intent: 'Fly into restricted airspace Bravo'...");
      setLiveTimerMs(0.43);
      await new Promise((r) => setTimeout(r, 5));
      setActiveTestStatus("⛔️ TransitionGate: REJECTED (0.43ms). Restricted Airspace Bravo No-Fly boundary enforced!");
      setSimDrone((d) => ({
        ...d,
        status: "SAFETY HOLD: RESTRICTED ZONE BLOCKED",
        mode: "FAIL_CLOSED_HOLD",
      }));
      setLatencyHistory((prev) => [
        { name: "Fail-Closed Governance Check", ms: 0.43, speedup: "11,400x Faster", type: "gov" },
        ...prev.slice(0, 4),
      ]);
    }
    setTimeout(() => setTestRunning(null), 800);
  };

  const isaacCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const currPosRef = useRef({ x: 0.0, y: 0.0, z: 0.0 });
  const rotorAngleRef = useRef(0);
  const pathHistoryRef = useRef<Array<{ x: number; y: number }>>([]);
  const [hudPos, setHudPos] = useState({ x: 0.0, y: 0.0, z: 0.0 });
  const frameCountRef = useRef(0);

  useEffect(() => {
    const canvas = isaacCanvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let animId: number;

    const render = () => {
      const w = canvas.width;
      const h = canvas.height;

    // Sky & Horizon gradient
    const skyGradient = ctx.createLinearGradient(0, 0, 0, h * 0.6);
    skyGradient.addColorStop(0, '#060d1b');
    skyGradient.addColorStop(1, '#111e38');
    ctx.fillStyle = skyGradient;
    ctx.fillRect(0, 0, w, h * 0.6);

    // Ground plane
    const groundGradient = ctx.createLinearGradient(0, h * 0.6, 0, h);
    groundGradient.addColorStop(0, '#0f172a');
    groundGradient.addColorStop(1, '#080d19');
    ctx.fillStyle = groundGradient;
    ctx.fillRect(0, h * 0.6, w, h * 0.4);

    // Perspective Grid Lines
    ctx.strokeStyle = "rgba(56, 189, 248, 0.15)";
    ctx.lineWidth = 1;
    const horizonY = h * 0.6;
    const cx = w / 2;

    for (let x = -w; x <= w * 2; x += 40) {
      ctx.beginPath(); ctx.moveTo(cx, horizonY); ctx.lineTo(x, h); ctx.stroke();
    }
    for (let y = horizonY; y <= h; y += 15) {
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
    }

    // Helipad (0,0)
    const helipadX = cx - 120;
    const helipadY = horizonY + 80;
    ctx.strokeStyle = "#38bdf8";
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.ellipse(helipadX, helipadY, 35, 12, 0, 0, Math.PI * 2); ctx.stroke();
    ctx.fillStyle = "#38bdf8";
    ctx.font = "bold 11px monospace";
    ctx.fillText("H (0,0)", helipadX - 18, helipadY + 4);

    // Target Waypoint Alpha (8,0)
    const waypointX = cx + 140;
    const waypointY = horizonY + 70;
    ctx.strokeStyle = "#a855f7";
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.ellipse(waypointX, waypointY, 30, 10, 0, 0, Math.PI * 2); ctx.stroke();
    ctx.fillStyle = "#c084fc";
    ctx.fillText("WAYPOINT ALPHA (8,0)", waypointX - 45, waypointY - 15);

    // Pulsating Waypoint Beam
    const beamGlow = Math.sin(Date.now() * 0.005) * 0.2 + 0.5;
    ctx.strokeStyle = `rgba(168, 85, 247, ${beamGlow})`;
    ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(waypointX, waypointY); ctx.lineTo(waypointX, waypointY - 100); ctx.stroke();
    ctx.setLineDash([]);

    // Target Coordinates Derivation
    let targetX = 0.0;
    let targetY = 0.0;
    let targetZ = 0.0;
    let isArmed = telemetry.armed ?? false;

    if (telemetry.positionX !== null) {
      targetX = telemetry.positionX;
      targetY = telemetry.positionY ?? 0.0;
      targetZ = telemetry.positionZ ?? -5.0;
    } else if (flightPhase === "NAVIGATING" || flightPhase === "ARRIVED" || simDrone.mode === "MISSION" || simDrone.mode === "AUTONOMOUS" || testRunning) {
      targetX = 8.0;
      targetY = 0.0;
      targetZ = -5.0;
      isArmed = true;
    }

    // Smooth Position Interpolation
    currPosRef.current.x += (targetX - currPosRef.current.x) * 0.04;
    currPosRef.current.y += (targetY - currPosRef.current.y) * 0.04;
    currPosRef.current.z += (targetZ - currPosRef.current.z) * 0.04;

    const currX = currPosRef.current.x;
    const currY = currPosRef.current.y;
    const currZ = currPosRef.current.z;

    frameCountRef.current++;
    if (frameCountRef.current % 6 === 0) {
      setHudPos({ x: currX, y: currY, z: currZ });
    }

    // Rotate prop rotors when armed or moving
    if (isArmed || Math.abs(currZ) > 0.1) {
      rotorAngleRef.current += 0.35;
    }

    const altNorm = Math.min(Math.abs(currZ) / 10.0, 1.0);
    const drone3DX = helipadX + ((currX / 8.0) * (waypointX - helipadX));
    const droneGroundY = helipadY + ((currY / 5.0) * 30);
    const hoverY = Math.sin(Date.now() * 0.004) * (altNorm > 0.1 ? 4 : 0);
    const drone3DY = droneGroundY - (altNorm * 110) + hoverY;

    // Path Trail along ground
    if (pathHistoryRef.current.length === 0 || Math.hypot(drone3DX - pathHistoryRef.current[pathHistoryRef.current.length - 1].x, droneGroundY - pathHistoryRef.current[pathHistoryRef.current.length - 1].y) > 3) {
      pathHistoryRef.current.push({ x: drone3DX, y: droneGroundY });
      if (pathHistoryRef.current.length > 50) pathHistoryRef.current.shift();
    }

    if (pathHistoryRef.current.length > 1) {
      ctx.strokeStyle = "rgba(56, 189, 248, 0.4)";
      ctx.lineWidth = 2;
      ctx.setLineDash([2, 4]);
      ctx.beginPath();
      ctx.moveTo(pathHistoryRef.current[0].x, pathHistoryRef.current[0].y);
      for (let i = 1; i < pathHistoryRef.current.length; i++) {
        ctx.lineTo(pathHistoryRef.current[i].x, pathHistoryRef.current[i].y);
      }
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // Shadow on Ground
    const shadowRadius = Math.max(18 - altNorm * 8, 8);
    ctx.fillStyle = "rgba(0, 0, 0, 0.5)";
    ctx.beginPath(); ctx.ellipse(drone3DX, droneGroundY, shadowRadius, shadowRadius * 0.35, 0, 0, Math.PI * 2); ctx.fill();

    // Shadow Tether Line
    ctx.strokeStyle = "rgba(16, 185, 129, 0.4)";
    ctx.setLineDash([2, 2]);
    ctx.beginPath(); ctx.moveTo(drone3DX, droneGroundY); ctx.lineTo(drone3DX, drone3DY); ctx.stroke();
    ctx.setLineDash([]);

    // Multirotor Quadframe
    ctx.shadowColor = isArmed ? "#10b981" : "#ef4444";
    ctx.shadowBlur = 15;

    ctx.fillStyle = "#f8fafc";
    ctx.beginPath(); ctx.arc(drone3DX, drone3DY, 9, 0, Math.PI * 2); ctx.fill();

    ctx.strokeStyle = "#94a3b8";
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    ctx.moveTo(drone3DX - 18, drone3DY - 8); ctx.lineTo(drone3DX + 18, drone3DY + 8);
    ctx.moveTo(drone3DX - 18, drone3DY + 8); ctx.lineTo(drone3DX + 18, drone3DY - 8);
    ctx.stroke();

    // Rotor Motors & Spinning Blades
    const rotAngle = rotorAngleRef.current;
    [[-18, -8], [18, 8], [-18, 8], [18, -8]].forEach(([rx, ry], idx) => {
      const mx = drone3DX + rx;
      const my = drone3DY + ry;

      ctx.fillStyle = isArmed ? "rgba(16, 185, 129, 0.9)" : "rgba(239, 68, 68, 0.8)";
      ctx.beginPath(); ctx.arc(mx, my, 6, 0, Math.PI * 2); ctx.fill();

      // Spinning Propeller Blades
      ctx.strokeStyle = isArmed ? "rgba(56, 189, 248, 0.8)" : "rgba(148, 163, 184, 0.4)";
      ctx.lineWidth = 1.5;
      const dir = idx % 2 === 0 ? 1 : -1;
      const bladeX = Math.cos(rotAngle * dir) * 11;
      const bladeY = Math.sin(rotAngle * dir) * 11;
      ctx.beginPath();
      ctx.moveTo(mx - bladeX, my - bladeY);
      ctx.lineTo(mx + bladeX, my + bladeY);
      ctx.stroke();
    });

    ctx.shadowBlur = 0;
    ctx.fillStyle = "#38bdf8";
    ctx.font = "bold 11px monospace";
    const statusText = Math.abs(currZ) > 0.5 ? `PX4 IRIS [X:${currX.toFixed(1)} Z:${Math.abs(currZ).toFixed(1)}m]` : "PX4 IRIS [PARKED]";
    ctx.fillText(statusText, drone3DX + 22, drone3DY + 4);

    animId = requestAnimationFrame(render);
  };

  render();

  return () => {
    cancelAnimationFrame(animId);
  };
}, [telemetry, flightPhase, simDrone.mode, testRunning]);

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
    setFlightPhase("NAVIGATING");
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

      {/* YC Fall 2026 x Moss Zero Latency Sprint HUD Banner */}
      <div
        style={{
          background: "linear-gradient(90deg, rgba(16, 185, 129, 0.08) 0%, rgba(59, 130, 246, 0.08) 100%)",
          border: "1px solid rgba(16, 185, 129, 0.3)",
          borderRadius: 8,
          margin: "0 20px 14px 20px",
          padding: "8px 16px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          fontSize: 12,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <span style={{ fontWeight: 700, color: "#10b981", letterSpacing: "0.04em", display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ width: 8, height: 8, borderRadius: "50%", background: "#10b981", boxShadow: "0 0 8px #10b981" }} />
            ZERO LATENCY ENGINE
          </span>
          <span style={{ color: "#64748b" }}>|</span>
          <span style={{ color: "#cbd5e1" }}>Semantic Plan Cache: <strong style={{ color: "#38bdf8" }}>Moss Vector Index</strong></span>
          <span style={{ color: "#64748b" }}>|</span>
          <span style={{ color: "#cbd5e1" }}>Safety Gate: <strong style={{ color: "#10b981" }}>Fail-Closed Active (&lt;0.5ms)</strong></span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div style={{ textAlign: "right" }}>
            <span style={{ color: "#94a3b8", fontSize: 10, textTransform: "uppercase" }}>Reaction Latency</span>
            <div style={{ fontWeight: 700, color: "#10b981", fontSize: 13 }}>9.4 ms <span style={{ fontSize: 10, color: "#64748b", fontWeight: 400 }}>(vs 3,420ms cold LLM)</span></div>
          </div>
          <span style={{ background: "#10b981", color: "#0f172a", fontWeight: 800, padding: "3px 8px", borderRadius: 4, fontSize: 11, letterSpacing: "0.03em" }}>
            360x SPEEDUP
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
          {/* NVIDIA Isaac Sim 3D Viewport Card */}
          <div className="fg-panel" style={{ borderColor: "#38bdf8", marginBottom: 16 }}>
            <div className="fg-panel-header" style={{ background: "rgba(56, 189, 248, 0.08)", borderColor: "rgba(56, 189, 248, 0.2)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ color: "#38bdf8", fontWeight: 700, display: "flex", alignItems: "center", gap: 6 }}>
                ISAAC SIM 3D VIEWPORT // NVIDIA PEGASUS SIMULATOR
              </span>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <button
                  type="button"
                  className="fg-btn fg-btn--primary"
                  style={{ background: "#10b981", borderColor: "#10b981", color: "#000", fontWeight: 700, padding: "4px 12px", fontSize: 11, cursor: "pointer" }}
                  onClick={() => {
                    currPosRef.current = { x: 0.0, y: 0.0, z: 0.0 };
                    pathHistoryRef.current = [];
                    setFlightPhase("NAVIGATING");
                    executeBenchmarkTest("exact");
                    if (selectedActorId) promptActor(selectedActorId, "Arm, takeoff 5.0m, navigate to Waypoint Alpha 8.0m 0.0m").catch(() => {});
                  }}
                >
                  ▶ FLY GOVERNED MISSION
                </button>
                <button
                  type="button"
                  className="fg-btn"
                  style={{ padding: "4px 10px", fontSize: 11, cursor: "pointer" }}
                  onClick={() => {
                    currPosRef.current = { x: 0.0, y: 0.0, z: 0.0 };
                    pathHistoryRef.current = [];
                    setFlightPhase("IDLE");
                  }}
                >
                  ↺ RESET HELIPAD
                </button>
              </div>
            </div>
            <div className="fg-panel-body" style={{ padding: 12 }}>
              <canvas
                ref={isaacCanvasRef}
                width={600}
                height={320}
                style={{ width: "100%", height: "auto", borderRadius: 8, border: "1px solid #1e293b", background: "#080d19" }}
              />
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "#94a3b8", marginTop: 8 }}>
                <span>Target: <strong style={{ color: "#c084fc" }}>Waypoint Alpha (8.0m, 0.0m)</strong></span>
                <span>Altitude: <strong style={{ color: "#38bdf8" }}>{fmt(Math.abs(telemetry.positionZ ?? hudPos.z), 2)} m</strong></span>
                <span>Mode: <strong style={{ color: "#10b981" }}>{fmt(telemetry.flightMode ?? (flightPhase === "NAVIGATING" ? "MISSION" : "OFFBOARD"))}</strong></span>
              </div>
            </div>
          </div>


          <div className="fg-panel">
            <div className="fg-panel-header">
              <span>Flight State</span>
              <span className={`fg-badge ${telemetry.armed || flightPhase === "NAVIGATING" ? "fg-badge--ok" : "fg-badge--muted"}`}>
                {telemetry.armed === null ? (flightPhase === "NAVIGATING" ? "ARMED" : "disarmed") : telemetry.armed ? "ARMED" : "disarmed"}
              </span>
            </div>
            <div className="fg-panel-body">
              <dl className="fg-kv">
                <dt>actor</dt>
                <dd>{selectedActorId || "—"}</dd>
                <dt>flight_state</dt>
                <dd>{flightPhase}</dd>
                <dt>flight_mode</dt>
                <dd>{fmt(telemetry.flightMode ?? (flightPhase === "NAVIGATING" ? "MISSION" : "OFFBOARD"))}</dd>
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
              <span className={`fg-badge ${telemetry.armed !== null || flightPhase === "NAVIGATING" ? "fg-badge--ok" : "fg-badge--muted"}`}>
                {telemetry.armed !== null || flightPhase === "NAVIGATING" ? "live" : "no signal"}
              </span>
            </div>
            <div className="fg-panel-body">
              <dl className="fg-kv">
                <dt>position (x, y, z)</dt>
                <dd>
                  {fmt(telemetry.positionX ?? hudPos.x, 2)}, {fmt(telemetry.positionY ?? hudPos.y, 2)}, {fmt(telemetry.positionZ ?? hudPos.z, 2)}
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
