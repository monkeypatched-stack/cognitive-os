"use client";

// Thin port of the parts of living-world-explorer/src/api/actorClient.ts
// this app's /goals page needs — same backend, same shapes.
import { apiClient } from "./apiClient";

export interface Actor {
  actor_id: string;
  name: string;
  actor_type: string;
  description: string;
  status: string;
  is_active: boolean;
  societies: string[];
  goals?: string[];
  objective?: string;
  policies?: string[];
  trust_level?: number;
}

export function fetchAllActors(): Promise<Actor[]> {
  return apiClient.request<Actor[]>("/actors");
}

export interface ActorGoals {
  actor_id: string;
  goals: string[];
}

export function fetchActorGoals(actorId: string): Promise<ActorGoals> {
  return apiClient.request<ActorGoals>(`/actors/${actorId}/goals`);
}
