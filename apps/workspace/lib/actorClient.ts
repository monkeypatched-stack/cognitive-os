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

// Queues a goal via CognitiveActor.add_goal() (api/routes/actors.py::
// add_actor_goal) -- lower-level than promptActor below (no immediate
// execution against the actor's own dedicated Pod), kept for callers that
// genuinely just want to queue something for a later tick.
export function addActorGoal(actorId: string, goal: string, replaceGoal?: string): Promise<ActorGoals> {
  return apiClient.request<ActorGoals>(`/actors/${actorId}/goals`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ goal, replace_goal: replaceGoal ?? null }),
  });
}

export interface ActorPromptResult {
  actor_id: string;
  question: string;
  goal_achieved: boolean | null;
  actions: unknown[];
  plan: unknown;
}

// Sends a fresh mission straight to actorId's own dedicated actor Pod
// (api/routes/actors.py::prompt_actor -> kernel/edge/
// actor_prompt_forwarder.py -> actor_runtime.py's own POST /prompt) --
// the SAME call VoiceCommandRuntime makes for a spoken command, just
// reachable with typed/edited text. This is what actually flies a drone
// mission; addActorGoal() above only queues.
export function promptActor(actorId: string, question: string): Promise<ActorPromptResult> {
  return apiClient.request<ActorPromptResult>(`/actors/${actorId}/prompt`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
}
