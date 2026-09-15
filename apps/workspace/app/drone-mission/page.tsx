"use client";

// Drone mission / crash-test status view. Actor picker follows goals/
// page.tsx's exact pattern (GET /actors for the list); status is derived
// by polling the EXISTING GET /actors/{id}/beliefs route (droneClient.ts)
// for visual_landmark_match / collision_event / disabled facts -- no new
// backend endpoint. Polling (not a one-shot fetch) because this status
// changes over the course of a live simulated flight, closer to voice/
// page.tsx's live-session posture than goals/page.tsx's static list.
//
// The "SIMULATION — CRASH TEST" banner is permanent and unconditional
// whenever this page is showing crash-test-relevant state -- deliberately
// impossible to mistake for a real-flight status display.
import { useEffect, useState } from "react";
import { RequireAuth } from "../../components/RequireAuth";
import { fetchAllActors, type Actor } from "../../lib/actorClient";
import { deriveMissionStatus, fetchActorBeliefs, type DroneMissionStatus } from "../../lib/droneClient";

const POLL_INTERVAL_MS = 3000;

function missionPhase(status: DroneMissionStatus): string {
  if (status.collision) return "Impact (simulated)";
  if (status.disabled) return "Disabled";
  if (status.landmark?.geometric_verified) return "Approaching → Impact";
  return "Awaiting visual identification";
}

function DroneMissionContent() {
  const [actors, setActors] = useState<Actor[]>([]);
  const [selectedActorId, setSelectedActorId] = useState("");
  const [status, setStatus] = useState<DroneMissionStatus>({ landmark: null, collision: null, disabled: false });
  const [error, setError] = useState("");

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

  useEffect(() => {
    if (!selectedActorId) return;
    let cancelled = false;

    const poll = () => {
      fetchActorBeliefs(selectedActorId)
        .then((result) => {
          if (cancelled) return;
          setError("");
          setStatus(deriveMissionStatus(result.beliefs.facts ?? []));
        })
        .catch((err) => {
          if (!cancelled) setError(err instanceof Error ? err.message : String(err));
        });
    };

    poll();
    const interval = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [selectedActorId]);

  return (
    <div className="page">
      <div
        style={{
          background: "#7a1f1f",
          color: "#fff",
          padding: "0.75rem 1rem",
          borderRadius: 6,
          fontWeight: 700,
          letterSpacing: "0.05em",
          marginBottom: "1rem",
        }}
      >
        SIMULATION — CRASH TEST · never controls a real drone
      </div>
      <h1>Drone Mission</h1>
      {actors.length > 0 && (
        <select
          className="actor-picker"
          value={selectedActorId}
          onChange={(e) => setSelectedActorId(e.target.value)}
        >
          {actors.map((actor) => (
            <option key={actor.actor_id} value={actor.actor_id}>
              {actor.name || actor.actor_id}
            </option>
          ))}
        </select>
      )}
      {error && <div className="empty-state">{error}</div>}
      {!error && selectedActorId && (
        <div className="login-card" style={{ width: "auto", maxWidth: 480 }}>
          <p>
            Target: <strong>{status.landmark?.landmark_id ?? status.collision?.target_landmark ?? "—"}</strong>
          </p>
          <p>
            Visual verification: <strong>{status.landmark?.geometric_verified ? "LoFTR ✓" : "not yet verified"}</strong>
          </p>
          <p>
            Crash-test authorization: <strong>Simulation-only ✓</strong>
          </p>
          <p>
            Status: <strong>{missionPhase(status)}</strong>
          </p>
          {status.collision && (
            <p style={{ color: "#7a1f1f" }}>
              Simulated collision recorded against <strong>{status.collision.target_landmark}</strong> —
              simulation_only={String(status.collision.simulation_only)}, crash_test={String(status.collision.crash_test)}
            </p>
          )}
        </div>
      )}
      {!selectedActorId && !error && <div className="empty-state">No actors found.</div>}
    </div>
  );
}

export default function DroneMissionPage() {
  return (
    <RequireAuth>
      <DroneMissionContent />
    </RequireAuth>
  );
}
