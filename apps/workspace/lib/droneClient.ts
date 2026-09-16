"use client";

// Drone mission / crash-test status -- reuses the EXISTING GET
// /actors/{id}/beliefs route (kernel/pipeline/belief_state.py's canonical
// BeliefState.to_dict(), which includes `facts`) rather than a new
// backend endpoint: visual_landmark_match and collision_event both reach
// belief as ordinary Facts (kernel/pipeline/observations.py's
// WorldPollingProvider), so this client only needs to read and filter
// what's already exposed, same shape actorClient.ts's fetchActorGoals uses.
import { apiClient } from "./apiClient";

export interface BeliefFact {
  entity: string;
  attribute: string;
  value: unknown;
  confidence: number;
  source: string;
  observed_at: number;
}

export interface ActorBeliefs {
  actor_id: string;
  beliefs: { facts?: BeliefFact[]; [key: string]: unknown };
}

export function fetchActorBeliefs(actorId: string): Promise<ActorBeliefs> {
  return apiClient.request<ActorBeliefs>(`/actors/${actorId}/beliefs`);
}

export interface VisualLandmarkMatch {
  landmark_id: string;
  match_score?: number;
  inlier_ratio?: number;
  geometric_verified: boolean;
}

export interface CollisionEvent {
  target_landmark: string;
  simulation_only: boolean;
  crash_test: boolean;
}

export interface DroneMissionStatus {
  landmark: VisualLandmarkMatch | null;
  collision: CollisionEvent | null;
  disabled: boolean;
}

// Latest fact per attribute wins -- BeliefFusion updates a fact in place
// rather than appending duplicates, but this stays correct even if more
// than one somehow exists for the same (entity, attribute).
export function deriveMissionStatus(facts: BeliefFact[]): DroneMissionStatus {
  let landmark: VisualLandmarkMatch | null = null;
  let collision: CollisionEvent | null = null;
  let disabled = false;
  let landmarkAt = -Infinity;
  let collisionAt = -Infinity;

  for (const fact of facts) {
    if (fact.attribute === "visual_landmark_match" && fact.observed_at >= landmarkAt) {
      landmark = fact.value as VisualLandmarkMatch;
      landmarkAt = fact.observed_at;
    } else if (fact.attribute === "collision_event" && fact.observed_at >= collisionAt) {
      collision = fact.value as CollisionEvent;
      collisionAt = fact.observed_at;
    } else if (fact.attribute === "disabled") {
      disabled = Boolean(fact.value);
    }
  }

  return { landmark, collision, disabled };
}

// Flight telemetry (kernel/pipeline/observations.py's WorldPollingProvider,
// backed by kernel/edge/drone_state.py's DroneState) reaches belief as the
// SAME facts array deriveMissionStatus reads above -- armed/position_x/
// position_y/position_z/heading/battery/flight_mode/gps_state, gated by a
// 5s freshness check server-side, so a field is simply absent from `facts`
// (not zeroed) whenever PX4 isn't actively reporting. null here means "no
// current reading", never a fabricated value -- render it as "--", not 0.
export interface DroneTelemetry {
  armed: boolean | null;
  positionX: number | null;
  positionY: number | null;
  positionZ: number | null;
  heading: number | null;
  battery: number | null;
  flightMode: string | null;
  gpsState: string | null;
}

const TELEMETRY_ATTRIBUTES: Record<string, keyof DroneTelemetry> = {
  armed: "armed",
  position_x: "positionX",
  position_y: "positionY",
  position_z: "positionZ",
  heading: "heading",
  battery: "battery",
  flight_mode: "flightMode",
  gps_state: "gpsState",
};

export function deriveTelemetry(facts: BeliefFact[]): DroneTelemetry {
  const telemetry: DroneTelemetry = {
    armed: null,
    positionX: null,
    positionY: null,
    positionZ: null,
    heading: null,
    battery: null,
    flightMode: null,
    gpsState: null,
  };
  const latestAt: Partial<Record<keyof DroneTelemetry, number>> = {};

  for (const fact of facts) {
    const key = TELEMETRY_ATTRIBUTES[fact.attribute];
    if (!key) continue;
    const prior = latestAt[key] ?? -Infinity;
    if (fact.observed_at >= prior) {
      latestAt[key] = fact.observed_at;
      (telemetry[key] as unknown) = fact.value;
    }
  }

  return telemetry;
}
