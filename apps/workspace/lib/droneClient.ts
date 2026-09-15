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
