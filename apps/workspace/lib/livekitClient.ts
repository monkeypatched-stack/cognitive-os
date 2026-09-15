"use client";

import { apiClient } from "./apiClient";

// The backend endpoint built in kernel/edge/livekit_adapter.py +
// api/routes/livekit.py — mints a short-TTL, scope-bound room token for the
// authenticated caller (identity is the caller's own principal, never
// client-supplied). NEXT_PUBLIC_LIVEKIT_URL is the LiveKit server's own
// WebSocket URL (e.g. wss://your-livekit-host), separate from the
// CognitiveOS API this app otherwise talks to.
export const LIVEKIT_URL = process.env.NEXT_PUBLIC_LIVEKIT_URL ?? "";

export interface LiveKitTokenResponse {
  token: string;
  room: string;
  identity: string;
}

export function fetchLiveKitToken(room: string): Promise<LiveKitTokenResponse> {
  return apiClient.request<LiveKitTokenResponse>("/livekit/token", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ room }),
  });
}
